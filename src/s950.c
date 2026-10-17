/*
 * The S950 Fidelity modes' signal path, firmware-free (filter.c calls it; host/build.sh compiles it for the Mac, where
 * host/s950.py checks it against the reference model host/s950_ref.py).
 *
 * From the Akai service schematic (S900 voice block diagram 860913A) and the S950's own OS (see NOTES.md, "S950"):
 *   record    input -> 36 dB/oct low-pass at 0.4 x the sample rate -> 12-bit converter. The sample rate is 2.5 x the
 *             "Audio bandwidth" setting, 7.5 to 48 kHz.
 *   playback  per voice a 12-bit DAC whose CLOCK is the pitch: every stored sample comes out once, held until the next,
 *             no interpolation. Then a 36 dB/oct low-pass whose cutoff is 80000 / M Hz for a whole number M: at most
 *             0.4 x the voice's clock (so it follows the note down and takes the staircase's images off), at most
 *             16 kHz, and in steps that get coarse at the top (16 k, 13.3 k, 11.4 k, 10 k, 8.9 k, 8 k ...).
 * Here the pad's sample stands in for the recording: s950_read reads it only on the machine's sample grid, through a
 * 5-tap stand-in for the input filter, clips and rounds each stored sample to 12 bits, and gives each output frame the
 * AREA of the DAC staircase under it (a frame that straddles two or more stored samples gets their weighted mean), so
 * the staircase's edges are not snapped to the 48 kHz frame. s950_filter is the tracking low-pass.
 * Not here: the 1 to 4 cent pitch error of the real clock divider (the voice's rate is the firmware's to choose), the
 * VCA's 8-bit level steps, noise.
 */
#include <stdint.h>

#pragma GCC optimize("O2", "no-tree-loop-distribute-patterns")

#define PI_F 3.14159265f

static inline float s9_floor(float x)
{
    int i = (int)x;
    return (float)(i - (x < (float)i));
}

static inline int s9_round(float x)
{
    return (int)(x + (x >= 0.0f ? 0.5f : -0.5f));
}

/*
 * The voice reader's kernel for an S950 mode (same job and arguments as filter.c's machine_read, plus the anchoring it
 * worked out). base = absolute source position of src[0]; sgn = -1 when the scratch is a reversed read (src[j] is then
 * source[base - j]). gain / post: the Saturation knob as converter input level (1, 1 when off).
 */
void s950_read(float grid, const float *fir, int32_t base, float sgn, float gain, float post, float rate,
               const float *src, int n_src, int pos, int *newpos, float *frac, float *dst, int n)
{
    /* a float holds a position 60 s into a sample to only a quarter of a frame: anchor once in double */
    double gd = 48000.0 / (double)grid;
    double kb = (double)(int32_t)((double)base / gd);
    if (kb * gd > (double)base)
        kb -= 1.0;
    float a0 = (float)((double)base - kb * gd);             /* 0 <= a0 < g: where src[0] sits on the grid */
    float g = (float)gd, inv_g = grid * (1.0f / 48000.0f);
    float c = sgn * a0 * inv_g;                             /* cell time = scratch index / g + c; cell m is [m, m + 1) */
    float per = rate * inv_g;                               /* stored samples clocked out per output frame */
    float inv_per = 1.0f / per;
    float joff = sgn > 0.0f ? -a0 : g + a0;                 /* cell m's stored sample is source index m x g + joff */
    int hi = n_src - 3, held_m = -0x7fffffff;
    float held = 0.0f, f = *frac;
    int idx = pos;

#define CELL(M, OUT)                                                                                              \
    if ((M) != held_m) {                                                                                          \
        int j = s9_round((float)(M) * g + joff);                                                                  \
        j = j < 2 ? 2 : j > hi ? hi : j;                                                                          \
        const float *s = src + j;                                                                                 \
        float v = (fir[0] * (s[-2] + s[2]) + fir[1] * (s[-1] + s[1]) + fir[2] * s[0]) * gain;                     \
        v = v > 0.99951f ? 0.99951f : v < -1.0f ? -1.0f : v;                                                      \
        held = (float)s9_round(v * 2048.0f) * (1.0f / 2048.0f) * post;                                            \
        held_m = (M);                                                                                             \
    }                                                                                                             \
    OUT = held;

    for (int i = 0; i < n; i++) {
        float t0 = ((float)idx + f) * inv_g + c, t1 = t0 + per;
        float m0f = s9_floor(t0), m1f = s9_floor(t1);
        int m0 = (int)m0f, m1 = (int)m1f;
        float v;
        CELL(m0, v)
        if (m1 != m0) {
            float acc = v * (m0f + 1.0f - t0), w;
            for (int m = m0 + 1; m < m1; m++) {
                CELL(m, w)
                acc += w;
            }
            CELL(m1, w)
            v = (acc + w * (t1 - m1f)) * inv_per;
        }
        dst[i] = v;
        float adv = rate + f;
        int step = (int)adv;
        idx += step;
        f = adv - (float)step;
    }
#undef CELL
    *frac = f;
    *newpos = idx;                       /* the stock kernels return (pointer - src) + 1, pointer = src + idx - 1 */
}

/* The OS's filter table (image offset 0x98a8 + 0x62): the filter clock's count for filter index 98 .. 168, a semitone
   per index; index 168 is the Filter setting 99, wide open. */
static const uint8_t filter_count[71] = {
    255, 241, 227, 214, 202, 191, 180, 170, 161, 152, 143, 135, 128, 120, 114, 107, 101, 96, 90, 85, 80, 76, 72, 68,
    64,  60,  57,  54,  51,  48,  45,  43,  40,  38,  36,  34,  32,  30,  28,  27,  25,  24, 23, 21, 20, 19, 18, 17,
    16,  15,  14,  13,  13,  12,  11,  11,  10,  9,   9,   8,   8,   8,   7,   7,   6,   6,  6,  5,  5,  5,  5,
};

/* The filter clock's count for a voice whose DAC runs at `clock` Hz: the cutoff is 80000 / count Hz. */
unsigned s950_filter_count(float clock, unsigned index)
{
    if (clock < 800.0f)
        clock = 800.0f;                                      /* the OS keeps the voice timer's count under 20000 */
    unsigned track = (unsigned)(16000000.0f / clock) / 80u; /* the voice timer's count / 80: 0.4 x the clock */
    unsigned user = filter_count[(index < 98 ? 98 : index > 168 ? 168 : index) - 98];
    unsigned m = user > track ? user : track;
    return m > 255 ? 255 : m;
}

/*
 * The MF6CN-50: 6th-order Butterworth low-pass at 80000 / count Hz, three biquads; state z[channel * 6 + section * 2].
 * It runs at 96 kHz, each frame in twice and the second result out. At 48 kHz the usual (bilinear) design falls far too
 * fast near the top: with the cutoff at 10 kHz it is 18 dB under the chip at 16 kHz and 46 dB under at 20 kHz, and the
 * S950's sound when pitched down is partly what its filter does NOT take off. At 96 kHz the shape holds to 20 kHz, and
 * feeding each frame twice droops the top by about what the real DAC's hold does (1.3 dB at 16 kHz).
 */
void s950_filter(float *z, float **ch, int n, unsigned count)
{
    static const float damp[3] = {1.9318517f, 1.4142136f, 0.5176381f};      /* 1 / Q of the three sections */
    float x = PI_F * (80000.0f / 96000.0f) / (float)count;                 /* <= 0.53: the table's top is 16 kHz */
    float x2 = x * x, k = x * (15.0f - x2) / (15.0f - 6.0f * x2), k2 = k * k;   /* tan, Pade (3,2) */
    float b0[3], a1[3], a2[3];
    for (int s = 0; s < 3; s++) {
        float norm = 1.0f / (1.0f + k * damp[s] + k2);
        b0[s] = k2 * norm;
        a1[s] = 2.0f * (k2 - 1.0f) * norm;
        a2[s] = (1.0f - k * damp[s] + k2) * norm;
    }
    for (int c = 0; c < 2; c++) {
        float *p = ch[c];
        if (!p)
            continue;
        float *zc = z + c * 6;
        float s0 = zc[0], s1 = zc[1], s2 = zc[2], s3 = zc[3], s4 = zc[4], s5 = zc[5];
        for (int i = 0; i < n; i++) {
            float y = 0.0f;
            for (int half = 0; half < 2; half++) {          /* transposed direct form II; b1 = 2 b0, b2 = b0 */
                float in = b0[0] * p[i];
                y = in + s0;
                s0 = in + in - a1[0] * y + s1;
                s1 = in - a2[0] * y;
                in = b0[1] * y;
                y = in + s2;
                s2 = in + in - a1[1] * y + s3;
                s3 = in - a2[1] * y;
                in = b0[2] * y;
                y = in + s4;
                s4 = in + in - a1[2] * y + s5;
                s5 = in - a2[2] * y;
            }
            p[i] = y;
        }
        zc[0] = s0, zc[1] = s1, zc[2] = s2, zc[3] = s3, zc[4] = s4, zc[5] = s5;
    }
}
