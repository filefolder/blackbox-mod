"""Turn a built image into a release patch:

  python3 make_patch.py out/<patchsets>/BLACKBOX.bin "JOEBOX 1" [--extra out/BBEXTRA.BIN]

Writes docs/blackbox-mod-<version>.json (the version in lower case with dashes: blackbox-mod-joebox-1.json). The patch holds only the bytes this project adds or changes (new bytes and
their offsets). It contains no bytes of the stock firmware; the stock image is identified by SHA-256 only.

--extra: the second card file. From 3.2.l on, part of the mod (Prism, Confetti, the per-pad insert effects) lives in
the chip's second flash bank, which 1010music's installer never writes; the mod programs it itself from BBEXTRA.BIN
on the card. That file is this project's own compiled code and nothing else, so it goes into the patch whole. The
image only accepts the exact BBEXTRA.BIN it was linked with, so the pair is checked here before anything is written.
"""
import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
STOCK_BASE = 0x08040000
B2_MAGIC = 0x21324242                                     # "BB2!", src/b2head.c

ap = argparse.ArgumentParser()
ap.add_argument("image")
ap.add_argument("version")
ap.add_argument("--extra", help="out/BBEXTRA.BIN, for a build with code in flash bank 2")
ap.add_argument("--elf", default=os.path.join(ROOT, "out", "cave.elf"), help="the link that produced both (to check the pair)")
ap.add_argument("--out", help="where to write the patch (default docs/blackbox-mod-<version>.json)")
a = ap.parse_args()

stock = open(os.path.join(ROOT, "firmware", "BLACKBOX-3.1.9.bin"), "rb").read()
built = open(a.image, "rb").read()
assert len(built) >= len(stock)

writes = []
i = 0
while i < len(stock):
    if stock[i] == built[i]:
        i += 1
        continue
    j = i
    while j < len(stock) and stock[j] != built[j]:
        j += 1
    writes.append({"offset": i, "bytes": built[i:j].hex()})
    i = j

slug = re.sub(r"[^a-z0-9.]+", "-", a.version.lower()).strip("-")       # "JOEBOX 1" -> "joebox-1", for file names
patch = {
    "name": "blackbox-mod",
    "version": a.version,
    "slug": slug,
    "stock_version": "3.1.9",
    "stock_size": len(stock),
    "stock_sha256": hashlib.sha256(stock).hexdigest(),
    "output_size": len(built),
    "output_sha256": hashlib.sha256(built).hexdigest(),
    "writes": writes,
    "append": built[len(stock):].hex(),
}


def linked_payload():
    """Bank 2 as the link that made this image produced it: (id word, body). build.sh fills in the length and the sum
    afterwards, so those two words are not compared here."""
    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "bank2.raw")
        subprocess.run(["arm-none-eabi-objcopy", "-O", "binary", "-j", ".b2head", "-j", ".bank2", a.elf, raw], check=True)
        data = open(raw, "rb").read()
    return data[4:8], data[16:]


def expected_sum():
    """The sum of the bank 2 payload this image was linked with (src/cpu.c: b2_expect_sum), read out of the image."""
    nm = subprocess.run(["arm-none-eabi-nm", a.elf], capture_output=True, text=True, check=True).stdout
    at = next(int(line.split()[0], 16) for line in nm.splitlines() if line.endswith(" b2_expect_sum"))
    return struct.unpack_from("<I", built, at - STOCK_BASE)[0]


if a.extra:
    extra = open(a.extra, "rb").read()
    magic, _ident, length, total = struct.unpack_from("<4I", extra)
    body = struct.unpack(f"<{(len(extra) - 16) // 4}I", extra[16:])
    if magic != B2_MAGIC or length != len(extra) or len(extra) % 32 or sum(body) & 0xFFFFFFFF != total:
        raise SystemExit(f"{a.extra} is not a whole bank 2 payload. Nothing written.")
    # The unit checks the id AND the sum, and the sum does not cover the id: two payloads can share a sum.
    ident, linked = linked_payload()
    if expected_sum() != total or extra[4:8] != ident or extra[16:16 + len(linked)] != linked or any(extra[16 + len(linked):]):
        raise SystemExit(f"{a.extra} is NOT the bank 2 payload this image was linked with: the unit would refuse it and run\n"
                         "with those effects off. Take both files from the same ./build.sh. Nothing written.")
    patch["extra"] = [{"name": "BBEXTRA.BIN", "size": len(extra), "sha256": hashlib.sha256(extra).hexdigest(), "bytes": extra.hex()}]

out = a.out or os.path.join(ROOT, "docs", f"blackbox-mod-{slug}.json")
json.dump(patch, open(out, "w"), indent=1)
print(f"wrote {out}: {len(writes)} writes, {sum(len(w['bytes']) for w in writes) // 2} changed bytes, "
      f"{len(patch['append']) // 2} appended" + (f", + BBEXTRA.BIN ({len(extra)} bytes)" if a.extra else ""))
