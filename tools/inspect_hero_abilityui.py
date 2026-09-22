#!/usr/bin/env python3
"""Read-only inspector for the client's HUD ability-slot data path.

`docs/24-hero-and-abilities.md` left one trace open: *which* `ui/dist/*.js`
component draws the in-match ability bar, and whether the slot content comes
from the server. This answers both, read-only.

Three modes:

  --endpoints   Enumerate the client's UI RPC endpoint catalog. Each endpoint
                ships in .rdata as the triple `<name>\\0 <uuid>\\0 <ClassName>\\0`
                (e.g. `abilities.updates` / `033225a0-...` / `AbilitiesUpdates`),
                so the whole bus can be listed without a debugger. The UI calls
                these by name; `abilities.updates` is the one that fills the bar.

  --slots HERO  The `abilitySlot` of every `.ability` asset in that hunter's
                `<hero>_abilities.loadout`, i.e. the authored slot assignment.
                Compare with the static table in `gamedata.pak ui/dist/abilities.js`.

  --bar         Print the located HUD component's decision chain, with the
                identifiers to grep for in the extracted bundles.

Pairing caveat for --endpoints: the names, uuids and class names are three
separate string pools, matched here by adjacency. Adjacency is correct for the
great majority but skips a pool entry occasionally, so a row is evidence of
*an* endpoint existing, not proof that a particular name/class pair belongs
together. `abilities.updates`/`AbilitiesUpdates` was confirmed individually.

Nothing under the Steam install is written or executed, and no pak is repacked.
"""
import argparse
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inspect_creature_ai as ica  # noqa: E402
import objectstream  # noqa: E402
import pak as pakmod  # noqa: E402

# <lower.dotted.name> NULs <uuid> NULs <ClassName>, all NUL-padded in .rdata.
ENDPOINT = re.compile(
    rb'([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)\x00{1,16}'
    rb'([0-9a-f]{7,8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\x00{1,16}'
    rb'([A-Z][A-Za-z0-9]{3,40})\x00')

BAR = """\
In-match ability bar: gamedata.pak ui/dist/health-area.js, component class Kt.

  Kt  instance fn Ht: subscribes W (hero key, hud.js export a5) and
      rt (reticle-container.js export e). Slot list is the literal
      ["Ability3", "Ability1", "Ability2", "Ability4"].
  Gt  renders the whole bar only while the hero key is truthy.
  Mt  draws the fixed part, then one _t per slot.
  _t  per slot: `n = t[1].has(t[3])`. A slot missing from the map renders
      NOTHING - no frame, no keybinding.
  Et  -> Dt({action: slot, ability: map.get(slot) || false}).
  Dt  instance fn Jt destructures {id, active, disabled, capacity, charges,
      recharge} off that per-slot object. The icon is
          shared/img/<hero>/abilities/<id>.svg
      and when that path is not in the bundle manifest it warns
          console.warn("Unknown ability icon, " + path)
      and falls back to shared/img/unknown-ability.svg.
      Only `timer` is read from the static abilities.js table; `id` is server
      data. An empty id yields ".../abilities/.svg", i.e. the fallback.

The map is reticle-container.js `ur` (exported as `$r`/`e`), and the ONLY
writer is the native subscription:

  M.subscribe(({player: p}) => {
    ur.update(m => (m.clear(), m));
    if (!p) return fr.cancel("abilities");
    fr("abilities", j("abilities.updates", {object: p}, (err, res) => {
      if (err) return;
      const {abilities} = res;
      ur.update(m => { for (const slot in abilities) {
        const v = abilities[slot];
        if (slot === "MedPack") { v.charges < 0
            ? console.warn("abilities.updates sent a broken Medpack charges"
                           + " value! " + JSON.stringify(v))
            : pr.set(v); }
        else m.set(slot, v); } return m; }); }));
  });

M is preloader.js `a_` (export o), the `observed.updates` store. It sets
.player only when object.get returns {player, types} with
types.includes("Player"); a falsy object or the "Nope!" sentinel clears the
map and cancels the subscription. So the bar is a pure mirror of what the
native abilities.updates endpoint publishes for the observed entity.
"""


def endpoints(exe):
    image = exe.read_bytes()
    try:
        import pefile
        pe = pefile.PE(str(exe), fast_load=True)

        def rva(offset):
            return pe.get_rva_from_offset(offset)
    except Exception:                                   # pragma: no cover
        def rva(offset):
            return offset
    for match in ENDPOINT.finditer(image):
        yield (rva(match.start()), match.group(1).decode(),
               match.group(2).decode(), match.group(3).decode())


def _assets(pak, predicate):
    for entry in pakmod.load(str(ica.INSTALL / pak)):
        name = entry['name'].decode('utf-8', 'replace')
        if predicate(name.lower()):
            yield name


def loadout_abilities(pak, hero):
    """Ordered `.ability` asset paths in <hero>_abilities.loadout."""
    hero = hero.lower()
    wanted = [n for n in _assets(
        pak, lambda low: low.endswith('.loadout')
        and ('/%s/' % hero) in low and 'abilities' in low.rsplit('/', 1)[1])]
    if not wanted:
        # duelist really does ship as deulist_abilities.loadout
        wanted = [n for n in _assets(
            pak, lambda low: low.endswith('.loadout') and ('/%s/' % hero) in low)]
    paths = []
    for name in wanted:
        data = ica.load_asset(pak, name)
        # Paths are authored inconsistently: marine's loadout ends with
        # `Assets/Player/_Common/stance_crouch.ability`, mixed case.
        for match in re.finditer(rb'(?i)assets/[ -~]+?\.ability', data):
            path = match.group(0).decode()
            if path not in paths:
                paths.append(path)
    return wanted, paths


def _load(pak, asset):
    """Pak entry bytes, tolerating the assets' inconsistent path casing."""
    try:
        return ica.load_asset(pak, asset)
    except SystemExit:
        want = asset.lower()
        for entry in pakmod.load(str(ica.INSTALL / pak)):
            name = entry['name'].decode('utf-8', 'replace')
            if name.lower() == want:
                return pakmod.entry_bytes(entry)
        raise


def ability_slot(pak, asset):
    """(abilitySlot, Name) strings out of one .ability asset."""
    names = _names()
    tree = [ica.describe(root, names)
            for root in objectstream.parse(_load(pak, asset))]
    found = {}
    for root in tree:
        for label, _offset, kind, value in ica.flatten(root):
            field = label.rsplit('.', 1)[-1]
            if field in ('abilitySlot', 'Name') and field not in found:
                found[field] = _string(kind, value)
    return found.get('abilitySlot'), found.get('Name')


_NAMES = None


def _names():
    global _NAMES
    if _NAMES is None:
        _NAMES = ica.name_dictionary()
    return _NAMES


def _string(kind, value):
    if kind == 'string':
        return value
    if kind == 'raw':
        blob = bytes.fromhex(value)
        if len(blob) >= 8:
            length = struct.unpack('>I', blob[4:8])[0]
            return blob[8:8 + length].decode('utf-8', 'replace')
    if kind == 'text':
        return value
    return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--pak', default='misc.pak')
    parser.add_argument('--endpoints', action='store_true')
    parser.add_argument('--slots', metavar='HERO')
    parser.add_argument('--bar', action='store_true')
    args = parser.parse_args()

    if not (args.endpoints or args.slots or args.bar):
        parser.error('choose --endpoints, --slots HERO or --bar')

    if args.bar:
        print(BAR)

    if args.endpoints:
        rows = list(endpoints(ica.EXE))
        print('%-10s  %-26s  %-38s  %s' % ('rva', 'endpoint', 'uuid', 'class'))
        for rva, name, uuid, cls in rows:
            print('%#010x  %-26s  %-38s  %s' % (rva, name, uuid, cls))
        print('-- %d endpoint triples' % len(rows), file=sys.stderr)

    if args.slots:
        files, paths = loadout_abilities(args.pak, args.slots)
        for name in files:
            print('# %s' % name)
        print('%-14s  %-12s  %s' % ('slot', 'name', 'asset'))
        for path in paths:
            slot, name = ability_slot(args.pak, path)
            print('%-14s  %-12s  %s' % (slot or '-', name or '-', path))
        print('-- %d authored abilities' % len(paths), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
