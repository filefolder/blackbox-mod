"""Master compressor models: the Tools Compressor switch becomes Off/Stock/Glue/Punch/Opto/Squash/Limit (see src/comp.c)."""
import os
import struct

from thumb import ROOT, bl, symbols, word

sym = symbols(os.path.join(ROOT, "out", "cave.elf"))


def hook(at, stock, name):
    return (at, bl(at, stock), bl(at, sym[name]))


PATCHES = [
    hook(0x0808D350, 0x0808C12C, "comp_register"),   # parameter 0xb4: On/Off -> 7-entry list
    hook(0x08053CA0, 0x0804EDDC, "comp_process"),    # engine: the compressor stage on Out 1
    hook(0x08094886, 0x08093EF6, "comp_defaults"),   # global settings defaults: + Comp Thresh = 0
    # the global settings store holds 64 entries and stock + ours had reached 50: make it 96 (movs r1, #0x40 -> #0x60)
    (0x08094820, bytes.fromhex("4021"), bytes.fromhex("6021")),
    # Tools Main page (address 0x40, page 0, 0x080eed18): Headphone, Compressor, Comp Thresh, then the rest as before
    (0x080EED18, struct.pack("<11H", 0xBA, 0xBB, 0xBC, 0xBD, 0x7F, 0xCD, 0xF6, 0xF7, 0xB4, 0x19B, 0),
     struct.pack("<11H", 0xBA, 0xB4, 0x1D0, 0xBB, 0xBC, 0xBD, 0x7F, 0xCD, 0xF6, 0xF7, 0x19B)),
    # pad Overdrive label -> "Saturation:" (literal in the parameter registration; xml name stays "overdrive")
    (0x0808D878, word(0x080CC614), word(sym["od_label"])),
    # Record pre-roll (src/preroll.c). Tools Rec page (address 0x40, page 1, 0x080eed60): RecToPlay, RecConfig,
    # Lead In, then Pre-roll in the first empty slot
    (0x080EED66, struct.pack("<H", 0), struct.pack("<H", 0x1B1)),
    hook(0x08062108, 0x08061C94, "preroll_start"),      # WAV recorder: its start routine, on message 0x25
    hook(0x0806283A, 0x08069C38, "preroll_position"),   # WAV recorder, waiting: the position behind "start at once"
]
