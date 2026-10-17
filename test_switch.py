"""Type switching in the delay slot, with the REAL stock line clear (FUN_0806AAC0) running.

Every other suite STUBS that call, which is the blind spot that let the Relapse hang through: a stub returns in one
instruction, so no test could ever see what clearing three multi-megabyte lines costs, or that a clear repeats. Here
the firmware's own clear runs against lines the size the test unit really has, and the cost is counted in instructions
and converted at the measured 398.8 MHz against the 2.67 ms audio block.

Real firmware runs for: the line clear, the settings lookup, the buffer pointer / length helpers, the output mix.
Stubbed: the stock delay process, the send gate, the bus lookup.
NOT checked: the GUI task (its page rebuild goes through stock's screen handler, which this harness fakes), task
priorities, and the watchdog - a stall this harness measures in ms is only a hang on hardware if something else
depends on the audio task inside that window.
"""
import struct
import sys

from unicorn import UC_HOOK_CODE

from emu import Emu

e = Emu("out/solo+slice+duck+chord+cond+filter+cpu+od+comp+seqfix+fx2+munchi+master+cave/BLACKBOX.bin", "out/cave.elf")
e.uc.mem_map(0x38800000, 0x1000)
e.uc.mem_map(0x58024000, 0x1000)
e.uc.mem_map(0x50003000, 0x1000)                       # WWDG1: audio_watch re-arms the early wakeup each block
e.uc.mem_map(0xC0000000, 0x800000)                     # SDRAM: the delay's three lines, at their real sizes
e.uc.mem_map(0x30000000, 0x10000)
OBJ, VT, BUFS, CTX, BUS, IN = 0x30000000, 0x30001000, 0x30001100, 0x30001200, 0x30001300, 0x30001400
BUSL, BUSR, INL, INR, ARR = 0x30002000, 0x30002400, 0x30002800, 0x30002C00, 0x30003000
LIVE, STATE, FRZ = 0xC0000000, 0xC0400000, 0xC0500000
APPFX = 0x24020088 + 0x2FC0
N = 128
MHZ, BLOCK_MS = 398.8, 128 / 48.0                      # the measured core clock; a 128-frame block is 2.67 ms

# The line sizes are the ones read off the test unit: LIVE and FREEZE are 4 s of 16-bit stereo, STATE is 2 s of floats.
LINES = ((0x30, LIVE, 192256), (0x4C, STATE, 96256), (0x68, FRZ, 192256))

e.w32(OBJ, VT)
e.w32(VT + 0x54, 0x08046A15)
e.w32(OBJ + 0x18, 3 << 16 | 0 << 8)
e.uc.mem_write(OBJ + 0x1E, struct.pack("<H", 2))
e.uc.mem_write(OBJ + 0x2C, struct.pack("<f", 0.5))
e.uc.mem_write(OBJ + 0x21F, b"\x01")
for off, ptr, floats in LINES:
    e.w32(OBJ + off, ptr)
    e.w32(OBJ + off + 0x14, floats)
e.w32(BUFS, CTX)
e.uc.mem_write(CTX + 0x18, struct.pack("<f", 120.0))


def buf(addr, l, r):
    e.w32(addr, N)
    e.w32(addr + 4, N)
    e.w32(addr + 8, l)
    e.w32(addr + 12, r)
    e.uc.mem_write(addr + 0x10, b"\x00\x01")


buf(BUS, BUSL, BUSR)
buf(IN, INL, INR)

calls = []
e.stub(0x080548C8, lambda: calls.append("stock"), value=1)
e.stub(0x0804F0D4)
e.stub(0x0804F270, lambda: e.w32(e.arg(2), IN))
e.stub(0x0805F37C, value=BUS)
# NOT a stub: let the firmware's clear run, and only note that it was reached.
e.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, _: calls.append(("clear", e.arg(0) - OBJ)), begin=0x0806AAC0, end=0x0806AAC0)

IDS = {"type": 0x197, "fb": 0x38, "wand": 0x1D1, "random": 0x1D2, "freeze": 0x1D3, "clock": 0x1D4, "width": 0x1D5,
       "duck": 0x1D6, "size": 0x1E3, "recall": 0x1E4, "reach": 0x1E9, "twist": 0x1E6, "tone": 0x1E7, "feed": 0x1E8,
       "mokind": 0x1BF, "morate": 0x1C0, "modepth": 0x1C1, "cfmode": 0x1EC, "cfdensity": 0x1ED, "cfcontour": 0x1EE,
       "cfspace": 0x1EF}
params = {}


def store(**kv):
    params.update(kv)
    data = b"".join(struct.pack("<HHI", IDS[k], 0, v) for k, v in params.items())
    e.uc.mem_write(ARR, data)
    e.w32(APPFX + 4, ARR)
    e.uc.mem_write(APPFX + 10, struct.pack("<H", len(params)))


counting = [False]
count = [0]
e.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, _: count.__setitem__(0, count[0] + 1) if counting[0] else None)


def block(measure=False):
    e.uc.mem_write(INL, struct.pack(f"<{N}f", *([0.05] * N)))
    e.uc.mem_write(INR, struct.pack(f"<{N}f", *([0.05] * N)))
    calls.clear()
    counting[0], count[0] = measure, 0
    e.call("munchi_process", OBJ, BUFS, count=200_000_000)
    counting[0] = False
    return count[0]


def ms(instructions):
    """Instructions -> ms, at ~1.25 cycles per instruction (the clear loop is a 3-instruction store loop)."""
    return instructions * 1.25 / (MHZ * 1000)


fails = 0


def check(label, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if not cond else ""))
    fails += not cond


TYPES = {0: "Delay", 1: "Munchi", 2: "Motion", 3: "Relapse"}
store(fb=500, size=5, recall=400, reach=500, twist=0, tone=300, feed=0, wand=250, random=300, freeze=0, clock=0,
      width=0, duck=0, mokind=1, morate=400, modepth=600, cfmode=0, cfdensity=500, cfcontour=500, cfspace=300)

# --- 1. the real clear costs this much, and it lands in ONE audio block
e.w32(0x38800B14, 0)                                   # MODE: unknown epoch, so the first block always clears
store(type=1)
block()                                                # settle on Munchi
store(type=3)
switch = block(measure=True)
cleared = [c for c in calls if c[0] == "clear"]
check(f"Munchi -> Relapse clears all three lines ({len(cleared)})", len(cleared) == 3, calls[:5])
print(f"     the switching block: {switch:,} instructions = {ms(switch):.2f} ms of work in a {BLOCK_MS:.2f} ms block"
      f" ({ms(switch) / BLOCK_MS:.1f}x)")
steady = block(measure=True)
check(f"the next block does not clear again ({ms(steady):.2f} ms, {steady:,} instructions)",
      not [c for c in calls if c[0] == "clear"], calls[:5])
check("a type switch is not a hang: the clear happens once, and the block after it is back to normal",
      ms(steady) < BLOCK_MS and steady * 8 < switch, (steady, switch))

# --- 2. every transition: does any pair clear more than once, or clear on every block?
print("\n     from -> to          clears   switch block      next block")
worst = (0, "")
for frm in TYPES:
    for to in TYPES:
        if frm == to:
            continue
        e.w32(0x38800B14, 0)
        store(type=frm)
        block()
        block()
        store(type=to)
        a = block(measure=True)
        na = len([c for c in calls if c[0] == "clear"])
        b = block(measure=True)
        nb = len([c for c in calls if c[0] == "clear"])
        print(f"     {TYPES[frm]:9}-> {TYPES[to]:9} {na:4}      {ms(a):6.2f} ms       {ms(b):6.2f} ms"
              + ("   <-- CLEARS AGAIN" if nb else ""))
        check(f"{TYPES[frm]} -> {TYPES[to]}: clears once, then settles", nb == 0 and ms(b) < BLOCK_MS, f"{nb} clears, {ms(b):.2f} ms")
        if ms(a) > worst[0]:
            worst = (ms(a), f"{TYPES[frm]} -> {TYPES[to]}")

print(f"\n     worst switch: {worst[1]} at {worst[0]:.2f} ms ({worst[0] / BLOCK_MS:.1f} blocks' worth of work in one block)")

# --- 3. the clear really does hand the lines over empty: no type reads the last one's state
e.w32(0x38800B14, 0)
store(type=1)
block()
e.uc.mem_write(LIVE, b"\xAA" * 4096)                   # Munchi's leftovers
e.uc.mem_write(STATE, b"\xAA" * 4096)
e.uc.mem_write(FRZ, b"\xAA" * 4096)
store(type=3)
block()
left = (bytes(e.uc.mem_read(LIVE, 4096)).count(0xAA) + bytes(e.uc.mem_read(FRZ, 4096)).count(0xAA))
check("the lines are handed over zeroed, so Relapse never reads Munchi's state", left == 0, f"{left} bytes left")

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
