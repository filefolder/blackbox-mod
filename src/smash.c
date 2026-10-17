/*
 * Smash, a three-band upward + downward compressor. Original 1010music Blackbox, firmware 3.1.9.
 *
 * Clean-room: the technique is the "OTT" preset of Ableton's Multiband Dynamics (which Xfer's free OTT recreates).
 * Three bands (Linkwitz-Riley 24 dB crossovers at 88.3 Hz and 2.5 kHz); each band is squashed from above AND lifted
 * from below, so everything ends up at the same loud level. Thresholds, ratios, times and band gains are that preset's
 * as I remember them (NOT checked against Live):
 *            above        below          attack / release   out
 *   High   -35.5 inf:1   -40.8 1:4.17    13.5 / 132 ms     +10.3 dB
 *   Mid    -30.2 66.7:1  -41.8 1:4.17    22.4 / 282 ms      +5.7 dB
 *   Low    -33.8 66.7:1  -40.8 1:4.17    47.8 / 282 ms     +10.3 dB
 *
 *   depth   0..1: scales every gain in dB (0 = only the crossover's phase shift is left)
 *   time    0..1: attack / release x0.25 .. x4, 0.5 = the preset's
 *   push    linear input gain (comp.c: the Comp Thresh knob, lower threshold = more push)
 *
 * No firmware calls in here, so host/ can compile the same file for the Mac and render audio with it.
 */
#include <stdint.h>

#pragma GCC optimize("Os", "no-tree-loop-distribute-patterns")

#include "smash_coef.inc"

#define SMASH_FS 48000.0f
#define UP_MAX 24.0f             /* dB: the most a quiet band is lifted */
#define GATE_DB -72.0f           /* below this the lift fades out (20 dB further down it is gone) */
#ifndef MAKEUP_DB
#define MAKEUP_DB 6.0f           /* at full depth: a hot mix comes out ~2.5 dB louder (host/dsp.py), quiet ones far more */
#endif
#ifndef SMASH_CEIL
#define SMASH_CEIL 0.9661f         /* -0.3 dBFS, the other models' ceiling */
#endif

#define LIM_AT (1.5f * SMASH_CEIL)
#define LIM_REL 0.99965f          /* 60 ms */

struct smash_band {
    float above, slope_above, below, slope_below, att_ms, rel_ms, out_db, det;
};

static const struct smash_band bands[3] = {
    /* low */ {-33.8f, 1.0f - 1.0f / 66.7f, -40.8f, 1.0f - 1.0f / 4.17f, 47.8f, 282.0f, 10.3f, 0.9979f},
    /* mid */ {-30.2f, 1.0f - 1.0f / 66.7f, -41.8f, 1.0f - 1.0f / 4.17f, 22.4f, 282.0f, 5.7f, 0.9917f},
    /* high */ {-35.5f, 1.0f, -40.8f, 1.0f - 1.0f / 4.17f, 13.5f, 132.0f, 10.3f, 0.9794f},
};

struct smash_state {
    uint32_t magic;
    float z[2][9][2];            /* per channel: lp1 lp1 hp1 hp1 lp2 lp2 hp2 hp2 ap2 */
    float ms[3], g[3];           /* per band: detector mean square, gain in dB */
    float lim;                   /* output limiter: gain reduction, 0 = none .. 1 */
};

static inline __attribute__((always_inline)) float smash_log2(float x)
{
    union { float f; uint32_t u; } v = {x};
    float e = (float)((int)((v.u >> 23) & 0xff) - 128);
    v.u = (v.u & 0x007fffffu) | 0x3f800000u;
    float m = v.f;
    return e + (-0.34484843f * m + 2.02466578f) * m - 0.67487759f;
}

static inline __attribute__((always_inline)) float smash_exp2(float x)
{
    if (x < -30.0f)
        return 0.0f;
    if (x > 30.0f)
        x = 30.0f;
    int i = (int)x - (x < 0.0f);
    float f = x - (float)i;
    float p = 1.0f + f * (0.6565f + f * 0.3435f);
    union { float f; uint32_t u; } v = {p};
    v.u += (uint32_t)i << 23;
    return v.f;
}

static inline __attribute__((always_inline)) float biquad(const float *c, float *z, float x)
{
    float y = c[0] * x + z[0];
    z[0] = c[1] * x - c[3] * y + z[1];
    z[1] = c[2] * x - c[4] * y;
    return y;
}

static inline float smash_soft(float x)
{
    x *= 1.0f / SMASH_CEIL;
    x = x < -3.0f ? -3.0f : x > 3.0f ? 3.0f : x;
    return SMASH_CEIL * x * (27.0f + x * x) / (27.0f + 9.0f * x * x);
}

void smash_reset(struct smash_state *s, uint32_t magic)
{
    for (unsigned i = 0; i < sizeof *s / 4; i++)
        ((volatile uint32_t *)s)[i] = 0;
    s->magic = magic;
}

void smash_block(struct smash_state *s, float depth, float time, float push, float *l, float *r, int n)
{
    depth = depth < 0.0f ? 0.0f : depth > 1.0f ? 1.0f : depth;
    time = time < 0.0f ? 0.0f : time > 1.0f ? 1.0f : time;
    float stretch = smash_exp2(4.0f * (0.5f - time));          /* 1 / (x0.25 .. x4) */
    float att[3], rel[3], out[3];
    for (int b = 0; b < 3; b++) {
        att[b] = smash_exp2(-1.4427f * stretch / (bands[b].att_ms * 0.001f * SMASH_FS));
        rel[b] = smash_exp2(-1.4427f * stretch / (bands[b].rel_ms * 0.001f * SMASH_FS));
        out[b] = depth * (bands[b].out_db + MAKEUP_DB);
    }
    float ms[3] = {s->ms[0], s->ms[1], s->ms[2]}, g[3] = {s->g[0], s->g[1], s->g[2]}, lim = s->lim;
    float *ch[2] = {l, r};
    for (int i = 0; i < n; i++) {
        float band[2][3];
        for (int c = 0; c < 2; c++) {
            float (*z)[2] = s->z[c];
            float x = ch[c][i] * push;
            float lo = biquad(xo_lp1, z[1], biquad(xo_lp1, z[0], x));
            float rest = biquad(xo_hp1, z[3], biquad(xo_hp1, z[2], x));
            band[c][0] = biquad(xo_ap2, z[8], lo);           /* same phase as the split bands above it */
            band[c][1] = biquad(xo_lp2, z[5], biquad(xo_lp2, z[4], rest));
            band[c][2] = biquad(xo_hp2, z[7], biquad(xo_hp2, z[6], rest));
        }
        float yl = 0.0f, yr = 0.0f;
        for (int b = 0; b < 3; b++) {
            const struct smash_band *p = &bands[b];
            float a = band[0][b], c = band[1][b];
            float pw = a * a > c * c ? a * a : c * c;
            ms[b] = pw + p->det * (ms[b] - pw);
            float level = 3.0103f * smash_log2(ms[b] + 1e-12f), target = 0.0f;
            if (level > p->above) {
                target = (p->above - level) * p->slope_above;
            } else if (level < p->below) {
                target = (p->below - level) * p->slope_below;
                if (target > UP_MAX)
                    target = UP_MAX;
                if (level < GATE_DB)
                    target *= level < GATE_DB - 20.0f ? 0.0f : (level - (GATE_DB - 20.0f)) * 0.05f;
            }
            g[b] = target + (target < g[b] ? att[b] : rel[b]) * (g[b] - target);
            float lin = smash_exp2((depth * g[b] + out[b]) * 0.16610f);
            yl += a * lin;
            yr += c * lin;
        }
        /* the slow attacks let a hit through at up to band gain over full scale: ride it down (instant, 60 ms back),
           and leave only the last 3.5 dB to the soft clip */
        float pk = yl < 0.0f ? -yl : yl, pr = yr < 0.0f ? -yr : yr;
        pk = pk > pr ? pk : pr;
        lim *= LIM_REL;
        if (pk * (1.0f - lim) > LIM_AT)
            lim = 1.0f - LIM_AT / pk;
        l[i] = smash_soft(yl * (1.0f - lim));
        r[i] = smash_soft(yr * (1.0f - lim));
    }
    for (int b = 0; b < 3; b++) {
        s->ms[b] = ms[b];
        s->g[b] = g[b];
    }
    s->lim = lim;
}
