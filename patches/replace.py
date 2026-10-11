"""Replace recording: Step Mode OFF / ON / REPLACE (see src/replace.c, src/replace_thunk.S). Apply together with `cave`."""
import os

from thumb import ROOT, bl, symbols

sym = symbols(os.path.join(ROOT, "out", "cave.elf"))


def hook(at, stock, name):
    return (at, bl(at, stock), bl(at, sym[name]))


PATCHES = [
    # parameter registration: Step Mode (0x88) toggle -> list OFF / ON / REPLACE (stored values 0, 1, 2)
    hook(0x0808CEAC, 0x0808C12C, "rpl_register"),
    # app-side recorder: note-on append, note-off length set
    hook(0x080A05FE, 0x08063B28, "rpl_on_thunk"),
    hook(0x080A073C, 0x08063B38, "rpl_off_thunk"),
]
