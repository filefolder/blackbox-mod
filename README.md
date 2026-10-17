# blackbox-mod

An unofficial feature mod for the **original 1010music Blackbox** (not the Blackbox 2), built on stock firmware 3.1.9.

> **Not affiliated with, endorsed by, or supported by 1010music.** This repository does not contain 1010music's
> firmware. You supply your own copy, downloaded from 1010music, and patch it yourself. Use at your own risk: this is
> tested on one unit, it may misbehave on yours, and running modified firmware may affect your warranty. Don't ask
> 1010music for support on a modded unit; flash stock first.

**This branch (upstream-picks)** is 3.1.n plus a chosen part of upstream's JOEBOX 1 work, ported as source onto the
3.1.n base rather than merged. It is one file, `BLACKBOX.bin`, like 3.1.n: none of JOEBOX 1's second flash bank, its
card loader or the effects that live there. TOOLS shows `3.1.n`.

## What it adds

Taken from JOEBOX 1:

- **Record pre-roll**: TOOLS, Rec, Pre-roll (1, 2, 4 or 8 bars). The loop plays that long before the recording
  starts, so a resampled loop holds its own tails.
- **MUTE and SOLO on the Pads screen.**
- **Load readout on TOOLS**: average / peak, for example `62/75%`. Over about 100% peak the engine is out of time
  and you will hear it.
- **Fixed**: the naming keyboard's 9 key (3.1.n typed "n").
- **Smash**, a three-band compressor, in the compressor list, and a **master clipper** after the compressor
  (Clean or Diode).
- **Delay types**: Motion (the stock delay with tremolo, filter, duck or swell on the repeats) and Relapse (a memory
  echo), next to Delay and Munchi.
- **Fidelity**: the S950 mode rebuilt from the machine's schematic, S950 Lo and SP 45 added, and the machine modes
  now work on Clip and Slicer pads.
- **LFO 2 and LFO 3 per pad**, on a new "LFO+" tab: twelve shapes (the stock nine plus two exponential ramps and
  a smooth random), tempo-synced rates from 32 bars to 1/32, Free / Retrig / Once, and a start Offset. They appear
  as LFO2 and LFO3 in the modulation Source list.
- **ENV and KEY as modulation sources**: the pad's own envelope, and key tracking.
- **Master FX slot** on TOOLS: VHS (wow, flutter, saturation, dropouts, noise). INFO on the row opens the knobs.

Not taken: Prism, Confetti, the per-pad inserts and the live input (they need the second flash bank), USB on the power
jack, upstream's rotate in the Event editor, and the fault / boot recorder. A preset saved on JOEBOX 1 with one of
those keeps loading: Master FX Prism and delay Type Confetti play dry / as the stock delay.

From 3.1.n and earlier:

- **Solo** in the mixer: MIX cycles Mixer → Mute → Solo. Tap pads to solo them (yellow).
- **Slicer knobs**: on the waveform screen, bottom-left = slice Start, bottom-right = slice End, top-right = Zoom.
- **Pad-1 ducker**: four modulation Sources (EXP1, EXP2, PUMP, LINR) that fire when pad 1 plays. Put your kick
  on pad 1, set a negative Amount on another pad's Level, and you have sidechain pumping. About 0.65 dB per 1%.
- **Chords on the Keys screen**: top-right knob picks a shape (Tri, 7th, 9th, Su2, Su4, 6th, Ad9, Pwr, Oct, Opn, Shl,
  Qrt, m7, m9, Mj7). In-scale shapes follow the Keys Scale/Root. Chords record into sequences.
- **Note conditions**: the piano roll's PLAY list gains 1:2 … 8:8 ("play the Ath of every B loops").
- **Fidelity** (was Interp): sample-rate crunch plus machine modes SP1200, SP-12, S950, MPC60 and SP Raw, with
  drop-sample pitching.
- **Filter**: 24 dB/oct state-variable filter; drive comes in as Res goes past noon.
- **Saturation** (was Overdrive): soft saturation in place of the hard clip.
- **Master compressor models**: Stock, Glue, Punch, Opto, Squash, Limit, plus a Comp Thresh setting.
- **Reverb Type**: Plate (stock) or Room.
- **Munchi**, a port of the CHOMPI TEMPO granular delay, as a delay type.
- **Sequencer fix**: a note added just ahead of the playhead plays on that pass.

### Known issues

- This has run on very few units. If yours does something this list doesn't mention, I want to hear about it.
- The Blackbox only has so much CPU, and the mod lets you spend all of it. Room is the expensive one.
  Watch the load readout on TOOLS.
- LFO 2 / 3 and ENV are not on Granular pads.
- A note placed very close ahead of the playhead is still sometimes silent.
- Chords need the pad out of Mono Poly Mode.
- Presets saved on an older mod build can come back with a neighbouring Fidelity or delay Type, because those
  lists gained entries. Set them again and save.

## Install

1. Download **blackbox 3.1.9** from <https://1010music.com/downloads> and unzip it. You need the `BLACKBOX.bin` inside.
2. Build and patch it (below), or patch it with a patch file made from such a build: `python3 apply.py path/to/BLACKBOX.bin`.
3. Keep the stock `BLACKBOX.bin` somewhere safe. That file is your way back.
4. Copy the patched `BLACKBOX.bin` to the root of the Blackbox's microSD card.
5. Install it the way you install any 1010music firmware update: hold BACK + INFO while powering on, and let go as
   soon as the install screen shows.

**To go back to stock:** put the stock `BLACKBOX.bin` on the card and install it the same way.

The patcher checks your file's SHA-256 before it writes anything
(`281ae303d32e5eb52adca7817a648a34e4bd3848017f26673dd9df3905f1341d`) and checks the result afterwards. It only
works on 3.1.9.

## Build from source

Needs `arm-none-eabi-gcc` and Python 3. Put your stock image at `firmware/BLACKBOX-3.1.9.bin` (see
`firmware/README.md`).

```
./build.sh
python3 patch.py solo slice duck chord cond filter cpu od comp seqfix fx2 munchi master cave
```

That writes `out/<patchsets>/BLACKBOX.bin`. `python3 make_patch.py <image> <version>` turns it into a patch file.

How it works: the stock image is left as it is. New code (`src/`) is compiled and appended after it, and a handful of
call sites and table entries (`patches/`) are redirected to the new code.

`./check.sh` builds, patches and runs every suite (`test_*.py`) under Unicorn (`pip install unicorn capstone numpy`).
Several of them hold the firmware's DSP against a build of the same C for the computer (`host/build.sh`, written
for macOS; on Linux, build it with gcc under the same name). `test_machine.py`'s four S950 Lo checks compare
against the Mac build's exact output and fail against a Linux one. They test the new functions, not a full boot. The first power-on of any build is the real test.

`ghidra/`, `disasm.py`, `findbl.py`, `findref.py` are the helpers used to find things in your own copy of the image.

## What is and isn't in this repository

In it: original source code, patch definitions (addresses and replacement bytes), tools, and MIT-licensed third-party
DSP with its notices ([THIRD_PARTY.md](THIRD_PARTY.md)).

Not in it, and please don't add it in issues, pull requests or forks: 1010music's firmware, patched firmware images,
decompiled or disassembled listings of the firmware, and 1010music's manuals or artwork.

## License

MIT for the original work here ([LICENSE](LICENSE)). "1010music" and "Blackbox" are trademarks of 1010music LLC, used
only to say what this mod is for.

If you are from 1010music and have a concern about anything here, open an issue or email justin.joe.dev@gmail.com and
it will be dealt with promptly.
