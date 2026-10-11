"""USB host MIDI: state on the version label, and recovery of transfers that will never finish (see src/usbdiag.c).
Apply with `cpu` (which shows the label) and `cave`."""
import os

from thumb import ROOT, bl, symbols, word

sym = symbols(os.path.join(ROOT, "out", "cave.elf"))

PATCHES = [
    # USB host MIDI class table (0x080f1e18): BgndProcess and SOFProcess
    (0x080F1E2C, word(0x080467FF), word(sym["usbdiag_bgnd"] | 1)),
    (0x080F1E30, word(0x08046791), word(sym["usbdiag_sof"] | 1)),
    # background process: receive complete -> receive callback (count transfers)
    (0x08046906, bl(0x08046906, 0x08044F1C), bl(0x08046906, sym["usbdiag_rxdone"])),
]
