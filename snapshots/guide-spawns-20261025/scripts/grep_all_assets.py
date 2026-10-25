"""Count raw-byte occurrences of several tokens in every asset of a pak (read-only)."""
import sys, re
from pathlib import Path  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import pak
pakpath = sys.argv[1]
tokens = [t.encode() for t in sys.argv[2].split(',')]
for ent in pak.load(pakpath):
    name = ent['name'].decode('utf-8', 'replace')
    try:
        data = pak.entry_bytes(ent)
    except Exception:
        continue
    counts = {t.decode(): data.count(t) for t in tokens}
    if any(counts.values()):
        print(name, {k: v for k, v in counts.items() if v})
