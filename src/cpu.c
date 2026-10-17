/*
 * CPU meter for the audio engine. Original 1010music Blackbox, firmware 3.1.9.
 *
 * The audio task (FUN_08041470) waits on a queue for the SAI DMA half / full interrupts, then renders 256 frames
 * (5.33 ms at 48 kHz) and waits again. This wraps that wait: busy = cycles from waking to waiting again,
 * period = cycles between wake-ups, both from the Cortex-M7 DWT cycle counter. Once a second the average and the
 * worst block of that second are kept.
 *
 * Where it shows (3.1.I): the Tools screen's bottom bar has three labels (FUN_080c5450 builds them): the version, an
 * EMPTY middle one, and the card's free space, which FUN_080c5180 fills in whenever Tools refreshes. That refresh now
 * also writes "avg/peak%" into the middle label ("--%" if the cycle counter never moves). So the figure is as fresh
 * as the last time Tools drew itself. (Earlier builds appended it to the version text from the audio task; that never
 * showed on the unit, and the same text was the header of the master knob pages, where it wrapped.)
 *
 * cpu_name still supplies the global cell's name: the version, or a master page's title (master.c).
 */
#include <stdint.h>

#define FN(addr) ((addr) | 1u)

typedef int (*receive_fn)(void *queue, void *item, uint32_t ticks, int peek);
typedef void (*label_fn)(uint8_t *label, const char *s);

#define fw_receive  ((receive_fn)FN(0x08088756))     /* FreeRTOS queue receive */
#define fw_label_set ((label_fn)FN(0x080a3dec))      /* label: set its text, then redraw */

/*
 * Our version text, in the cave. It must NOT be patched over stock's "3.1.9" at 0x080cf290: the compiler pooled
 * that string's last character as the standalone "9", and three tables point at it - the naming keyboard's top
 * row (0x080f0ba8: 5 6 7 8 9 0 q w e r t y) and two numeric value lists. Every build from the first one to 3.2.b
 * patched that byte, so the 9 key typed the build letter instead (reported by a user on the public 3.1.n, which
 * typed "n"; 3.2.b typed "b"). Instead the four places that DISPLAY the version are repointed here (patches/cave.py).
 */
const char fw_version[] = "3.1.n";
#define VERSION     (fw_version)
#define LABEL_SIZE  0xa0                             /* the bar's labels sit one after the other */

#define DEMCR      (*(volatile uint32_t *)0xe000edfcu)
#define DWT_CTRL   (*(volatile uint32_t *)0xe0001000u)
#define DWT_CYCCNT (*(volatile uint32_t *)0xe0001004u)
#define DWT_LAR    (*(volatile uint32_t *)0xe0001fb0u)

#define BLOCKS_PER_REPORT 188    /* ~1 s of 5.33 ms blocks */

/*
 * THE READING USED TO INVERT (3.2.c and earlier). Load was busy / period, both measured wake to wake, so when
 * blocks arrived late the period stretched and the ratio FELL: a dropout with four Flanger inserts + clipper +
 * Glue + Prism read 58/78 %, while VHS at 85/99 % was clean. A low number during a dropout was the symptom, not
 * headroom. The denominator is now the NOMINAL block - the shortest period seen in the previous report window -
 * so the figure is busy time against the time the engine actually had, and over 100 % means the render did not
 * fit. Healthy readings are unchanged (nominal == actual when nothing is late), so earlier photos still
 * compare.
 *
 * THE FIGURES SPAN THE TIME BETWEEN DRAWS, not a fixed second. The bar is only written when Tools is ENTERED
 * (FUN_080c5180 runs on msg 0x66 for cell 0x40 and on msg 0xd4), so a per-second average was a stale one-second
 * sample of whenever that happened - useless for "leave Tools, play something, come back", which is the only way
 * to watch the engine under load. Average, peak and the late count now accumulate until the bar is drawn and are
 * cleared by the draw, so one look summarises everything since the last look. The displayed pair is also held, so
 * an immediate second look shows the same figures rather than going blank. Past about five minutes without a look
 * the accumulator halves itself rather than wrapping, which turns the average into a slow fade of the oldest half.
 *
 * Beside it, "+n" counts blocks whose period ran past 1.5x nominal: a boundary the task did not meet. Reading the
 * pair during a dropout says which kind it is:
 *   peak at or over 100 %      -> out of CPU, the render does not fit in a block.
 *   "+n" with peak under 100 % -> something stalled the engine (SDRAM contention, a higher-priority task).
 *   +0 and peak under 100 %    -> NOT the audio task at all. Look at sample streaming starvation instead.
 * The count accumulates until the bar is drawn and is cleared by the draw, so a dropout that happened with another
 * screen up still shows on arriving at Tools (a boot or a preset load will show one too).
 */
struct cpu_state {
    uint32_t magic;
    uint32_t woke;               /* cycle count when the task last woke */
    uint32_t sum;                /* per-mille loads this report */
    uint32_t nom;                /* nominal block, cycles: the smallest report mean seen since boot */
    uint32_t win_start;          /* cycle count when this report began */
    uint16_t n, peak;            /* measured blocks and the worst one SINCE THE BAR WAS LAST DRAWN */
    uint16_t wakes;              /* all wake-ups, measured or not; only the dead-clock check reads it */
    uint16_t win_n;              /* blocks this nominal-estimate window */
    uint16_t avg_shown, peak_shown;   /* what the bar last showed, held so a second look is not blank */
    uint8_t armed, status;
    uint8_t late;                /* late blocks since the bar was last drawn, clamped at 99 */
    uint8_t _r;
};
#define M ((volatile struct cpu_state *)0x2405ffd0u)
#define MAGIC 0x43505534u        /* "CPU4": bumped with the layout */
#define STATUS_NONE    0         /* nothing measured yet */
#define STATUS_OK      1
#define STATUS_NO_CLOCK 2        /* a second of wake-ups and the cycle counter never moved */
#define PERIOD_FLOOR 100000u     /* a plausible block is far longer; two interrupts queued at once are not one */


static void start(void)
{
    DEMCR |= 1u << 24;           /* TRCENA */
    DWT_LAR = 0xc5acce55u;       /* unlock (Cortex-M7) */
    DWT_CTRL |= 1u;              /* CYCCNTENA */
    M->armed = 0;
    M->status = STATUS_NONE;
    M->sum = 0;
    M->nom = 0;
    M->win_start = DWT_CYCCNT;   /* so the first report's mean is a real elapsed time, not the raw counter */
    M->late = 0;
    M->n = M->peak = M->wakes = M->win_n = 0;
    M->magic = MAGIC;
}

static char *put_uint(char *p, unsigned v)
{
    char tmp[4];
    int n = 0;
    do {
        tmp[n++] = (char)('0' + v % 10);
        v /= 10;
    } while (v && n < 4);
    while (n)
        *p++ = tmp[--n];
    return p;
}

/* "34/71%", "34/71%+3", "--%", or nothing before the first second. Writes at most 12 bytes. */
static void compose(char *buf)
{
    char *p = buf;
    if (M->magic == MAGIC && M->status == STATUS_OK) {
        p = put_uint(p, (M->avg_shown + 5u) / 10u);
        *p++ = '/';
        p = put_uint(p, (M->peak_shown + 5u) / 10u);
        *p++ = '%';
        if (M->late) {
            *p++ = '+';
            p = put_uint(p, M->late);
        }
    } else if (M->magic == MAGIC && M->status == STATUS_NO_CLOCK) {
        *p++ = '-';
        *p++ = '-';
        *p++ = '%';
    }
    *p = 0;
}

/* Replaces the audio task's queue wait (bl @0x080414e4). */
int cpu_wait(void *queue, void *item, uint32_t ticks, int peek)
{
    if (M->magic != MAGIC)
        start();
    uint32_t idle_from = DWT_CYCCNT, busy = idle_from - M->woke;
    int got = fw_receive(queue, item, ticks, peek);
    uint32_t now = DWT_CYCCNT, period = now - M->woke;
    if (got != 1) {
        M->armed = 0;
        return got;
    }
    if (M->armed && period > 1000u) {
        uint32_t base = M->nom ? M->nom : period;            /* first report: as it was before, busy / period */
        if (M->nom && period > base + (base >> 1) && M->late < 99u)
            M->late++;                                       /* a block boundary the task did not meet */
        uint32_t load = busy / (base / 1000u + 1u);          /* per mille of the time the engine actually had */
        if (load > 9990u)
            load = 9990u;
        if (M->n >= 60000u) {        /* ~5 minutes without a look: forget the oldest half rather than wrap */
            M->sum >>= 1;
            M->n >>= 1;
        }
        M->sum += load;
        M->n++;
        if (load > M->peak)
            M->peak = (uint16_t)load;
        if (++M->win_n >= BLOCKS_PER_REPORT) {
            M->status = STATUS_OK;
            /*
             * The nominal block, re-estimated: this report's MEAN period. The task cannot wake more often than the
             * SAI DMA fires, so a report's mean can never fall below the true block and anything that goes wrong
             * (late blocks, a missed interrupt, catching up on a backlog) only pushes it UP. Keeping the smallest
             * mean seen since boot therefore settles on the hardware's own rate after one quiet report and is never
             * dragged down by an outlier - which a per-period minimum would be, since a task working through two
             * queued interrupts shows one very short period.
             */
            uint32_t mean = (now - M->win_start) / M->win_n;
            if (mean > PERIOD_FLOOR && (M->nom == 0 || mean < M->nom))
                M->nom = mean;
            M->win_start = now;
            M->win_n = 0;
        }
    }
    if (++M->wakes >= 2 * BLOCKS_PER_REPORT) {
        if (M->n == 0)
            M->status = STATUS_NO_CLOCK;
        M->wakes = 0;
    }
    M->woke = now;
    M->armed = 1;
    return got;
}

/*
 * Replaces the 6-byte copy of the version string into the global cell's info (FUN_0809ad74, address 0x40, the
 * `ldr; ldr; str; ldrh; strh` @0x0809ad84). dest is the info's name field. The knob screen draws that name as its
 * header from three quarters of the way across, and what does not fit wraps round to the left edge (the 3.1.H
 * photos: "Prism 1/" then the "2" at the far left), so a title is 8 characters at most.
 */
const char *master_title(void);  /* master.c: the header for a Compressor / Master FX knob page, or 0 */
/* Once per boot, from master_alloc (so before any audio block): forget the meter's state. This RAM is not cleared
 * by a reset, so a restart without a power cycle would carry the last boot's figures over. */
void cpu_boot(void)
{
    M->magic = 0;
}

void cpu_name(char *dest)
{
    const char *from = master_title();
    char *p = dest;
    int version = !from;
    if (version)
        from = VERSION;
    while (*from && p < dest + 8)
        *p++ = *from++;
    *p = 0;
}

/*
 * Replaces the Tools refresh's call that sets the free-space label (bl FUN_080a3dec @0x080c52a4): the empty label
 * before it in the bar gets the load. GUI task.
 */
void cpu_tools(uint8_t *space_label, const char *space)
{
    char buf[16];
    fw_label_set(space_label, space);
    if (M->n) {                  /* close off the period being reported BEFORE composing it, not after */
        M->avg_shown = (uint16_t)(M->sum / M->n);
        M->peak_shown = M->peak;
        M->sum = 0;
        M->n = M->peak = 0;
    }
    compose(buf);                /* reads avg_shown / peak_shown / late */
    M->late = 0;                 /* drawn: the next look reports only what happens from here */
    fw_label_set(space_label - LABEL_SIZE, buf);
}
