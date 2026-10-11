"""USB host MIDI diagnostics and recovery under Unicorn.

Real firmware runs for: the MIDI class background process (FUN_080467fe) and SOF callback (FUN_08046790), the receive
complete callback (FUN_08044f1c) with usbmidi_rx behind it, the HAL URB-state / transfer-size getters, cpu.c's label
text (cpu_name).
Stubbed: bulk IN / OUT submits (recorded), osMessagePut (recorded), the USB core's channel registers (plain RAM).
NOT run: the USB hardware and the HAL interrupt handler (the test sets URB / channel states the way they would).
"""
import struct
import sys

from unicorn.arm_const import UC_ARM_REG_R0

from emu import Emu

IMAGE = "out/solo+slice+duck+chord+cond+filter+cpu+od+comp+seqfix+fx2+munchi+usbmidi+echo+usbdiag+cave/BLACKBOX.bin"
PH, CLS, C, HCD, CORE, RXBUF, MGR = 0x24050000, 0x24051000, 0x24051100, 0x24052000, 0x24061000, 0x24009710, 0x24002E88
IN, OUT = 1, 2
DG = 0x38800E00

e = Emu(IMAGE, "out/cave.elf")
e.uc.mem_map(0x38800000, 0x1000)
e.uc.mem_map(0x58024000, 0x1000)
submits, posts = [], []


def bulk_in():
    submits.append(("in", e.arg(1), e.arg(2), e.arg(3)))
    e.uc.mem_write(HCD + 0x38 + IN * 0x28 + 0x24, b"\x00")   # HAL: URB idle while the transfer runs
    e.ret(0)


def bulk_out():
    submits.append(("out", e.arg(1), e.arg(2), e.arg(3)))
    e.ret(0)


e.stubs[0x0808A2D6] = bulk_in
e.stubs[0x0808A2A4] = bulk_out
e.stub(0x08087B8E, lambda: posts.append(e.arg(1)), value=0)

fails = 0


def check(label, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if not cond else ""))
    fails += not cond


def hc(ch, off):
    return HCD + 0x38 + ch * 0x28 + off


def set_in(urb=None, state=None, count=None, enabled=None):
    if urb is not None:
        e.uc.mem_write(hc(IN, 0x24), bytes([urb]))
    if state is not None:
        e.uc.mem_write(hc(IN, 0x25), bytes([state]))
    if count is not None:
        e.w32(hc(IN, 0x14), count)
    if enabled is not None:
        e.w32(CORE + 0x500 + IN * 0x20, 0x80000000 if enabled else 0)


def setup():
    e.uc.mem_write(PH, bytes(0x600))
    e.w32(PH + 0x540, CLS)
    e.w32(CLS + 0x1C, C)
    e.w32(PH + 0x594, HCD)
    e.w32(PH + 0x59C, 0x24053000)
    e.uc.mem_write(C, bytes(0x20))
    e.uc.mem_write(C, bytes([1, IN, OUT]))
    e.uc.mem_write(C + 6, struct.pack("<HH", 64, 64))
    e.w32(C + 0x10, RXBUF)
    e.uc.mem_write(C + 0x16, struct.pack("<H", 0x200))
    e.uc.mem_write(HCD, bytes(0x38 + 16 * 0x28))
    e.w32(HCD, CORE)
    e.uc.mem_write(CORE, bytes(0x800))
    e.uc.mem_write(DG, bytes(0x40))


def rx_state():
    return e.r8(C + 0x19)


def tx_state():
    return e.r8(C + 0x18)


def dg(off, size=4):
    return e.r32(DG + off) if size == 4 else e.r8(DG + off)


def sof(n=1):
    for _ in range(n):
        e.call("usbdiag_sof", PH)


def bgnd():
    e.call("usbdiag_bgnd", PH)


def label():
    e.call("cpu_name", 0x24054000)
    return e.cstr(0x24054000)


def queue():
    r, w = e.r32(MGR + 0x4B60), e.r32(MGR + 0x4B64)
    out = []
    for i in range(r, w):
        ent = bytes(e.uc.mem_read(MGR + 0x4660 + (i & 0xFF) * 5, 5))
        out.append(ent[:ent[4]].hex())
    e.w32(MGR + 0x4B60, w)
    return out


# --- normal receive cycle
setup()
e.uc.mem_write(C + 0x19, b"\x03")
bgnd()
check("receive state 3: the stock process submits the IN transfer and waits (state 4)",
      submits[-1][0] == "in" and rx_state() == 4, (submits, rx_state()))
e.uc.mem_write(RXBUF, bytes([9, 0x90, 60, 100]))
set_in(urb=1, state=1, count=4, enabled=False)
bgnd()
check("transfer DONE: decoded into the queue, counted, receive re-armed", queue() == ["903c64"] and dg(4) == 1 and rx_state() == 3,
      (dg(4), rx_state()))
bgnd()
check("and submitted again", rx_state() == 4 and submits[-1][0] == "in")

# --- idle device NAKing: channel keeps being re-enabled, never a recovery
set_in(urb=2, state=3, enabled=True)
posts.clear()
sof(200)
check("200 ms of NAKs with the channel enabled: no recovery, no extra events", dg(8) == 0 and posts == [], (dg(8), posts))
lbl = label()
check("label shows the state: 3.1.U01 423 00 0 (1 transfer, waiting, notready, nak)", lbl == "3.1.U01 423 00 0", lbl)
check("label goes back to the version once the device stops reporting", label().startswith("3.1.n"), label())

# --- babble: channel halted for good, URB never DONE
set_in(urb=2, state=7, enabled=False)
sof(19)
check("19 ms halted: not yet", posts == [] and dg(8) == 0, posts)
sof(1)
check("20 ms halted (babble): the thread is woken", posts == [4], posts)
n = len(submits)
bgnd()
check("and the receive is submitted again, one recovery counted",
      dg(8) == 1 and len(submits) == n + 1 and submits[-1][0] == "in" and rx_state() == 4, (dg(8), submits[n:], rx_state()))
set_in(urb=2, state=3, enabled=True)
sof(50)
check("afterwards, NAKing normally: no further recovery", dg(8) == 1)

# --- data arrives after the recovery
e.uc.mem_write(RXBUF, bytes([8, 0x80, 60, 0]))
set_in(urb=1, state=1, count=4, enabled=False)
sof(50)
check("a finished transfer the thread has not handled yet is never 'recovered'", dg(8) == 1, dg(8))
bgnd()
check("it is decoded as normal", queue() == ["803c00"] and dg(4) == 2, dg(4))
bgnd()

# --- STALL on IN: recovered at once
posts.clear()
set_in(urb=5, state=5, enabled=False)
sof(1)
bgnd()
check("URB_STALL on IN: recovered on the next frame", dg(8) == 2 and rx_state() == 4 and posts == [4], (dg(8), posts))

# --- stuck send
set_in(urb=2, state=3, enabled=True)
e.uc.mem_write(C + 0x18, b"\x02")
e.uc.mem_write(hc(OUT, 0x24), b"\x04")
posts.clear()
sof(1)
check("send waiting on URB_ERROR: the thread is woken", posts == [4], posts)
bgnd()
check("and the stuck send is dropped (send state 0)", tx_state() == 0 and dg(8) == 3, (tx_state(), dg(8)))
e.uc.mem_write(hc(OUT, 0x24), b"\x02")
e.uc.mem_write(C + 0x18, b"\x02")
posts.clear()
sof(5)
check("send waiting on NOTREADY (device NAKs): left to the stock resend", posts == [] and dg(8) == 3)

print(f"{fails} failed" if fails else "all passed")
sys.exit(1 if fails else 0)
