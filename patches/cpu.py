"""Audio CPU meter in the middle label of the Tools bar: "avg/peak%" (see src/cpu.c). Apply together with `cave`."""
import os

from thumb import ROOT, bl, symbols

sym = symbols(os.path.join(ROOT, "out", "cave.elf"))


def hook(at, stock, name):
    return (at, bl(at, stock), bl(at, sym[name]))


PATCHES = [
    # audio task: the queue wait between 256-frame blocks
    hook(0x080414E4, 0x08088756, "cpu_wait"),
    # global cell info (address 0x40): its name is the version text the screens show. The 10 bytes that copied
    # 6 bytes of "3.1.x" become: mov r0, r2; bl cpu_name; nop; nop
    (0x0809AD84, bytes.fromhex("6a4b18681060 9b889380".replace(" ", "")),
     bytes.fromhex("1046") + bl(0x0809AD86, sym["cpu_name"]) + bytes.fromhex("00bf00bf")),
    # Tools refresh: where it sets the free-space label, also put the load in the empty label beside it
    hook(0x080C52A4, 0x080A3DEC, "cpu_tools"),
]
