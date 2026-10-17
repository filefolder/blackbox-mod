/*
 * Relapse, a memory echo for the delay slot (FX1, Type: Relapse). Original 1010music Blackbox, firmware 3.1.9.
 *
 * Clean-room, after the Chase Bliss Habit manual: an echo that keeps everything it hears in a long memory and, besides
 * the normal repeats, drops in snippets from earlier in that memory. The real pedal remembers 3 minutes; here the
 * memory is the stock delay's two long lines used as one 16-bit stereo loop, 384512 frames = 8.0 s at 48 kHz.
 *
 *   Size      echo time as a note length at the preset tempo (1/32 .. 1 bar, at most 4 s)
 *   Feedback  the delay's own (0x38): repeats
 *   Recall      how often a snippet from the memory is played: each echo period rolls against it (0 = a plain echo)
 *   Reach    how far back the snippets reach: 0 = just behind the echo, 100 % = the whole memory
 *   Twist    what happens to a snippet: Off, Reverse, Oct Dn, Oct Up, Stutter
 *   Tone      darkens the repeats (low-pass in the feedback path)
 *   Feed      On = snippets are written back into the memory, so it keeps re-collecting itself
 *
 * munchi.cpp owns the slot (type dispatch, send in, wet out); this file is the engine. State lives at the start of the
 * delay's third line (line 2 in munchi.cpp's naming), guarded by a magic word.
 */
#include <stdint.h>

#pragma GCC optimize("Os", "no-tree-loop-distribute-patterns")

#define FN(addr) ((addr) | 1u)
typedef int (*setting_fn)(const void *store, unsigned param);
#define fw_setting ((setting_fn)FN(0x08093e9c))

#define P_FEEDBK 0x38
#define P_SIZE   0x1e3
#define P_RECALL   0x1e4
#define P_REACH 0x1e9           /* not 0x1e5: stock treats that id as a flag (see munchi.cpp) */
#define P_TWIST 0x1e6
#define P_TONE   0x1e7
#define P_FEED   0x1e8

#define FS        48000.0f
#define MEM_GAIN  16384.0f           /* 16-bit store with 6 dB of headroom */
#define XFADE     480                /* 10 ms when the echo time changes */
#define FADE      240                /* 5 ms on each end of a snippet */
#define CHUNK_FADE 48
#define RECALL_GAIN 0.8f
#define MAGIC     0x524c5031u        /* "RLP1" */

enum { TWIST_OFF, TWIST_REVERSE, TWIST_OCT_DN, TWIST_OCT_UP, TWIST_STUTTER, TWISTS };
#define SIZES 9
static const float size_beats[SIZES] = {0.125f, 0.25f, 1.0f / 3.0f, 0.5f, 0.75f, 1.0f, 1.5f, 2.0f, 4.0f};

struct relapse {
    uint32_t magic;
    uint32_t w, filled;              /* write frame, frames recorded so far (up to the memory length) */
    uint32_t d_cur, d_old, xf;       /* echo time in frames, the one before it, cross-fade frames left */
    uint32_t period;                 /* frames until the next recall roll */
    uint32_t rng;
    float lp[2];
    uint32_t s_left, s_len;          /* snippet: frames left, total */
    float s_pos, s_rate;             /* read position in the memory (frames), step per output frame */
    uint32_t st_len, st_cnt;         /* stutter: chunk length, frames into the chunk */
    float st_start;
};

static inline float rnd(uint32_t *s)
{
    *s = *s * 1664525u + 1013904223u;
    return (float)(*s >> 8) * (1.0f / 16777216.0f);
}

static inline float softclip(float x)
{
    x = x < -3.0f ? -3.0f : x > 3.0f ? 3.0f : x;
    return x * (27.0f + x * x) / (27.0f + 9.0f * x * x);
}

static inline float unit(float v)
{
    return v < 0.0f ? 0.0f : v > 1.0f ? 1.0f : v;
}

/*
 * One block. state: struct relapse; a / b: the two memory halves, `half` stereo frames each; in / out: n frames.
 * fresh: munchi.cpp just cleared the lines (type change).
 */
void relapse_block(uint8_t *state, int16_t *a, int16_t *b, uint32_t half, const void *store, float bpm,
                 const float *il, const float *ir, float *ol, float *orr, int n, int fresh)
{
    struct relapse *h = (struct relapse *)state;
    uint32_t mem = half * 2;
    if (fresh || h->magic != MAGIC) {
        for (unsigned i = 0; i < sizeof *h / 4; i++)
            ((volatile uint32_t *)h)[i] = 0;
        h->rng = 0x9e3779b9u;
        h->magic = MAGIC;
    }
#define MEMP(i, c) (((i) < half ? a + (i) * 2 : b + ((i) - half) * 2)[c])

    float fb = unit((float)fw_setting(store, P_FEEDBK) * 0.001f) * 0.95f;
    unsigned size = (unsigned)fw_setting(store, P_SIZE), twist = (unsigned)fw_setting(store, P_TWIST);
    float recall = unit((float)fw_setting(store, P_RECALL) * 0.001f), reach = unit((float)fw_setting(store, P_REACH) * 0.001f);
    float lp_a = 1.0f - 0.9f * unit((float)fw_setting(store, P_TONE) * 0.001f);
    float feed = fw_setting(store, P_FEED) ? 0.5f * fb : 0.0f;
    if (!(bpm > 20.0f))
        bpm = 120.0f;
    float df = size_beats[size < SIZES ? size : 5] * 60.0f / bpm * FS;
    uint32_t d = df < 480.0f ? 480u : df > (float)(mem / 2 - 4800) ? mem / 2 - 4800 : (uint32_t)df;
    if (d != h->d_cur) {
        h->d_old = h->d_cur ? h->d_cur : d;
        h->d_cur = d;
        h->xf = XFADE;
        if (h->period > d)
            h->period = d;
    }

    const float *in[2] = {il, ir};
    float *out[2] = {ol, orr};
    for (int i = 0; i < n; i++) {
        uint32_t r1 = h->w >= h->d_cur ? h->w - h->d_cur : h->w + mem - h->d_cur;
        uint32_t r0 = h->w >= h->d_old ? h->w - h->d_old : h->w + mem - h->d_old;
        float xw = (float)h->xf * (1.0f / XFADE);

        if (!h->period) {                        /* once per echo period: maybe start a snippet */
            h->period = h->d_cur;
            uint32_t have = h->filled < mem - h->d_cur - 1024 ? h->filled : mem - h->d_cur - 1024;
            if (!h->s_left && recall > 0.0f && have >= 3 * h->d_cur && rnd(&h->rng) < recall) {
                float span = 2.0f * (float)h->d_cur + reach * (float)(have - 2 * h->d_cur);
                uint32_t kmax = (uint32_t)(span / (float)h->d_cur), k = 2 + (uint32_t)(rnd(&h->rng) * (float)(kmax - 1));
                if (k > kmax)
                    k = kmax;
                uint32_t back = k * h->d_cur;
                h->s_rate = twist == TWIST_REVERSE ? -1.0f : twist == TWIST_OCT_DN ? 0.5f : twist == TWIST_OCT_UP ? 2.0f : 1.0f;
                if (twist == TWIST_REVERSE)
                    back -= h->d_cur;            /* start at the segment's end and walk back */
                h->s_pos = (float)(h->w >= back ? h->w - back : h->w + mem - back);
                h->s_len = h->s_left = h->d_cur;
                h->st_len = twist == TWIST_STUTTER ? (h->d_cur / 4 < 2 * CHUNK_FADE ? 2 * CHUNK_FADE : h->d_cur / 4) : 0;
                h->st_cnt = 0;
                h->st_start = h->s_pos;
            }
        }
        h->period--;

        float env = 0.0f, frac = 0.0f;
        uint32_t s0 = 0, s1 = 0;
        if (h->s_left) {
            uint32_t done = h->s_len - h->s_left, fade = h->s_len / 4 < FADE ? h->s_len / 4 : FADE;
            env = done < fade ? (float)done / (float)fade : h->s_left < fade ? (float)h->s_left / (float)fade : 1.0f;
            if (h->st_len) {
                uint32_t c = h->st_cnt, rest = h->st_len - c;
                if (c < CHUNK_FADE)
                    env *= (float)c * (1.0f / CHUNK_FADE);
                else if (rest < CHUNK_FADE)
                    env *= (float)rest * (1.0f / CHUNK_FADE);
            }
            s0 = (uint32_t)h->s_pos;
            frac = h->s_pos - (float)s0;
            if (s0 >= mem)
                s0 = 0;
            s1 = s0 + 1 < mem ? s0 + 1 : 0;
            h->s_pos += h->s_rate;
            if (h->s_pos >= (float)mem)
                h->s_pos -= (float)mem;
            else if (h->s_pos < 0.0f)
                h->s_pos += (float)mem;
            if (h->st_len && ++h->st_cnt >= h->st_len) {
                h->st_cnt = 0;
                h->s_pos = h->st_start;
            }
            h->s_left--;
        }

        for (int c = 0; c < 2; c++) {
            float echo = (float)MEMP(r1, c);
            if (h->xf)
                echo += ((float)MEMP(r0, c) - echo) * xw;
            echo *= 1.0f / MEM_GAIN;
            float snip = 0.0f;
            if (env > 0.0f) {
                float p = (float)MEMP(s0, c);
                snip = (p + ((float)MEMP(s1, c) - p) * frac) * (env * RECALL_GAIN / MEM_GAIN);
            }
            h->lp[c] += (echo - h->lp[c]) * lp_a;
            float wr = 2.0f * softclip(0.5f * (in[c][i] + fb * h->lp[c] + feed * snip)) * MEM_GAIN;
            MEMP(h->w, c) = (int16_t)(wr > 32767.0f ? 32767.0f : wr < -32767.0f ? -32767.0f : wr);
            out[c][i] = echo + snip;
        }
        if (h->xf)
            h->xf--;
        if (++h->w >= mem)
            h->w = 0;
        if (h->filled < mem)
            h->filled++;
    }
#undef MEMP
}
