/*
 * USB MIDI echo filter. Original 1010music Blackbox, firmware 3.1.9.
 *
 * Stock: notes and CCs arriving on the USB host port go to the engine as raw MIDI too (so a MIDI sequence cell can
 * record and monitor them), and with USB Dev Out on, whatever the engine plays as MIDI goes back out of the same USB
 * port. A synth on one USB cable therefore gets its own notes straight back. With an arpeggiator that takes incoming
 * MIDI as held keys (Oberheim TEO-5 with "send arp MIDI notes" on), every echoed note feeds the arp, which sends more:
 * a loop that floods the link until the USB connection wedges and only a replug brings it back.
 *
 * The same happens to transport with nothing to do with USB Dev Out: when the Blackbox follows a synth's clock, a
 * Start (or Stop, Continue) received from it starts the transport, and the clock-send setting (default: DIN and USB)
 * sends a Start straight back. An arp that sends Start when it begins and restarts on Start ping-pongs with it.
 *
 * This drops the USB copy of a note-on, note-off, CC, start, continue or stop when it is the echo of the same message
 * received from USB a moment ago. Every message the engine reads from USB is remembered with the audio block it arrived in; when the
 * engine sends a note or CC to USB, a matching remembered message from the last ECHO_BLOCKS blocks (~21 ms) cancels
 * the USB copy and is used up, so one received message cancels at most one echo. DIN output is never touched, and
 * notes the Blackbox plays itself (sequences, pads) still reach the synth: only a send that matches something the
 * synth sent within the window is held back.
 * Matching: note-on and note-off by channel and note (the engine may pass a different velocity), CC by channel,
 * controller and value, start / continue / stop by kind. Note-on with velocity 0 counts as note-off on both sides.
 *
 * Everything here runs in the audio task: the engine's MIDI reads and sends happen in its per-block process.
 */
#include <stdint.h>

#define FN(addr) ((addr) | 1u)

struct midi_msg {
    uint8_t b[4];
    uint8_t len;
};

typedef int (*read_fn)(uint8_t *mgr, struct midi_msg *out, uint8_t *port);
typedef void (*drain_fn)(uint8_t *mgr);
typedef void (*send_fn)(uint8_t *mgr, const struct midi_msg *m, int din, int usb);
#define fw_read  ((read_fn)FN(0x0804495e))    /* next message from any input; *port = 0 DIN, 1 USB */
#define fw_drain ((drain_fn)FN(0x08044844))   /* once per block, before the reads: USB host slots -> queue */
#define fw_send  ((send_fn)FN(0x08044a0e))    /* queue a message for DIN (din != 0) and USB (usb != 0) */

#define PORT_USB    1
#define ECHO_BLOCKS 4            /* blocks of 256 frames: ~21 ms at 48 kHz */
#define ENTRIES     32

void bkp_enable(void);

struct echo_state {
    uint32_t magic;
    uint32_t block;              /* audio blocks seen */
    uint32_t next;               /* ring position for the next remembered message */
    struct {
        uint32_t block;
        uint8_t key[3];          /* kind | channel, data 1, data 2 (CC only) */
        uint8_t live;
    } e[ENTRIES];
};
#define ES ((volatile struct echo_state *)0x38800c00u)   /* backup SRAM, after munchi.cpp's 0x38800b14 */
#define MAGIC 0x45434831u        /* "ECH1" */

static void ensure(void)
{
    bkp_enable();
    if (ES->magic == MAGIC)
        return;
    ES->block = 0;
    ES->next = 0;
    for (int i = 0; i < ENTRIES; i++)
        ES->e[i].live = 0;
    ES->magic = MAGIC;
}

/* Key for matching, or 0 for messages that are never filtered. */
static int key_of(const struct midi_msg *m, uint8_t key[3])
{
    unsigned st = m->b[0], kind = st & 0xf0;
    if (m->len == 1 && st >= 0xfa && st <= 0xfc) {          /* start, continue, stop */
        key[0] = (uint8_t)st;
        key[1] = key[2] = 0;
        return 1;
    }
    if (m->len != 3 || st < 0x80 || st >= 0xf0)
        return 0;
    if (kind == 0x90 && m->b[2] == 0)
        kind = 0x80;
    if (kind != 0x80 && kind != 0x90 && kind != 0xb0)
        return 0;
    key[0] = (uint8_t)(kind | (st & 0x0f));
    key[1] = m->b[1];
    key[2] = kind == 0xb0 ? m->b[2] : 0;
    return 1;
}

/* Replaces the per-block USB drain call (bl @0x0804cb4e): count blocks, then the stock drain. */
void echo_block(uint8_t *mgr)
{
    ensure();
    ES->block++;
    fw_drain(mgr);
}

/* Replaces the engine's two MIDI reads (bl @0x0804cb80, 0x0804cc78): remember what came from USB. */
int echo_read(uint8_t *mgr, struct midi_msg *out, uint8_t *port)
{
    int got = fw_read(mgr, out, port);
    uint8_t key[3];
    if (got && *port == PORT_USB && key_of(out, key)) {
        ensure();
        unsigned i = ES->next % ENTRIES;
        ES->next = i + 1;
        ES->e[i].block = ES->block;
        ES->e[i].key[0] = key[0];
        ES->e[i].key[1] = key[1];
        ES->e[i].key[2] = key[2];
        ES->e[i].live = 1;
    }
    return got;
}

/* Replaces the engine's note / CC send (bl @0x0804d004) and its stop / start sends (0x0804d1e2, 0x0804d20a): no USB
 * copy of an echo. */
void echo_send(uint8_t *mgr, const struct midi_msg *m, int din, int usb)
{
    uint8_t key[3];
    if (usb && key_of(m, key)) {
        ensure();
        for (int i = 0; i < ENTRIES; i++) {
            if (!ES->e[i].live)
                continue;
            if (ES->block - ES->e[i].block > ECHO_BLOCKS) {
                ES->e[i].live = 0;                               /* too old to be an echo */
                continue;
            }
            if (ES->e[i].key[0] == key[0] && ES->e[i].key[1] == key[1] && ES->e[i].key[2] == key[2]) {
                ES->e[i].live = 0;                               /* one received message cancels one echo */
                usb = 0;
                break;
            }
        }
    }
    if (din || usb)
        fw_send(mgr, m, din, usb);
}
