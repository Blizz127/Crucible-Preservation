# Hero selection and the empty ability slots

`19-hunter-patrol.md` left two user requests open: startup forces Marine, and
the visible HUD has empty ability slots. This is the audit of both. Hero
selection is implemented and unit-tested; the ability slots are diagnosed as
far as static evidence allows and the remaining gap is named precisely.

Nothing here was live-verified. No pak was repacked, no client memory was
written, no service was restarted and the game was never launched.

## The installed hunter roster

`assets/gamesystems/characterrosters/commoncharacterroster.roster` (misc.pak)
is an ObjectStream v3 asset holding eleven entries: ten playable hunters plus
Spectator. Each entry carries a `characterclass` BakedName, a `characterslice`
asset reference, a skin list, a perkdata asset and emote/emoji lists.

Three identities have to agree, and they come from three different places.

| # | Hunter | CharacterClass | Character slice hash | Authored abilities |
|--:|---|--:|--:|--:|
| 0 | Marine | 8 | 9256072 | 13 |
| 1 | Pyro | 10 | 1913935216 | 15 |
| 2 | Minigunner | 9 | 4288064808 | 12 |
| 3 | Inventor | 7 | 3739945788 | 12 |
| 4 | Duelist | 4 | 1641511682 | 13 |
| 5 | Robotanist | 2 | 1443315109 | 12 |
| 6 | Fighter | 5 | 2220298799 | 16 |
| 7 | Hero | 6 | 501823405 | 14 |
| 8 | Saboteur | 12 | 2391706089 | 12 |
| 9 | Commando | 3 | 3623436538 | 14 |

Column 1 is the entry's position in the roster asset. It is **not** the
CharacterClass value and must not be used as one.

`CharacterClass` is a native enum, not a baked-name asset: there is no
`assets/bakednames/characterclass.names`. Its name pointer table is at .rdata
RVA `0x6c1de08`, 38 entries, returned by the one-instruction accessor at
`0x432a380` (`lea rax, [rip+0x28f3a81]; ret`). Index 0 is `Unknown`, 1 `None`,
2..13 the twelve hunters, then Juggernaut 14 .. MapBoundsMarker 37. **Index 8
is `Marine`**, which independently confirms the `00 08` the accepted Practice
bootstrap has been sending since the 19:46 arena-entry run; that value had
only ever been assumed to mean Marine.

The slice hashes are the `Key=` values of
`levels/<level>/commondata/stringhashlibrary.xml`. All six shipped hash
libraries (practicearena, april2018, HotH_02, tutorial_02, tutorial_03,
mainmenu) publish the identical eleven `slices/player/*/*_character` entries,
so the hash does not depend on the selected map.

Two shipped hunters are deliberately **not** selectable:

- `rocketeer` (hash 2758943952) is absent from the roster asset, and its slice
  drops `{4f80779f-9085-fc7a-d978-bf32338e0c35}` = `DamageFloaterComponent::Client`,
  network type 36 with 36 autonomous bits. Its autonomous record would have 24
  roots, not 25, so it cannot reuse the accepted Marine layout.
- `technomancer` has a character slice in misc.pak but **no entry in any
  shipped level hash library**, so the shard has no name hash to send.

`tools/inspect_hero_roster.py` re-derives this whole table read-only from the
install (`--components`, `--classes` for the two supporting dumps).

## Why one replication layout serves all ten

`build_practice_character_probe` hard-codes Marine's 33 shared component roots
and 25 autonomous roots, established live in the 19:18 GMT capture. Those are
valid for every hunter in the table above, because the only differences
between the installed `*_character.dynamicslice` component sets are
network-irrelevant:

- duelist / fighter / saboteur add `{ac9b8fa0-a6da-4377-8219-25ba7e4a22e8}` =
  `cloth` (exe string pair at raw file offset `0x67a9009`). No component of
  that name exists in the client's 180-entry network descriptor table
  (`snapshots/gamemode-20260919/component-replication-schema.json`), so it has
  no network type and consumes no record bit.
- hero adds `{2aa86eeb-4b0c-9b17-15d1-f41791b3f79c}` =
  `AudioJointComponent::Client`. It *is* network type 16, but its four
  expanded direction counts are `[0, 0, 0, 0]`.

Everything else is byte-identical to Marine's 81-component list. This is what
makes a one-line hero swap safe rather than a per-hero reverse-engineering job.

The UUID→class-name evidence comes from the executable's own `{UUID}\0\0Name`
string pairs; 2,617 pairs are recoverable that way, including
`LoadoutSetupComponent::Client` `{ef0e66b8-...}` and `AbilityComponent::Client`
`{46a6f257-...}`.

## Selectable hero

`server/hero_loadout.py` holds the table above and resolves
`CRUCIBLE_SOLO_HERO=<name>` (case-insensitive) to a slice hash, a slice entry
index, a CharacterClass and the HUD ability slots. Unset, empty or absent
selects Marine, so every accepted wire byte is unchanged. An unknown or
unsupported name raises rather than silently falling back — a typo must not
quietly put the user back in Marine.

`server/test_hero_loadout.py` asserts that Marine's values are exactly the
constants already baked into `shard_stub.build_practice_character_probe`,
`build_single_player_roster_probe` and `build_character_connection_links`, so
wiring the module in cannot move an accepted byte.
`server/test_character_spawn.py` is unchanged and still passes.

### Is hero choice declared to the hub?

No, and it cannot be. Traced, not assumed:

- `match_stub.py` implements Connect, UserStatus and GetServerShardAddress.
  None carries a character field.
- The shard Connect payload serialiser `0x4b9ed30..0x4b9f094` carries protocol
  hashes, DebugComponentsEnabled, AutonomousEntityIds, spawn-transform fields
  and build IDs (docs/16). No character.
- In the LAN path the client's own matchmaking machine never tells anything
  which hunter it picked. `ui/dist/menu.js`, machine `id:"lanmatchmaking"`,
  state `ready`: its entry calls `ee(character)` and `te()`, which resolve
  through `preloader.js` exports `bY`/`bZ` to
  `sa("Music_CharSelect", "Char_<Name>")` and
  `sa("Music_GameState","Queuing")` — **audio switches only**. The sibling
  state `setloadout` invokes `localconnection.loadout.set`, whose `src` is the
  literal `() => Promise.resolve()`: a stub shipped in retail. Then
  `data.set {key:"lanloadout"}` writes to the client's local store, and
  `matchserver.connect {type:"lan", ipAddress, port}` carries no character.

So in this project the hero is purely the server's decision. One environment
variable, three consumers (spawn slice, roster CharacterClass, LoadoutSetup
Character), is the correct shape.

### Is the character-select UI reachable?

Partly, and it is broken for a reason we can fix. MatchPhaseType has
`CharacterPreview` 1 and `CharacterSelect` 2, which Practice's authored
`design/gamemodes/practice.gamemodeconfig` does not use — solo Practice goes
straight to GamePlay, so there is no in-match select screen by design.

The **menu** character screen is reachable: in
`snapshots/gamemode-20260919/NovaGameClient-20260919T173321Z.log` the client
issues `SVCSREQ:17 GET .../playerdata/loadouts/Marine` at 17:33:32 and then
`SVCSREQ:31 .../playerdata/loadouts/Saboteur` at 17:33:34, i.e. the user
browsed from Marine to Saboteur. Selecting is what fails: `ui/dist/party-actions.js`
sets a hero with
`a.data("/playerdata/activeloadout", {method:"POST", data:{ActiveLoadout:{character, slot:0}}})`
inside a `try`, whose `catch` raises `ERROR_MODAL / LOADOUT_SET_FAIL`.

## The live-services gap this audit found

`crucible_backend.ROUTERS` has no `CrucibleLivePlayerDataService` entry, so
every one of these 404s. In the 17:33 log, requests `SVCSREQ` 17, 19–29, 31
and 32 have **no matching `SVCSRESP`** anywhere in the file:

| Route | Consumer | Shape the client destructures |
|---|---|---|
| `GET /playerdata/activeloadout` | `menu.js` boot `loadout.activeloadout` | `t.loadout.character.toLowerCase()` |
| `GET /playerdata/loadouts/<Name>` | `menu.js` `go()` | `{loadouts:[{skin, dropPodSkin, dropPodDecals, wheel1, wheel2, wheel3}]}` |
| `POST /playerdata/activeloadout` | `party-actions.js` | `{ActiveLoadout:{character, slot}}` |
| `PUT /playerdata/loadouts/<Name>` | `party-actions.js` | `{loadouts:[{character, ...}]}` |
| `GET /playerdata/perkset/<Name>` | `perks.js` `se()` / `ce()` | `{perkSet:{character, perks}}` |
| `PUT /playerdata/perkset/<Name>` | `perks.js` `ne()` | `{perkSet:{character, perks}}` |

This explains **the forced Marine**, exactly: the boot machine's
`activeloadout` invoke fails, its `onError` jumps straight to `done`, and the
LAN machine's `data` state falls back to
`const {characters:e} = await s.promise("characters.get"), [{character:t}] = e`
— the *first* entry of the native character list, which is roster index 0,
Marine. `playerdata/loadouts/Marine` being the very first request in the log
is that fallback firing.

`hero_loadout.playerdata_activeloadout/_loadouts/_perkset` build the three GET
payloads from the same selected hero. They are proposals; they have never been
served to the client.

## Empty ability slots

### What is proven

The HUD's ability→slot assignment is **not server data**. `ui/dist/abilities.js`
(gamedata.pak) is a static table compiled into the client, one entry per
hunter, e.g.

```js
marine:{abilities:[{id:"marine_primary",slot:"PrimaryFire"},
                   {id:"marine_sprint",slot:"Ability3"},
                   {id:"marine_bunker",slot:"Ability1"},
                   {id:"marine_grenade",slot:"Ability2"},
                   {id:"marine_ads",slot:"Ability4"}],
       modifiers:{Marinebunkerduration:"marine_bunker"}}
```

`ui/dist/hud.js` imports it as `Dt` and keys it with the derived store

```js
const ts = t([m,u,f], ([e,t,n]) => {
  let {characterClass:s=""} = e;
  return s && t.has(s.toLowerCase()) || (s = n ? n.character : "Marine"),
         s.toLowerCase();
}, !1);
```

`m` is the local player's entry in the replicated match player list (so the
key follows the CharacterClass this project sends), `u` is the Map built from
the native `characters.get` callback, `f` is its first value. There is a
hard-coded `"Marine"` fallback, so this store cannot be the sole cause.

Each slot's icon is a CSS mask built from the same two values:
`url(shared/img/<hero>/abilities/<ability id>.svg)`. All seven Marine ability
SVGs are present in gamedata.pak under `ui/dist/shared/img/marine/abilities/`.

The slot vocabulary is a native 8-value enum: `Crucible::AbilitiesUtils::EnumToString`
at RVA `0x42f3310` (bound-checked `< 8`, jump table `0x65c3d80`) gives
`None, Ability1, Ability2, Ability3, Ability4, MedPack, PrimaryFire, Reload`,
matching the `.slot` strings above and the character's AnimGraph parameters
(`using_ability1/2/3`, `using_medpack`, `using_primary1/2`). The orthogonal
"which ability" vocabulary is `AbilityNames`, a uint16 index into the
112-entry `assets/bakednames/abilitynames.names` (Marine occupies 44..51:
`marine_ads, marine_bunker_cancel, marine_bunker_place, marine_bunker,
marine_grenade, marine_primary, marine_quickmelee, marine_reload`).

The authored per-hunter ability set is an ordered array of `.ability` asset
references inside `assets/player/<hero>/<hero>_abilities.loadout` (the duelist
file really is spelled `deulist_abilities.loadout`). Marine's thirteen are
sprint, grenade, primary, reload, aimdownsights, `_common/usemedpack`,
quickmelee, `character/interact`, bunker, bunker_place, and the three common
emote/emoji/voiceline entries. The asset stores no slot index; slotting is
applied entirely by the client-side table above. The character slice itself
carries the reference: component `{eb475e04-77d7-5c98-4cef-746f9e16d80d}` on
`marine_character` points at `marine_abilities.loadout`.

### What is ruled out

- **Not an entitlement gate.** `ui/dist/perks.js` gates hunters with
  `<name>_character_unlock` in the `"Character"` entitlement category and
  perks with `<name>_upgrade_NN` in `"Upgrade"`, but both checks begin
  `if(!Z) return !0` / `if(!X) return !0`, where `Z`/`X` are "does any
  entitlement in this category have amount > 0". `crucible_data.py` grants
  only `Currency`, `AccountIcon` and `Gamemode` entitlements, so both
  categories are empty and **everything is treated as unlocked**. No change to
  `crucible_data.py` is warranted, and none was made.
- **Not `/playerdata/*`.** Those routes carry cosmetics (skin, drop-pod skin,
  emote/emoji/voice wheels) and the perk *upgrade* selection. `abilities.js`
  contains no entitlement reference at all.
- **Not a missing PerkPreview/PerkSelect phase.** Practice's authored config
  has no selection phases; perks are the level-up upgrade tree, not the
  ability bar.
- **Not `LoadoutSetup`.** Its generated serialisers are
  `SerializeAuthorityToClientProperties` RVA `0x961210` (`SelectedEmote`,
  `SelectedVoiceLine`, `SelectedEmoji`) and
  `SerializeAuthorityToAutonomousProperties` RVA `0x9614e0` (`Character`,
  `SelectedPerks`, `CharacterAccountLevel`, `CharacterSkin`). There is no
  ability property in either direction. This also confirms the existing
  bootstrap is right to write `Character` into the *autonomous* record.

### The screenshot, and what it narrows to

`snapshots/gamemode-20260919/alpha-three-hunters-visible.png` shows the bug
directly: the bottom-right bar renders **three parallelogram slots with the
correct Marine keybindings** — LShift (`marine_sprint`, Ability3), Q
(`marine_bunker`, Ability1), E (`marine_grenade`, Ability2) — and each one is
a flat unfilled shape with no ability icon. So the hero key resolved, the
static slot table was found, and the keybinding component rendered. Only the
per-ability content is missing. "Empty ability slots" is an icon/state
failure, not a missing list.

### Best-evidenced remaining hypothesis

The client's local `AbilityComponent` never receives its ability instances.
`AbilityComponent` is network type 6 with expanded direction counts
`[0, 0, 26, 0]` — 26 **autonomous** bits, zero shared. The character spawn
sends `build_replication_record([False] * 25)` for the autonomous record, so
every one of those bits is "unchanged" and the component keeps its
default-constructed state.

Against that: its autonomous property list, recovered from
`AbilityComponent::Client::SerializeAuthorityToAutonomousProperties`
(name string at RVA `0x66bfcbd`, body around `0x796500..0x796a80`), is
`RunningAbilities`, a `SerializableFixedSizeVector` of `{RunningAbilityIndex
(int8_t), ResetCount (uint8_t)}`. That is *transient* in-flight ability state,
not an equipped set — so replicating it would not by itself populate the bar.

The two candidates that survive, in order of strength:

1. **The ability instances are created server-side and this shard never does
   it.** The character entity was materialised by a network spawn message, not
   by the retail drop-pod/`CharacterSpawnLoadout` path
   (`assets/gamesystems/spawnloadouts/defaultspawnloadout.spawnloadoutdata`,
   whose `character` field is a BakedName and whose `perks` array is empty).
   If the authoritative side is what reads `<hero>_abilities.loadout` and
   pushes ability existence/cooldown state, a client-only spawn leaves the bar
   with slots but no abilities. The next probe is
   `EntityResourceCollectionComponent`, network type 46, `[38, 0, 92, 0]` — 92
   autonomous bits, the largest autonomous surface on the character, and the
   natural home of per-ability charges and cooldowns. It is currently sent as
   a single `False` shared root with all 92 autonomous bits unset.
2. **The bar's icon is gated on a per-ability native state lookup** that
   returns nothing, leaving the mask unset and the slot painted flat. The
   in-match bar component was not located in `hud.js` (the two mask-setting
   components there, `gs` and `Ss`, are the `help.abilities.label` help
   screen, and `reticle-container.js`'s `te[...]` use is the primary/secondary
   name readout). Finding it is the cheapest next step and would discriminate
   between (1) and (2) without a launch.

Neither is implemented. Fabricating ability state before the reading path is
traced is exactly the kind of guess this project has already paid for twice.

## What is implemented

- `server/hero_loadout.py` — the roster, CharacterClass table, slice hashes,
  Marine's shared/autonomous root layout with the proof that it generalises,
  the verbatim HUD ability-slot table, the authored ability lists, and the
  three `CrucibleLivePlayerDataService` payload builders.
- `server/test_hero_loadout.py` — 17 tests: Marine default is byte-identical
  to the accepted constants, environment selection, refusal of rocketeer /
  technomancer / unknown names, class-value uniqueness, roster order, slot
  completeness, payload shapes, plus three install-backed tests that re-derive
  the hashes, the enum table and the component-set equivalence from the
  read-only install (skipped when the install is absent).
- `tools/inspect_hero_roster.py` — read-only re-derivation of all of it.

All 239 server tests pass, including the unchanged `test_character_spawn.py`.

## Integration notes

`hero_loadout.py` is pure data plus helpers; nothing in it is wired up yet.
The three protected files need these edits, and no others.

### `server/shard_stub.py`

1. Add `import hero_loadout` beside the existing `import solo_modes`.
2. `build_practice_character_probe` — replace the two Marine literals and the
   hard-coded tuple. Add a `hero=None` keyword argument defaulting to
   `hero_loadout.selected()`:
   - `shared_types = (...)` → `hero_loadout.SHARED_TYPES`
   - `build_replication_record([False] * 25)` →
     `build_replication_record([False] * hero_loadout.AUTONOMOUS_ROOTS)`
   - `slice_hash=9256072, slice_entry_index=0` →
     `slice_hash=hero.slice_hash, slice_entry_index=hero.slice_entry`
   Keep `build_solo_hunter_probes` calling it **without** a hero argument, or
   pass `hero_loadout.get('marine')` explicitly: the three Alpha Hunters bots
   are an accepted visual baseline and must stay Marine.
3. `build_single_player_roster_probe` — the local player's CharacterClass is
   the literal `b'\x00\x08'` inside the `spawned_character` branch of the
   entry builder. Replace **only slot 1's** value with
   `hero_loadout.character_class_bytes()`; slots 2..4 are the Marine bots and
   keep `b'\x00\x08'`. The `b'\x00\x00'` in the pre-character branch is the
   `Unknown` class and must not change.
4. `build_character_connection_links` — the `b'\x00\x08'` that precedes
   `b'\x01'` is `LoadoutSetup.Character`. Replace it with
   `hero_loadout.loadout_character_bytes()`. Leave the `b'\x01'` (the
   `ChildEntityIds` array size) and the three `ranged_uint32(0x01000002)`
   alone.
5. Optional, log only: `log('      CHARACTER SPAWN PROBE Marine -> %s' ...)`
   and `'      CHARACTER LINKS Marine slot 1 -> %s'` should print
   `hero_loadout.selected().display`.

No other byte in the file depends on the hero. With `CRUCIBLE_SOLO_HERO`
unset, all five edits are no-ops.

### `server/match_stub.py`

No change. The hub protocol has no character field, and the LAN path never
sends one. Do not invent one.

### `server/crucible_backend.py`

Add the missing live-services router. This is what unforces Marine in the
menu and stops the `LOADOUT_SET_FAIL` modal; it does not by itself change what
the shard spawns.

```python
import hero_loadout

def handle_playerdata(path, body):
    if path == "/playerdata/activeloadout":
        return hero_loadout.playerdata_activeloadout()      # GET and POST
    if path.startswith("/playerdata/activeloadout/"):
        return hero_loadout.playerdata_activeloadout()
    if path.startswith("/playerdata/loadouts/"):
        return hero_loadout.playerdata_loadouts(path.rsplit("/", 1)[1])
    if path.startswith("/playerdata/perkset/"):
        return hero_loadout.playerdata_perkset(path.split("/")[2])
    return None
```

and one entry in `ROUTERS`:
`("CrucibleLivePlayerDataService", handle_playerdata)`.

Two cautions carried over from `docs/09`: the handler must return `None` for
anything it does not recognise so the route still 404s rather than answering
with a wrong shape, and the backend process must be **restarted** after the
edit — a warm process silently serves the old shapes.

`server/crucible_data.py` needs no change. The character/perk unlock gates
were checked and are inert (see "What is ruled out").

## Open traces

- The in-match ability-bar component in `ui/dist/*.js`, and whether its icon
  mask is gated on native per-ability state. Cheapest next step.
- `EntityResourceCollectionComponent` (type 46) autonomous schema, 92 bits.
- Whether ability instantiation is authoritative-side, i.e. whether a
  network-spawned character can ever have a populated bar without the shard
  driving it.
- `{eb475e04-77d7-5c98-4cef-746f9e16d80d}`, the character-slice component
  holding the `<hero>_abilities.loadout` reference, is not in the recovered
  `{UUID}→name` pairs; its class name and network type are unknown.
- `{0ddfa8fc-fc5e-ddcf-7607-84e70cd8a2fb}`, present on every hunter except
  rocketeer, likewise unresolved. It only matters if rocketeer is ever wanted.
- Live acceptance of any non-Marine hunter. Nothing in this document has been
  observed in a running client.

## Addendum, same day: the ability bar located, and what that changes

This section closes the first item under "Open traces" above. It was produced
independently, read-only, against the same install; nothing was launched,
restarted, repacked or written outside this repository. It does **not** revise
the roster, the CharacterClass table or the hero-selection implementation, all
of which were re-derived and agree.

### The bar is `ui/dist/health-area.js`, not `hud.js`

The in-match ability bar is component class `Kt` in
`gamedata.pak ui/dist/health-area.js`. Its instance function `Ht` subscribes
exactly two stores and holds the slot order as a literal:

```js
function Ht(t,a,e){ let n,s;
  p(t, W,  t => e(0, n = t));     // hero key, hud.js export a5
  p(t, rt, t => e(1, s = t));     // slot map, reticle-container.js export e
  return [n, s, ["Ability3","Ability1","Ability2","Ability4"]] }
```

The render chain is `Gt` → `Mt` → `_t` → `Et` → `Dt`:

- `Gt` draws nothing at all while the hero key is falsy.
- `_t` is per slot and gates on `n = t[1].has(t[3])`. **A slot absent from the
  map renders nothing — no frame, no keybinding glyph.**
- `Et` passes `{action: slot, ability: map.get(slot) || false}` into `Dt`.
- `Dt`'s instance `Jt` destructures `{id, active, disabled, capacity, charges,
  recharge}` off that per-slot object and builds the icon as a CSS
  `background-image` of `shared/img/<hero>/abilities/<id>.svg`. When the path
  is not in the bundle manifest it emits
  `console.warn("Unknown ability icon, " + path)` and substitutes
  `shared/img/unknown-ability.svg`, which does ship in `gamedata.pak`.
  Only `timer` is read from the static `abilities.js` table; **`id` is server
  data.** An empty `id` produces `.../abilities/.svg`, i.e. the fallback.

### The map has exactly one writer, and it is the shard's data

`rt` is `reticle-container.js`'s `ur` (exported as `$r`, imported as `e`). The
sole writer is a native subscription:

```js
M.subscribe(({player: p}) => {
  ur.update(m => (m.clear(), m));
  if (!p) return fr.cancel("abilities");
  fr("abilities", j("abilities.updates", {object: p}, (err, res) => {
    if (err) return;
    const {abilities} = res;
    ur.update(m => { for (const slot in abilities) { const v = abilities[slot];
      if (slot === "MedPack") { v.charges < 0
          ? console.warn("abilities.updates sent a broken Medpack charges value! "
                         + JSON.stringify(v))
          : pr.set(v); }
      else m.set(slot, v); } return m; }); }));
});
```

`M` is `preloader.js` `a_` (export `o`), the `observed.updates` store. It only
sets `.player` when `object.get` answers `{player, types}` with
`types.includes("Player")`; a falsy object or the `"Nope!"` sentinel clears the
map and cancels the subscription. `MedPack` is split into its own store, which
is why the med-pack pip is not one of the four slots.

Evidence for the endpoints, from `.rdata` string triples
`<name>\0 <uuid>\0 <ClassName>\0`:

| Endpoint | UUID | Class | RVA |
|---|---|---|---|
| `abilities.updates` | `033225a0-90e9-55f9-6a63-29feba7376e5` | `AbilitiesUpdates` | `0x668cda0` |
| `observed.updates` | `63c7ac07-aae8-af29-cba3-323b9eed4d80` | `ObservedUpdates` | `0x668edc0` |
| `object.get` | `197d46c8-2931-33e0-7bb2-f564d7269d06` | `ObjectGetEndpoint` | `0x668ece8` |
| `players.updates` | `baa6fed3-261a-e603-a7f2-41211fd2fb3d` | `MatchPlayersUpdates` | `0x668eabe` |
| `resources.updates` | `d6e067c4-e097-a903-b13a-a871a7cb0319` | `ResourcesUpdates` | `0x668f3c0` |
| `bindings.updates` | `c7ec2bd0-0daf-aa35-3044-05686cbf53cc` | `BindingsUpdates` | `0x668d470` |

Eighty-six such triples exist; `tools/inspect_hero_abilityui.py --endpoints`
lists them all. The payload's `abilities` field name is reflected by
`AZExtensions::IValueReflector` at `0xc6c9b0` (the `lea` is `0xc6c9c2`), whose
failure strings are `"Unable to get required IValueReflector field '%s'"` and
`"Unable to reflect into required field %s"`.

### Authored slots, read from the assets rather than the JS

Each `.ability` asset carries its own `abilitySlot` string, and the native slot
vocabulary is the 8-value enum already documented above. Reading
`abilitySlot` out of every entry of every `<hero>_abilities.loadout`
(`tools/inspect_hero_abilityui.py --slots <hero>`) reproduces
`ui/dist/abilities.js` for all ten hunters — with one discrepancy that matters:

| Hunter | Authored HUD slots |
|---|---|
| Marine | PrimaryFire `marine_primary`, Ability1 `marine_bunker`, Ability2 `marine_grenade`, Ability3 `marine_sprint`, MedPack |
| Pyro | + Ability4 `pyro_fireball`, Reload `pyro_vent_heat` |
| Minigunner | + Ability4 `minigunner_upshift`, Reload `minigunner_reload` |
| Inventor | + Ability4 `inventor_grenade`, Reload `inventor_reload` |
| Duelist | Ability1..4 (`bubble`, `deflect`, `weapon_swap`, `disarming_Strike`), MedPack |
| Robotanist | Ability1..4 (`shield_burst`, `crop_dust`, `thrusters`, `seed_pod`), MedPack |
| Fighter | PrimaryFire + Ability1..4, MedPack |
| Hero | PrimaryFire + Ability1/2/4, Ability3 twice (`send_bot`, `bot_teleport`), MedPack |
| Saboteur | PrimaryFire + Ability1/2/3, Reload, MedPack |
| Commando | PrimaryFire + Ability1/2/3/4, Reload `commando_bandage` |

**Marine authors no `Ability4`.** `abilities.js` maps `marine_ads` to Ability4,
but `assets/player/marine/marine_aimdownsights.ability` sets
`abilitySlot = None` (and `Name = marine_ads`). Marine's `.ability` `Name`
fields are `marine_primary`, `marine_bunker`, `marine_grenade`,
`marine_sprint` — byte-for-byte the four `ui/dist/shared/img/marine/abilities/*.svg`
basenames. Also note `marine_sprint` is *absent* from the 336-string
`assets/bakednames/abilitynames.names` table even though its `.ability` and its
SVG both exist, so `id` is more likely the asset's `Name` than a baked-name
index; that distinction is not yet settled.

Marine's loadout has **fourteen** elements, not thirteen: the fourteenth is
`Assets/Player/_Common/stance_crouch.ability`, authored with mixed-case path
and `abilitySlot = None`. This corrects the count above; nothing else depends
on it.

### What this does to the two hypotheses

The screenshot reading in "The screenshot, and what it narrows to" — three
parallelogram slots carrying the **correct Marine keybindings** and no icon —
is now diagnostic, because `_t` proves a slot missing from the map renders
*nothing*, and the keybinding glyph exists only inside `Dt`. Three rendered
slots therefore means the map contained exactly three keys, and the three
Marine authors are precisely Ability1, Ability2 and Ability3. Ability4 is
legitimately absent for Marine and always would be.

That inverts the ranking above:

- Hypothesis 1 (*no ability instances exist at all*) does not fit. An empty
  map renders an empty bar, not three correctly-bound slots.
- Hypothesis 2 is the survivor, and is now specific rather than speculative:
  `abilities.updates` **is** publishing one object per authored slot, and the
  per-object `id` is empty or unresolvable, so every slot paints
  `unknown-ability.svg`.

This is an inference from the render chain plus one screenshot, not a live
observation. Two cheap ways to settle it without changing any server byte,
in order:

1. The client ships a debug cvar for exactly this. The strings at RVA
   `0x679b0ff` are `_Single_Endpoint`, *"The name of the UI endpoint to run,
   case matters. For example \"abilities.updates\" will let cooldowns update,
   but nothing else."* and *"If set, only update one UI endpoint at a time"*.
   Setting it to `abilities.updates` isolates the endpoint under test.
2. Look for `Unknown ability icon, shared/img/marine/abilities/.svg` in the
   HUD console on the next user-initiated launch. Its presence confirms
   hypothesis 2 and names the empty field; its absence sends us back to
   whether `Dt` rendered at all. It is not in any captured
   `NovaGameClient-*.log`, but those logs do not carry the embedded browser's
   console, so that is not evidence either way.

Only after one of those should anything be implemented. Nothing in this
addendum was implemented, and no protected file was touched.

### Corrections to "Open traces"

- The first item is closed: the component is `health-area.js` `Kt`, and the
  icon mask **is** gated on native per-ability state (`d.id`).
- `{eb475e04-77d7-5c98-4cef-746f9e16d80d}`, the slice component holding the
  `<hero>_abilities.loadout` reference, is very probably the game-side
  `AbilityComponent`, i.e. network type 6. Its serialized base chain in
  `marine_character.dynamicslice` is
  `eb475e04 → 4a945688-aa43-9d32-faf6-d40819b777a9 →
  a9158d2d-4652-becf-ffcc-89f73943b32a → db456c67-db95-a786-dfd1-02cacbbd519a →
  b7f5b743-ccd3-4981-8f1a-fc2b95ce22d7 → edfcb2cf-f75d-43be-b26b-f35821b29247`.
  The last is Lumberyard's `AZ::Component`; the one before it is the NovaNet
  network-component base (71 of the marine entity's 81 components derive from
  it). The two middle UUIDs sit in the same `.rdata` string block as
  `AbilityComponent::Common`, `RunningAbilities`, `ResetCount` and
  `LoadoutDataAsset {ebf055e3-81df-44df-bb73-70d3d8cca1a5}` — and `ebf055e3`
  is exactly the asset type id embedded in this component's
  `AbilityLoadoutContainer` element. Strong, but string-pool adjacency is not
  proof; the type index is still unconfirmed.
- Worth recording for the type-46 trace: the marine character entity has
  **71 network components**, while the accepted spawn declares 33 shared and 25
  autonomous roots. That is consistent (most have zero counts in a given
  direction) but it means any future per-component work should start from the
  71, not the 33.
