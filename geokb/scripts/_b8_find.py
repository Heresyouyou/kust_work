import sys, re
from pathlib import Path
lines = Path(r"D:/ZSK/geokb/state/text/77e038513bc0.txt").read_text(encoding="utf-8", errors="ignore").split("\n")
q = sys.argv[1]
for i,l in enumerate(lines,1):
    if q.lower() in l.lower():
        print(i, l.strip()[:200])
