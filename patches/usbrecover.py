"""USB host MIDI: recover transfers that will never finish instead of waiting for a replug (see src/usbrecover.c).
Apply together with `cave`."""
import os

from thumb import ROOT, symbols, word

sym = symbols(os.path.join(ROOT, "out", "cave.elf"))

PATCHES = [
    # USB host MIDI class table (0x080f1e18): BgndProcess and SOFProcess
    (0x080F1E2C, word(0x080467FF), word(sym["usbrecover_bgnd"] | 1)),
    (0x080F1E30, word(0x08046791), word(sym["usbrecover_sof"] | 1)),
]
