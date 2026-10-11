"""Piano roll knobs under Unicorn.

Real firmware runs for: the box-selection lookup (FUN_080ba99c) and the knob steppers' set-value (FUN_080b074c).
Stubbed and recorded: the four stock knob handlers (what state they are entered with: current note, knob values, box
selection) and the note highlight setter.
NOT run: the stock handlers themselves (posting the edit, the parent's selection copy, the app) and drawing.
"""
import struct
import sys

from unicorn.arm_const import UC_ARM_REG_R4

from emu import Emu

IMAGE = "out/solo+slice+duck+chord+cond+filter+cpu+od+comp+seqfix+fx2+munchi+roll+prknobs+cave/BLACKBOX.bin"
e = Emu(IMAGE, "out/cave.elf")
e.uc.mem_map(0x38800000, 0x1000)
e.uc.mem_map(0x58024000, 0x1000)

V = 0x24020038                                  # piano roll view (its parent at V - 0x38)
CPS = 400
SEL, LEN, PITCH, START = 0xA070, 0xA898, 0xA900, 0xA918
NOTES = [(0, 1, 60, 1001), (4, 2, 62, 1002), (8, 1, 64, 1003), (12, 2, 67, 1004)]   # (start, len, pitch, id)
calls, hl = [], {}

fails = 0


def check(label, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if not cond else ""))
    fails += not cond


def box():
    n = e.r32(V + 0xB2A8)
    return [e.r32(V + 0xAEA8 + 4 * i) for i in range(n)]


def stepper_value(off):
    return e.r32(V + off + 0xC)


def record(name):
    def f():
        calls.append(dict(name=name, delta=struct.unpack("<i", struct.pack("<I", e.arg(1)))[0],
                          cur=e.r32(V + 0xA088), cur_id=e.r32(V + 0xA08C), box=box(),
                          start=stepper_value(START), length=stepper_value(LEN), pitch=stepper_value(PITCH),
                          sel=stepper_value(SEL), dirty=e.r8(V + 0x11900)))
        e.ret()
    return f


for name, addr in (("select", 0x080BBF90), ("start", 0x080BAE50), ("length", 0x080BACD8), ("pitch", 0x080BBBC4)):
    e.stubs[addr] = record(name)


def set_hl():
    hl[e.arg(0)] = e.arg(1)
    e.ret()


e.stubs[0x080BA048] = set_hl


def widget(i):
    return V + i * 0x50 + 0x34                    # cell i = note i here


def lit():
    return [i for i in range(len(NOTES)) if hl.get(widget(i))]


def stepper(off, count, value=0):
    e.uc.mem_write(V + off, bytes(0x18))
    e.w32(V + off + 8, CPS)
    e.w32(V + off + 0x10, count)
    e.call(0x080B074C, V + off, value)


def setup(boxed=(), cur=0, fresh=True):
    e.uc.mem_write(V - 0x38, bytes(0x12000))
    for i, (st, ln, p, nid) in enumerate(NOTES):
        c = V + i * 0x50
        e.uc.mem_write(c + 0x68, struct.pack("<IIIIII", st, ln, st, ln, p, nid))
        e.w32(V + 0xA094 + 4 * i, i)
    e.w32(V + 0xA894, len(NOTES))
    e.w32(V + 0xAE80, 1)                          # whole steps
    e.w32(V + 0xAE78, 24)                         # pitch knob offset
    stepper(SEL, len(NOTES), cur)
    stepper(START, 16)
    stepper(LEN, 17)
    stepper(PITCH, 73)
    e.w32(V + 0xA088, cur)
    e.w32(V + 0xA08C, NOTES[cur][3])
    for i, nid in enumerate(boxed):
        e.w32(V + 0xAEA8 + 4 * i, nid)
    e.w32(V + 0xB2A8, len(boxed))
    if fresh:
        e.uc.mem_write(0x38800E40, bytes(16))     # no state yet
    calls.clear()
    hl.clear()


def knob(fn, delta):
    calls.clear()
    e.call(fn, V, delta & 0xFFFFFFFF)
    return calls


# --- opening a pattern: the reload's "first note becomes current" site
setup()
e.uc.reg_write(UC_ARM_REG_R4, V)                  # the reload function keeps the view in r4
e.call("prk_hl_first", widget(0), 1)
check("opening (thunk, view from r4): the first note is not highlighted (nothing selected)", hl.get(widget(0)) == 0, hl)
e.uc.reg_write(UC_ARM_REG_R4, V)
e.call("prk_hl_keep", widget(2), 1)
check("reload keeping the current note while nothing is selected: still not highlighted", hl.get(widget(2)) == 0, hl)
setup(boxed=(1001,))
e.uc.reg_write(UC_ARM_REG_R4, V)
e.call("prk_hl_first", widget(0), 1)
check("opening with that note boxed: it is highlighted (box selection)", hl.get(widget(0)) == 1, hl)

# --- nothing selected, no box: lower-left moves every note
setup()
e.call("prk_reload_hl", widget(0), 1, V, 0)        # opened: nothing selected
c = knob("prk_start", CPS)
check("bottom-left with nothing selected: one stock call", len(c) == 1 and c[0]["name"] == "start" and c[0]["delta"] == CPS, c)
check("  ... with every note standing in as the box selection", c and c[0]["box"] == [n[3] for n in NOTES], c and c[0]["box"])
check("  ... the first note as anchor, the time knob loaded from it, parent told to rebuild",
      c and c[0]["cur"] == 0 and c[0]["cur_id"] == 1001 and c[0]["start"] == 0 and c[0]["dirty"] == 1, c)
check("  ... afterwards the box is empty again and nothing is highlighted", box() == [] and lit() == [], (box(), lit()))
c = knob("prk_pitch", -CPS)
check("bottom-right with nothing selected: pitch knob loaded from the anchor (60 - 24), every note boxed",
      len(c) == 1 and c[0]["name"] == "pitch" and c[0]["pitch"] == 36 and len(c[0]["box"]) == 4, c)
c = knob("prk_length", CPS)
check("top-right with nothing selected: length of every note (knob loaded with the anchor's 1 step)",
      len(c) == 1 and c[0]["name"] == "length" and c[0]["length"] == 1 and len(c[0]["box"]) == 4, c)

# --- box selection while nothing is current
setup(boxed=(1003, 1004))
e.call("prk_reload_hl", widget(0), 1, V, 0)        # opened: nothing selected
c = knob("prk_start", -CPS)
check("box of two, nothing current: anchor is the first boxed note, box untouched",
      c and c[0]["cur"] == 2 and c[0]["box"] == [1003, 1004] and c[0]["start"] == 8, c)
check("  ... the boxed notes stay highlighted, nothing else", lit() == [2, 3], lit())

# --- top-left: into and out of "nothing selected"
setup()
e.call("prk_reload_hl", widget(0), 1, V, 0)
c = knob("prk_select", CPS // 2)
check("top-left half a step from nothing: no change yet", c == [])
c = knob("prk_select", CPS // 2)
check("  ... the rest of the step: the first note is picked by the stock handler",
      len(c) == 1 and c[0]["name"] == "select" and c[0]["delta"] == 0 and c[0]["sel"] == 0 and c[0]["cur"] == -1, c)
e.w32(V + 0xA088, 0)                              # what the stock handler does
c = knob("prk_select", CPS)
check("a current note picked: further steps are stock", len(c) == 1 and c[0]["delta"] == CPS, c)
e.w32(V + 0xA088, 3)                              # on the last note
hl[widget(3)] = 1
c = knob("prk_select", CPS)
check("forward past the last note: back to nothing selected (no stock call, highlight cleared)",
      c == [] and lit() == [] and box() == [], (c, lit()))
c = knob("prk_select", -CPS)
check("from nothing, a step back: the last note", len(c) == 1 and c[0]["sel"] == 3, c)
e.w32(V + 0xA088, 3)
c = knob("prk_select", -CPS)
check("stepping back from the last note: stock (no wrap at that end)", len(c) == 1 and c[0]["delta"] == -CPS, c)

# --- a current note picked with the top-left knob: lower knobs edit it (stock)
setup(cur=1)
e.call("prk_select", V, CPS)                      # leave "nothing": first note
e.w32(V + 0xA088, 1)
c = knob("prk_pitch", CPS)
check("current note picked, no box: bottom-right is plain stock (no box stand-in)",
      len(c) == 1 and c[0]["box"] == [] and c[0]["cur"] == 1, c)
setup(boxed=(1002, 1003), cur=1)
e.call("prk_select", V, CPS)
e.w32(V + 0xA088, 1)
c = knob("prk_start", CPS)
check("current note inside a box: it stays the anchor", c and c[0]["cur"] == 1 and c[0]["box"] == [1002, 1003], c)
setup(boxed=(1003,), cur=0)
e.call("prk_select", V, CPS)
e.w32(V + 0xA088, 0)
c = knob("prk_start", CPS)
check("current note outside the box: the box's note becomes the anchor", c and c[0]["cur"] == 2, c)
check("  ... and only the boxed note is highlighted", lit() == [2], lit())

# --- empty pattern
setup()
e.w32(V + 0xA894, 0)
c = knob("prk_start", CPS)
check("no notes: stock handler as is", len(c) == 1 and c[0]["box"] == [], c)

# --- the dispatch patches
img = open(IMAGE, "rb").read()
B = 0x08040000
check("bottom-right no longer skipped by the +0xf5e0 flag", img[0x080C17B4 - B:0x080C17B8 - B] == bytes.fromhex("00bf00bf"))

print(f"{fails} failed" if fails else "all passed")
sys.exit(1 if fails else 0)
