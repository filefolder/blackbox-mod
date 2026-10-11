"""Replace recording: Step Mode OFF / ON / REPLACE (see src/replace.c, src/replace_thunk.S). Apply together with `cave`."""
import os

from thumb import ROOT, bl, symbols

sym = symbols(os.path.join(ROOT, "out", "cave.elf"))


def hook(at, stock, name):
    return (at, bl(at, stock), bl(at, sym[name]))


PATCHES = [
    # parameter registration: Step Mode (0x88) toggle -> list OFF / ON / REPLACE / ON+REPL (stored values 0..3)
    hook(0x0808CEAC, 0x0808C12C, "rpl_register"),
    # every stock read of Step Mode: "snap to steps" = ON or ON+REPL
    *[hook(a, 0x08093E9C, "stepmode_snap") for a in (0x08097DC0, 0x08097FAE, 0x08098756, 0x08098970, 0x080A031A,
                                                      0x080A2194, 0x080A21A4, 0x0809C5F6)],
    *[hook(a, 0x08099504, "stepmode_snap_get") for a in (0x080B49B4, 0x080BFF60)],
    # Step Mode changed: the pattern's notes are converted when "snap to steps" changes, not on every value change
    (0x080A029E, bytes.fromhex("bbf10002"), bl(0x080A029E, sym["rpl_snap_fp"])),
    # engine: its own copy of Step Mode (sequence + 0x5e, event times in steps x 960 instead of clock ticks)
    (0x0805EF8E, bytes.fromhex("003b18bf0123"), bl(0x0805EF8E, sym["rpl_snap_r3"]) + bytes.fromhex("00bf")),
    # app-side recorder: note-on append, note-off length set
    hook(0x080A05FE, 0x08063B28, "rpl_on_thunk"),
    hook(0x080A073C, 0x08063B38, "rpl_off_thunk"),
]
