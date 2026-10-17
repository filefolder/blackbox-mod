"""The firmware's S950 path (src/s950.c, compiled for the Mac by host/build.sh) driven the way the Blackbox drives it:
64-frame blocks, the stock reader's scratch buffer (source[P - 4 ..], int(n x rate) + 8 samples), then the filter.
Also the 3.1.l - 3.1.S "S950" mode and a clean re-pitch, for comparison renders (host/render_s950.py)."""
import ctypes
import math
import os

import numpy as np
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
lib = ctypes.CDLL(os.path.join(HERE, "libdsp.dylib"))
F = ctypes.POINTER(ctypes.c_float)
FS = 48000
N = 64

HI = dict(grid=48000.0, fir=(-0.07796, 0.18391, 0.7881, 0.18391, -0.07796))          # filter.c's "S950"
LO = dict(grid=31250.0, fir=(-0.06897, 0.27256, 0.59283, 0.27256, -0.06897))        # "S950 Lo": 12.5 kHz bandwidth
LO10 = dict(grid=25000.0, fir=(0.05302, 0.25399, 0.38598, 0.25399, 0.05302))        # 10 kHz bandwidth, not built in:
#   five taps cannot be flat to 8 kHz and 13 dB down at 12.5 kHz; this one keeps the aliasing right and is 3 - 5 dB dull

lib.s950_read.argtypes = [ctypes.c_float, F, ctypes.c_int32, ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float,
                          F, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int), F, F, ctypes.c_int]
lib.s950_filter_count.argtypes = [ctypes.c_float, ctypes.c_uint]
lib.s950_filter_count.restype = ctypes.c_uint
lib.s950_filter.argtypes = [F, ctypes.POINTER(F), ctypes.c_int, ctypes.c_uint]


def fp(a):
    return a.ctypes.data_as(F)


def read(x, rate, mode, drive=0.0):
    """the voice reader over a whole sample, forwards: mono float32 in, the clocked-out 48 kHz stream back"""
    fir = np.array(mode["fir"], dtype=np.float32)
    gain, post = 1 + 7 * drive * drive, 1 / (1 + 1.1 * drive * drive)
    src = np.concatenate([np.zeros(8, np.float32), x.astype(np.float32), np.zeros(64, np.float32)])
    n_src = int(N * rate) + 8
    out = []
    p = 8.0
    while p + N * rate + 12 < len(src):
        ip = math.floor(p)
        scratch = np.ascontiguousarray(src[ip - 4:ip - 4 + n_src])
        frac = ctypes.c_float(p - ip)
        dst = np.zeros(N, np.float32)
        newpos = ctypes.c_int(0)
        lib.s950_read(mode["grid"], fp(fir), ip - 4 - 8, 1.0, gain, post, rate, fp(scratch), n_src, 4, ctypes.byref(newpos),
                      ctypes.byref(frac), fp(dst), N)
        out.append(dst)
        p += rate * N
    return np.concatenate(out) if out else np.zeros(0, np.float32)


def lowpass(y, clock, index=168, counts=None):
    """the tracking filter, block by block; index may be a per-block list (a Filter sweep)"""
    z = np.zeros(12, np.float32)
    y = y.copy()
    used = []
    for b, at in enumerate(range(0, len(y) - N + 1, N)):
        ix = index[min(b, len(index) - 1)] if hasattr(index, "__len__") else index
        m = lib.s950_filter_count(clock, ix)
        used.append(m)
        blk = np.ascontiguousarray(y[at:at + N])
        ch = (F * 2)(fp(blk), None)
        lib.s950_filter(fp(z), ch, N, m)
        y[at:at + N] = blk
    if counts is not None:
        counts.extend(used)
    return y


def s950(x, semis=0.0, mode=HI, drive=0.0, index=168):
    rate = 2 ** (semis / 12)
    return lowpass(read(x, rate, mode, drive), mode["grid"] * rate, index)


def old_s950(x, semis=0.0):
    """3.1.l - 3.1.S: 31.25 kHz grid, linear between stored samples, 12 bits, fixed 13.5 kHz low-pass"""
    g = 48000 / 31250
    h = np.array([-0.02035, 0.2289, 0.58291, 0.2289, -0.02035])
    xs = np.convolve(x, h, mode="same")
    rate = 2 ** (semis / 12)
    pos = np.arange(int((len(x) - 8) / rate)) * rate
    gp = pos / g
    k0 = np.floor(gp)
    j0 = np.clip(np.round(k0 * g).astype(int), 0, len(x) - 1)
    j1 = np.clip(np.round((k0 + 1) * g).astype(int), 0, len(x) - 1)
    v = xs[j0] + (gp - k0) * (xs[j1] - xs[j0])
    v = np.round(v * 2048) / 2048
    b0, b1, a1, a2 = 0.361304, 0.722608, 0.235922, 0.209295
    return signal.lfilter([b0, b1, b0], [1, a1, a2], v).astype(np.float32)


def clean(x, semis=0.0):
    rate = 2 ** (semis / 12)
    pos = np.arange(int((len(x) - 8) / rate)) * rate
    up = signal.resample_poly(x.astype(np.float64), 8, 1)
    return np.interp(pos * 8, np.arange(len(up)), up).astype(np.float32)
