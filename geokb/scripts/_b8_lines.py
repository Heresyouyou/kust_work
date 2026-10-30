import sys
from pathlib import Path
p = Path(r"D:/ZSK/geokb/state/text/77e038513bc0.txt")
lines = p.read_text(encoding="utf-8", errors="ignore").split("\n")
a=int(sys.argv[1]); b=int(sys.argv[2])
print("总行数", len(lines))
for i in range(a, min(b,len(lines))+1):
    s=lines[i-1].strip()
    if s: print("L%d\t%s"%(i,s))
