/*
 * USB host MIDI: recover a transfer that will never finish, instead of waiting for a replug.
 * Original 1010music Blackbox, firmware 3.1.9.
 *
 * The USB host MIDI class (class table 0x080f1e18) runs a receive and a send state machine in its background process
 * (FUN_080467fe, USBH_Thread, woken by events) and dequeues output in its SOF callback (FUN_08046790, every 1 ms frame,
 * in the USB interrupt). Receive: state 3 submits a bulk IN transfer, state 4 waits for the URB to be DONE. Send:
 * state 1 submits a chunk, state 2 waits for DONE (NOTREADY resends). Nothing else is handled. The HAL halts a channel
 * for good after a STALL or a babble error, and reports URB_ERROR after repeated transaction errors; the class then
 * waits forever, nothing posts another event, and only a replug (re-enumeration) brings USB MIDI back.
 *
 * The SOF callback (wrapped) watches both directions. Receive waiting (state 4) with its channel disabled and no DONE
 * for STUCK_MS frames, or on URB_STALL / URB_ERROR, is stuck; so is send waiting (state 2) on URB_STALL or URB_ERROR.
 * It flags the direction and posts a class event; the background process (wrapped) then puts receive back to state 3
 * (submit again) or drops the stuck send (state 0), before running the stock process. A finished transfer the thread
 * has not handled yet (URB DONE) is never touched, and a device that is simply idle (NAKing, channel re-enabled by the
 * HAL every time) is left alone.
 */
#include <stdint.h>

#define FN(addr) ((addr) | 1u)

typedef uint8_t (*class_fn)(uint8_t *phost);
typedef int (*post_fn)(void *queue, uint32_t msg, uint32_t timeout);
#define fw_bgnd   ((class_fn)FN(0x080467fe))
#define fw_sof    ((class_fn)FN(0x08046790))
#define fw_post   ((post_fn)FN(0x08087b8e))  /* osMessagePut, interrupt-safe */

/* USBH handle */
#define PH_CLASS   0x540         /* -> active class; its +0x1c -> class data */
#define PH_HCD     0x594         /* -> HCD handle */
#define PH_QUEUE   0x59c         /* USBH_Thread's event queue */
#define CLASS_EVT  4
/* MIDI class data */
#define C_IN_PIPE  0x01
#define C_OUT_PIPE 0x02
#define C_TX       0x18
#define C_RX       0x19
/* HCD handle: hc[i] at +0x38 + i * 0x28 */
#define HC(h, i)   ((h) + 0x38 + (i) * 0x28)
#define HC_ERRCNT  0x20
#define HC_URB     0x24
#define CHANNELS   16
#define CHENA      0x80000000u
#define URB_DONE   1
#define URB_ERROR  4
#define URB_STALL  5

#define STUCK_MS   20

void bkp_enable(void);

struct usbrecover {
    uint32_t magic;
    uint32_t recoveries;         /* for the tests */
    uint16_t idle_ms;            /* frames the IN channel has sat disabled without DONE */
    uint8_t kick_rx, kick_tx;
};
/* backup SRAM: after od.c's filter states (0x38800000..0x38800384), before comp.c's at 0x38800400 */
#define UR ((volatile struct usbrecover *)0x388003c0u)
_Static_assert(sizeof(struct usbrecover) <= 0x400 - 0x3c0, "usbrecover state runs into comp.c's");
#define MAGIC 0x55535231u        /* "USR1" */

static void ensure(void)
{
    bkp_enable();
    if (UR->magic == MAGIC)
        return;
    UR->recoveries = 0;
    UR->idle_ms = 0;
    UR->kick_rx = UR->kick_tx = 0;
    UR->magic = MAGIC;
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
uint8_t usbrecover_sof(uint8_t *phost)
{
    ensure();
    uint8_t *c = class_data(phost), *hcd = *(uint8_t **)(phost + PH_HCD);
    if (c && hcd && c[C_IN_PIPE] < CHANNELS && c[C_OUT_PIPE] < CHANNELS) {
        uint8_t *in = HC(hcd, c[C_IN_PIPE]), *out = HC(hcd, c[C_OUT_PIPE]);
        int kick = 0;
        if (c[C_RX] == 4 && in[HC_URB] != URB_DONE) {
            if (in[HC_URB] == URB_STALL || in[HC_URB] == URB_ERROR)
                UR->idle_ms = STUCK_MS;
            else if (!channel_enabled(hcd, c[C_IN_PIPE]))
                UR->idle_ms++;
            else
                UR->idle_ms = 0;
            if (UR->idle_ms >= STUCK_MS && !UR->kick_rx) {
                UR->kick_rx = 1;
                kick = 1;
            }
        } else {
            UR->idle_ms = 0;
        }
        if (c[C_TX] == 2 && (out[HC_URB] == URB_STALL || out[HC_URB] == URB_ERROR) && !UR->kick_tx) {
            UR->kick_tx = 1;
            kick = 1;
        }
        if (kick)
            fw_post(*(void **)(phost + PH_QUEUE), CLASS_EVT, 0);
    }
    return fw_sof(phost);
}

/* Class table BgndProcess (USBH_Thread): carry out a recovery the SOF watch asked for, then the stock process. */
uint8_t usbrecover_bgnd(uint8_t *phost)
{
    ensure();
    uint8_t *c = class_data(phost), *hcd = *(uint8_t **)(phost + PH_HCD);
    if (c && hcd) {
        if (UR->kick_rx) {
            UR->kick_rx = 0;
            if (c[C_RX] == 4 && HC(hcd, c[C_IN_PIPE])[HC_URB] != URB_DONE) {
                HC(hcd, c[C_IN_PIPE])[HC_ERRCNT] = 0;
                c[C_RX] = 3;                                  /* submit the IN transfer again */
                UR->recoveries++;
            }
            UR->idle_ms = 0;
        }
        if (UR->kick_tx) {
            UR->kick_tx = 0;
            uint8_t u = HC(hcd, c[C_OUT_PIPE])[HC_URB];
            if (c[C_TX] == 2 && (u == URB_STALL || u == URB_ERROR)) {
                c[C_TX] = 0;                                  /* drop the stuck send; the next SOF sends what is queued */
                UR->recoveries++;
            }
        }
    }
    return fw_bgnd(phost);
}
