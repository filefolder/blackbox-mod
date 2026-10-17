"""Host renders of the DSP cores (host/build.sh first): test signals, WAV in / out, level measurements."""
import ctypes
import os
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
lib = ctypes.CDLL(os.path.join(HERE, "libdsp.dylib"))
FS = 48000
F = ctypes.POINTER(ctypes.c_float)


def fp(a):
    return a.ctypes.data_as(F)


def db(x):
    return 20 * np.log10(max(float(x), 1e-12))


def rms(x):
    return db(np.sqrt(np.mean(np.square(x))))


def peak(x):
    return db(np.max(np.abs(x)))


def beat(seconds=8.0, bpm=120.0, level=0.5, seed=1):
    """A synthetic loop: kick on the quarters, snare-ish noise on 2 and 4, hats on the eighths, a bass note per bar."""
    rng = np.random.default_rng(seed)
    n = int(seconds * FS)
    t = np.arange(n) / FS
    out = np.zeros((2, n), dtype=np.float32)
    step = 60.0 / bpm
    for k in range(int(seconds / step) + 1):
        at = int(k * step * FS)
        m = min(n - at, int(0.3 * FS))
        if m <= 0:
            break
        tt = np.arange(m) / FS
        kick = np.sin(2 * np.pi * (50 * tt + 60 * (1 - np.exp(-tt * 30)) / 30)) * np.exp(-tt * 12)
        out[:, at:at + m] += kick
        if k % 2 == 1:
            sn = rng.standard_normal(m) * np.exp(-tt * 25) * 0.5 + np.sin(2 * np.pi * 190 * tt) * np.exp(-tt * 30) * 0.4
            out[:, at:at + m] += sn
        for h in (0, 0.5):
            ah = at + int(h * step * FS)
            mh = min(n - ah, int(0.05 * FS))
            if mh > 0:
                hat = rng.standard_normal(mh) * np.exp(-np.arange(mh) / FS * 120) * 0.25
                hat = np.diff(hat, prepend=0)
                out[0, ah:ah + mh] += hat * 0.8
                out[1, ah:ah + mh] += hat * 1.2
    bar = 4 * step
    for k in range(int(seconds / bar) + 1):
        at = int(k * bar * FS)
        m = min(n - at, int(bar * FS * 0.9))
        if m <= 0:
            break
        tt = np.arange(m) / FS
        f = [55, 65.4, 49, 73.4][k % 4]
        out[:, at:at + m] += 0.5 * np.tanh(2 * np.sin(2 * np.pi * f * tt)) * np.minimum(1, (m - np.arange(m)) / 2000)
    chord = sum(np.sin(2 * np.pi * f * t + i) for i, f in enumerate((220, 277.2, 329.6, 440))) * 0.08
    out[0] += chord * (0.5 + 0.5 * np.sin(2 * np.pi * 0.25 * t))
    out[1] += chord * (0.5 + 0.5 * np.cos(2 * np.pi * 0.25 * t))
    out *= level / np.max(np.abs(out))
    return out.astype(np.float32)


def save(path, x):
    y = np.clip(x.T, -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes((y * 32767).astype("<i2").tobytes())


def bands(x):
    """RMS in dB of low (< 120 Hz), mid, high (> 2.5 kHz), from the FFT of the mono sum."""
    m = x.mean(axis=0)
    s = np.abs(np.fft.rfft(m)) ** 2
    f = np.fft.rfftfreq(len(m), 1 / FS)
    tot = len(m) ** 2 / 2
    return [10 * np.log10(max(s[(f >= a) & (f < b)].sum() / tot, 1e-12)) for a, b in ((20, 120), (120, 2500), (2500, 20000))]


def smash(x, depth=0.5, time=0.5, push=1.0, block=256):
    st = (ctypes.c_float * 64)()
    lib.smash_reset(st, 1)
    lib.smash_block.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_float, ctypes.c_float, F, F, ctypes.c_int]
    l, r = x[0].copy(), x[1].copy()
    for at in range(0, len(l), block):
        n = min(block, len(l) - at)
        lib.smash_block(st, depth, time, push, fp(l[at:at + n]), fp(r[at:at + n]), n)
    return np.stack([l, r])


class ConfettiParams(ctypes.Structure):
    _fields_ = [("mode", ctypes.c_uint32), ("size", ctypes.c_uint32), ("repeats", ctypes.c_float), ("density", ctypes.c_float),
                ("contour", ctypes.c_float), ("tone", ctypes.c_float), ("space", ctypes.c_float)]


CONFETTI_MODES = ["Loops", "Cloud", "Slide", "Stutter", "Arp", "Taps"]


def confetti(x, mode=0, size=3, repeats=0.4, density=0.5, contour=0.5, tone=0.3, space=0.3, bpm=120.0, block=128, half=192256):
    """x: the send (2 x n). Returns the wet return."""
    st = (ctypes.c_uint32 * 4096)()
    rev = (ctypes.c_float * 20000)()
    a = (ctypes.c_int16 * (half * 2))()
    b = (ctypes.c_int16 * (half * 2))()
    p = ConfettiParams(mode, size, repeats, density, contour, tone, space)
    lib.confetti_block.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                ctypes.c_float, F, F, F, F, ctypes.c_int, ctypes.c_int]
    out = np.zeros_like(x)
    for at in range(0, x.shape[1], block):
        n = min(block, x.shape[1] - at)
        il, ir = np.ascontiguousarray(x[0, at:at + n]), np.ascontiguousarray(x[1, at:at + n])
        ol, orr = np.zeros(n, dtype=np.float32), np.zeros(n, dtype=np.float32)
        lib.confetti_block(st, rev, a, b, half, ctypes.byref(p), bpm, fp(il), fp(ir), fp(ol), fp(orr), n, int(at == 0))
        out[0, at:at + n], out[1, at:at + n] = ol, orr
    return out


class PrismParams(ctypes.Structure):
    _fields_ = [("fx", ctypes.c_uint32 * 4), ("amt", ctypes.c_float * 4), ("tilt", ctypes.c_float), ("rate", ctypes.c_float),
                ("mix", ctypes.c_float), ("time", ctypes.c_uint32)]


PRISM_FX = [["Drive", "Sweet", "Fuzz", "Snarl", "Bloom"], ["Doubler", "Vibrato", "Phaser", "Tremolo", "Pitch"],
            ["PingPong", "Tape", "Hall", "Scatter", "Reverse"], ["Filter", "Clamp", "Cassette", "Glitch", "Static"]]


def prism(x, fx=(1, 0, 1, 2), amt=(0.0, 0.0, 0.0, 0.0), tilt=0.5, rate=0.3, mix=1.0, time=3, bpm=120.0, block=256):
    mem = (ctypes.c_uint8 * 0x60000)()
    p = PrismParams((ctypes.c_uint32 * 4)(*fx), (ctypes.c_float * 4)(*amt), tilt, rate, mix, time)
    lib.prism_block.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_float, F, F, ctypes.c_int]
    l, r = x[0].copy(), x[1].copy()
    for at in range(0, len(l), block):
        n = min(block, len(l) - at)
        lib.prism_block(mem, ctypes.byref(p), bpm, fp(l[at:at + n]), fp(r[at:at + n]), n)
    return np.stack([l, r])
