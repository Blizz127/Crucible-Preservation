#!/usr/bin/env python3
"""Replay the client's mode-select gate against the backend's responses.

The rule below is transcribed verbatim from ui/dist/perks.js (build 5685572),
which is what decides whether the PRACTICE button renders at all:

    G = derived([sysmsgs, gamemodeStore], ([e, a]) =>
          !e || !a.size ? [] :
          p.TUTORIAL.entitlementId.find(id => a.has(id) && a.get(id).amount)
            ? Object.entries(e.modes).reduce((out, [name, enabled]) => {
                if (!p[name]) return out;
                const {entitlementId} = p[name];
                let want = entitlementId;
                if (Array.isArray(entitlementId))
                  want = entitlementId.find(id => a.has(id) && a.get(id).amount > 0);
                const owned = a.get(want) || false;
                out.push({gametype: name, unlocked: owned.amount > 0, enabled});
                return out;
              }, [])
            : [])

Two independent requirements fall out of it, and this test pins both:
  1. /sysmsgs/gamemodes.json must carry a "modes" key (else it throws).
  2. the "Gamemode" entitlement category must exist in the catalogue AND have
     amount > 0 granted, or every entry gates out.

Run: python3 tools/test_mode_gate.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import crucible_backend as B  # noqa: E402
import crucible_data as D  # noqa: E402

# The client's gametype catalogue, transcribed from ui/dist/preloader.js
# (`Hl`), with the minified `Ll` resolved to its literal "mode_standard_unlock".
CATALOG = {
    "DOUBLES_CASUAL": {"entitlementId": ["mode_standard_unlock",
                                         "mode_alphaHunterDoubles_unlock"]},
    "CRUCIHEARTS_CASUAL": {"entitlementId": ["mode_standard_unlock",
                                             "mode_heartOfTheHives_unlock"]},
    "CONQUEST_CASUAL": {"entitlementId": ["mode_standard_unlock",
                                          "mode_harvesterCommand_unlock"]},
    "PRACTICE": {"entitlementId": ["mode_standard_unlock",
                                   "mode_practiceArena_unlock"]},
    "TUTORIAL": {"entitlementId": ["mode_initial_unlock",
                                   "mode_tutorial_unlock"]},
    "HOTH_GUIDE": {"entitlementId": ["mode_initial_unlock"]},
    "CUSTOM_CRUCIHEARTS": {"entitlementId": ["mode_standard_unlock"]},
    "SOLO_BOTS": {"entitlementId": ""},
    "SOLO_CASUAL": {"entitlementId": ""},
}


def mode_list(sysmsgs, gamemode_store, catalog=CATALOG):
    """Faithful Python transcription of the client's `G` derived store."""
    if not sysmsgs or not gamemode_store:
        return []
    tutorial = catalog["TUTORIAL"]["entitlementId"]
    if not any(gamemode_store.get(e, {}).get("amount") for e in tutorial):
        return []
    out = []
    for name, enabled in sysmsgs["modes"].items():   # KeyError mirrors the JS throw
        entry = catalog.get(name)
        if not entry:
            continue
        want = entry["entitlementId"]
        if isinstance(want, list):
            want = next((e for e in want
                         if gamemode_store.get(e, {}).get("amount", 0) > 0), None)
        owned = gamemode_store.get(want) or {}
        out.append({"gametype": name,
                    "unlocked": owned.get("amount", 0) > 0,
                    "enabled": enabled})
    return out


def build_gamemode_store():
    """Model the client: catalogue defines the category (amount zeroed), then
    /entitlements/list quantities are merged in for catalogue-known ids."""
    store = {}
    for e in D.entitlement_catalog():
        if e["category"] == D.MODE_UNLOCK_CATEGORY:
            store[e["entitlementId"]] = dict(e, amount=0)
    for q in D.base_entitlements():
        eid = q["entitlementId"]
        if eid in store:
            store[eid].update(q)
    return store


def route(path):
    """Drive the real backend router and unwrap the gameconfig double-encoding."""
    handler = B.Handler.__new__(B.Handler)
    handler.path = path
    parts = path.strip("/").split("/", 1)
    for name, fn in B.ROUTERS:
        if name == parts[0]:
            result = fn("/" + parts[1], {})
            if isinstance(result, dict) and set(result) == {"data"}:
                return json.loads(result["data"])
            return result
    raise AssertionError("no route for %s" % path)


def main():
    failures = []

    sysmsgs = route("/CrucibleLiveSystemMessages/gamemodes.json")
    store = build_gamemode_store()
    modes = mode_list(sysmsgs, store)
    by_name = {m["gametype"]: m for m in modes}

    # 1. the payload must carry the modes key
    if "modes" not in sysmsgs:
        failures.append("gamemodes.json has no 'modes' key: %r" % (sysmsgs,))

    # 2. the Gamemode category must be defined and granted
    cat_ids = {e["entitlementId"] for e in D.entitlement_catalog()
               if e["category"] == D.MODE_UNLOCK_CATEGORY}
    if not cat_ids:
        failures.append("entitlement catalogue defines no Gamemode entries")
    granted = {q["entitlementId"] for q in D.base_entitlements()
               if q["category"] == D.MODE_UNLOCK_CATEGORY
               and q.get("amount", 0) > 0}
    if not granted:
        failures.append("no Gamemode entitlement granted with amount > 0")

    # 3. the master gate must pass, or the list is empty regardless
    gate = [e for e in CATALOG["TUTORIAL"]["entitlementId"] if e in granted]
    if not gate:
        failures.append("master gate fails: need one of "
                        "mode_initial_unlock / mode_tutorial_unlock granted")

    # 4. PRACTICE must actually come out unlocked
    if "PRACTICE" not in by_name:
        failures.append("PRACTICE missing from the rendered mode list")
    elif not by_name["PRACTICE"]["unlocked"]:
        failures.append("PRACTICE rendered but locked")
    elif not by_name["PRACTICE"]["enabled"]:
        failures.append("PRACTICE rendered but disabled")

    # 5. the old payload shape must reproduce the bug this test guards
    try:
        mode_list({"messages": []}, store)
    except KeyError:
        pass
    else:
        failures.append("regression guard: {\"messages\": []} should throw "
                        "on missing .modes")

    if failures:
        print("FAIL")
        for f in failures:
            print("  " + f)
        return 1
    unlocked = [m["gametype"] for m in modes if m["unlocked"] and m["enabled"]]
    print("PASS: %d/%d modes render unlocked=%s"
          % (len(modes), len(CATALOG), unlocked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
