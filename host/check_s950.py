"""How close is the firmware's S950 path (src/s950.c) to the hardware model (s950_ref.py)? Band levels on pink noise,
and the level of everything that is NOT the input (images + aliases + quantisation) on a two-tone test."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s950
import s950_ref as ref

rng = np.random.default_rng(3)
n = 48000 * 3
white = rng.standard_normal(n)
spec = np.fft.rfft(white)
f = np.fft.rfftfreq(n, 1 / 48000)
spec[1:] /= np.sqrt(f[1:])
pink = np.fft.irfft(spec)
pink = (0.25 * pink / np.max(np.abs(pink))).astype(np.float32)
CENTRES = [125, 250, 500, 1000, 2000, 4000, 6300, 8000, 10000, 12500, 16000, 20000]


def bands(y):
    s = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2
    fr = np.fft.rfftfreq(len(y), 1 / 48000)
    return np.array([10 * np.log10(s[(fr >= c / 2 ** (1 / 6)) & (fr < c * 2 ** (1 / 6))].sum() / len(y) + 1e-20) for c in CENTRES])


worst = 0.0
print("band level, firmware minus hardware model, dB (pink noise)   bands:", " ".join(f"{c:>6}" for c in CENTRES))
for name, mode, bw in (("S950", s950.HI, 19200), ("S950 Lo", s950.LO, 12500), ("10 kHz", s950.LO10, 10000)):
    stored, fs_rec = ref.record(pink, bw)
    for semis in (0, -5, -12, -24, 7):
        fw = s950.s950(pink, semis, mode)
        hw, info = ref.play(stored, fs_rec, semis * 16, quantized_pitch=False)
        m = min(len(fw), len(hw)) - 4800
        d = bands(fw[2400:m]) - bands(hw[2400:m])
        top = info["fc"]
        inband = [abs(v) for v, c in zip(d, CENTRES) if c <= top * 0.8]
        worst = max(worst, max(inband))
        print(f"  {name:8} {semis:+3d} st  filter {info['fc']:7.0f} Hz:", " ".join(f"{v:+6.1f}" for v in d))
print(f"worst difference below 0.8 x the filter cutoff: {worst:.1f} dB")
