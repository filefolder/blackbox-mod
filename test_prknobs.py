"""Piano roll knobs under Unicorn.

Real firmware runs for: the box-selection lookup (FUN_080ba99c) and the knob steppers' set-value (FUN_080b074c).
Modelled: the app's note list for the shown layer (a pool and a handle array sorted by start, as the firmware keeps
it), the sequence's Step Count / Step Len / Step Mode.
Stubbed and recorded: the four stock knob handlers (what state they are entered with), the note highlight setter, the
engine's add / update by id (FUN_0804c798) and the view refresh post (FUN_080b5758).
NOT run: the app's own handling of the refresh (the piano roll reload), the engine, drawing.
"""
import struct
import sys

from unicorn.arm_const import UC_ARM_REG_R4

from emu import Emu

IMAGE = "out/solo+slice+duck+chord+cond+filter+cpu+od+comp+seqfix+fx2+munchi+roll+prknobs+cave/BLACKBOX.bin"
e = Emu(IMAGE, "out/cave.elf")
e.uc.mem_map(0x38800000, 0x1000)
e.uc.mem_map(0x58024000, 0x1000)

V = 0x24040038                                  # piano roll view (its parent at V - 0x38)
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

# --- the app's side: the shown sequence's settings and its layer's note list
CELL, HANDLES, POOLPTR, POOL = 0x24060100, 0x24060400, 0x24060200, 0x24061000
SEQ_ID = 0x0021                                   # row 2, column 1: a sequence cell
params = {}
engine, refresh = [], []


def param_get():
    e.w32(e.arg(3), params[e.arg(2)])
    e.ret(1)


e.stubs[0x08099504] = param_get
e.stub(0x08097CE8, value=CELL)                    # cell for (app, ref, layer)
e.stub(0x08093E9C, value=0)                       # its layer
e.stub(0x0804C798, lambda: engine.append(e.r32(e.arg(2) + 0xC)))
e.stub(0x080B5758, lambda: refresh.append(e.r16(e.arg(1))))
unit = 960


def pool():
    """{id: (start, length, pitch)} in steps, and the ids in handle order."""
    n = e.r32(CELL + 0x18 + 4)
    order, notes = [], {}
    for k in range(n):
        h = e.r16(HANDLES + 2 * k) & 0x3FFF
        t, _, _, st, ln, nid, p, _ = struct.unpack_from("<BBHIIIHH", e.uc.mem_read(POOL + 0x18 * h, 0x18))
        order.append((st, nid))
        notes[nid] = (st / unit, ln / unit, p)
    return notes, order


def edits():
    """{event id: new value} of the field that changed: start / length in steps, or pitch."""
    now, _ = pool()
    out = {}
    for nid, was in before.items():
        for f in range(3):
            if now[nid][f] != was[f]:
                out[nid] = now[nid][f] if f == 2 else int(now[nid][f]) if now[nid][f] == int(now[nid][f]) else now[nid][f]
    return out


def sorted_by_start():
    _, order = pool()
    return [st for st, _ in order] == sorted(st for st, _ in order)


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
        e.uc.mem_write(0x38800388, bytes(0x38))   # no state yet
    global unit
    params.update({0x86: 16, 0x85: 10, 0x88: 1})  # 16 steps, Step Mode ON: note times are steps x 960
    unit = 960
    e.uc.mem_write(V - 0x38 + 0xB490, struct.pack("<IHH", 0, SEQ_ID, 0))
    e.uc.mem_write(POOL, bytes(0x18 * 64))
    for i, (st, ln, p, nid) in enumerate(NOTES):
        e.uc.mem_write(POOL + 0x18 * i, struct.pack("<BBHIIIHH", 1, 0, 0, st * unit, ln * unit, nid, p, 100))
        e.uc.mem_write(HANDLES + 2 * i, struct.pack("<H", i))
    e.uc.mem_write(CELL + 0x18, struct.pack("<IIIII", HANDLES, len(NOTES), 0, 0, POOLPTR))
    e.w32(POOLPTR, POOL)
    calls.clear()
    engine.clear()
    refresh.clear()
    hl.clear()


def knob(fn, delta):
    global before
    calls.clear()
    engine.clear()
    refresh.clear()
    before, _ = pool()
    e.call(fn, V, delta & 0xFFFFFFFF)
    return calls


before = {}


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

# --- nothing selected, no box: the lower knobs move every note
setup()
e.call("prk_reload_hl", widget(0), 1, V, 0)        # opened: nothing selected
c = knob("prk_start", CPS)
check("bottom-left one step, nothing selected: no stock handler, every note moved one step later",
      c == [] and edits() == {1001: 1, 1002: 5, 1003: 9, 1004: 13}, (c, edits()))
check("  ... each moved note sent to the engine once, and ONE refresh for the views (not one per note)",
      sorted(engine) == [1001, 1002, 1003, 1004] and refresh == [0x66], (engine, refresh))
check("  ... the app's list is still in start order; nothing highlighted, no box",
      sorted_by_start() and lit() == [] and box() == [], (pool()[1], lit()))
c = knob("prk_start", -2 * CPS)
check("bottom-left two steps back: the note on step 2 wraps to step 16",
      edits() == {1001: 15, 1002: 3, 1003: 7, 1004: 11}, edits())
check("  ... and the list is re-sorted (the wrapped note is now last)", sorted_by_start() and pool()[1][-1][1] == 1001,
      pool()[1])
c = knob("prk_start", CPS // 2)
check("half a step: nothing yet", engine == [] and refresh == [], (engine, refresh))
c = knob("prk_start", CPS // 2)
check("  ... the other half: one step", edits() == {1001: 0, 1002: 4, 1003: 8, 1004: 12}, edits())

setup()
e.call("prk_reload_hl", widget(0), 1, V, 0)
c = knob("prk_pitch", 2 * CPS)
check("bottom-right two steps, nothing selected: every note up 2 semitones",
      c == [] and edits() == {1001: 62, 1002: 64, 1003: 66, 1004: 69} and refresh == [0x66], (edits(), refresh))
c = knob("prk_pitch", -100 * CPS)
check("bottom-right far down: the set stops when its lowest note reaches the bottom (24), intervals kept",
      edits() == {1001: 24, 1002: 26, 1003: 28, 1004: 31}, edits())
c = knob("prk_pitch", -CPS)
check("  ... and does not move further (nothing sent, no refresh)", engine == [] and refresh == [], (engine, refresh))

setup()
e.call("prk_reload_hl", widget(0), 1, V, 0)
c = knob("prk_length", CPS)
check("top-right one step, nothing selected: every note one step longer",
      c == [] and edits() == {1001: 2, 1002: 3, 1003: 2, 1004: 3}, edits())
c = knob("prk_length", -5 * CPS)
check("top-right far down: every note down to 1 step, not below", edits() == {1001: 1, 1002: 1, 1003: 1, 1004: 1},
      edits())
setup()
e.call("prk_reload_hl", widget(0), 1, V, 0)
c = knob("prk_length", -CPS)
check("top-right one step shorter: only the two 2-step notes change, and only they are sent to the engine",
      edits() == {1002: 1, 1004: 1} and sorted(engine) == [1002, 1004], (edits(), engine))

# --- a note left past the loop end (Step Count made shorter) is not pulled into the loop
setup()
e.call("prk_reload_hl", widget(0), 1, V, 0)
params[0x86] = 10                                 # loop now 10 steps: the note on step 13 is past its end
c = knob("prk_start", CPS)
check("bottom-left with a note past the loop end: it stays, the others move (and wrap at the new end)",
      edits() == {1001: 1, 1002: 5, 1003: 9} and 1004 not in edits(), edits())

# --- free timing (Step Mode OFF or REPLACE): a step is Step Len's ticks
for mode in (0, 2):
    setup()
    e.call("prk_reload_hl", widget(0), 1, V, 0)
    params[0x88] = mode
    unit = 240                                    # Step Len 1/16: a quarter of 960
    e.uc.mem_write(POOL, bytes(0x18 * 64))
    for i, (st, ln, p, nid) in enumerate(NOTES):
        e.uc.mem_write(POOL + 0x18 * i, struct.pack("<BBHIIIHH", 1, 0, 0, st * unit + 30, ln * unit, nid, p, 100))
    c = knob("prk_start", CPS)
    now, _ = pool()
    check(f"Step Mode {mode}: one step later is 240 ticks, and off-grid notes stay off-grid",
          {k: round(v[0] * unit) for k, v in now.items()} == {1001: 270, 1002: 1230, 1003: 2190, 1004: 3150}, now)

# --- box selection: only the boxed notes move
setup(boxed=(1003, 1004))
e.call("prk_reload_hl", widget(0), 1, V, 0)        # opened: nothing current
c = knob("prk_start", -CPS)
check("box of two: only they move (no stock handler), box untouched",
      c == [] and edits() == {1003: 7, 1004: 11} and box() == [1003, 1004], (c, edits(), box()))
check("  ... the boxed notes stay highlighted, nothing else", lit() == [2, 3], lit())
c = knob("prk_pitch", CPS)
check("  ... bottom-right moves just them too", edits() == {1003: 65, 1004: 68}, edits())

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
check("current note picked, no box: bottom-right is plain stock", len(c) == 1 and c[0]["name"] == "pitch" and
      engine == [] and refresh == [], (c, engine))
setup(boxed=(1002, 1003), cur=1)
e.call("prk_select", V, CPS)                      # picking a note (stock clears the box there; here it stays)
e.w32(V + 0xA088, 1)
c = knob("prk_start", CPS)
check("a box drawn while a note is current: the box moves, not via the stock handler",
      c == [] and edits() == {1002: 5, 1003: 9}, (c, edits()))

# --- empty pattern
setup()
e.w32(V + 0xA894, 0)
c = knob("prk_start", CPS)
check("no notes: stock handler as is", len(c) == 1 and c[0]["box"] == [] and refresh == [], c)

# --- the dispatch patches
img = open(IMAGE, "rb").read()
B = 0x08040000
check("bottom-right no longer skipped by the +0xf5e0 flag", img[0x080C17B4 - B:0x080C17B8 - B] == bytes.fromhex("00bf00bf"))

print(f"{fails} failed" if fails else "all passed")
sys.exit(1 if fails else 0)
