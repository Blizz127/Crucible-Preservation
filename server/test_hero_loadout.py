"""Hero selection and ability metadata.

These are wire-format and table checks. Nothing here observes a running
client, so nothing here certifies that a non-Marine hunter actually spawns.
"""
import os
import re
import sys
import unittest
from unittest.mock import patch

import hero_loadout as heroes
import shard_stub as shard

GAME = os.environ.get(
    'CRUCIBLE_DIR',
    os.path.expanduser('~/.local/share/Steam/steamapps/common/Crucible'))


class SelectionTests(unittest.TestCase):
    def test_default_is_the_accepted_marine_baseline(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(heroes.ENV, None)
            hero = heroes.selected()
        self.assertEqual(hero.name, 'marine')
        self.assertEqual(hero.slice_hash, 9256072)
        self.assertEqual(hero.slice_entry, 0)
        self.assertEqual(heroes.character_class_bytes(hero), b'\x00\x08')

    def test_marine_matches_the_constants_already_in_the_shard(self):
        """Wiring hero_loadout in must not move a single accepted byte."""
        marine = heroes.get('marine')
        spawn = shard.build_practice_character_probe()
        self.assertEqual(int.from_bytes(spawn.header[2:6], 'big'),
                         marine.slice_hash)
        self.assertEqual(spawn.header[6], marine.slice_entry)
        # The roster probe's CharacterClass and the connection's
        # LoadoutSetup.Character are both the same two accepted bytes.
        roster = shard.build_single_player_roster_probe(
            b'Hunter', spawned_character=True)
        self.assertIn(heroes.character_class_bytes(marine), roster.payload)
        links = shard.build_character_connection_links()
        self.assertEqual(links.payload[6:8],
                         heroes.loadout_character_bytes(marine))

    def test_environment_selects_another_hunter(self):
        with patch.dict(os.environ, {heroes.ENV: 'Saboteur'}):
            hero = heroes.selected()
        self.assertEqual(hero.name, 'saboteur')
        self.assertEqual(hero.character_class, 12)
        self.assertEqual(hero.slice_hash, 2391706089)
        self.assertEqual(heroes.character_class_bytes(hero), b'\x00\x0c')
        self.assertEqual(hero.slice_path,
                         'slices/player/saboteur/saboteur_character')

    def test_blank_selection_falls_back_without_raising(self):
        with patch.dict(os.environ, {heroes.ENV: ''}):
            self.assertEqual(heroes.selected().name, 'marine')

    def test_unknown_or_unsupported_hunter_is_refused(self):
        for name in ('technomancer', 'rocketeer', 'spectator', 'bucket', None):
            with self.assertRaises(ValueError):
                heroes.get(name)
        with patch.dict(os.environ, {heroes.ENV: 'rocketeer'}):
            with self.assertRaises(ValueError):
                heroes.selected()

    def test_every_class_value_is_unique_and_in_the_native_table(self):
        seen = {}
        for name in heroes.names():
            hero = heroes.get(name)
            self.assertEqual(hero.character_class,
                             heroes.CHARACTER_CLASS[name])
            self.assertNotIn(hero.character_class, seen)
            seen[hero.character_class] = name
            # A full-range uint16 enum: never the Unknown/None sentinels.
            self.assertGreater(hero.character_class, 1)
            self.assertLess(hero.character_class, 1 << 16)

    def test_roster_order_is_the_shipped_asset_order(self):
        self.assertEqual(
            heroes.names(),
            ('marine', 'pyro', 'minigunner', 'inventor', 'duelist',
             'robotanist', 'fighter', 'hero', 'saboteur', 'commando'))
        self.assertEqual([heroes.get(n).roster_index for n in heroes.names()],
                         list(range(10)))


class ComponentLayoutTests(unittest.TestCase):
    def test_shared_and_autonomous_roots_match_the_live_marine_capture(self):
        self.assertEqual(len(heroes.SHARED_TYPES), 33)
        self.assertEqual(heroes.AUTONOMOUS_ROOTS, 25)
        self.assertEqual(list(heroes.SHARED_TYPES),
                         sorted(set(heroes.SHARED_TYPES)))
        # The shard's own spawn builder still uses exactly this layout.
        spawn = shard.build_practice_character_probe()
        record_bits = spawn.payload[0]
        self.assertEqual(record_bits, len(heroes.SHARED_TYPES) + 5 + 2)


class AbilityTableTests(unittest.TestCase):
    SLOTS = ('PrimaryFire', 'Ability1', 'Ability2', 'Ability3', 'Ability4')

    def test_every_selectable_hunter_has_a_full_hud_slot_set(self):
        for name in heroes.names():
            slots = heroes.hud_ability_slots(heroes.get(name))
            self.assertEqual(len(slots), 5, name)
            self.assertEqual(sorted(slot for _, slot in slots),
                             sorted(self.SLOTS), name)

    def test_slot_ability_ids_are_hero_prefixed_icon_names(self):
        for name in heroes.names():
            for ability, _slot in heroes.hud_ability_slots(heroes.get(name)):
                for one in (ability if isinstance(ability, tuple) else (ability,)):
                    # hud.js builds url(shared/img/<hero>/abilities/<id>.svg)
                    self.assertTrue(one.startswith(name + '_'), (name, one))
                    self.assertTrue(re.fullmatch(r'[a-z0-9_]+', one), one)

    def test_authored_loadouts_are_non_empty_and_share_the_common_abilities(self):
        for name in heroes.names():
            hero = heroes.get(name)
            self.assertGreaterEqual(len(hero.abilities), 12, name)
            self.assertIn('_common/usemedpack', hero.abilities, name)
            self.assertIn('character/interact', hero.abilities, name)
            self.assertTrue(hero.loadout_asset.endswith('.loadout'), name)


class BackendPayloadTests(unittest.TestCase):
    def test_activeloadout_names_the_selected_hunter(self):
        with patch.dict(os.environ, {heroes.ENV: 'pyro'}):
            payload = heroes.playerdata_activeloadout()
        # menu.js reads t.loadout.character.toLowerCase()
        self.assertEqual(payload['loadout']['character'].lower(), 'pyro')

    def test_loadouts_payload_has_every_field_menu_js_destructures(self):
        payload = heroes.playerdata_loadouts('Marine')
        self.assertEqual(len(payload['loadouts']), 1)
        entry = payload['loadouts'][0]
        for key in ('character', 'skin', 'dropPodSkin', 'dropPodDecals',
                    'wheel1', 'wheel2', 'wheel3'):
            self.assertIn(key, entry)
        self.assertEqual(entry['skin'], 'marine_t1_default_01')

    def test_perkset_payload_shape(self):
        payload = heroes.playerdata_perkset('Marine')
        self.assertEqual(payload['perkSet']['character'], 'Marine')
        self.assertEqual(payload['perkSet']['perks'], [])


@unittest.skipUnless(os.path.isdir(GAME), 'game install not present')
class InstalledAssetTests(unittest.TestCase):
    """Re-derive the table from the read-only install; nothing is written."""

    @classmethod
    def setUpClass(cls):
        tools = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'tools')
        sys.path.insert(0, tools)
        import inspect_hero_roster
        cls.inspect = inspect_hero_roster

    def test_slice_hashes_match_every_shipped_level(self):
        for level in ('practicearena', 'april2018', 'HotH_02'):
            found = self.inspect.slice_hashes(level)
            for name in heroes.names():
                hero = heroes.get(name)
                self.assertEqual(found.get(hero.slice_path), hero.slice_hash,
                                 '%s in %s' % (name, level))

    def test_character_class_values_match_the_executable_table(self):
        table = self.inspect.class_table()
        for name in heroes.names():
            hero = heroes.get(name)
            self.assertEqual(table[hero.character_class], hero.display, name)

    def test_selectable_slices_keep_marines_replicated_component_set(self):
        sets = self.inspect.character_components()
        marine = set(sets['marine'])
        # The only tolerated differences carry no replication record bit:
        # "cloth" is not a network component at all, and
        # AudioJointComponent::Client (type 16) has zero expanded bits.
        inert = {'ac9b8fa0-a6da-4377-8219-25ba7e4a22e8',
                 '2aa86eeb-4b0c-9b17-15d1-f41791b3f79c'}
        for name in heroes.names():
            other = set(sets[name])
            self.assertEqual(marine - other, set(), name)
            self.assertLessEqual(other - marine, inert, name)


if __name__ == '__main__':
    unittest.main()
