"""USB MIDI echo filter under Unicorn, stock vs patched.

Real firmware runs for: the USB host receive callback and the per-block drain (FUN_08044f1c, FUN_08044844), the
engine's message reader (FUN_0804495e), and the MIDI send (FUN_08044a0e) that queues for USB out; the hooks around
them (echo_block, echo_read, echo_send).
Stubbed: re-arming the USB pipe; the DIN output port push (FUN_08045124, recorded: it drives the UART).
NOT run: the engine deciding to echo (a "block" here is: drain, read everything, then the sends the test makes).
"""
import struct
import sys

from unicorn.arm_const import UC_ARM_REG_R0

from emu import Emu

PATCHED = "out/solo+slice+duck+chord+cond+filter+cpu+od+comp+seqfix+fx2+munchi+echo+cave/BLACKBOX.bin"
MGR, RXBUF, OUT, PORT, MSG = 0x24002E88, 0x24009710, 0x24060000, 0x24060010, 0x24060020
USBQ, USBQ_W = MGR + 0x4B68, MGR + 0x506C

fails = 0


def check(label, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if not cond else ""))
    fails += not cond


class Box:
    def __init__(self, image, patched):
        self.e = e = Emu(image, "out/cave.elf")
        e.uc.mem_map(0x38800000, 0x1000)                      # backup SRAM
        e.uc.mem_map(0x58024000, 0x1000)                      # RCC / PWR (bkp_enable)
        e.stub(0x080467CC, value=0)                           # re-arm USB pipe
        self.din = []
        e.stub(0x08045124, lambda: self.din.append(bytes(e.uc.mem_read(e.arg(1), 3)).hex()))
        self.patched, self.usb_seen = patched, 0

    def usb_in(self, *msgs):
        pk = []
        for m in msgs:
            cin = m[0] >> 4
            pk.append(bytes([cin] + list(m)))
        self.e.uc.mem_write(RXBUF, b"".join(pk))
        self.e.call(0x08044F1C, 0x24070000)

    def din_in(self, *data):
        self.e.uc.mem_write(MGR + 0x20D, b"\x01")
        for b in data:
            self.e.call(0x080446C4, MGR, b, 0)

    def block(self):
        """Drain + read every pending message (as the engine's input loop does)."""
        e = self.e
        e.call("echo_block" if self.patched else 0x08044844, MGR)
        got = []
        for _ in range(100):
            e.call("echo_read" if self.patched else 0x0804495E, MGR, OUT, PORT)
            if e.uc.reg_read(UC_ARM_REG_R0) & 0xFF == 0:
                break
            m = bytes(e.uc.mem_read(OUT, 5))
            got.append((e.r8(PORT), m[:m[4]].hex()))
        return got

    def send(self, msg, din=0, usb=1):
        """The engine's MIDI output for one message; returns what reached the USB out queue / DIN."""
        e = self.e
        e.uc.mem_write(MSG, bytes(msg).ljust(4, b"\0") + bytes([len(msg)]))
        before, self.din = e.r32(USBQ_W), []
        e.call("echo_send" if self.patched else 0x08044A0E, MGR, MSG, din, usb)
        after = e.r32(USBQ_W)
        out = []
        for i in range(before, after):
            ent = bytes(e.uc.mem_read(USBQ + (i & 0xFF) * 5, 5))
            out.append(ent[:ent[4]].hex())
        return out, self.din


stock, mod = Box("firmware/BLACKBOX-3.1.9.bin", False), Box(PATCHED, True)

for name, b in (("stock", stock), ("patched", mod)):
    b.usb_in([0x90, 60, 100])
    got = b.block()
    check(f"{name}: arp note-on from USB reaches the engine", got == [(1, "903c64")], got)
    usb, _ = b.send([0x90, 60, 100])
    if name == "stock":
        check("stock: the engine's echo of it goes straight back out to USB", usb == ["903c64"], usb)
    else:
        check("patched: the echo is not sent back to USB", usb == [], usb)

usb, _ = mod.send([0x90, 60, 100])
check("patched: a second send of the same note is not an echo (one received note cancels one send)", usb == ["903c64"], usb)
mod.usb_in([0x80, 60, 0])
mod.block()
usb, _ = mod.send([0x90, 60, 0])
check("patched: the echo of the note-off is dropped too (note-on vel 0 = note-off)", usb == [], usb)
usb, _ = mod.send([0x90, 64, 90])
check("patched: a note the Blackbox plays itself still goes to USB", usb == ["90405a"], usb)

mod.usb_in([0x91, 48, 70])
mod.block()
usb, _ = mod.send([0x90, 48, 70])
check("patched: same note on another channel is not an echo", usb == ["903046"], usb)
usb, _ = mod.send([0x91, 48, 127])
check("patched: same channel and note with a different velocity is an echo", usb == [], usb)

mod.usb_in([0xB0, 74, 33])
mod.block()
usb, _ = mod.send([0xB0, 74, 34])
check("patched: a CC with a different value is not an echo", usb == ["b04a22"], usb)
usb, _ = mod.send([0xB0, 74, 33])
check("patched: the CC echo (same controller and value) is dropped", usb == [], usb)

mod.usb_in([0x90, 50, 100])
mod.block()
usb, din = mod.send([0x90, 50, 100], din=1, usb=1)
check("patched: echo with DIN out on: DIN still gets the note, USB does not", usb == [] and din == ["903264"] * 2, (usb, din))

mod.usb_in([0x90, 55, 100])
mod.block()
for _ in range(5):
    mod.block()
usb, _ = mod.send([0x90, 55, 100])
check("patched: five blocks (~27 ms) later the same note is no longer treated as an echo", usb == ["903764"], usb)
mod.usb_in([0x90, 56, 100])
mod.block()
for _ in range(3):
    mod.block()
usb, _ = mod.send([0x90, 56, 100])
check("patched: within the window (~21 ms) it still is", usb == [], usb)

mod.din_in(0x90, 57, 100)
got = mod.block()
usb, _ = mod.send([0x90, 57, 100])
check("patched: a note from DIN sent on to USB is not filtered (different device)", got == [(0, "903964")] and usb == ["903964"],
      (got, usb))

mod.usb_in(*[[0x90, 30 + i, 100] for i in range(16)])
got = mod.block()
outs = [mod.send([0x90, 30 + i, 100])[0] for i in range(16)]
check("patched: a 16-note burst from USB: every echo dropped", len(got) == 16 and all(o == [] for o in outs), outs)
for name, b in (("stock", stock), ("patched", mod)):
    b.usb_in([0xFA])
    got = b.block()
    usb, din = b.send([0xFA], din=1, usb=1)
    if name == "stock":
        check("stock: nothing stops the transport's Start from going back to the USB device it came from", got == [(1, "fa")] and usb == ["fa"],
              (got, usb))
    else:
        check("patched: the Start echo is dropped for USB, DIN still gets it", usb == [] and din == ["fa0000"] * 2, (usb, din))
mod.usb_in([0xFC])
mod.block()
usb, _ = mod.send([0xFC])
check("patched: the Stop echo is dropped", usb == [], usb)
usb, _ = mod.send([0xFA])
check("patched: a Start the Blackbox sends on its own still goes to USB", usb == ["fa"], usb)
usb, _ = mod.send([0xF8])
check("patched: one-byte messages pass through", usb == ["f8"], usb)

print(f"{fails} failed" if fails else "all passed")
sys.exit(1 if fails else 0)
