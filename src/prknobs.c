/*
 * Piano roll knobs: nothing selected by default; the lower knobs move the selection, or every note.
 * Original 1010music Blackbox, firmware 3.1.9.
 *
 * Stock, the piano roll always has a "current" note (view + 0xa088, highlighted) and every knob edits it:
 * top-left steps through notes, bottom-left moves it in time, top-right sets its length, bottom-right its pitch. A box
 * selection (view + 0xaea8 ids, count + 0xb2a8) follows along: the parent view (FUN_080c0288) applies the same change
 * to every other boxed note. The bottom-right knob is also skipped whenever the view flag +0xf5e0 is set.
 *
 * Here:
 *  - The view can be in a "nothing selected" state. It starts that way whenever the note list is (re)loaded without a
 *    current note to keep (opening a pattern), and the current note is then not highlighted.
 *  - Top-left: from nothing, a step forward selects the first note, a step back the last; stepping forward past the
 *    last note goes back to nothing selected. Otherwise stock.
 *  - Bottom-left (time), top-right (length), bottom-right (pitch): with a box selection they act on the boxed notes;
 *    with nothing selected and no box, on every note; with a current note picked by the top-left knob, on it (stock).
 *    Acting on a set works by making one note of the set current, loading the knob from it and letting the stock
 *    handler and the stock selection copy do the rest, with the whole set briefly standing in as the box selection.
 *    Time wraps around the pattern (roll.c), pitch and length are clamped per note by the stock clamp.
 *  - The bottom-right knob works in every sequence mode (the +0xf5e0 skip is patched out).
 */
#include <stdint.h>

#define FN(addr) ((addr) | 1u)

typedef void (*knob_fn)(uint8_t *view, int delta);
typedef void (*hl_fn)(uint8_t *cell, int on);
typedef int (*insel_fn)(uint8_t *view, uint32_t id);
typedef void (*setval_fn)(uint8_t *stepper, int value);
#define fw_select   ((knob_fn)FN(0x080bbf90))  /* top-left: current note */
#define fw_start    ((knob_fn)FN(0x080bae50))  /* bottom-left: time */
#define fw_length   ((knob_fn)FN(0x080bacd8))  /* top-right: length */
#define fw_pitch    ((knob_fn)FN(0x080bbbc4))  /* bottom-right: pitch */
#define fw_hl       ((hl_fn)FN(0x080ba048))    /* note cell widget: highlight on / off */
#define fw_in_box   ((insel_fn)FN(0x080ba99c)) /* is this event id in the box selection */
#define fw_setval   ((setval_fn)FN(0x080b074c))

/* piano roll view (parent + 0x38) */
#define V_CELL(v, c)   ((v) + (c) * 0x50)      /* note cell c; its widget at +0x34 */
#define CELL_WIDGET    0x34
#define CELL_START     0x68                    /* steps */
#define CELL_LEN       0x6c                    /* steps */
#define CELL_START_T   0x70                    /* fine units */
#define CELL_LEN_T     0x74
#define CELL_PITCH     0x78
#define CELL_ID        0x7c
#define V_CUR          0xa088                  /* current note: index into the sorted note list */
#define V_CUR_ID       0xa08c
#define V_NOTES        0xa094                  /* sorted note list: cell numbers */
#define V_NCOUNT       0xa894
#define V_SEL_STEP     0xa070                  /* top-left stepper; counts per step at +8 */
#define V_LEN_STEP     0xa898
#define V_LEN_LAST     0xa8b0
#define V_PITCH_STEP   0xa900
#define V_PITCH_LAST   0xa934
#define V_PITCH_OFS    0xae78
#define V_START_STEP   0xa918
#define V_START_LAST   0xa930
#define V_FINE         0xae80                  /* sub-steps per step; 1 = whole steps (always, in 3.1.9) */
#define V_BOX          0xaea8                  /* box selection: event ids */
#define V_BOX_N        0xb2a8
#define BOX_MAX        256
#define V_SEL_DIRTY    0x11900                 /* parent (view - 0x38) + 0x11938: rebuild its selection copy */

void bkp_enable(void);

struct prk_state {
    uint32_t magic;
    uint8_t *view;               /* the view the state belongs to */
    int32_t acc;                 /* top-left counts toward a step into / out of "nothing selected" */
    uint8_t none;
};
#define PS ((volatile struct prk_state *)0x38800e40u)   /* backup SRAM, after usbrecover's 0x38800e00 */
#define MAGIC 0x50524b31u        /* "PRK1" */

static void ensure(uint8_t *v)
{
    bkp_enable();
    if (PS->magic != MAGIC || PS->view != v) {
        PS->view = v;
        PS->acc = 0;
        PS->none = 1;
        PS->magic = MAGIC;
    }
}

static inline int32_t rd32(const uint8_t *p) { return *(const volatile int32_t *)p; }
static inline void wr32(uint8_t *p, int32_t x) { *(volatile int32_t *)p = x; }

static uint8_t *note_cell(uint8_t *v, int i)
{
    return V_CELL(v, rd32(v + V_NOTES + 4 * i));
}

static int index_of(uint8_t *v, uint32_t id)
{
    int n = rd32(v + V_NCOUNT);
    for (int i = 0; i < n; i++)
        if ((uint32_t)rd32(note_cell(v, i) + CELL_ID) == id)
            return i;
    return -1;
}

/* Highlight exactly the boxed notes. */
static void show_box(uint8_t *v)
{
    int n = rd32(v + V_NCOUNT);
    for (int i = 0; i < n; i++) {
        uint8_t *c = note_cell(v, i);
        fw_hl(c + CELL_WIDGET, fw_in_box(v, (uint32_t)rd32(c + CELL_ID)) ? 1 : 0);
    }
}

static void enter_none(uint8_t *v)
{
    PS->none = 1;
    PS->acc = 0;
    wr32(v + V_BOX_N, 0);
    v[V_SEL_DIRTY] = 1;
    show_box(v);
}

/* Replaces the note list reload's two "highlight the current note" calls (FUN_080bc17c @0x080bc1ce keeps the old
 * current note, @0x080bc398 makes the first note current). keep = 1 at the first site. */
void prk_reload_hl(uint8_t *widget, int on, uint8_t *v, int keep)
{
    ensure(v);
    if (!keep)
        PS->none = 1;                                   /* nothing to keep: a freshly opened pattern */
    if (PS->none) {
        uint8_t *c = widget - CELL_WIDGET;
        on = fw_in_box(v, (uint32_t)rd32(c + CELL_ID)) ? 1 : 0;
    }
    fw_hl(widget, on);
}

/* Top-left knob (bl @0x080c1776). */
void prk_select(uint8_t *v, int delta)
{
    ensure(v);
    int n = rd32(v + V_NCOUNT), cps = rd32(v + V_SEL_STEP + 8);
    if (n <= 0 || cps <= 0) {
        fw_select(v, delta);
        return;
    }
    if (PS->none) {
        int32_t acc = PS->acc + delta;
        if (acc < cps && acc > -cps) {
            PS->acc = acc;
            return;
        }
        PS->acc = 0;
        PS->none = 0;
        wr32(v + V_CUR, -1);                            /* force the stock handler to treat it as a change */
        fw_setval(v + V_SEL_STEP, acc > 0 ? 0 : n - 1);
        fw_select(v, 0);                                /* highlights it, loads the knobs, clears the box */
        return;
    }
    if (rd32(v + V_CUR) >= n - 1 && delta > 0) {
        int32_t acc = PS->acc + delta;
        if (acc >= cps)
            enter_none(v);
        else
            PS->acc = acc;
        return;
    }
    PS->acc = 0;
    fw_select(v, delta);
}

/* Make note i current for knob k without the select handler's side effects, and load knob k from it. */
static void load_anchor(uint8_t *v, int i, int k)
{
    uint8_t *c = note_cell(v, i);
    int fine = rd32(v + V_FINE) != 1;
    wr32(v + V_CUR, i);
    wr32(v + V_CUR_ID, rd32(c + CELL_ID));
    fw_setval(v + V_SEL_STEP, i);
    if (k == 1) {
        int s = rd32(c + (fine ? CELL_START_T : CELL_START));
        wr32(v + V_START_LAST, s);
        fw_setval(v + V_START_STEP, s);
    } else if (k == 2) {
        int l = rd32(c + (fine ? CELL_LEN_T : CELL_LEN));
        wr32(v + V_LEN_LAST, l);
        fw_setval(v + V_LEN_STEP, l);
    } else {
        int p = rd32(c + CELL_PITCH);
        wr32(v + V_PITCH_LAST, p);
        fw_setval(v + V_PITCH_STEP, p - rd32(v + V_PITCH_OFS));
    }
}

static void edit(uint8_t *v, int delta, int k, knob_fn stock)
{
    ensure(v);
    int boxed = rd32(v + V_BOX_N), n = rd32(v + V_NCOUNT);
    if (n <= 0 || (!PS->none && boxed == 0)) {
        stock(v, delta);                                /* a current note picked with the top-left knob: stock */
        return;
    }
    int filled = 0, anchor = -1;
    if (boxed > 0) {
        int cur = rd32(v + V_CUR);
        if (!PS->none && cur >= 0 && cur < n && fw_in_box(v, (uint32_t)rd32(note_cell(v, cur) + CELL_ID)))
            anchor = cur;                               /* stock case: current note inside the box */
        else
            anchor = index_of(v, (uint32_t)rd32(v + V_BOX));
    } else {
        int m = n < BOX_MAX ? n : BOX_MAX;              /* every note stands in as the box for this edit */
        for (int i = 0; i < m; i++)
            wr32(v + V_BOX + 4 * i, rd32(note_cell(v, i) + CELL_ID));
        wr32(v + V_BOX_N, m);
        filled = 1;
        anchor = 0;
    }
    if (anchor < 0) {
        stock(v, delta);
        return;
    }
    v[V_SEL_DIRTY] = 1;
    if (anchor != rd32(v + V_CUR) || PS->none)
        load_anchor(v, anchor, k);
    stock(v, delta);
    if (filled) {
        wr32(v + V_BOX_N, 0);
        v[V_SEL_DIRTY] = 1;
    }
    if (PS->none || boxed)
        show_box(v);                                    /* no current note highlight of its own */
}

void prk_start(uint8_t *v, int delta) { edit(v, delta, 1, fw_start); }   /* bl @0x080c1792 */
void prk_length(uint8_t *v, int delta) { edit(v, delta, 2, fw_length); } /* bl @0x080c179a */
void prk_pitch(uint8_t *v, int delta) { edit(v, delta, 3, fw_pitch); }   /* bl @0x080c17ba */
