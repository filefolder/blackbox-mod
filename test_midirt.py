"""DIN MIDI realtime bytes under Unicorn, stock vs patched.

Real firmware runs for: the DIN byte push the UART receive interrupt makes (FUN_080446c4 -> FUN_080444f4), the
per-port byte parser (FUN_08044512), and the engine's message reader (FUN_0804495e).
NOT run: the UART and its interrupt, the engine acting on the messages.
"""
import sys

from unicorn.arm_const import UC_ARM_REG_R0

from emu import Emu

PATCHED = "out/solo+slice+duck+chord+cond+filter+cpu+od+comp+seqfix+fx2+munchi+midirt+cave/BLACKBOX.bin"
MGR, OUT, PORT = 0x24002E88, 0x24060000, 0x24060010

fails = 0


def check(label, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if not cond else ""))
    fails += not cond


def machine(image):
    e = Emu(image, "out/cave.elf")
    for port in (0, 1):
        e.uc.mem_write(MGR + 0x210 * port + 0x20D, b"\x01")   # receiving enabled (set by the parser's first run)
    return e


def din(e, data, port=0):
    for b in data:
        e.call(0x080446C4, MGR, b, port)


def read(e):
    got = []
    for _ in range(300):
        e.call(0x0804495E, MGR, OUT, PORT)
        if e.uc.reg_read(UC_ARM_REG_R0) & 0xFF == 0:
            break
        m = bytes(e.uc.mem_read(OUT, 5))
        got.append(m[:m[4]].hex())
    return got


stock, mod = machine("firmware/BLACKBOX-3.1.9.bin"), machine(PATCHED)

for name, e in (("stock", stock), ("patched", mod)):
    din(e, [0x90, 0x3C, 0x64, 0x3E, 0x00, 0xB0, 0x07, 0x50, 0xC0, 0x05, 0xF8])
    got = read(e)
    check(f"{name}: plain stream: note on, running-status note on vel 0 -> off, CC, program change, clock",
          got == ["903c64", "803e00", "b00750", "c005", "f8"], got)
    din(e, [0x90, 0x3C, 0xF8, 0x64])
    check(f"{name}: clock in the middle of a note-on: both arrive", read(e) == ["f8", "903c64"])

for rt in (0xFE, 0xFA, 0xFB, 0xFC, 0xFF):
    din(stock, [0x80, 0x3C, rt, 0x00])
    got = read(stock)
    check(f"stock: {rt:02X} in the middle of a note-off -> the note-off is lost", "803c00" not in got, got)
    din(mod, [0x80, 0x3C, rt, 0x00])
    got = read(mod)
    check(f"patched: {rt:02X} in the middle of a note-off -> {rt:02x} delivered, then the note-off",
          got == [f"{rt:02x}", "803c00"], got)

din(mod, [0x90, 0x40, 0xFE, 0x50, 0x41, 0xFE, 0x50])
got = read(mod)
check("patched: running status survives realtime bytes (two note-ons, sensing between and inside)",
      got == ["fe", "904050", "fe", "904150"], got)
din(mod, [0xFE])
check("patched: active sensing on its own is a one-byte message, as stock", read(mod) == ["fe"])
din(mod, [0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7, 0x90, 0x30, 0x40])
check("patched: SysEx is still ignored and the next note arrives", read(mod) == ["903040"])
din(mod, [0x90, 0x31, 0x40], port=1)
din(mod, [0x80, 0x31, 0x00], port=0)
check("patched: both DIN ports parse independently", read(mod) == ["803100", "903140"])

print(f"{fails} failed" if fails else "all passed")
sys.exit(1 if fails else 0)
