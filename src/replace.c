/*
 * Replace recording: Step Mode OFF / ON / REPLACE / ON+REPL. Original 1010music Blackbox, firmware 3.1.9.
 *
 * Stock, Step Mode (sequence parameter 0x88, xml "seqstepmode") is an OFF/ON toggle. Every reader treats any
 * non-zero value as ON: recorded notes snap to whole steps, and a note played on a step where a note of the same
 * pitch already starts replaces it. Everything else is layered on top of what is there.
 *
 * Here the parameter is a list: 0 OFF, 1 ON (old presets keep their 0 or 1), 2 REPLACE (free timing, like OFF),
 * 3 ON+REPL (snapped to steps, like ON). Every stock read of Step Mode goes through stepmode_snap / stepmode_snap_get
 * (app) or rpl_snap_r3 / rpl_snap_fp (engine's copy, change handler), which hand the stock code "ON" for 1 and 3 and
 * "OFF" for 0 and 2. Step Mode decides the unit of note times (steps x 960, or clock ticks), so every reader has to
 * agree. In both replace modes, while recording:
 *  - a note deletes every older note starting in the same step (any pitch) as soon as it is played;
 *  - when it is released, every older note starting inside its length is deleted too;
 *  - a note still held when the loop wraps is cut at the loop end, and continues as a new note from the loop start
 *    (unless it is let go within half a step after the loop end: then it just ends there).
 * "Older" = recorded before the loop pass the new note belongs to (notes already in the pattern, and notes recorded
 * on earlier passes). Notes recorded on the same pass (chords, an arp, the rest of the take) are never deleted.
 *
 * The app-side recorder (FUN_080a02c0) builds the note in its frame and calls the event list: append at note-on
 * (bl @0x080a05fe), set-with-length at note-off (bl @0x080a073c). Both calls are replaced (replace_thunk.S passes the
 * recorder's sp and app); outside the replace modes they do exactly the stock call.
 * Loop passes are told apart by their start time, from the time the note was played and the recorder's loop base
 * (moved by one loop when quantising pulled the note across the loop end). A note released past the loop end
 * belongs to the next pass.
 */
#include <stdint.h>

#define FN(addr) ((addr) | 1u)

typedef void (*reglist_fn)(void *table, int param, const char *label, const char *const *names, int count,
                           const char *xml);
typedef uint8_t *(*paramset_fn)(uint8_t *app, uint8_t *pads, int layer);
typedef int (*pget_fn)(uint8_t *paramset, int param);
typedef int (*lget_fn)(void *list, int index, void *event_out);
typedef void (*lev_fn)(void *list, void *event);
typedef void (*ldel_fn)(void *list, uint32_t id);
typedef void (*edel_fn)(void *engine, uint32_t pad, uint32_t id, uint32_t layer);
typedef void (*eadd_fn)(void *engine, uint32_t pad, void *event, uint32_t zero, uint32_t layer);
typedef void (*notify_fn)(uint8_t *target, void *msg);
typedef int64_t (*ldiv_fn)(int64_t a, int64_t b);

#define fw_register_list ((reglist_fn)FN(0x0808c0d8))
#define fw_paramset      ((paramset_fn)FN(0x08097ce8))
#define fw_param         ((pget_fn)FN(0x08093e9c))
#define fw_list_get      ((lget_fn)FN(0x08063d08))
#define fw_list_append   ((lev_fn)FN(0x08063b28))
#define fw_list_set      ((lev_fn)FN(0x08063b38))
#define fw_list_del      ((ldel_fn)FN(0x08063c08))
#define fw_engine_del    ((edel_fn)FN(0x0804c7e8))
#define fw_engine_add    ((eadd_fn)FN(0x0804c798))
#define fw_notify        ((notify_fn)FN(0x080b5758))
#define fw_ldiv          ((ldiv_fn)FN(0x080c8494))     /* __aeabi_ldivmod: quotient */

#define ENGINE        ((void *)0x2400a9c0)
#define NOTIFY_TAG    0x080cfba4u
#define STEP_LEN_TBL  ((const float *)0x080eca6c)      /* Step Len -> beats per step */
#define STEP_LEN_N    15

#define P_STEP_LEN    0x85
#define P_STEPS       0x86
#define P_STEP_MODE   0x88
#define MODE_ON       1
#define MODE_REPLACE  2          /* free timing */
#define MODE_ON_REPL  3

/* app */
#define APP_PADS      0x18
#define APP_PADCODE   0x1c       /* u16 */
#define APP_NOTIFY    0x30
#define APP_NEXT_ID   0xe808

/* recorder frame (sp after its prologue) */
#define F_LAYER       0x0c       /* u8 by now */
#define F_STEPS       0x14
#define F_PAD         0x18
#define F_BASE        0x30       /* int64 ticks: the playing pass's loop start */
#define F_SEQ_START   0x44       /* steps */

/* event */
#define EV_TYPE       0x00
#define EV_START      0x04
#define EV_LEN        0x08
#define EV_ID         0x0c
#define EV_SIZE       0x18
#define MSG_TIME      0x28       /* int64 ticks: when the note was played */
#define EV_NOTE       1
#define TICKS_STEP    960        /* event ticks per step when snapping to steps */

void bkp_enable(void);

static const char *const mode_names[4] = {"OFF", "ON", "REPLACE", "ON+REPL"};

/* Replaces Step Mode's registration (bl FUN_0808c12c @0x0808ceac: toggle, 0..1000) with a three-entry list. */
void rpl_register(void *table, int param, int type, const char *label, int min, int max, const char *xml)
{
    (void)type;
    (void)min;
    (void)max;
    fw_register_list(table, param, label, mode_names, 4, xml);
}

/* What the stock code means by Step Mode: snap to steps. */
static int snaps(int v)
{
    return v != 0 && v != MODE_REPLACE;
}

/* Replaces FUN_08093e9c(params, 0x88) at every stock read of Step Mode. */
int stepmode_snap(uint8_t *params, int param)
{
    return snaps(fw_param(params, param));
}

typedef int (*pget2_fn)(uint8_t *obj, void *pad, int param, int *out);
#define fw_param_get ((pget2_fn)FN(0x08099504))

/* Replaces FUN_08099504(obj, pad, 0x88, &out) at the piano roll's reads of Step Mode. */
int stepmode_snap_get(uint8_t *obj, void *pad, int param, int *out)
{
    int ok = fw_param_get(obj, pad, param, out);
    *out = snaps(*out);
    return ok;
}

#define IDS 44
struct rpl_state {
    uint32_t magic;
    uint32_t key;                /* pad code | layer << 24 */
    int64_t pass;                /* start time of the current pass */
    int64_t loop;                /* its length */
    uint8_t ncur, nprev, _r[2];
    uint32_t cur[IDS];           /* ids recorded on this pass (ring) */
    uint32_t prev[IDS];          /* ... and on the pass before */
};
#define RS ((volatile struct rpl_state *)0x38800e80u)   /* backup SRAM, after prknobs.c's 0x38800e40 */
#define MAGIC 0x52504c31u        /* "RPL1" */

static inline uint32_t rd32(const uint8_t *p) { return *(const volatile uint32_t *)p; }

static int in_set(const volatile uint32_t *set, int n, uint32_t id)
{
    for (int i = 0; i < n && i < IDS; i++)
        if (set[i] == id)
            return 1;
    return 0;
}

static void add_cur(uint32_t id)
{
    int n = RS->ncur;
    RS->cur[n % IDS] = id;
    if (n < 2 * IDS - 1)
        RS->ncur = (uint8_t)(n + 1);
    else
        RS->ncur = IDS;          /* keep counting inside the ring */
}

struct ctx {
    uint8_t *app, *frame;
    uint8_t *list;
    uint32_t pad, layer;
    int steps;
    uint32_t unit;               /* event ticks per step */
};

static int mode(uint8_t *app)
{
    return fw_param(fw_paramset(app, app + APP_PADS, 0), P_STEP_MODE);
}

/* Ticks per step from Step Len, as the recorder computes them. 0 if unknown. */
static int32_t ticks_per_step(struct ctx *c)
{
    int sl = fw_param(fw_paramset(c->app, c->app + APP_PADS, (int)c->layer), P_STEP_LEN);
    if (sl < 0 || sl >= STEP_LEN_N)
        return 0;
    int32_t tps = (int32_t)((double)(STEP_LEN_TBL[sl] * 960.0f) + 0.5);
    return tps > 0 ? tps : 0;
}

/* Start time of the loop pass a note played at `when` and quantised to `step` belongs to. The recorder places notes
 * the same way: position = when - (loop base - sequence offset), taken modulo the loop. */
static int64_t pass_of(struct ctx *c, int64_t when, int step, int64_t *loop_out)
{
    int32_t tps = ticks_per_step(c);
    *loop_out = 0;
    if (!tps)
        return 0;
    int64_t loop = (int64_t)tps * c->steps;
    int64_t base = *(const int64_t *)(c->frame + F_BASE) - (int64_t)(int32_t)rd32(c->frame + F_SEQ_START) * tps;
    int64_t t = when - base;
    int64_t q = fw_ldiv(t, loop), r = t - q * loop;
    if (r < 0) {
        q--;
        r += loop;
    }
    int raw = (int32_t)r / tps;  /* r < loop, fits */
    if (2 * step + c->steps < 2 * raw)
        q++;                     /* quantised forward across the loop end */
    else if (2 * step > 2 * raw + c->steps)
        q--;
    *loop_out = loop;
    return base + q * loop;
}

static void enter_pass(struct ctx *c, int64_t pass, int64_t loop)
{
    bkp_enable();
    uint32_t key = c->pad | c->layer << 24;
    if (RS->magic != MAGIC || RS->key != key) {
        RS->key = key;
        RS->pass = pass;
        RS->loop = loop;
        RS->ncur = RS->nprev = 0;
        RS->magic = MAGIC;
        return;
    }
    RS->loop = loop;
    if (RS->pass == pass)
        return;
    int n = RS->ncur < IDS ? RS->ncur : IDS;
    for (int i = 0; i < n; i++)
        RS->prev[i] = RS->cur[i];
    RS->nprev = (uint8_t)(RS->pass + loop == pass ? n : 0);   /* not the pass right before: nothing to keep */
    RS->ncur = 0;
    RS->pass = pass;
}

/* Delete older notes starting at [from, to) (event units), except `keep` and notes of this pass (and of the one
 * before, if with_prev). */
static void clear_range(struct ctx *c, uint32_t from, uint32_t to, uint32_t keep, int with_prev)
{
    uint8_t ev[EV_SIZE] __attribute__((aligned(4)));
    for (int i = 0; fw_list_get(c->list, i, ev);) {
        uint32_t id = rd32(ev + EV_ID), start = rd32(ev + EV_START);
        if (ev[EV_TYPE] == EV_NOTE && start >= from && start < to && id != keep &&
            !in_set(RS->cur, RS->ncur, id) && !(with_prev && in_set(RS->prev, RS->nprev, id))) {
            fw_list_del(c->list, id);
            fw_engine_del(ENGINE, c->pad, id, c->layer);
            continue;            /* the next note moved into slot i */
        }
        i++;
    }
}

static int setup(struct ctx *c, uint8_t *list, uint8_t *frame, uint8_t *app)
{
    int m = mode(app);
    if (m != MODE_REPLACE && m != MODE_ON_REPL)
        return 0;
    c->app = app;
    c->frame = frame;
    c->list = list;
    c->pad = rd32(frame + F_PAD);
    c->layer = frame[F_LAYER];
    c->steps = (int)rd32(frame + F_STEPS);
    /* event times: 960 per step when snapping to steps, else clock ticks (960 per beat) */
    c->unit = m == MODE_ON_REPL ? TICKS_STEP : (uint32_t)ticks_per_step(c);
    return c->steps > 0 && c->unit > 0;
}

/* Note-on: replaces the append of the new (open) note, bl @0x080a05fe. */
void rpl_on(uint8_t *list, uint8_t *ev, uint8_t *frame, uint8_t *app, const uint8_t *msg)
{
    struct ctx c;
    if (setup(&c, list, frame, app)) {
        uint32_t step = rd32(ev + EV_START) / c.unit;
        int64_t loop, pass = pass_of(&c, *(const int64_t *)(msg + MSG_TIME), (int)step, &loop);
        enter_pass(&c, pass, loop);
        clear_range(&c, step * c.unit, (step + 1) * c.unit, rd32(ev + EV_ID), 0);   /* the step it is played in */
        add_cur(rd32(ev + EV_ID));
    }
    fw_list_append(list, ev);
}

/* Note-off: replaces the list update with the note's length, bl @0x080a073c. The recorder then hands the same event
 * to the engine and the piano roll. */
void rpl_off(uint8_t *list, uint8_t *ev, uint8_t *frame, uint8_t *app)
{
    struct ctx c;
    if (!setup(&c, list, frame, app)) {
        fw_list_set(list, ev);
        return;
    }
    uint32_t id = rd32(ev + EV_ID);
    uint32_t loop = (uint32_t)c.steps * c.unit;
    uint32_t s = rd32(ev + EV_START), len = rd32(ev + EV_LEN);
    if (len < 1)
        len = 1;
    uint32_t first = len, second = 0;
    if (s < loop && s + len > loop) {
        first = loop - s;
        second = s + len - loop;
        if (second > s)
            second = s;          /* held more than a whole loop: up to where it started */
        if (2 * second <= c.unit)
            second = 0;          /* let go within half a step after the loop end: that is the end, not a new note */
    }
    bkp_enable();
    int known = RS->magic == MAGIC && RS->key == (c.pad | c.layer << 24);
    int same_pass = known && in_set(RS->cur, RS->ncur, id);
    if (!known)
        enter_pass(&c, 0, 0);
    if (second && same_pass && RS->loop)
        enter_pass(&c, RS->pass + RS->loop, RS->loop);  /* released on the next pass, nothing played there yet */
    if (first != len)
        *(volatile uint32_t *)(ev + EV_LEN) = first;   /* cut at the loop end */
    fw_list_set(list, ev);
    /* older than the pass the note started on: if that is no longer the current pass, keep the one before too */
    clear_range(&c, s, s + first, id, !in_set(RS->cur, RS->ncur, id));
    if (!second)
        return;

    /* the part after the loop wrap: a new note from the loop start */
    uint8_t ev2[EV_SIZE] __attribute__((aligned(4)));
    for (int i = 0; i < EV_SIZE; i++)
        ev2[i] = ev[i];
    volatile uint32_t *next = (volatile uint32_t *)(app + APP_NEXT_ID);
    uint32_t id2 = (*next)++;
    *(uint32_t *)(ev2 + EV_START) = 0;
    *(uint32_t *)(ev2 + EV_LEN) = second;
    *(uint32_t *)(ev2 + EV_ID) = id2;
    clear_range(&c, 0, second, id2, 0);
    add_cur(id2);
    fw_list_append(list, ev2);
    fw_engine_add(ENGINE, c.pad, ev2, 0, c.layer);
    uint32_t msg[6] = {0x66, NOTIFY_TAG, *(const uint16_t *)(app + APP_PADCODE), 0, id2, 0};
    fw_notify(app + APP_NOTIFY, msg);
}
