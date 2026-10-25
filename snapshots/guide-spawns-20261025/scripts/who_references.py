"""For each guide_hoth script, list which other pak assets mention its path (case-insensitive, raw bytes)."""
import sys, re
from pathlib import Path  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import pak
pakpath = sys.argv[1]
targets = [l.strip() for l in open(sys.argv[2]) if l.strip()]
needles = {t: t.lower().encode() for t in targets}
refs = {t: [] for t in targets}
for ent in pak.load(pakpath):
    name = ent['name'].decode('utf-8', 'replace')
    if name == 'assetcatalog.xml':
        continue
    try:
        data = pak.entry_bytes(ent)
    except Exception:
        continue
    low = data.lower()
    for t, n in needles.items():
        if n in low and name != t:
            refs[t].append(name)
for t in targets:
    print(t.split('/')[-1], '<-', refs[t])
