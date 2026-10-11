"""Replace recording under Unicorn.

Real firmware runs for: the app-side recorder (FUN_080a02c0) end to end (its step quantising, note-off length and
loop wrap maths, the stock Step Mode same-pitch replace), the Step Len table and 64-bit helpers; the parameter
registration (FUN_0808c158) for the Step Mode descriptor.
Stubbed: parameter reads (a Python dict), the clock ("now"), the sequence state copy, the pattern's event list (a
Python list), the engine add / delete and the piano roll notify (recorded).
NOT run: the engine playing the notes, the piano roll redrawing, a real MIDI message source.
"""
import struct
import sys

from emu import Emu

IMAGE = "out/solo+slice+duck+chord+cond+filter+cpu+od+comp+seqfix+fx2+munchi+roll+prknobs+replace+cave/BLACKBOX.bin"
STOCK = "firmware/BLACKBOX-3.1.9.bin"

fails = 0


def check(label, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if not cond else ""))
    fails += not cond


APP, PS, MSG = 0x24010000, 0x24030000, 0x24031000
LIST = PS + 0x18
STEPS, TPS = 16, 240                              # Step Len index 10 = 1/4 beat x 960 = 240 ticks per step
LOOP = STEPS * TPS


class Rig:
    def __init__(self, image):
        e = self.e = Emu(image, "out/cave.elf")
        e.uc.mem_map(0x38800000, 0x1000)
        e.uc.mem_map(0x58024000, 0x1000)
        self.params = {0xC0: 0, 0x88: 3, 0x85: 10, 0x86: STEPS, 0x46: 15, 0xB7: 1}   # ON+REPL, DispMode KEYS
        self.events, self.engine, self.notified = [], [], []
        self.now = 0
        e.stub(0x08097CE8, value=PS)                                   # paramset for (app, pads, layer)
        e.stubs[0x08093E9C] = lambda: e.ret(self.params.get(e.arg(1), 0) & 0xFFFFFFFF)
        e.stubs[0x0804CA68] = self.clock
        e.stubs[0x08097860] = lambda: (e.uc.mem_write(e.arg(0), struct.pack("<iii", 0, 0, 0)), e.ret(e.arg(0)))
        e.stubs[0x08063D08] = self.lget
        e.stubs[0x08063B28] = self.lappend
        e.stubs[0x08063B38] = self.lset
        e.stubs[0x08063C08] = self.ldel
        e.stub(0x0804C7E8, lambda: self.engine.append(("del", e.arg(2))))
        e.stub(0x0804C798, lambda: self.engine.append(("add", self.ev_at(e.arg(2)))))
        e.stub(0x080B5758, lambda: self.notified.append(e.r32(e.arg(1) + 0x10)))
        e.stub(0x0809B798, value=0)
        e.uc.mem_write(APP, bytes(0x10000))
        e.uc.mem_write(APP + 0x8CC1, b"\x01")                          # recording
        e.w32(APP + 0xE808, 1000)                                      # next event id
        e.uc.mem_write(APP + 0x1C, struct.pack("<H", 0x21))

    def clock(self):
        self.e.uc.mem_write(self.e.arg(0), struct.pack("<q", self.now))
        self.e.ret(self.e.arg(0))

    def ev_at(self, a):
        t, _, _, start, length, eid, pitch, vel = struct.unpack_from("<BBHIIIHH", self.e.uc.mem_read(a, 24))
        return dict(type=t, start=start, len=length, id=eid, pitch=pitch)

    def ev_bytes(self, ev):
        return struct.pack("<BBHIIIHH", ev["type"], 0, 0, ev["start"], ev["len"], ev["id"], ev["pitch"], 100) + bytes(4)

    def lget(self):
        i = self.e.arg(1)
        if self.e.arg(0) != LIST or i >= len(self.events):
            self.e.ret(0)
            return
        self.e.uc.mem_write(self.e.arg(2), self.ev_bytes(self.events[i]))
        self.e.ret(1)

    def lappend(self):
        assert self.e.arg(0) == LIST
        self.events.append(self.ev_at(self.e.arg(1)))
        self.e.ret(1)

    def lset(self):
        ev = self.ev_at(self.e.arg(1))
        for i, x in enumerate(self.events):
            if x["id"] == ev["id"]:
                self.events[i] = ev
        self.e.ret(1)

    def ldel(self):
        self.events = [x for x in self.events if x["id"] != self.e.arg(1)]
        self.e.ret(1)

    def existing(self, *notes):
        """Notes already in the pattern: (step, length steps, pitch)."""
        for k, (st, ln, p) in enumerate(notes):
            self.events.append(dict(type=1, start=st * 960, len=ln * 960, id=10 + k, pitch=p))

    def midi(self, kind, pitch, t):
        self.now = t // LOOP * LOOP                                    # the recorder's loop base: this pass's start
        self.e.uc.mem_write(MSG, struct.pack("<BBHIIIHH", kind, 0, 0, 0, 0, 0, pitch, 100) + bytes(0x14) +
                            struct.pack("<q", t))
        self.e.call(0x080A02C0, APP, MSG, count=5_000_000)

    def on(self, pitch, step, loop=0, early=0):
        self.midi(5, pitch, loop * LOOP + step * TPS - early)

    def off(self, pitch, step, loop=0):
        self.midi(6, pitch, loop * LOOP + step * TPS)

    def notes(self):
        return sorted((x["start"] // 960, x["len"] // 960, x["pitch"]) for x in self.events)


# --- Step Mode registration: a list OFF / ON / REPLACE
r = Rig(IMAGE)
TABLE = 0x24050000
r.e.stub(0x0804436C, value=TABLE)                                     # malloc for the descriptor table
r.e.uc.mem_write(TABLE, bytes(0x36B0))
r.e.call(0x0808C158, 0, count=20_000_000)
d = TABLE + 0x88 * 28
kind, names, count, xml = r.e.r8(d + 2), r.e.r32(d + 8), r.e.r32(d + 12), r.e.r32(d + 24)
check("Step Mode is registered as a list of 4, xml name unchanged",
      r.e.r16(d) == 0x88 and kind == 5 and count == 4 and r.e.cstr(xml) == "seqstepmode", (kind, count))
check("  ... named OFF / ON / REPLACE / ON+REPL, label unchanged",
      [r.e.cstr(r.e.r32(names + 4 * i)) for i in range(4)] == ["OFF", "ON", "REPLACE", "ON+REPL"] and
      r.e.cstr(r.e.r32(d + 4)) == "Step Mode:", r.e.cstr(r.e.r32(d + 4)))

# --- stock recorder, step mode ON, for comparison: layered
s = Rig(STOCK)
s.params[0x88] = 1
s.existing((4, 2, 60), (4, 1, 64), (6, 1, 67))
s.on(62, 4)
s.off(62, 6)
check("stock ON: a note on step 5 is added on top of the notes there", s.notes() == [(4, 1, 64), (4, 2, 60), (4, 2, 62), (6, 1, 67)],
      s.notes())

# --- ON is unchanged with the patch
r = Rig(IMAGE)
r.params[0x88] = 1
r.existing((4, 2, 60), (4, 1, 64), (6, 1, 67))
r.on(62, 4)
r.off(62, 6)
check("patched ON: same as stock", r.notes() == s.notes(), r.notes())
r = Rig(IMAGE)
r.params[0x88] = 0
r.existing((4, 2, 60))
r.on(62, 4)
r.off(62, 6)
check("patched OFF: layered, nothing deleted", len(r.notes()) == 2 and (4, 2, 60) in r.notes(), r.notes())

# --- ON+REPL (snapped to steps)
r = Rig(IMAGE)
r.existing((0, 1, 50), (4, 2, 60), (4, 1, 64), (5, 1, 65), (6, 1, 67), (9, 1, 70))
r.on(62, 4)
check("ON+REPL note-on on step 5: the older notes starting there are gone at once (any pitch)",
      r.notes() == [(0, 1, 50), (4, 0, 62), (5, 1, 65), (6, 1, 67), (9, 1, 70)], r.notes())
check("  ... and the engine was told", sorted(x[1] for x in r.engine if x[0] == "del") == [11, 12], r.engine)
r.on(66, 4)
check("a second note on the same step this pass (a chord) does not delete the first",
      [n for n in r.notes() if n[0] == 4] == [(4, 0, 62), (4, 0, 66)], r.notes())
r.off(62, 6)
r.off(66, 5)
check("note-off: older notes starting inside the held length are deleted, notes after it stay",
      r.notes() == [(0, 1, 50), (4, 1, 66), (4, 2, 62), (6, 1, 67), (9, 1, 70)], r.notes())

# next pass: playing over this pass's note on step 5 replaces it
r.on(72, 4, loop=1)
r.off(72, 5, loop=1)
check("next loop pass: a note on step 5 replaces the notes recorded there on the pass before",
      r.notes() == [(0, 1, 50), (4, 1, 72), (6, 1, 67), (9, 1, 70)], r.notes())

# --- a note held across the loop end
r = Rig(IMAGE)
r.existing((0, 1, 50), (1, 1, 51), (3, 1, 53), (14, 1, 40), (15, 1, 41))
r.on(60, 13)
r.on(62, 15)                                      # recorded after 60 started, same pass: must survive
r.off(62, 16)
r.off(60, 2, loop=1)
n = r.notes()
check("held over the wrap: cut at the loop end (steps 14-16) and continued from step 1 (steps 1-2)",
      (13, 3, 60) in n and (0, 2, 60) in n, n)
check("  ... older notes under both parts deleted, the note played meanwhile kept, later notes kept",
      n == [(0, 2, 60), (3, 1, 53), (13, 3, 60), (15, 1, 62)], n)
new = [x for x in r.events if x["start"] == 0]
check("  ... the continuation is a new event: added to the engine and the piano roll",
      new and ("add", new[0]) in r.engine and new[0]["id"] in r.notified, (r.engine, r.notified))

# --- played a hair early for the downbeat: quantised to step 1 of the next pass, replaces that pass's step 1
r = Rig(IMAGE)
r.existing((0, 1, 50))
r.on(60, 0, loop=0)
r.off(60, 1, loop=0)
r.on(61, 0, loop=1, early=TPS // 8)
check("early downbeat (quantised forward across the loop end) replaces the pass before's step-1 note",
      [x for x in r.notes() if x[0] == 0] == [(0, 0, 61)], r.notes())

# --- a note held over the wrap while another is played on the new pass
r = Rig(IMAGE)
r.existing((0, 1, 50), (1, 1, 51), (14, 1, 40))
r.on(60, 13)
r.on(64, 1, loop=1)                               # new pass begins with a note while 60 is still held
r.off(64, 2, loop=1)
r.off(60, 2, loop=1)
n = r.notes()
check("held over the wrap, another note played meanwhile on the new pass: both kept, older ones under them gone",
      n == [(0, 2, 60), (1, 1, 64), (13, 3, 60)], n)

# --- nothing recorded for a whole pass in between: two passes later still replaces
r = Rig(IMAGE)
r.on(60, 3)
r.off(60, 4)
r.on(61, 3, loop=2)
r.off(61, 4, loop=2)
check("two passes later, the same step: replaced", r.notes() == [(3, 1, 61)], r.notes())

# --- notes not on a played step stay, whatever their pass
r = Rig(IMAGE)
r.existing((2, 1, 50))
r.on(60, 3, loop=0)
r.off(60, 4, loop=0)
r.on(61, 7, loop=1)
r.off(61, 8, loop=1)
check("only where played: other steps untouched across passes", r.notes() == [(2, 1, 50), (3, 1, 60), (7, 1, 61)],
      r.notes())

# --- every stock read of Step Mode sees "snap to steps" only for ON and ON+REPL
from unicorn.arm_const import UC_ARM_REG_R11, UC_ARM_REG_R2, UC_ARM_REG_R0
r = Rig(IMAGE)
for v, want in ((0, 0), (1, 1), (2, 0), (3, 1)):
    r.params[0x88] = v
    r.e.call("stepmode_snap", PS, 0x88)
    a = r.e.uc.reg_read(UC_ARM_REG_R0)
    OUT = 0x24032000
    r.e.stub(0x08099504, lambda: r.e.w32(r.e.arg(3), r.params[0x88]), value=1)
    r.e.call("stepmode_snap_get", APP, MSG, 0x88, OUT)
    b = r.e.r32(OUT)
    r.e.uc.reg_write(UC_ARM_REG_R11, v)
    r.e.call("rpl_snap_fp")
    c = r.e.uc.reg_read(UC_ARM_REG_R2)
    check(f"Step Mode {v}: stock reads see {want} (param read, piano roll read, change handler)", (a, b, c) == (want,) * 3,
          (a, b, c))
img = open(IMAGE, "rb").read()
check("change handler: the bl sits where subs.w r2, fp, #0 was, followed by it ne; movne r2, #1",
      img[0x080A02A2 - 0x08040000:0x080A02A6 - 0x08040000] == bytes.fromhex("18bf0122"))

# --- REPLACE: free timing (event times in clock ticks, 240 per step here)
r = Rig(IMAGE)
r.params[0x88] = 2
r.existing()
for k, (st, ln, p) in enumerate(((0, TPS, 50), (4 * TPS + 30, TPS, 60), (4 * TPS + 200, 60, 64), (5 * TPS + 100, TPS, 65),
                                 (6 * TPS + 120, TPS, 67))):
    r.events.append(dict(type=1, start=st, len=ln, id=10 + k, pitch=p))
r.midi(5, 62, 4 * TPS + 100)
ticks = lambda: sorted((x["start"], x["len"], x["pitch"]) for x in r.events)
check("REPLACE note-on: not snapped (start in ticks as played)", any(x == (4 * TPS + 100, 0, 62) for x in ticks()), ticks())
check("  ... older notes starting anywhere in that step are gone", [x[2] for x in ticks()] == [50, 62, 65, 67], ticks())
r.midi(6, 62, 6 * TPS + 50)
check("REPLACE note-off: length as played, older notes starting inside it gone, the one after it stays",
      ticks() == [(0, TPS, 50), (4 * TPS + 100, 2 * TPS - 50, 62), (6 * TPS + 120, TPS, 67)], ticks())

r = Rig(IMAGE)
r.params[0x88] = 2
r.events.append(dict(type=1, start=TPS // 2, len=60, id=10, pitch=50))
r.events.append(dict(type=1, start=TPS + 10, len=60, id=11, pitch=51))
r.midi(5, 60, 15 * TPS + 40)
r.midi(6, 60, LOOP + TPS // 2 + 20)
check("REPLACE held over the loop end: cut at the end, continued from the loop start, older notes under it gone",
      ticks() == [(0, TPS // 2 + 20, 60), (TPS + 10, 60, 51), (15 * TPS + 40, TPS - 40, 60)], ticks())

print(f"{fails} failed" if fails else "all passed")
sys.exit(1 if fails else 0)
