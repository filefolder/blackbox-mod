"""Audition renders for the S950 modes: out/render/s950/*.wav (host/build.sh first).

Every file is the same two bars three times: at the original pitch, 5 semitones down, an octave down, with half a
second of silence between. Source: a drum loop (measure/4X4DRUMS103-48k.wav), summed to mono
(an S950 voice is mono). Listen in file order.
"""
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s950
import s950_ref as ref

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "out", "render", "s950")
os.makedirs(OUT, exist_ok=True)
FS = 48000


def load(path, seconds, start=0.0):
    w = wave.open(path)
    assert w.getframerate() == FS
    c, sw = w.getnchannels(), w.getsampwidth()
    raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.uint8).reshape(-1, c, sw)
    v = np.zeros(raw.shape[:2], np.int32)
    for b in range(sw):
        v |= raw[:, :, b].astype(np.int32) << (8 * b)
    v = (v << (32 - 8 * sw)) >> (32 - 8 * sw)                 # sign-extend
    x = v[:, :2].mean(axis=1) / float(1 << (8 * sw - 1))
    x = x[int(start * FS):int((start + seconds) * FS)]
    return (0.7 * x / np.max(np.abs(x))).astype(np.float32)


def save(name, parts):
    gap = np.zeros(FS // 2, np.float32)
    y = np.concatenate([np.concatenate([p, gap]) for p in parts])
    y = np.clip(y, -1, 1)
    with wave.open(os.path.join(OUT, name + ".wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes((y * 32767).astype("<i2").tobytes())


def main():
    # 4X4DRUMS103 (chosen because the USB capture used first sounded bad even clean),
    # resampled to 48 kHz; four bars at 126 BPM, the first two used
    x = load(os.path.join(HERE, "..", "measure", "4X4DRUMS103-48k.wav"), 2 * 4 * 60 / 126)
    PITCHES = (0, -5, -12)
    save("01_clean", [s950.clean(x, p) for p in PITCHES])
    save("02_s950_before", [s950.old_s950(x, p) for p in PITCHES])
    save("03_s950_new", [s950.s950(x, p, s950.HI) for p in PITCHES])
    save("04_s950_lo_new", [s950.s950(x, p, s950.LO) for p in PITCHES])
    save("05_option_lo_at_10k", [s950.s950(x, p, s950.LO10) for p in PITCHES])
    save("06_s950_lo_saturation_60", [s950.s950(x, p, s950.LO, drive=0.6) for p in PITCHES])

    # the machine's own Filter setting, 99 down to 40 and back over the two bars: the cutoff moves in the OS's steps
    parts = []
    for p in PITCHES:
        dry = s950.read(x, 2 ** (p / 12), s950.HI)
        blocks = len(dry) // 64
        sweep = [ref.filter_index(int(round(99 - 59 * (1 - abs(1 - 2 * b / blocks))))) for b in range(blocks)]
        parts.append(s950.lowpass(dry, 48000 * 2 ** (p / 12), sweep))
    save("07_option_filter_knob_sweep", parts)

    # the hardware model itself (record at the bandwidth, the real clock divider with its pitch error, analog filters)
    for name, bw in (("08_reference_machine_19k", 19200), ("09_reference_machine_12k5", 12500)):
        stored, fs_rec = ref.record(x, bw)
        save(name, [ref.play(stored, fs_rec, p * 16)[0] for p in PITCHES])
    print(sorted(os.listdir(OUT)))


if __name__ == "__main__":
    main()
