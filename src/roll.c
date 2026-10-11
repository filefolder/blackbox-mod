/*
 * Piano roll: moving notes in time wraps around the pattern instead of stopping at its start and end.
 * Original 1010music Blackbox, firmware 3.1.9.
 *
 * Stock, two things stop a note at the edges:
 *  1. The selected note's timing knob is a stepper (view + 0xa918; the stepper code is FUN_080b0b74) whose range is
 *     0 .. Step Count - 1. Steppers already have a wrap mode (byte +0x14), but the piano roll view's constructor
 *     (@0x080ba1e2) leaves it off for this one. patches/roll.py turns it on there, so the knob carries on from the
 *     last step to the first and back.
 *  2. Every other selected note follows the knob by the same delta (FUN_080c0288), and each moved value goes through
 *     a per-field clamp (FUN_080c01c8). For the start it is 0 .. Step Count - length, so a selection squashes against
 *     the boundary. roll_clamp replaces that one call: start fields wrap modulo the pattern, everything else is stock.
 *
 * Because both wrap modulo the same length, a selection keeps its shape: when the knob note wraps from the last step
 * to the first, the delta is -(steps - 1), and old - (steps - 1) is old + 1 modulo the pattern.
 * A note may end past the pattern's end after a wrap, as the knob note already could on stock.
 */
#include <stdint.h>

#define FN(addr) ((addr) | 1u)

typedef int (*clamp_fn)(int value, int field, const void *event, int limit, int ticks_per_step);
#define fw_clamp ((clamp_fn)FN(0x080c01c8))

/* field numbers of message 0x16f (piano roll -> app: set one field of a note event) */
#define FIELD_START_STEPS 2      /* start in steps; the app stores value * 960 */
#define FIELD_START_TICKS 3      /* start in ticks (fine mode; 3.1.9 never turns it on) */
#define MAX_STEPS 0x100          /* Step Count's range (param 0x86) */
#define MAX_TICKS_PER_STEP 0x100000

static int wrap(int value, int n)
{
    value %= n;
    return value < 0 ? value + n : value;
}

/*
 * Replaces the clamp call (bl @0x080c03aa) in the "apply this edit to the rest of the selection" loop.
 * limit = the pattern's Step Count, ticks_per_step = its Step Len in ticks (the stack argument).
 */
int roll_clamp(int value, int field, const void *event, int limit, int ticks_per_step)
{
    if (field == FIELD_START_STEPS && limit > 0 && limit <= MAX_STEPS)
        return wrap(value, limit);
    if (field == FIELD_START_TICKS && limit > 0 && limit <= MAX_STEPS && ticks_per_step > 0 &&
        ticks_per_step < MAX_TICKS_PER_STEP)
        return wrap(value, limit * ticks_per_step);
    return fw_clamp(value, field, event, limit, ticks_per_step);
}
