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
 *    Acting on a set posts the same per-note edit the stock knob handlers post for the current note (message 0x16f
 *    to the app, plus the view's own 0x42 / 0x43 redraw), one per note, by whole steps of the knob.
 *    Time wraps around the pattern; pitch moves the set together and stops it at the knob's range (chord shapes
 *    are kept); length is clamped per note.
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
    int32_t knob[3];             /* bottom-left, top-right, bottom-right: counts toward the next step */
};
#define PS ((volatile struct prk_state *)0x38800e40u)   /* backup SRAM, after usbrecover's 0x38800e00 */
#define MAGIC 0x50524b32u        /* "PRK2" */

static void ensure(uint8_t *v)
{
    bkp_enable();
    if (PS->magic != MAGIC || PS->view != v) {
        PS->view = v;
        PS->acc = 0;
        PS->none = 1;
        PS->knob[0] = PS->knob[1] = PS->knob[2] = 0;
        PS->magic = MAGIC;
    }
}

static inline int32_t rd32(const uint8_t *p) { return *(const volatile int32_t *)p; }
static inline void wr32(uint8_t *p, int32_t x) { *(volatile int32_t *)p = x; }

static uint8_t *note_cell(uint8_t *v, int i)
{
    return V_CELL(v, rd32(v + V_NOTES + 4 * i));
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

struct edit_msg {
    uint16_t id, _r0;
    uint32_t tag;
    uint16_t pad, _r1;
    int32_t event, field, value;
};
typedef void (*post_fn)(uint8_t *obj, void *msg);
#define fw_post   ((post_fn)FN(0x080aed14))   /* hand a message to obj's parent */
#define MSG_TAG   0x080cfba4u
#define V_PARENT  0x2c                          /* -> the piano roll's parent; its parent is the app */
#define V_PADCODE 0xa04c

#define F_PITCH       1
#define F_START_STEPS 2
#define F_START_TICKS 3
#define F_LEN_STEPS   4
#define F_LEN_TICKS   5

static int32_t mod(int32_t a, int32_t n) { a %= n; return a < 0 ? a + n : a; }

/* Change one field of one note: the cell (what the view draws), a local redraw message (0x42 / 0x43, as the stock
 * knob handlers send for the current note) and the edit itself to the app. The edit goes to the parent's parent
 * directly: through the parent it would also be copied to every other note of the box selection (FUN_080c0288). */
static void set_note(uint8_t *v, uint8_t *c, int field, int32_t value)
{
    uint16_t pad = *(volatile uint16_t *)(v + V_PADCODE);
    uint32_t id = (uint32_t)rd32(c + CELL_ID);
    int fine = rd32(v + V_FINE);
    switch (field) {
    case F_PITCH:       wr32(c + CELL_PITCH, value); break;
    case F_START_STEPS: wr32(c + CELL_START, value); break;
    case F_START_TICKS: wr32(c + CELL_START_T, value); wr32(c + CELL_START, value / fine); break;
    case F_LEN_STEPS:   wr32(c + CELL_LEN, value); break;
    case F_LEN_TICKS:   wr32(c + CELL_LEN_T, value); wr32(c + CELL_LEN, value / fine < 1 ? 1 : value / fine); break;
    }
    int start_msg = field == F_START_STEPS || field == F_START_TICKS;
    struct edit_msg local = {start_msg ? 0x43 : 0x42, 0, MSG_TAG, pad, 0, (int32_t)id,
                             (int32_t)(uint16_t)(int16_t)rd32(c + CELL_LEN) |
                                 (int32_t)((uint32_t)rd32(c + (start_msg ? CELL_START : CELL_PITCH)) << 16), 0};
    fw_post(v, &local);
    struct edit_msg edit = {0x16f, 0, MSG_TAG, pad, 0, (int32_t)id, field, value};
    uint8_t *parent = *(uint8_t **)(v + V_PARENT);
    if (parent)
        fw_post(parent, &edit);
}

static int targeted(uint8_t *v, uint8_t *c, int boxed)
{
    return !boxed || fw_in_box(v, (uint32_t)rd32(c + CELL_ID));
}

/* Move every boxed note (boxed) or every note by d steps of knob k. */
static void shift(uint8_t *v, int d, int k, int boxed)
{
    int n = rd32(v + V_NCOUNT), fine = rd32(v + V_FINE);
    if (fine < 1)
        fine = 1;
    uint8_t *cells[BOX_MAX];
    int m = 0;
    for (int i = 0; i < n && m < BOX_MAX; i++) {
        uint8_t *c = note_cell(v, i);
        if (targeted(v, c, boxed))
            cells[m++] = c;
    }
    if (k == 3) {
        /* pitch: the whole set stops at the edge, so chords keep their shape */
        int lo = rd32(v + V_PITCH_OFS), hi = lo + rd32(v + V_PITCH_STEP + 0x10) - 1;
        if (hi < lo)
            lo = 0, hi = 127;
        for (int j = 0; j < m; j++) {
            int p = rd32(cells[j] + CELL_PITCH);
            if (p + d > hi)
                d = hi - p;
            if (p + d < lo)
                d = lo - p;
        }
        if (d == 0)
            return;
        for (int j = 0; j < m; j++)
            set_note(v, cells[j], F_PITCH, rd32(cells[j] + CELL_PITCH) + d);
    } else if (k == 1) {
        /* time: wraps around the pattern. Notes that start at or past the loop end (left over from a longer Step
           Count) stay where they are, so they are back in place if the pattern is made longer again. */
        int count = rd32(v + V_START_STEP + 0x10);
        for (int j = 0; j < m; j++) {
            if (fine == 1) {
                int32_t s0 = rd32(cells[j] + CELL_START), s = s0 + d;
                if (count > 0 && s0 >= count)
                    continue;
                set_note(v, cells[j], F_START_STEPS, count > 0 ? mod(s, count) : (s < 0 ? 0 : s));
            } else {
                int32_t t0 = rd32(cells[j] + CELL_START_T), t = t0 + d * fine;
                if (count > 0 && t0 >= count * fine)
                    continue;
                set_note(v, cells[j], F_START_TICKS, count > 0 ? mod(t, count * fine) : (t < 0 ? 0 : t));
            }
        }
    } else {
        /* length: each note from 1 step to the longest the knob allows */
        int max = rd32(v + V_LEN_STEP + 0x10) - 1;
        for (int j = 0; j < m; j++) {
            if (fine == 1) {
                int32_t l = rd32(cells[j] + CELL_LEN) + d;
                l = l < 1 ? 1 : (max >= 1 && l > max ? max : l);
                if (l != rd32(cells[j] + CELL_LEN))
                    set_note(v, cells[j], F_LEN_STEPS, l);
            } else {
                int32_t l = rd32(cells[j] + CELL_LEN_T) + d * fine;
                l = l < fine ? fine : (max >= 1 && l > max * fine ? max * fine : l);
                if (l != rd32(cells[j] + CELL_LEN_T))
                    set_note(v, cells[j], F_LEN_TICKS, l);
            }
        }
    }
}

static const int stepper_of[4] = {0, V_START_STEP, V_LEN_STEP, V_PITCH_STEP};

static void edit(uint8_t *v, int delta, int k, knob_fn stock)
{
    ensure(v);
    int boxed = rd32(v + V_BOX_N), n = rd32(v + V_NCOUNT);
    if (n <= 0 || (!PS->none && boxed == 0)) {
        stock(v, delta);                                /* a current note picked with the top-left knob: stock */
        return;
    }
    int cps = rd32(v + stepper_of[k] + 8);
    if (cps <= 0)
        cps = 1;
    int32_t acc = PS->knob[k - 1] + delta;
    int d = acc / cps;                                  /* whole steps; the rest waits for the next turn */
    PS->knob[k - 1] = acc - d * cps;
    if (d)
        shift(v, d, k, boxed > 0);
    show_box(v);                                        /* only the boxed notes are lit */
}

void prk_start(uint8_t *v, int delta) { edit(v, delta, 1, fw_start); }   /* bl @0x080c1792 */
void prk_length(uint8_t *v, int delta) { edit(v, delta, 2, fw_length); } /* bl @0x080c179a */
void prk_pitch(uint8_t *v, int delta) { edit(v, delta, 3, fw_pitch); }   /* bl @0x080c17ba */
