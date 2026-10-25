"""Independent raw walk of guide_criticalpath.cdl: where does 'SpawnGuideBot' occur?"""
import sys, re, collections
from pathlib import Path  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import objectstream
from inspect_creature_ai import name_dictionary, _preferred

data = open(sys.argv[1], 'rb').read()
names = name_dictionary()
roots = objectstream.parse(data)
print("roots:", len(roots))

def label(e):
    return _preferred(names.get(e.name)) or f"crc:{e.name:08x}"

# Find every element whose raw value contains SpawnGuideBot
hits = []
def walk(e, path):
    p = path + [label(e)]
    if b"SpawnGuideBot" in e.value:
        hits.append((e.offset, "/".join(p[-6:]), e.value[:80]))
    for c in e.children:
        walk(c, p)
for r in roots:
    walk(r, [])
print("elements with SpawnGuideBot in value:", len(hits))
for h in hits:
    print(hex(h[0]), h[1], h[2])
