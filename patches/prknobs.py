"""Piano roll knobs: nothing selected by default, lower knobs move the selection or every note (see src/prknobs.c).
Apply together with `roll` (time wraps) and `cave`."""
import os

from thumb import ROOT, bl, symbols

sym = symbols(os.path.join(ROOT, "out", "cave.elf"))


def hook(at, stock, name):
    return (at, bl(at, stock), bl(at, sym[name]))


PATCHES = [
    # piano roll encoder dispatch (parent view): the four knob handlers
    hook(0x080C1776, 0x080BBF90, "prk_select"),     # top-left
    hook(0x080C1792, 0x080BAE50, "prk_start"),      # bottom-left
    hook(0x080C179A, 0x080BACD8, "prk_length"),     # top-right
    hook(0x080C17BA, 0x080BBBC4, "prk_pitch"),      # bottom-right
    # bottom-right: no longer skipped when the view flag +0xf5e0 is set (bne.w -> two nops)
    (0x080C17B4, bytes.fromhex("7ff435af"), bytes.fromhex("00bf00bf")),
    # note list reload: the two "highlight the current note" calls
    hook(0x080BC1CE, 0x080BA048, "prk_hl_keep"),
    hook(0x080BC398, 0x080BA048, "prk_hl_first"),
]
