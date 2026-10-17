"""Reference model of the Akai S950's record and playback path, from the service schematic and the machine's own OS.

Sources (research/s950/): the S900 voice block diagram 860913A + voice schematic (the S950 voice board is the same
design with a 16 MHz sample clock), and the S950 OS 1.5 image (s950os.bin; addresses below are offsets in it).

Record (block diagram REC CIRCUIT; OS 0x4eb8):
  input -> MF6CN-50 6th-order Butterworth low-pass, fc = 80000 / M, M = max(4, 80000 // bandwidth)  ("fc = fs x 0.4")
        -> fixed 18 kHz 12 dB/oct low-pass -> sample and hold -> 12-bit successive-approximation converter
  sample clock = 16 MHz / (6,400,000 // bandwidth), i.e. 2.5 x the "Audio bandwidth" setting (3000 .. 19200 Hz)
Playback, per voice (PB CIRCUIT; OS 0x808d pitch, 0x7e40 + 0x884f filter):
  12-bit DAC clocked at 16 MHz / N: pitch IS the clock, every stored sample is output once, no interpolation
  -> MF6CN-50, clock 4 MHz / M (fc = 80000 / M), M = max(table[filter index], N // 80): never above 0.4 x the clock
  -> VCA (BA6110) set by an 8-bit DAC -> mix amps
  N comes from the stored rate in Hz walked down in 1/16-semitone steps by eight 16-bit multiplies (each truncates),
  one integer divide, then octave shifts; so every note is a little off its ideal pitch.
This model runs at OS x 48 kHz, takes the area of the DAC staircase per sample (exact), and uses the analog prototype
filters. Not modelled: the VCA's 8-bit level steps and its soft distortion, converter non-linearity, noise, the
switched-capacitor filter's own clock residue.
"""
import numpy as np
from scipy import signal

FS = 48000
OS = 8
FSO = FS * OS

# OS 0x98a8 + 0x62: filter clock divisor per filter index 98..168 (one index = a semitone; 80000 / M Hz)
FILTER_DIV = [255, 241, 227, 214, 202, 191, 180, 170, 161, 152, 143, 135, 128, 120, 114, 107, 101, 96, 90, 85, 80, 76, 72,
              68, 64, 60, 57, 54, 51, 48, 45, 43, 40, 38, 36, 34, 32, 30, 28, 27, 25, 24, 23, 21, 20, 19, 18, 17, 16, 15,
              14, 13, 13, 12, 11, 11, 10, 9, 9, 8, 8, 8, 7, 7, 6, 6, 6, 5, 5, 5, 5]
# OS 0x80d8: multipliers / 65536 for 1, 2, 4 ... 128 sixteenths of a semitone down
STEP_DOWN = [0xFF14, 0xFE29, 0xFC54, 0xF8B6, 0xF1A2, 0xE412, 0xCB30, 0xA145]


def record_clock(bandwidth):
    """(sample rate, input filter cutoff) the OS programs for an Audio bandwidth setting"""
    n = 6400000 // int(bandwidth)
    m = max(4, 80000 // int(bandwidth))
    return 16e6 / n, 80000 / m


def pitch_divisor(rate_hz, sixteenths):
    """the voice timer's count for a sample stored at rate_hz played `sixteenths` (1/16 semitones) above its own pitch"""
    q = int(sixteenths / 192)                    # idiv: truncates toward zero
    r = sixteenths - q * 192
    if r >= 0:
        q, r = q + 1, r - 192
    down = -r                                    # 1 .. 192 sixteenths below the octave above
    hz = int(rate_hz)
    for bit in range(8):
        if down >> bit & 1:
            hz = hz * STEP_DOWN[bit] >> 16
    n = 16000000 // hz
    if q < 0:
        n <<= -q
    else:
        for _ in range(q):
            if n < 212:                          # the top of the range: no more octaves up
                break
            n = (n + 1) >> 1
    while n >= 20000:
        n >>= 1
    return n


def filter_index(filt=99, key=60, key_filter=0, velocity=64, vel_filter=0, env=0):
    """OS 0x7e40: 70 + 1.25 x Filter + key tracking + velocity (+ the filter envelope), clamped to the table"""
    idx = 70 + (filt * 320 >> 8) + int((key - 63) * key_filter / 50) + ((velocity - 64) * vel_filter * 2 >> 8) + env
    return min(168, max(98, idx))


def filter_divisor(n, index=168):
    return max(FILTER_DIV[index - 98], n // 80)


def _butter(order, fc):
    return signal.butter(order, min(fc, 0.45 * FSO), fs=FSO, output="sos")


def record(x, bandwidth=19200, drive=1.0):
    """x: mono float at 48 kHz -> (12-bit samples as floats, stored rate in Hz)"""
    fs_rec, fc = record_clock(bandwidth)
    up = signal.resample_poly(x.astype(np.float64), OS, 1)
    up = signal.sosfilt(_butter(6, fc), up)
    up = signal.sosfilt(_butter(2, 18000.0), up)
    t = np.arange(int(len(x) / FS * fs_rec)) / fs_rec * FSO
    s = np.interp(t, np.arange(len(up)), up) * drive
    return np.clip(np.round(s * 2048), -2048, 2047) / 2048, fs_rec


def play(samples, fs_rec, sixteenths=0, index=168, quantized_pitch=True, seconds=None):
    """one voice: the DAC staircase at the voice clock through the tracking filter, returned at 48 kHz"""
    rate_hz = int(round(fs_rec))
    if quantized_pitch:
        n = pitch_divisor(rate_hz, sixteenths)
        clock = 16e6 / n
    else:
        clock = fs_rec * 2 ** (sixteenths / 192)
        n = int(16e6 / clock)
    m = filter_divisor(n, index)
    dur = len(samples) / clock if seconds is None else min(seconds, len(samples) / clock)
    edges = np.arange(int(dur * FSO) + 1) / FSO * clock          # in stored samples
    c = np.concatenate([[0.0], np.cumsum(samples)])
    k = np.minimum(edges.astype(np.int64), len(samples) - 1)
    area = c[k] + (edges - k) * samples[k]                        # integral of the staircase up to each edge
    y = np.diff(area) * (FSO / clock)
    y = signal.sosfilt(_butter(6, 80000 / m), y)
    return signal.resample_poly(y, 1, OS).astype(np.float32), dict(n=n, clock=clock, m=m, fc=80000 / m)


def cents_error(rate_hz, sixteenths):
    n = pitch_divisor(int(rate_hz), sixteenths)
    return 1200 * np.log2((16e6 / n) / (rate_hz * 2 ** (sixteenths / 192)))


if __name__ == "__main__":
    for bw in (19200, 16000, 12500, 10000, 7500, 3000):
        fs, fc = record_clock(bw)
        print(f"bandwidth {bw:5d}: sample rate {fs:8.1f} Hz, input filter {fc:7.1f} Hz")
    print("pitch error in cents, sample stored at 48000 / 25000 Hz, semitones -24 .. +12:")
    for rate in (48000, 25000):
        print(f"  {rate}:", " ".join(f"{cents_error(rate, s * 16):+.1f}" for s in range(-24, 13)))
    print("filter cutoff by clock (index 168 = Filter 99):")
    for semis in (12, 7, 0, -5, -12, -24):
        n = pitch_divisor(48000, semis * 16)
        print(f"  {semis:+3d} st: clock {16e6 / n:8.1f} Hz  N {n:5d}  filter {80000 / filter_divisor(n):7.1f} Hz")
