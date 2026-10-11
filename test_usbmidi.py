"""USB host MIDI input under Unicorn, stock vs patched.

Real firmware runs for: the USB host MIDI receive callback (FUN_08044f1c) with its buffer copy and clear, the
engine's per-block drain of the slots (FUN_08044844 -> FUN_08044708), and the engine's message reader
(FUN_0804495e, which also polls the two DIN ports).
Stubbed: re-arming the USB pipe (FUN_080467cc, recorded).
NOT run: the USB host stack itself, USBH_Thread / audio task scheduling (a "block" here is: drain, then read all).
"""
import struct
import sys

from unicorn.arm_const import UC_ARM_REG_R0

from emu import Emu

PATCHED = "out/solo+slice+duck+chord+cond+filter+cpu+od+comp+seqfix+fx2+munchi+usbmidi+cave/BLACKBOX.bin"
MGR, RXBUF, OUT, PORT = 0x24002E88, 0x24009710, 0x24060000, 0x24060010

fails = 0


def check(label, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if not cond else ""))
    fails += not cond


def machine(image):
    e = Emu(image, "out/cave.elf")
    e.rearms = 0

    def rearm():
        e.rearms += 1
    e.stub(0x080467CC, rearm, value=0)
    return e


def transfer(e, packets):
    """One completed USB IN transfer: the host stack has filled the receive buffer, then calls the callback."""
    e.uc.mem_write(RXBUF, b"".join(packets))
    e.call(0x08044F1C, 0x24070000)                         # phost (only passed through to the stubbed re-arm)


def block(e):
    """One audio block: the engine drains the USB slots, then reads every pending message."""
    e.call(0x08044844, MGR)
    got = []
    for _ in range(600):
        e.call(0x0804495E, MGR, OUT, PORT)
        if e.uc.reg_read(UC_ARM_REG_R0) & 0xFF == 0:
            break
        m = bytes(e.uc.mem_read(OUT, 5))
        got.append((e.r8(PORT), m[:m[4]].hex()))
    return got


def pkt(cin, *b):
    return bytes([cin] + list(b) + [0] * (3 - len(b)))


on = lambda n, v=100: pkt(9, 0x90, n, v)
off = lambda n: pkt(8, 0x80, n, 0)

stock, mod = machine("firmware/BLACKBOX-3.1.9.bin"), machine(PATCHED)

# --- baseline: both deliver a plain transfer the same way
for name, e in (("stock", stock), ("patched", mod)):
    transfer(e, [on(60), off(59), pkt(0xB, 0xB0, 1, 64), pkt(0xC, 0xC0, 5), pkt(0xF, 0xF8), on(61, 0)])
    got = block(e)
    check(f"{name}: one transfer of notes, CC, program change, clock, note-on vel 0 -> note-off",
          got == [(1, "903c64"), (1, "803b00"), (1, "b00140"), (1, "c005"), (1, "f8"), (1, "803d00")], got)
    check(f"{name}: the pipe is re-armed after each transfer", e.rearms == 1, e.rearms)

# --- bug 1: a system packet (CIN 0..7) aborts the rest of the transfer
SPP = pkt(3, 0xF2, 0, 0)
SYSEX = [pkt(4, 0xF0, 0x7E, 0x7F), pkt(4, 0x06, 0x01, 0x00), pkt(5, 0xF7)]
transfer(stock, [on(64), SPP, off(60)])
got = block(stock)
check("stock: a Song Position Pointer packet loses the note-off after it in the same transfer",
      got == [(1, "904064")], got)
transfer(mod, [on(64), SPP, off(60)])
got = block(mod)
check("patched: the SPP packet is skipped, the note-off arrives", got == [(1, "904064"), (1, "803c00")], got)
transfer(mod, [on(65)] + SYSEX + [off(65)])
got = block(mod)
check("patched: a SysEx between notes is skipped, both notes arrive", got == [(1, "904164"), (1, "804100")], got)

# --- bug 2: more than four transfers between two audio blocks
for e in (stock, mod):
    for n in range(5):
        transfer(e, [on(70 + n)])
got = block(stock)
check("stock: five transfers in one block -> the fifth overwrites the first, which is lost; the fifth arrives twice",
      [m for _, m in got] == ["904a64", "904764", "904864", "904964", "904a64"], got)
got = block(mod)
check("patched: five transfers in one block -> all five, once each, in order",
      [m for _, m in got] == [f"90{70 + n:02x}64" for n in range(5)], got)

# an arp as a sequencer sends it: each note-on and note-off in its own transfer
ARP = [on(40), off(40), on(41), off(41), on(42)]
for e in (stock, mod):
    for p_ in ARP:
        transfer(e, [p_])
got = block(stock)
check("stock: arp, 5 transfers in one block -> note 42 on twice with no note-off, note 40 off with no note-on",
      [m for _, m in got] == ["902a64", "802800", "902964", "802900", "902a64"], got)
got = block(mod)
check("patched: arp, 5 transfers in one block -> every note-on and note-off once, in order",
      [m for _, m in got] == ["902864", "802800", "902964", "802900", "902a64"], got)
for e in (stock, mod):
    for n in range(12):
        transfer(e, [on(40 + n), off(40 + n)])
got = block(stock)
check("stock: 12 transfers in one block -> notes 40..47 never arrive, 48..51 arrive three times",
      not any(m in ("902864", "902f64") for _, m in got) and [m for _, m in got].count("903064") == 3, got)
got = block(mod)
check("patched: the same 12 transfers deliver 12 note-ons and 12 note-offs, paired and in order",
      [m for _, m in got] == [x for n in range(12) for x in (f"90{40 + n:02x}64", f"80{40 + n:02x}00")], got)

# --- devices that pad every 64-byte USB packet with empty events
PAD = lambda *events: list(events) + [bytes(4)] * (16 - len(events))
for e in (stock, mod):
    transfer(e, PAD(on(80)) + PAD(off(80), on(81)) + PAD(off(81)))
got = block(stock)
check("stock: a transfer of three padded 64-byte packets delivers only the first packet's events",
      got == [(1, "905064")], got)
got = block(mod)
check("patched: all three padded packets are decoded", got == [(1, "905064"), (1, "805000"), (1, "905164"), (1, "805100")], got)

# --- the queue never overwrites unread messages
for n in range(20):
    transfer(mod, [pkt(0xB, 0xB0, 7, n % 128)] * 16)       # 320 messages, one block
got = block(mod)
check("patched: 320 messages in one block -> the first 256 arrive intact and in order, the rest are dropped",
      [m for _, m in got] == [f"b007{(i // 16) % 128:02x}" for i in range(256)], len(got))
transfer(mod, [on(90)])
check("patched: the queue keeps working after being full", block(mod) == [(1, "905a64")])

# --- DIN ports are untouched: a byte on DIN port 0 still parses and comes first
mod.uc.mem_write(MGR + 0x20D, b"\x01")
for i, b in enumerate((0x90, 0x30, 0x50)):
    mod.uc.mem_write(MGR + i, bytes([b]))
mod.w32(MGR + 0x204, 3)
transfer(mod, [on(91)])
got = block(mod)
check("patched: DIN input still parsed (port 0) alongside USB (port 1)", got == [(0, "903050"), (1, "905b64")], got)

print(f"{fails} failed" if fails else "all passed")
sys.exit(1 if fails else 0)
