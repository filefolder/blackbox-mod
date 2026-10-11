"""Piano roll wrap-around under Unicorn.

Real firmware runs for: the knob stepper (FUN_080b0b74) with its set-count / set-value helpers; the piano roll's
"apply this edit to the rest of the selection" loop (FUN_080c0288) end to end, with the stock per-field clamp behind
roll_clamp; the Step Len table in flash.
Stubbed: the selection list lookup (fed from a Python list), parameter reads (Step Len, Step Count), the post to the
app (recorded).
NOT run: the view constructor (the patched byte is checked statically), the app applying the posts, the engine.
"""
import struct
import sys

from capstone import CS_ARCH_ARM, CS_MODE_MCLASS, CS_MODE_THUMB, Cs

from emu import BASE, Emu

IMAGE = "out/solo+slice+duck+chord+cond+filter+cpu+od+comp+seqfix+fx2+munchi+roll+cave/BLACKBOX.bin"
STOCK = "firmware/BLACKBOX-3.1.9.bin"
e = Emu(IMAGE, "out/cave.elf")
s = Emu(STOCK, "out/cave.elf")

fails = 0


def check(label, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if not cond else ""))
    fails += not cond


# --- the constructor patch: the timing knob's stepper wrap byte is written from r1, and r1 holds 0x10 there
img = open(IMAGE, "rb").read()
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB + CS_MODE_MCLASS)
ins = list(md.disasm(img[0x080BA13A - BASE:0x080BA1E6 - BASE], 0x080BA13A))
check("constructor stores the wrap byte from r1", (ins[-1].mnemonic, ins[-1].op_str) == ("strb.w", "r1, [r3, #0x92c]"),
      f"{ins[-1].mnemonic} {ins[-1].op_str}")
check("r1 is 0x10 at that store (movs r1, #0x10, never written again before it)",
      (ins[0].mnemonic, ins[0].op_str) == ("movs", "r1, #0x10") and
      not any(i.op_str.split(",")[0] == "r1" and not i.mnemonic.startswith("str") for i in ins[1:-1]),
      [(i.mnemonic, i.op_str) for i in ins if "r1" in i.op_str])

# --- the stock knob stepper, driven like the timing knob: 400 counts per step, Step Count 16
STEP = 0x24050000
CPS, COUNT = 400, 16


def stepper(emu, value, wrap):
    emu.uc.mem_write(STEP, bytes(0x18))
    emu.w32(STEP + 8, CPS)
    emu.uc.mem_write(STEP + 0x14, bytes([0x10 if wrap else 0]))
    emu.call(0x080B0984, STEP, COUNT)        # set count
    emu.call(0x080B074C, STEP, value)        # set value (what selecting a note does)


def turn(emu, counts):
    emu.call(0x080B0B74, STEP, counts)
    return emu.r32(STEP + 0xC)


stepper(s, 0, wrap=False)
check("stock, wrap off: turning left from step 1 stays on step 1", turn(s, -CPS) == 0)
stepper(s, COUNT - 1, wrap=False)
check("stock, wrap off: turning right from the last step stays there", turn(s, CPS) == COUNT - 1)
stepper(e, 0, wrap=True)
check("wrap on: turning left from step 1 goes to the last step", turn(e, -CPS) == COUNT - 1)
check("wrap on: and right again comes back to step 1", turn(e, CPS) == 0)
stepper(e, COUNT - 1, wrap=True)
check("wrap on: turning right from the last step goes to step 1", turn(e, CPS) == 0)
stepper(e, 5, wrap=True)
check("wrap on: mid-pattern moves are unchanged", turn(e, CPS) == 6 and turn(e, -2 * CPS) == 4)
stepper(e, 3, wrap=True)
check("wrap on: a fast turn of a full pattern + 2 lands 2 steps on", turn(e, CPS * (COUNT + 2)) == 5)

# --- the rest-of-selection loop (FUN_080c0288), real code end to end
VIEW, MSG = 0x24010000, 0x24030000
STEP_LEN_16TH = None
events = []                                   # dicts: id, start (ticks), length (ticks), pitch
params = {}


def event_bytes(ev):
    return struct.pack("<BBHIIIHH", 1, 0, 0, ev["start"], ev["length"], ev["id"], ev["pitch"], 100)


def list_get(emu):
    def f():
        i, out = emu.arg(1), emu.arg(2)
        if i >= len(events):
            emu.ret(0)
            return
        emu.uc.mem_write(out, event_bytes(events[i]))
        emu.ret(1)
    return f


def param_get(emu):
    def f():
        emu.w32(emu.arg(3), params[emu.arg(2)])
        emu.ret(1)
    return f


def post(emu):
    def f():
        m = bytes(emu.uc.mem_read(emu.arg(1), 0x18))
        mid, ev, field, value = struct.unpack_from("<H", m)[0], *struct.unpack_from("<iii", m, 0xC)
        emu.calls.append((mid, ev, field, value))
        emu.ret()
    return f


for emu in (e, s):
    emu.stubs[0x0806381C] = list_get(emu)
    emu.stubs[0x08099504] = param_get(emu)
    emu.stubs[0x080AED14] = post(emu)


def move(emu, sel, primary, new_step, steps=16, field=2):
    """The knob note `primary` was moved to `new_step`; return {id: start step posted} for the rest."""
    global events
    events = [dict(id=i, start=st * 960, length=ln * 960, pitch=60) for i, (st, ln) in sel.items()]
    params.update({0x86: steps, 0x85: params.get(0x85, 0)})
    emu.uc.mem_write(VIEW, bytes(0x12000))
    emu.uc.mem_write(VIEW + 0x11938, b"\0")               # selection list up to date
    emu.w32(VIEW + 0x11934, len(sel))                     # selection size
    emu.uc.mem_write(MSG, struct.pack("<HHIHHiii", 0x16F, 0, 0, 0, 0, primary, field, new_step))
    calls = emu.call(0x080C0288, VIEW, MSG, count=5_000_000)
    assert all(c[0] == 0x16F and c[2] == field for c in calls), calls
    return {c[1]: c[3] for c in calls}


params[0x85] = 0
sel = {1: (0, 1), 2: (4, 1), 3: (14, 2)}               # id: (start step, length steps)
got = move(s, sel, 2, 3)
check("stock: moving the selection left squashes the note on step 1 against the start", got == {1: 0, 3: 13}, got)
check("stock: moving right past the end clamps the last note at Step Count - length",
      move(s, sel, 2, 5) == {1: 1, 3: 14})
got = move(e, sel, 2, 3)
check("wrap: the same move carries the note on step 1 round to the last step", got == {1: 15, 3: 13}, got)
got = move(e, sel, 2, 5)
check("wrap: moving right by 1 moves every other note by 1 (no clamp needed)", got == {1: 1, 3: 15}, got)
got = move(e, {1: (15, 1), 2: (3, 1), 3: (14, 2)}, 1, 0)   # knob note wrapped 15 -> 0: delta -15
check("wrap: knob note wrapping 15 -> 0 (delta -15) moves the others +1 modulo 16", got == {2: 4, 3: 15}, got)
got = move(e, {1: (0, 1), 2: (3, 1), 3: (15, 1)}, 1, 15)   # knob note wrapped 0 -> 15: delta +15
check("wrap: knob note wrapping 0 -> 15 (delta +15) moves the others -1 modulo 16", got == {2: 2, 3: 14}, got)
got = move(e, {1: (0, 1), 2: (31, 1)}, 1, 31, steps=32)
check("wrap: follows Step Count (32 steps)", got == {2: 30}, got)
got = move(e, {1: (5, 1), 2: (9, 1)}, 1, 6, steps=0)
check("Step Count unreadable (0): falls back to the stock clamp", got == {2: 10}, got)

# --- other fields still take the stock clamp (roll_clamp tail-calls it with the stack argument intact)
from unicorn.arm_const import UC_ARM_REG_R0
from emu import STACK

EV = 0x24031000


def clamp(emu, target, value, field, limit=16, tps=960):
    emu.uc.mem_write(EV, event_bytes(dict(id=1, start=2 * 960, length=960, pitch=60)))
    emu.w32(STACK, tps)                                   # 5th argument, on the stack
    emu.call(target, value, field, EV, limit)
    return struct.unpack("<i", struct.pack("<I", emu.uc.reg_read(UC_ARM_REG_R0)))[0]


for field, value in ((1, 200), (1, 0), (4, 0), (4, 40), (5, 5), (5, 99999), (6, 300), (7, 300)):
    a, b = clamp(e, "roll_clamp", value, field), clamp(s, 0x080C01C8, value, field)
    check(f"field {field}, value {value}: same as the stock clamp ({b})", a == b, (a, b))
check("start ticks (field 3) wrap modulo Step Count x ticks per step", clamp(e, "roll_clamp", -960, 3) == 15 * 960)
check("start ticks, stock clamp for comparison stops at 0", clamp(s, 0x080C01C8, -960, 3) == 0)

print(f"{fails} failed" if fails else "all passed")
sys.exit(1 if fails else 0)
