/*
 * "Motion": modulation applied to the delay's repeats. Original 1010music Blackbox, firmware 3.1.9.
 * No firmware calls in this file: host/render_motion.py compiles it for the Mac, so what is rendered there is this
 * code, and it would port to another box unchanged.
 *
 * The delay slot is a SEND, so after the stock delay has run, the bus holds the repeats and nothing else. That is
 * what this works on: the stock delay keeps its own Delay time, Feedback, Beat sync and filter, and one of these
 * rides on top of the repeats. The idea is the Strymon Timeline's: one engine, one knob row, the knobs' meaning
 * set by which motion is picked. Nothing here is taken from that pedal; these are the obvious four.
 *
 *   Trem    the repeats pulse. Rate = how fast, Depth = how far down each dip goes.
 *   Filter  a low-pass sweeps the repeats. Rate = how fast, Depth = how wide the sweep.
 *   Duck    the repeats drop out of the way while you play and come back after. Rate = how quickly they come
 *           back (slow .. fast), Depth = how far down.
 *   Swell   each time you play, the repeats fade in rather than arriving. Rate = how long the fade, Depth = how
 *           much of the signal waits for it.
 * Trem and Filter free-run in Hz (0.05 .. 12.8), like the per-pad insert's Rate. Tempo sync would be a later step.
 */
#include "motion.h"

#define FS      48000.0f
#define PI_F    3.14159265f
#define MAGIC   0x4d4f5431u      /* "MOT1" */
#define SMOOTH  0.002f           /* per-sample gain smoothing: no zipper when Depth or the motion changes */

static inline float clampf(float x, float lo, float hi)
{
    return x < lo ? lo : x > hi ? hi : x;
}

/* 2^x, within 0.3 % */
static float exp2_f(float x)
{
    if (x < -30.0f)
        return 0.0f;
    int i = (int)x - (x < 0.0f);
    float f = x - (float)i, p = 1.0f + f * (0.6565f + f * 0.3435f);
    union { float f; uint32_t u; } v;
    v.u = (uint32_t)(i + 127) << 23;
    return v.f * p;
}

/* -1 .. 1 over one turn of the phase, parabola sine */
static inline float sine(uint32_t phase)
{
    float y = (float)(int32_t)phase * (1.0f / 2147483648.0f);
    return 4.0f * y * (1.0f - (y < 0.0f ? -y : y));
}

void motion_block(struct motion_state *s, int type, float rate, float depth, float in_level, float *l, float *r,
                  int n)
{
    if (type <= MOTION_OFF || type >= MOTION_TYPES || n <= 0 || !l) {
        if (s->magic == MAGIC)
            s->magic = 0;                                    /* starts clean when it is switched on again */
        return;
    }
    rate = clampf(rate, 0.0f, 1.0f);
    depth = clampf(depth, 0.0f, 1.0f);
    if (s->magic != MAGIC || s->type != type) {
        for (unsigned i = 0; i < sizeof *s / 4; i++)
            ((uint32_t *)s)[i] = 0;
        s->g = 1.0f;
        s->ramp = 1.0f;
        s->type = (uint8_t)type;
        s->magic = MAGIC;
    }

    /* the repeats' own peak this block, and the peak of what went into the delay: one follower per block is
       enough at 128 frames (2.7 ms) */
    float wet = 0.0f;
    for (int i = 0; i < n; i++) {
        float a = l[i] < 0.0f ? -l[i] : l[i];
        if (a > wet)
            wet = a;
        if (r) {
            a = r[i] < 0.0f ? -r[i] : r[i];
            if (a > wet)
                wet = a;
        }
    }
    float env = s->env;
    if (in_level > env)
        env += 0.5f * (in_level - env);                      /* fast up */
    else
        env += 0.02f * (in_level - env);                     /* slow down */
    s->env = env;
    float wet_env = s->wet_env;
    wet_env = wet > wet_env ? wet : wet_env + 0.08f * (wet - wet_env);
    s->wet_env = wet_env;

    float target = 1.0f, cut = 0.0f;
    switch (type) {
    case MOTION_TREM: {
        float hz = 0.05f * exp2_f(8.0f * rate);
        s->phase += (uint32_t)(hz * (4294967296.0f / FS) * (float)n);
        target = 1.0f - depth * (0.5f - 0.5f * sine(s->phase));
        break;
    }
    case MOTION_FILTER: {
        float hz = 0.05f * exp2_f(8.0f * rate);
        s->phase += (uint32_t)(hz * (4294967296.0f / FS) * (float)n);
        /* 1.5 kHz at the middle of the sweep, up to three octaves either way at full Depth */
        cut = 1500.0f * exp2_f(3.0f * depth * sine(s->phase));
        break;
    }
    case MOTION_DUCK: {
        /* Rate = how fast the repeats come back: 1.5 s down to 40 ms */
        float ms = 1500.0f * exp2_f(-5.2f * rate);
        s->ramp += clampf((float)n * 1000.0f / (ms * FS), 0.0f, 1.0f) * (0.0f - s->ramp);
        if (env * 2.5f > s->ramp)
            s->ramp = clampf(env * 2.5f, 0.0f, 1.0f);        /* playing pushes it back down */
        target = 1.0f - depth * s->ramp;
        break;
    }
    case MOTION_SWELL: {
        /* The repeats' level is allowed to RISE only this slowly (2 s down to 60 ms) but to fall at once. What is
           left of a repeat once its attack has been taken off is the ratio of the two, so each repeat fades in
           instead of arriving. Rate = how long the fade, Depth = how much of it waits. */
        float ms = 2000.0f * exp2_f(-5.0f * rate);
        if (wet_env < s->ramp)
            s->ramp = wet_env;
        else
            s->ramp += clampf((float)n * 1000.0f / (ms * FS), 0.0f, 1.0f) * (wet_env - s->ramp);
        target = 1.0f - depth + depth * clampf(s->ramp / (wet_env + 1e-4f), 0.0f, 1.0f);
        break;
    }
    default:
        break;
    }

    float *ch[2] = {l, r};
    if (type == MOTION_FILTER) {
        /* two one-poles in series (12 dB/oct), the coefficient fixed for the block */
        float k = clampf(2.0f * PI_F * clampf(cut, 60.0f, 16000.0f) / FS, 0.0f, 1.0f);
        for (int c = 0; c < 2; c++) {
            float *x = ch[c];
            if (!x)
                continue;
            float z0 = s->lp[c], z1 = s->lp2[c];
            for (int i = 0; i < n; i++) {
                z0 += k * (x[i] - z0);
                z1 += k * (z0 - z1);
                x[i] = z1;
            }
            s->lp[c] = z0;
            s->lp2[c] = z1;
        }
        s->g = 1.0f;
        return;
    }
    float g = s->g;
    for (int c = 0; c < 2; c++) {
        float *x = ch[c];
        if (!x)
            continue;
        g = s->g;
        for (int i = 0; i < n; i++) {
            g += SMOOTH * (target - g);
            x[i] *= g;
        }
    }
    s->g = g;
}
