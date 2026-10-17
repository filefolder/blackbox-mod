"""Audition renders of the three effects on a synthetic loop: out/render/*.wav (host/build.sh first). Dry first, then wet."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dsp import *

OUT = os.path.join(HERE, "..", "out", "render")
os.makedirs(OUT, exist_ok=True)
x = beat(seconds=8, level=0.5)
gap = np.zeros((2, FS // 2), dtype=np.float32)


def ab(name, wet):
    save(os.path.join(OUT, name + ".wav"), np.concatenate([x[:, :4 * FS], gap, wet], axis=1))


for d in (25, 50, 100):
    ab(f"smash_depth{d}", smash(x, d / 100))
send = x * 0.6
for mode, name in enumerate(CONFETTI_MODES):
    ab(f"confetti_{mode + 1}_{name.lower()}", x + confetti(send, mode=mode))
for b, block in enumerate(("drive", "motion", "space", "texture")):
    for f, name in enumerate(PRISM_FX[b]):
        fx, amt = [0, 0, 0, 0], [0, 0, 0, 0]
        fx[b], amt[b] = f, 0.6
        ab(f"prism_{b + 1}{block}_{f + 1}_{name.lower()}", prism(x, fx=fx, amt=amt))
ab("prism_defaults", prism(x, fx=(1, 0, 1, 2), amt=(0.3, 0, 0, 0.25)))
print(len(os.listdir(OUT)), "files in", os.path.abspath(OUT))
