/*
 * USB host MIDI: diagnostics on the version label, and recovery of a transfer that will never finish.
 * Original 1010music Blackbox, firmware 3.1.9.
 *
 * The USB host MIDI class (class table 0x080f1e18) runs a receive and a send state machine in its background process
 * (FUN_080467fe, USBH_Thread, woken by events) and dequeues output in its SOF callback (FUN_08046790, every 1 ms frame,
 * in the USB interrupt). Receive: state 3 submits a bulk IN transfer, state 4 waits for the URB to be DONE. Send:
 * state 1 submits a chunk, state 2 waits for DONE (NOTREADY resends). Nothing else is handled. The HAL halts a channel
 * for good after a STALL or a babble error, and reports URB_ERROR after repeated transaction errors on OUT; the class
 * then waits forever, nothing posts another event, and only a replug (re-enumeration) brings MIDI back.
 *
 * Recovery: the SOF callback (wrapped) watches both directions. Receive waiting (state 4) with its channel disabled
 * and no DONE for STUCK_MS frames, or URB_STALL / URB_ERROR, is stuck; so is send waiting (state 2) on URB_STALL or
 * URB_ERROR. It flags the direction and posts a class event; the background process (wrapped) then puts receive back
 * to state 3 (submit again) or drops the stuck send (state 0), before running the stock process.
 *
 * Diagnostics: cpu.c shows usbdiag_compose()'s text on the version label (TOOLS) while a USB MIDI device is attached:
 *   3.1.U<rr> <s><u><h> <t><o> <x>
 *   rr  completed receive transfers, mod 100      s  receive state (3 submit, 4 waiting, 0 idle)
 *   u   IN URB state (0 idle 1 done 2 notready 3 nyet 4 error 5 stall)   h  IN channel state (1 xfrc 2 halted 3 nak
 *       4 nyet 5 stall 6 xacterr 7 babble 8 data toggle error)
 *   t   send state (1 submit, 2 waiting, 0 idle)  o  OUT URB state       x  recoveries so far, mod 10
 */
#include <stdint.h>

#define FN(addr) ((addr) | 1u)

typedef uint8_t (*class_fn)(uint8_t *phost);
typedef void (*rxdone_fn)(uint8_t *phost);
typedef int (*post_fn)(void *queue, uint32_t msg, uint32_t timeout);
#define fw_bgnd   ((class_fn)FN(0x080467fe))
#define fw_sof    ((class_fn)FN(0x08046790))
#define fw_rxdone ((rxdone_fn)FN(0x08044f1c))
#define fw_post   ((post_fn)FN(0x08087b8e))  /* osMessagePut, interrupt-safe */

/* USBH handle */
#define PH_CLASS   0x540         /* -> active class; its +0x1c -> class data */
#define PH_HCD     0x594         /* -> HCD handle */
#define PH_QUEUE   0x59c         /* USBH_Thread's event queue */
#define CLASS_EVT  4
/* MIDI class data */
#define C_STATE    0x00
#define C_IN_PIPE  0x01
#define C_OUT_PIPE 0x02
#define C_TX       0x18
#define C_RX       0x19
/* HCD handle: hc[i] at +0x38 + i * 0x28 */
#define HC(h, i)   ((h) + 0x38 + (i) * 0x28)
#define HC_ERRCNT  0x20
#define HC_URB     0x24
#define HC_STATE   0x25
#define CHANNELS   16
#define CHENA      0x80000000u
#define URB_DONE   1
#define URB_ERROR  4
#define URB_STALL  5

#define STUCK_MS   20

void bkp_enable(void);

struct usbdiag {
    uint32_t magic;
    uint32_t rx_done;            /* completed receive transfers */
    uint32_t recoveries;
    uint16_t idle_ms;            /* frames the IN channel has sat disabled without DONE */
    uint8_t kick_rx, kick_tx;
    uint8_t rx, in_urb, in_hc, tx, out_urb, attached;
};
#define DG ((volatile struct usbdiag *)0x38800e00u)    /* backup SRAM, after echo.c's 0x38800c00..0x38800d0c */
#define MAGIC 0x55534431u        /* "USD1" */

static void ensure(void)
{
    bkp_enable();
    if (DG->magic == MAGIC)
        return;
    DG->rx_done = 0;
    DG->recoveries = 0;
    DG->idle_ms = 0;
    DG->kick_rx = DG->kick_tx = 0;
    DG->rx = DG->in_urb = DG->in_hc = DG->tx = DG->out_urb = DG->attached = 0;
    DG->magic = MAGIC;
}

static uint8_t *class_data(uint8_t *phost)
{
    uint8_t *cls = *(uint8_t **)(phost + PH_CLASS);
    return cls ? *(uint8_t **)(cls + 0x1c) : 0;
}

static int channel_enabled(uint8_t *hcd, unsigned ch)
{
    uint32_t base = *(uint32_t *)hcd;                       /* USB_OTG_GlobalTypeDef * */
    return (*(volatile uint32_t *)(base + 0x500 + ch * 0x20) & CHENA) != 0;
}

/* Class table SOFProcess (in the USB interrupt, every 1 ms frame): watch, flag, wake the thread. */
uint8_t usbdiag_sof(uint8_t *phost)
{
    ensure();
    uint8_t *c = class_data(phost), *hcd = *(uint8_t **)(phost + PH_HCD);
    if (c && hcd && c[C_IN_PIPE] < CHANNELS && c[C_OUT_PIPE] < CHANNELS) {
        uint8_t *in = HC(hcd, c[C_IN_PIPE]), *out = HC(hcd, c[C_OUT_PIPE]);
        DG->attached = 1;
        DG->rx = c[C_RX];
        DG->tx = c[C_TX];
        DG->in_urb = in[HC_URB];
        DG->in_hc = in[HC_STATE];
        DG->out_urb = out[HC_URB];

        int kick = 0;
        if (c[C_RX] == 4 && in[HC_URB] != URB_DONE) {
            if (in[HC_URB] == URB_STALL || in[HC_URB] == URB_ERROR)
                DG->idle_ms = STUCK_MS;
            else if (!channel_enabled(hcd, c[C_IN_PIPE]))
                DG->idle_ms++;
            else
                DG->idle_ms = 0;
            if (DG->idle_ms >= STUCK_MS && !DG->kick_rx) {
                DG->kick_rx = 1;
                kick = 1;
            }
        } else {
            DG->idle_ms = 0;
        }
        if (c[C_TX] == 2 && (out[HC_URB] == URB_STALL || out[HC_URB] == URB_ERROR) && !DG->kick_tx) {
            DG->kick_tx = 1;
            kick = 1;
        }
        if (kick)
            fw_post(*(void **)(phost + PH_QUEUE), CLASS_EVT, 0);
    }
    return fw_sof(phost);
}

/* Class table BgndProcess (USBH_Thread): carry out a recovery the SOF watch asked for, then the stock process. */
uint8_t usbdiag_bgnd(uint8_t *phost)
{
    ensure();
    uint8_t *c = class_data(phost), *hcd = *(uint8_t **)(phost + PH_HCD);
    if (c && hcd) {
        if (DG->kick_rx) {
            DG->kick_rx = 0;
            if (c[C_RX] == 4 && HC(hcd, c[C_IN_PIPE])[HC_URB] != URB_DONE) {
                HC(hcd, c[C_IN_PIPE])[HC_ERRCNT] = 0;
                c[C_RX] = 3;                                  /* submit the IN transfer again */
                DG->recoveries++;
            }
            DG->idle_ms = 0;
        }
        if (DG->kick_tx) {
            DG->kick_tx = 0;
            uint8_t u = HC(hcd, c[C_OUT_PIPE])[HC_URB];
            if (c[C_TX] == 2 && (u == URB_STALL || u == URB_ERROR)) {
                c[C_TX] = 0;                                  /* drop the stuck send; the next SOF sends what is queued */
                DG->recoveries++;
            }
        }
    }
    return fw_bgnd(phost);
}

/* Replaces the class's call to the receive-complete callback (bl @0x08046906): count, then the callback. */
void usbdiag_rxdone(uint8_t *phost)
{
    ensure();
    DG->rx_done++;
    fw_rxdone(phost);
}

/* For cpu.c: the label text while a USB MIDI device is attached; 0 = none, show the CPU meter. At most 16 chars. */
int usbdiag_compose(char *buf)
{
    ensure();
    if (!DG->attached)
        return 0;
    unsigned r = DG->rx_done % 100;
    char *p = buf;
    const char *v = "3.1.U";
    while (*v)
        *p++ = *v++;
    *p++ = (char)('0' + r / 10);
    *p++ = (char)('0' + r % 10);
    *p++ = ' ';
    *p++ = (char)('0' + DG->rx % 10);
    *p++ = (char)('0' + DG->in_urb % 10);
    *p++ = (char)('0' + DG->in_hc % 10);
    *p++ = ' ';
    *p++ = (char)('0' + DG->tx % 10);
    *p++ = (char)('0' + DG->out_urb % 10);
    *p++ = ' ';
    *p++ = (char)('0' + DG->recoveries % 10);
    *p = 0;
    DG->attached = 0;                                        /* the SOF watch sets it again within 1 ms */
    return 1;
}
