/*
 * USB host MIDI input. Original 1010music Blackbox, firmware 3.1.9.
 *
 * Stock: the USB host MIDI class (USBH_Thread) finishes an IN transfer and calls FUN_08044f1c, which copies the
 * 512-byte receive buffer into one of four slots (FUN_080446da: slot = write & 3, write++), zeroes the buffer and
 * re-arms the pipe. Once per audio block (~5.3 ms) the engine drains the slots (FUN_08044844 -> FUN_08044708),
 * decoding the 4-byte USB-MIDI event packets into a 256-entry message queue that it then reads (FUN_0804495e).
 * Two problems:
 *  1. Four slots, no full check. USB delivers a transfer as often as every 1 ms frame, so more than four transfers
 *     can land between two drains (busy MIDI, an arpeggiator, clock + notes, or an audio block that runs late).
 *     The fifth overwrites the oldest unread slot: the drain then decodes the newest transfer twice and never sees
 *     the oldest. Lost note-offs (stuck notes), doubled note-ons, notes cut short.
 *  2. The packet decoder switches on the Code Index Number and only knows 8..F (channel messages, single bytes).
 *     For 0..7 it does not skip the packet, it stops decoding the transfer: one SysEx, Song Position Pointer, MTC
 *     or Song Select packet throws away every packet after it in the same transfer. (Stopping on CIN 0 is how it
 *     finds the end of the zero-padded buffer, which is why the other values went the same way.)
 *
 * This replaces the copy into a slot (bl FUN_080446da @0x08044f2c) with decoding the transfer straight into the
 * message queue, packet by packet, exactly as the stock decoder builds each message. Packets with CIN 0..7 are
 * skipped (stock never delivers system common or SysEx on any port), an all-zero packet ends the transfer, and if
 * the queue is ever full the new message is dropped instead of overwriting one the engine has not read yet.
 * The slots are never written again, so the engine's drain finds them empty; the queue's single writer is now this
 * callback (USBH_Thread), its single reader the audio task, as before.
 */
#include <stdint.h>

#define FN(addr) ((addr) | 1u)

typedef void (*inc_fn)(volatile uint32_t *index);
#define fw_index_inc ((inc_fn)FN(0x08040bac))   /* dmb; index++ (the stock queue publish) */

/* MIDI manager (0x24002e88): parsed-message queue fed from the USB host port */
#define Q_ENTRIES  0x4660        /* 256 x 5 bytes: status, data1, data2, 0, length */
#define Q_READ     0x4b60        /* engine side */
#define Q_WRITE    0x4b64        /* USB side */
#define Q_SIZE     256
#define ENTRY      5

struct usbmidi_msg {
    uint8_t b[4];
    uint8_t len;
};

/* Message length per Code Index Number, as the stock decoder (FUN_08044708) builds them; 0 = skip the packet. */
static const uint8_t cin_len[16] = {
    0, 0, 0, 0, 0, 0, 0, 0,      /* 0..7: reserved, cable events, system common, SysEx: stock never delivers these */
    3, 3, 3, 3,                  /* 8 note off, 9 note on, A poly pressure, B control change */
    2, 2,                        /* C program change, D channel pressure */
    3,                           /* E pitch bend */
    1,                           /* F single byte (clock, start, continue, stop, ...) */
};

/* Decode one 4-byte USB-MIDI event packet. Returns 0 when it carries nothing to deliver. Shared with the test. */
int usbmidi_decode(const uint8_t *pkt, struct usbmidi_msg *m)
{
    unsigned n = cin_len[pkt[0] & 0xf];
    if (!n)
        return 0;
    m->b[0] = pkt[1];
    m->b[1] = n > 1 ? pkt[2] : 0;
    m->b[2] = n > 2 ? pkt[3] : 0;
    m->b[3] = 0;
    m->len = (uint8_t)n;
    if ((m->b[0] & 0xf0) == 0x90 && n == 3 && m->b[2] == 0)      /* note on, velocity 0 -> note off (FUN_08044d68) */
        m->b[0] = (uint8_t)(0x80 | (m->b[0] & 0x0f));
    return 1;
}

/*
 * Replaces FUN_080446da(manager, buf, n) in the USB host receive callback: decode the transfer now instead of
 * parking it in a slot. buf is the zero-padded receive buffer (the callback clears it after this returns).
 */
void usbmidi_rx(uint8_t *mgr, const uint8_t *buf, unsigned n)
{
    volatile uint32_t *rd = (volatile uint32_t *)(mgr + Q_READ);
    volatile uint32_t *wr = (volatile uint32_t *)(mgr + Q_WRITE);
    struct usbmidi_msg m;

    for (unsigned i = 0; i + 4 <= n; i += 4) {
        const uint8_t *p = buf + i;
        if ((p[0] | p[1] | p[2] | p[3]) == 0)
            break;                                           /* end of this transfer */
        if (!usbmidi_decode(p, &m))
            continue;
        uint32_t w = *wr;
        if (w - *rd >= Q_SIZE)
            continue;                                        /* full: never overwrite an unread message */
        volatile uint8_t *e = mgr + Q_ENTRIES + (w & (Q_SIZE - 1)) * ENTRY;
        e[0] = m.b[0];
        e[1] = m.b[1];
        e[2] = m.b[2];
        e[3] = m.b[3];
        e[4] = m.len;
        fw_index_inc(wr);                                    /* publish after the entry is written */
    }
}
