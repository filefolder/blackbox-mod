"""USB MIDI echo filter: notes / CCs / start-stop just received from USB are not sent straight back to it (see src/echo.c). Apply with `cave`."""
import os

from thumb import ROOT, bl, symbols

sym = symbols(os.path.join(ROOT, "out", "cave.elf"))


def hook(at, stock, name):
    return (at, bl(at, stock), bl(at, sym[name]))


PATCHES = [
    # engine per-block process: the USB host drain (once per block) counts blocks
    hook(0x0804CB4E, 0x08044844, "echo_block"),
    # engine MIDI input loop: both reads remember what came from USB
    hook(0x0804CB80, 0x0804495E, "echo_read"),
    hook(0x0804CC78, 0x0804495E, "echo_read"),
    # engine MIDI output: the note / CC send drops the USB copy of an echo
    hook(0x0804D004, 0x08044A0E, "echo_send"),
    # engine MIDI output: transport stop / start sends (DIN / USB by the clock-send setting)
    hook(0x0804D1E2, 0x08044A0E, "echo_send"),
    hook(0x0804D20A, 0x08044A0E, "echo_send"),
]
