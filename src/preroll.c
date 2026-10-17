/*
 * Record pre-roll: the loop plays for a set number of bars before a recording starts. Original 1010music Blackbox,
 * 3.1.9.
 *
 * Why: resampling a sequence from a standing start records a dry first pass: no note tails, no reverb or delay
 * trails, nothing carried round from the end of the loop. With a pre-roll the take begins after the loop has already
 * been round, so the clip holds what a loop really sounds like on its second pass and loops naturally.
 *
 * Stock: the WAV recorder (engine class with vtable 0x080d07ac, block method 0x0806201c, which Ghidra never made a
 * function) is told to start by message 0x25. Its start routine (0x08061c94) works in clock ticks, 960 to a beat:
 *   +0x28 int64  start = the clock position rounded up to Rec Quant (0 while the clock is stopped)
 *   +0x30 int64  stop  = start + Length, when a Length is set (+0x4a = 1)
 *   +0x38 int64  Rec Quant in ticks (0 = None), +0x40 int64 Length in ticks (0 = Custom)
 *   +0x48 armed, +0x49 waiting to start, +0x4b RecToPlay, +0x60 RecThresh
 * Each block while waiting it writes from the sample where `start` falls. One more rule there: if the next Rec Quant
 * point from now is before `start` and the position is not negative (a lead-in), it starts at once. That is for a
 * clock that jumped backwards, and it would also end any pre-roll in its first block.
 *
 * This adds the pre-roll to `start` and to `stop` after the stock routine has set them, and hands that one rule a
 * negative position while a pre-roll is selected, so the recorder keeps waiting for `start`. Everything else is
 * stock: the exact start sample, Length, RecToPlay, the message to the screen. With Pre-roll Off the recorder is
 * untouched.
 * Not pre-rolled: threshold recording (it starts on level, not on the clock) and a recording started with the clock
 * stopped and Rec Quant None (stock starts that one at once, with nothing playing to roll).
 *
 * Setting: "Pre-roll:" Off / 1 bar / 2 bars / 4 bars / 8 bars on Tools > Rec, after Lead In (global, saved with the
 * other Tools settings as "recpreroll"). A bar is four beats, as in stock's own Rec Quant list.
 */
#include <stdint.h>

#define FN(addr) ((addr) | 1u)

typedef void (*register_fn)(void *table, int param, const char *label, const char *const *names, int count,
                            const char *xml);
typedef void (*store_add_fn)(void *store, int param, int value);
typedef int (*setting_fn)(const void *store, int param);
typedef void (*rec_start_fn)(uint8_t *rec, const uint8_t *clock);
typedef void (*position_fn)(int32_t *out, const uint8_t *clock);

#define fw_register_list ((register_fn)FN(0x0808c0d8))
#define fw_store_add     ((store_add_fn)FN(0x08093ef6))
#define fw_setting       ((setting_fn)FN(0x08093e9c))
#define fw_rec_start     ((rec_start_fn)FN(0x08061c94))
#define fw_position      ((position_fn)FN(0x08069c38))     /* the clock's position in ticks, as an int64 */

#define SETTINGS ((const void *)(0x24020088u + 0x8c90u))    /* app: global settings store */
#define PARAM_PREROLL 0x1b1      /* free in stock (0x1b5 is not), ours: see NOTES.md "param ids" */
#define CHOICES 5
#define TICKS_PER_BAR (4 * 960)

#define CLOCK_PLAYING 0x38
#define REC_START     0x28
#define REC_STOP      0x30
#define REC_QUANT     0x38
#define REC_STOP_SET  0x4a
#define REC_THRESH    0x60

static const char *const preroll_names[CHOICES] = {"Off", "1 bar", "2 bars", "4 bars", "8 bars"};

void preroll_register(void *table)
{
    fw_register_list(table, PARAM_PREROLL, "Pre-roll:", preroll_names, CHOICES, "recpreroll");
}

void preroll_defaults(void *store)
{
    fw_store_add(store, PARAM_PREROLL, 0);
}

static int32_t preroll_ticks(void)
{
    unsigned v = (unsigned)fw_setting(SETTINGS, PARAM_PREROLL);
    return v - 1 < CHOICES - 1 ? TICKS_PER_BAR << (v - 1) : 0;
}

/* Replaces the recorder's call to its start routine (bl @0x08062108). */
void preroll_start(uint8_t *rec, const uint8_t *clock)
{
    fw_rec_start(rec, clock);
    int32_t pre = preroll_ticks();
    if (!pre || rec[REC_THRESH])
        return;
    if (!clock[CLOCK_PLAYING] && !*(const int64_t *)(rec + REC_QUANT))
        return;
    *(int64_t *)(rec + REC_START) += pre;
    if (rec[REC_STOP_SET])
        *(int64_t *)(rec + REC_STOP) += pre;
}

/* Replaces the position read whose sign decides "start at once" while the recorder waits (bl @0x0806283a). Only the
   sign of the high word is used from this copy; the recorder reads the position again for everything else. */
void preroll_position(int32_t *out, const uint8_t *clock)
{
    fw_position(out, clock);
    if (preroll_ticks())
        out[1] = -1;
}
