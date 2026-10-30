import json, sys
from pathlib import Path
lines = Path(r"D:/ZSK/geokb/state/text/77e038513bc0.txt").read_text(encoding="utf-8", errors="ignore").split("\n")
pats = json.loads(sys.argv[1])
for p in pats:
    print("### %s" % p)
    for i,l in enumerate(lines,1):
        if p.lower() in l.lower():
            print("  ", i, l.strip()[:160])
