"""One HTML file that carries the patch inside it, for handing to testers before a release is on the website:

  python3 make_standalone.py docs/blackbox-mod-joebox-1.json out/joebox-1-patcher.html

It is docs/index.html with the patch inlined instead of fetched. Like the patch, it holds none of 1010music's
firmware: whoever opens it supplies their own stock 3.1.9 BLACKBOX.bin, and nothing leaves their computer.
"""
import json
import re
import sys

ROOT = __file__.rsplit("/", 1)[0] if "/" in __file__ else "."
patch = json.load(open(sys.argv[1]))
page = open(ROOT + "/docs/index.html").read()
page, n = re.subn(r'const PATCH = "[^"]*";', lambda _: "const PATCH = " + json.dumps(patch, separators=(",", ":")) + ";", page)
assert n == 1, "docs/index.html no longer has the one PATCH line this replaces"
# A tester's copy goes out before the website knows about the build, so it does not send anyone to the repository
# (which still describes the previous release): it says what it is and where the list for new builds lives.
page, n = re.subn(r'<p>Unofficial mod for the original 1010music Blackbox\.[^\n]*</p>',
                  f'<p><b>{patch["version"]}, test copy.</b> Unofficial mod for the original 1010music Blackbox. This build is out for '
                  'testing before its release. It has run on very few units, so keep your stock <code>BLACKBOX.bin</code> to go back to.</p>', page)
assert n == 1, "the intro paragraph has changed"
page, n = re.subn(r'\s*\(<a href="https://github\.com/[^"]*THIRD_PARTY\.md">notices</a>\)', "; the full notices ship with the release", page)
assert n == 1, "the notices link has changed"
assert "github.com" not in page, "a repository link is still in the tester's copy"
open(sys.argv[2], "w").write(page)
print(f"wrote {sys.argv[2]} ({len(page):,} bytes): {patch['version']}, {1 + len(patch.get('extra', []))} file(s)")
