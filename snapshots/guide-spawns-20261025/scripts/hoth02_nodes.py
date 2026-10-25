"""Dump the authored data of HotH_02's SpawnNode_Guide_EnemyBot entities (entries 33..44) and
their container (entry 11940), and diff their serialized component payloads. Read-only."""
import sys, struct, hashlib
from pathlib import Path  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import objectstream
import inspect_map_entities as ime
from inspect_creature_ai import name_dictionary, _preferred, decode_value

data = open(sys.argv[1], 'rb').read()
names = name_dictionary()
roots = objectstream.parse(data)
arr = ime.entity_array(roots)
print("direct entities:", len(arr))
def lab(e): return _preferred(names.get(e.name)) or f"crc:{e.name:08x}"

def flat(e, path=''):
    """yield (path, type, value-hex) for every element below e, skipping ids/transforms"""
    for c in e.children:
        p = path + '/' + lab(c)
        yield p, c.type_id[:8], c.value
        yield from flat(c, p)

def dump(e, depth=0, maxd=7, out=None):
    k, v = decode_value(e)
    vs = '' if v is None else f" {k}={str(v)[:80]}"
    print("  " * depth + f"{lab(e)} [{e.type_id[:8]}]{vs}")
    if depth < maxd:
        for c in e.children:
            dump(c, depth + 1, maxd)

which = [int(x) for x in sys.argv[2].split(',')]
if sys.argv[3] == 'dump':
    for i in which:
        print(f"===== entry {i}")
        dump(arr[i])
else:
    # compare the serialized payload of entries 33..44 apart from ids and transform
    base = None
    for i in which:
        ents = {}
        for p, t, v in flat(arr[i]):
            ents[p] = v
        if base is None:
            base = ents
            print("entry", i, "elements", len(ents))
            continue
        diffs = [p for p in set(base) | set(ents) if base.get(p) != ents.get(p)]
        print("entry", i, "elements", len(ents), "differs from first in", len(diffs), "paths:", sorted(diffs)[:20])
