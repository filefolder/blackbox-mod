"""USB host MIDI input: decode each transfer straight into the message queue (see src/usbmidi.c). Apply with `cave`."""
import os

from thumb import ROOT, bl, symbols

sym = symbols(os.path.join(ROOT, "out", "cave.elf"))

PATCHES = [
    # USB host MIDI receive callback (FUN_08044f1c): copy into one of four slots -> decode into the queue now
    (0x08044F2C, bl(0x08044F2C, 0x080446DA), bl(0x08044F2C, sym["usbmidi_rx"])),
]
