"""Pad-1 ducking as modulation sources EXP1/EXP2/PUMP/LINR (see src/duck.c). Apply together with `cave`."""
import os
import struct

from thumb import ROOT, bl, symbols, word

sym = symbols(os.path.join(ROOT, "out", "cave.elf"))
NAMES, CODES = sym["duck_source_names"], sym["duck_source_codes"]


def hook(at, stock, name):
    return (at, bl(at, stock), bl(at, sym[name]))


PATCHES = [
    # engine: per-block envelope update in front of the engine process
    hook(0x0804CE74, 0x08053164, "duck_block"),
    # engine: every voice-start call, pad class A (8 sites) and class B (3 sites)
    *[hook(a, 0x08058548, "duck_voice_start_a") for a in
      (0x0805999C, 0x08059B3E, 0x0805A6F6, 0x0805A75C, 0x0805A800, 0x0805A910, 0x0805B6E4, 0x0805B9BA)],
    *[hook(a, 0x08065908, "duck_voice_start_b") for a in (0x080683AE, 0x08068656, 0x080686CC)],
    # ENV source (src/envmod.c): the pad slot setup and the voice attach
    hook(0x0805A138, 0x08056810, "env_mod_setup"),
    hook(0x08058C34, 0x0805623C, "env_mod_attach"),
    hook(0x0805A8DA, 0x0805623C, "env_mod_attach"),
    # UI: pad modulation Source list grows from 8 to 16 entries (KEY, LFO2, LFO3, ENV, 4 duck shapes)
    (0x0808CCC0, word(0x080ECD0C), word(NAMES)),      # parameter registration: names
    (0x080B6964, word(0x080ECD0C), word(NAMES)),      # knob page: names
    (0x0809A69C, word(0x080ECCFC), word(CODES)),      # app: list index -> source code
    (0x080B696C, word(0x080ECCFC), word(CODES)),      # knob page: source code -> list index
    (0x080B7B00, word(0x080ECCFC), word(CODES)),      # modulation screen: source code -> list index
    (0x0808CB3E, bytes.fromhex("cdf800b0"), bytes.fromhex("10210091")),  # registration count: str fp(8),[sp] -> movs r1,#16; str r1,[sp]
    (0x080B67CC, bytes.fromhex("082b"), bytes.fromhex("102b")),          # knob page loop: cmp r3,#8 -> #16
    (0x080B7994, bytes.fromhex("0820"), bytes.fromhex("1020")),          # modulation screen loop: movne r0,#8 -> #16
    # LFO 2 / 3 (src/lfo.c): pad defaults, the pad store's size (64 -> 96 entries; stock uses 59), the tab bar, page 3
    hook(0x0809423C, 0x08093EF6, "lfo_defaults"),
    (0x08093F26, bytes.fromhex("4021"), bytes.fromhex("6021")),
    # ... and the same size where a cell is first created (FUN_080949e4): this is the one a pad's store really gets.
    # 3.1.C..F missed it, so a sample pad's store filled up at 64 and LFO 3's Rate / Trig / Offset had nowhere to live
    # (the report: those three knobs do not move).
    (0x080949F0, bytes.fromhex("4021"), bytes.fromhex("6021")),
    hook(0x080B8844, 0x080B8724, "lfo_tabs"),
    hook(0x080B8BCC, 0x080B8724, "lfo_tabs"),
    (0x080EFCF0, bytes(16), struct.pack("<8H", *range(0x1A9, 0x1B1))),   # Sample pads, page 3
    (0x080EFB88, bytes(16), struct.pack("<8H", *range(0x1A9, 0x1B1))),   # Multisample pads, page 3
    (0x080EF948 + 3 * 0x48, bytes(16), struct.pack("<8H", *range(0x1A9, 0x1B1))),   # Clip pads, page 3
    (0x080EF7E0 + 3 * 0x48, bytes(16), struct.pack("<8H", *range(0x1A9, 0x1B1))),   # Slicer pads, page 3
    # the same pages exist a second time for pad cells of class type 0x4c (FUN_08098d40): Sample, Clip, Slicer there too
    *[(base + 3 * 0x48, bytes(16), struct.pack("<8H", *range(0x1A9, 0x1B1))) for base in (0x080EF3A8, 0x080EF240, 0x080EF0D8)],
    hook(0x080943B4, 0x08093EF6, "lfo_defaults"),   # ... and that cell type's defaults (its last add is 0xdc = 0 as well)
    # Clip / Slicer pads (engine class B): ENV, LFO2, LFO3 as sources
    hook(0x080685C4, 0x08067018, "env_mod_setup_b"),
]
