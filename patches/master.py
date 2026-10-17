"""Master FX slot + Compressor / Master FX knob pages (see src/master.c). Apply together with `comp`, `munchi`, `cave`."""
import os
import struct

from thumb import ROOT, bl, symbols

sym = symbols(os.path.join(ROOT, "out", "cave.elf"))


def hook(at, stock, name):
    return (at, bl(at, stock), bl(at, sym[name]))


def bw(at, target):
    """B.W: BL's encoding with bit 14 of the second halfword clear."""
    h1, h2 = struct.unpack("<HH", bl(at, target))
    return struct.pack("<HH", h1, h2 & ~0x4000)


SET_SCREEN = 0x0809EAEC

PATCHES = [
    # engine: the compressor stage runs every block (was: only with Compressor on), so Master FX works with it Off
    (0x0805352E, bytes.fromhex("40f0b383"), bw(0x0805352E, 0x08053C98)),
    # knob screen 0x66 / 0x67 handler: page builder -> ours (master pages for the global cell)
    hook(0x080AB94A, 0x08098D40, "master_list"),
    # INFO on Tools (screens 0x25..0x29, each re-showed Tools): open the knob page of the selected row
    *[hook(at, SET_SCREEN, "master_tools_info") for at in (0x080A3094, 0x080A30A0, 0x080A30AC, 0x080A30B8, 0x080A30C4)],
    # INFO on the knob screen: a master page -> Tools
    (0x080A3064, bytes.fromhex("038dc3f30312"), bl(0x080A3064, sym["master_info_thunk"]) + bytes.fromhex("00bf")),
    # BACK on the knob screen: a master page returns to Tools
    hook(0x080A3220, SET_SCREEN, "master_back"),
    # delay constructor, its third line allocation: also take our SDRAM block (once per boot)
    hook(0x0805462A, 0x0806AA80, "master_alloc"),
    # Tools Main page: Master FX takes Comp Thresh's row (Thresh is on the Compressor knob page now)
    # ... and sits ABOVE Compressor, the order the audio runs in: Master FX, Compressor (its page has the clipper)
    (0x080EED1A, struct.pack("<2H", 0xB4, 0x1D0), struct.pack("<2H", 0x1DB, 0xB4)),
]
