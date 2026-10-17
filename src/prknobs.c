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
 *    Acting on a set changes the notes in the app's list in one pass, sends each changed note to the engine and
 *    refreshes the views once (see shift), by whole steps of the knob.
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
/* backup SRAM: after od.c's filter states (0x38800000..0x38800384); usbrecover.c is at 0x388003c0 */
#define PS ((volatile struct prk_state *)0x38800388u)
_Static_assert(sizeof(struct prk_state) <= 0x3c0 - 0x388, "piano roll knob state runs into usbrecover.c's");
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

/*
 * Moving a set of notes, all in one go, the way upstream's rotate (JOEBOX 1, rotate.c) does it: the app keeps each
 * layer's notes in a pool with a handle array sorted by start, and the engine has its own copy, updated one note at a
 * time by id. The notes are changed in place in the app's pool, the handles re-sorted if starts moved, each changed
 * note is sent to the engine the way the stock edit handler sends one, and the views get ONE refresh (message 0x66),
 * which reloads the piano roll. (Until this, every note was a separate stock edit, and each one reloaded the piano
 * roll: a step of the knob on a busy pattern took a reload per note.)
 */
struct event {                   /* 0x18 bytes */
    uint8_t type, cond;
    uint16_t _r0;
    uint32_t start, length, id;
    uint16_t pitch, velocity;
    uint32_t _r1;
};
struct list {
    uint16_t *handles;           /* sorted by start; the low 14 bits index the pool */
    uint32_t count, index, _r;
    struct event **pool;
};
struct ref {
    uint32_t tag;
    uint16_t id, _r;
};
struct msg {
    uint16_t id, _r0;
    uint32_t tag;
    uint16_t seq, _r1;
    int32_t a, b, c;
};
typedef int (*param_get_fn)(uint8_t *app, const void *ref, unsigned param, uint32_t *out);
typedef uint8_t *(*cell_fn)(uint8_t *app, const void *ref, unsigned layer);
typedef uint32_t (*cell_param_fn)(uint8_t *cell, unsigned param);
typedef void (*engine_event_fn)(uint8_t *engine, uint32_t seq, const struct event *event, uint32_t zero,
                                unsigned layer);
typedef void (*app_post_fn)(uint8_t *queue, const struct msg *m);
#define fw_param_get    ((param_get_fn)FN(0x08099504))
#define fw_cell         ((cell_fn)FN(0x08097ce8))
#define fw_cell_param   ((cell_param_fn)FN(0x08093e9c))
#define fw_engine_event ((engine_event_fn)FN(0x0804c798))    /* add / update one note in the engine, by id */
#define fw_app_post     ((app_post_fn)FN(0x080b5758))        /* queue a message for the views */

#define APP        ((uint8_t *)0x24020088u)
#define ENGINE     ((uint8_t *)0x2400a9c0u)
#define STEP_TICKS ((const float *)0x080eca6cu)              /* Step Len index -> quarter notes; 960 ticks each */
#define STEP_LENS  15
#define VIEW_ROLL  0x38          /* the piano roll inside its view: the view is v - 0x38 */
#define VIEW_SEQ   0xb490        /* view: the shown sequence (struct ref) */
#define APP_QUEUE  0x30
#define APP_DIRTY  0x8cc4
#define CELL_LIST  0x18
#define P_STEP_LEN   0x85
#define P_STEP_COUNT 0x86
#define P_STEP_MODE  0x88        /* OFF, ON, REPLACE (free time, like OFF), ON+REPL (steps, like ON): replace.c */
#define P_LAYER      0xc0
#define MSG_REFRESH  0x66
#define MSG_TAG      0x080cfba4u
#define TICKS        960

static int32_t mod(int32_t a, int32_t n) { a %= n; return a < 0 ? a + n : a; }

/* Move every boxed note (boxed) or every note of the shown layer by d steps of knob k (1 time, 2 length, 3 pitch). */
static void shift(uint8_t *v, int d, int k, int boxed)
{
    const struct ref *shown = (const struct ref *)(v - VIEW_ROLL + VIEW_SEQ);
    unsigned id = shown->id;
    if ((id & 0xff) >> 4 > 3 || (id & 0xf) > 4)
        return;                                              /* not a sequence cell */
    uint32_t count = 0, len = 0, mode = 0;
    fw_param_get(APP, shown, P_STEP_COUNT, &count);
    fw_param_get(APP, shown, P_STEP_LEN, &len);
    fw_param_get(APP, shown, P_STEP_MODE, &mode);
    if (count < 1 || count > 0x100 || len >= STEP_LENS)
        return;
    int32_t unit = mode && mode != 2 ? TICKS : (int32_t)(STEP_TICKS[len] * (float)TICKS + 0.5f);
    if (unit < 1)
        return;
    int32_t loop = (int32_t)count * unit;

    struct ref ref = {0, (uint16_t)id, 0};
    unsigned layer = fw_cell_param(fw_cell(APP, &ref, 0), P_LAYER) & 0xff;
    struct list *l = (struct list *)(fw_cell(APP, &ref, layer) + CELL_LIST);
    if (!l->handles || !l->pool || !*l->pool)
        return;
    struct event *pool = *l->pool;
    uint16_t *h = l->handles;
    uint32_t n = l->count;

    /* which notes, and (pitch) how far the set as a whole may go */
    int lo = rd32(v + V_PITCH_OFS), hi = lo + rd32(v + V_PITCH_STEP + 0x10) - 1;
    if (hi < lo || lo < 0 || hi > 127)
        lo = 0, hi = 127;
    int any = 0;
    for (uint32_t i = 0; i < n; i++) {
        struct event *e = &pool[h[i] & 0x3fff];
        if (e->type != 1 || (boxed && !fw_in_box(v, e->id)))
            continue;
        any = 1;
        if (k == 3) {
            if ((int)e->pitch + d > hi)
                d = hi - (int)e->pitch;
            if ((int)e->pitch + d < lo)
                d = lo - (int)e->pitch;
        }
    }
    if (!any || d == 0)
        return;

    int32_t max_len = rd32(v + V_LEN_STEP + 0x10) - 1;       /* in steps, the longest the length knob goes */
    if (max_len < 1)
        max_len = (int32_t)count;
    uint16_t chg[128];                                       /* pool indices of the changed notes */
    unsigned nchg = 0, all = 0;
    for (uint32_t i = 0; i < n; i++) {
        struct event *e = &pool[h[i] & 0x3fff];
        if (e->type != 1 || (boxed && !fw_in_box(v, e->id)))
            continue;
        if (k == 1) {
            if ((int32_t)e->start >= loop)
                continue;                                    /* past the loop end (a shorter Step Count): stays */
            e->start = (uint32_t)mod((int32_t)e->start + d * unit, loop);
        } else if (k == 2) {
            int32_t nl = (int32_t)e->length + d * unit;
            nl = nl < unit ? unit : nl > max_len * unit ? max_len * unit : nl;
            if (nl == (int32_t)e->length)
                continue;
            e->length = (uint32_t)nl;
        } else {
            e->pitch = (uint16_t)((int)e->pitch + d);
        }
        if (nchg < sizeof chg / sizeof *chg)
            chg[nchg++] = h[i] & 0x3fff;
        else
            all = 1;                                         /* more than the list holds: send the whole layer */
    }
    /* starts moved: the handles must be in start order again (stable, so equal starts keep their order) */
    if (k == 1)
        for (uint32_t i = 1; i < n; i++) {
            uint16_t x = h[i];
            uint32_t sx = pool[x & 0x3fff].start, j = i;
            while (j > 0 && pool[h[j - 1] & 0x3fff].start > sx) {
                h[j] = h[j - 1];
                j--;
            }
            h[j] = x;
        }
    uint32_t seq = (id & 0xf) | (id >> 8 & 0x1f) << 16 | (id >> 4 & 0xf) << 8;
    if (all)
        for (uint32_t i = 0; i < n; i++)
            fw_engine_event(ENGINE, seq, &pool[h[i] & 0x3fff], 0, layer);
    else
        for (unsigned i = 0; i < nchg; i++)
            fw_engine_event(ENGINE, seq, &pool[chg[i]], 0, layer);
    if (!nchg)
        return;
    struct msg m = {MSG_REFRESH, 0, MSG_TAG, (uint16_t)id, 0, 0, 0, 0};
    fw_app_post(APP + APP_QUEUE, &m);
    APP[APP_DIRTY] = 1;
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
