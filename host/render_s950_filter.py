"""Side by side for one decision: the S950's own stepped filter against the pad's Filter knob, same sweep.

out/render/s950/10_filter_s950_then_ours.wav: the two bars four times, half a second apart:
  1. S950 mode, the machine's Filter swept 99 -> 40 -> 99 (file 07's first part)   2. the same sweep on the pad's
  Filter knob (what the unit does today: S950 mode, then the 24 dB filter at the default Res)
  3. and 4. the same pair an octave down.
The pad filter here is a Python copy of filter.c's linear path (two stmlib state-variable stages, Butterworth then
Q 1.345 = the stock Res curve at its default, coefficients smoothed a third of the way per 128-frame block). It is
not the compiled firmware code; the S950 side is.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s950
import s950_ref as ref
from render_s950 import FS, HERE, load, save

BLOCK, SMOOTH, R1, R2 = 128, 1 / 3, 1.84776, 1 / 1.345


def stage(x, g_blocks, r):
    y, s1, s2 = np.empty_like(x), 0.0, 0.0
    for b, g in enumerate(g_blocks):
        h, rg = 1.0 / (1.0 + r * g + g * g), r + g
        for i in range(b * BLOCK, min(len(x), (b + 1) * BLOCK)):
            hp = (x[i] - rg * s1 - s2) * h
            bp = g * hp + s1
            s1 = g * hp + bp
            lp = g * bp + s2
            s2 = g * bp + lp
            y[i] = lp
    return y


def pad_filter(x, hz_blocks):
    g, gs = None, []
    for hz in hz_blocks:
        target = np.tan(np.pi * min(hz, 0.45 * FS) / FS)
        g = target if g is None else g + SMOOTH * (target - g)
        gs.append(g)
    return stage(stage(x.astype(np.float64), gs, R1), gs, R2).astype(np.float32)


if __name__ == "__main__":
    x = load(os.path.join(HERE, "..", "measure", "4X4DRUMS103-48k.wav"), 2 * 4 * 60 / 126)
    parts = []
    for p in (0, -12):
        rate = 2 ** (p / 12)
        dry = s950.read(x, rate, s950.HI)
        blocks = len(dry) // s950.N
        sweep = [ref.filter_index(int(round(99 - 59 * (1 - abs(1 - 2 * b / blocks))))) for b in range(blocks)]
        counts = []
        parts.append(s950.lowpass(dry, 48000 * rate, sweep, counts))
        hz = 80000.0 / np.array(counts, float)                 # what the machine's filter was set to, block by block
        user = np.array([80000.0 / s950.lib.s950_filter_count(1e9, ix) for ix in sweep])
        per = np.repeat(user, s950.N)[::BLOCK]                  # the knob's cutoff per 128-frame block
        parts.append(pad_filter(s950.lowpass(dry, 48000 * rate), per))
        print(f"pitch {p}: S950 filter {hz.min():.0f} .. {hz.max():.0f} Hz in {len(set(counts))} steps; knob {user.min():.0f} .. {user.max():.0f} Hz")
    save("10_filter_s950_then_ours", parts)
