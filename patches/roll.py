"""Piano roll: moving notes in time wraps around the pattern's start and end (see src/roll.c). Apply with `cave`."""
import os

from thumb import ROOT, bl, symbols

sym = symbols(os.path.join(ROOT, "out", "cave.elf"))

PATCHES = [
    # piano roll view constructor: the timing knob's stepper (view + 0xa918) gets its wrap byte (+0x14) set.
    # strb.w r2, [r3, #0x92c] (r2 = 0) -> strb.w r1, [r3, #0x92c] (r1 = 0x10 here, from the stepper defaults above)
    (0x080BA1E2, bytes.fromhex("83f82c29"), bytes.fromhex("83f82c19")),
    # rest-of-selection loop: per-note clamp -> wrap for the start fields
    (0x080C03AA, bl(0x080C03AA, 0x080C01C8), bl(0x080C03AA, sym["roll_clamp"])),
]
