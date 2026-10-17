"""Audition renders for Fidelity "SP 45" and deeper versions of the same trick: out/render/sp45/*.wav.

Each file: the two bars at the original pitch, then 5 semitones down, half a second apart. Source as render_s950.py.
The trick: the record goes into the SP-1200 sped up by `up`, and the pad is tuned back down by the same amount. In
the firmware that is filter.c's machine reader on a 26.04 kHz / up grid, then the SP's 26.04 kHz hold, 12 bits and
output filter. This is a numpy copy of that path (machine_read, crunch, out_stage), not the compiled code.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s950
from render_s950 import FS, HERE, load

OUT = os.path.join(HERE, "..", "out", "render", "sp45")
SP_RATE, SP_OUT = 26040.0, (0.357143, 0.714286, 0.0, 0.428571)      # the SP-1200 row of filter.c's table
FIRS = {
    1.0: (0.00302, 0.24563, 0.5027, 0.24563, 0.00302),               # SP1200
    1.35: (0.05302, 0.25399, 0.38598, 0.25399, 0.05302),             # 45 rpm: SP 45 as built
    2.0: (0.1, 0.25, 0.3, 0.25, 0.1),                                # an octave up: not built
    78 / 33.333: (0.2, 0.2, 0.2, 0.2, 0.2),                          # 78 rpm: not built
}


def sp(x, semis, up):
    rate = 2 ** (semis / 12)
    g = FS / (SP_RATE / up)
    pos = np.arange(int((len(x) - 8) / rate)) * rate + 4
    j = np.rint(np.floor(pos / g + 1 / 8192) * g).astype(int)
    y = sum(h * x[j - 2 + t] for t, h in enumerate(FIRS[up]))
    held, phase, out = 0.0, 0.0, np.empty(len(y))                    # the converter: hold at 26.04 kHz, 12 bits
    step = SP_RATE / FS
    for i, v in enumerate(y):
        phase += step
        if phase >= 1.0:
            phase -= 1.0
            held = np.rint(v * 2048) / 2048
        out[i] = held
    b0, b1, a1, a2 = SP_OUT
    z1 = z2 = 0.0
    for i, v in enumerate(out):                                      # transposed direct form II, b2 = b0
        o = b0 * v + z1
        z1 = b1 * v - a1 * o + z2
        z2 = b0 * v - a2 * o
        out[i] = o
    return out.astype(np.float32)


def save(name, parts):
    import wave
    gap = np.zeros(FS // 2, np.float32)
    y = np.clip(np.concatenate([np.concatenate([p, gap]) for p in parts]), -1, 1)
    with wave.open(os.path.join(OUT, name + ".wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes((y * 32767).astype("<i2").tobytes())


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    x = load(os.path.join(HERE, "..", "measure", "4X4DRUMS103-48k.wav"), 2 * 4 * 60 / 126)
    P = (0, -5)
    save("1_clean", [s950.clean(x, p) for p in P])
    save("2_SP1200", [sp(x, p, 1.0) for p in P])
    save("3_SP_45_built", [sp(x, p, 1.35) for p in P])
    save("4_option_octave_up", [sp(x, p, 2.0) for p in P])
    save("5_option_78_rpm", [sp(x, p, 78 / 33.333) for p in P])
    print(sorted(os.listdir(OUT)))
