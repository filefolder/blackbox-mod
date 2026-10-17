"""Level of every Prism effect at 25 / 50 / 100 % against the dry loop (host/build.sh first)."""
import sys
sys.path.insert(0, "host")
from dsp import *

x = beat(seconds=8, level=float(sys.argv[1]) if len(sys.argv) > 1 else 0.5)
print(f"in: peak {peak(x):.1f} rms {rms(x):.1f}")
for b in range(4):
    for f, name in enumerate(PRISM_FX[b]):
        row = []
        for a in (0.25, 0.5, 1.0):
            fx, amt = [0, 0, 0, 0], [0, 0, 0, 0]
            fx[b], amt[b] = f, a
            y = prism(x, fx=fx, amt=amt)
            assert not np.isnan(y).any(), name
            row.append(f"{rms(y) - rms(x):+5.1f}/{peak(y):5.1f}")
        print(f"  {name:8} rms change / peak at 25, 50, 100 %: " + "   ".join(row))
