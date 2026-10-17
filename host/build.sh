#!/bin/sh
# Compile the firmware-free DSP cores for the Mac: host/libdsp.dylib, driven from Python (host/dsp.py) to render audio.
set -e
cd "$(dirname "$0")"
clang -O2 -shared -fPIC -Wno-unknown-pragmas -Wno-ignored-pragmas -o libdsp.dylib $(ls ../src/smash.c ../src/s950.c 2>/dev/null)
