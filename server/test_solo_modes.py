import unittest
from unittest.mock import patch
import shard_stub as shard
import solo_modes


# The exact bytes the two accepted profiles produced before solo_modes was
# split into map + mode profiles. Practice's arena run and the Alpha Hunters
# entry were both confirmed by the user, so these must never change.
ACCEPTED_PRACTICE = (
    'practicearena', 1289380704, 6, 123, 127, 133, 2,
    (501.09075927734375, 564.5346069335938, 45.27315902709961), ())
ACCEPTED_ALPHA_HUNTERS = (
    'april2018', 161882647, 1, 1656, 1660, 1666, 787,
    (1312.711181640625, 1271.156005859375, 23.670000076293945),
    ((1316.273193359375, 1257.5782470703125, 22.709447860717773),
     (1313.4710693359375, 1264.975341796875, 23.624998092651367),
     (1318.4801025390625, 1248.326904296875, 20.875)))


def wire_tuple(profile):
    return (profile.level, profile.level_hash, profile.configuration,
            profile.phase_entry, profile.mode_entry, profile.store_entry,
            profile.baked_counter_bias, profile.player_position,
            profile.bot_positions)


class SoloModeTests(unittest.TestCase):
    def test_additional_modes_use_their_shipped_configuration(self):
        for name, configuration in (('alpha-hunters', 1), ('solo-casual', 0)):
            with self.subTest(mode=name), patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE': name}):
                profile = solo_modes.selected()
                self.assertEqual(profile.configuration, configuration)
                self.assertEqual(profile.level, 'april2018')
                self.assertEqual(len(shard.build_solo_hunter_probes()), 3)
                self.assertEqual(shard.build_practice_gamemode_probe().payload[-2:],
                                 (32768 + configuration).to_bytes(2, 'big'))

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE':'heart-of-the-hives'})
    def test_hives_uses_own_baked_entities_and_configuration(self):
        m=shard.build_practice_gamemode_probe()
        counter,offset=shard.decode_entity_id(m.header)
        self.assertEqual(counter,2447)  # observed FE00098F; entry 1660
        self.assertEqual(int.from_bytes(m.header[offset+1:offset+5],'big'),161882647)
        self.assertEqual(m.payload[-2:],bytes.fromhex('8008'))
        m=shard.build_practice_match_store_probe()
        self.assertEqual(shard.decode_entity_id(m.header)[0],2453)  # FE000995

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE':'heart-of-the-hives'})
    def test_hunter_population_is_three_simulation_entities(self):
        messages=shard.build_solo_hunter_probes()
        previous=0
        for counter,message in zip((3,4,5),messages,strict=True):
            actual,offset=shard.decode_entity_id(message.header,previous_id=previous)
            self.assertEqual(actual,counter)
            self.assertEqual(message.header[offset],0x11)
            self.assertEqual(int.from_bytes(message.header[offset+1:offset+5],'big'),9256072)
            previous=actual
        self.assertEqual(len({m.payload[-12:] for m in messages}),3)

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE':'invalid'})
    def test_unknown_mode_is_not_silently_practice(self):
        with self.assertRaises(ValueError):solo_modes.selected()

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE':'heart-of-the-hives'})
    def test_spawned_roster_contains_local_player_and_three_hunters(self):
        message=shard.build_single_player_roster_probe(b'Local',spawned_character=True)
        bits,offset=shard.decode_varint(message.payload)
        offset+=(bits+7)//8
        self.assertEqual(message.payload[offset],4)
        for name in (b'Local',b'Hunter 1',b'Hunter 2',b'Hunter 3'):
            self.assertIn(name,message.payload)
        self.assertEqual(message.payload[-20:],bytes.fromhex(
            '0000000401000002010000030100000401000005'))

    @patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE':'heart-of-the-hives'})
    def test_roster_decodes_with_retail_shared_boolean_byte(self):
        # Retail reader 4f565d0 caches eight bools, even across intervening
        # integer/string reads. PlayerData's DropMaster is one such bool.
        payload = shard.build_single_player_roster_probe(
            b'Local', spawned_character=True).payload
        bits, offset = shard.decode_varint(payload)
        offset += (bits + 7) // 8
        count = payload[offset]
        offset += 1
        bool_index = 0
        for slot in range(1, count + 1):
            self.assertEqual(payload[offset], slot)
            offset += 1
            for expected in ((b'local-hunter' if slot == 1 else
                              ('solo-hunter-%d' % slot).encode()),
                             (b'Local' if slot == 1 else
                              ('Hunter %d' % (slot - 1)).encode())):
                size = payload[offset]
                offset += 1
                self.assertEqual(payload[offset:offset + size], expected)
                offset += size
            self.assertEqual(payload[offset:offset + 3], b'\0\0\x08')
            offset += 5  # name service, class, team, party
            if bool_index == 0:
                bool_byte = payload[offset]
                offset += 1
            self.assertEqual(bool(bool_byte & (1 << bool_index)), slot == 1)
            bool_index = (bool_index + 1) % 8
            self.assertEqual(payload[offset:offset + 3], b'\x7f\xff\x03')
            offset += 3
        self.assertEqual(int.from_bytes(payload[offset:offset + 4], 'big'), count)
        offset += 4
        self.assertEqual(len(payload) - offset, count * 4)


class AcceptedProfilePreservationTests(unittest.TestCase):
    """The two user-accepted profiles must stay byte-identical."""

    def test_practice_wire_fields_are_unchanged(self):
        self.assertEqual(wire_tuple(solo_modes.PRACTICE), ACCEPTED_PRACTICE)
        with patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE': 'practice'}):
            self.assertEqual(wire_tuple(solo_modes.selected()), ACCEPTED_PRACTICE)
            self.assertEqual(solo_modes.selected(), solo_modes.PRACTICE)

    def test_alpha_hunters_wire_fields_are_unchanged(self):
        self.assertEqual(wire_tuple(solo_modes.ALPHA_HUNTERS),
                         ACCEPTED_ALPHA_HUNTERS)
        with patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE': 'alpha-hunters'}):
            self.assertEqual(wire_tuple(solo_modes.selected()),
                             ACCEPTED_ALPHA_HUNTERS)

    def test_practice_serialisation_is_byte_identical(self):
        with patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE': 'practice'}):
            gamemode = shard.build_practice_gamemode_probe()
            store = shard.build_practice_match_store_probe()
            phase_counter = (solo_modes.PRACTICE.phase_entry
                             + solo_modes.PRACTICE.baked_counter_bias)
            roster = shard.build_single_player_roster_probe(b'Local')
        self.assertEqual(shard.decode_entity_id(gamemode.header)[0], 129)
        self.assertEqual(gamemode.payload[-2:], bytes.fromhex('8006'))
        self.assertEqual(shard.decode_entity_id(store.header)[0], 135)
        self.assertEqual(phase_counter, 125)
        # One roster entry only: Practice's mode profile has roster_size 1.
        bits, offset = shard.decode_varint(roster.payload)
        offset += (bits + 7) // 8
        self.assertEqual(roster.payload[offset], 1)

    def test_practice_keeps_the_authored_npc_path(self):
        # shard_stub chooses build_practice_npc_probes() when bot_positions is
        # empty; practicearena must therefore never gain bot positions.
        self.assertEqual(solo_modes.PRACTICE_ARENA.bot_positions, ())
        self.assertEqual(solo_modes.PRACTICE.bot_positions, ())


class LevelHashTests(unittest.TestCase):
    """The hash function was recovered from the shipped string hash library."""

    def test_shipped_level_hashes_are_reproduced(self):
        for level, shipped in (('practicearena', 1289380704),
                               ('april2018', 161882647),
                               ('mainmenu', 3193911061)):
            with self.subTest(level=level):
                self.assertEqual(solo_modes.level_hash(level), shipped)

    def test_other_shipped_library_entries_are_reproduced(self):
        # Two further Key/Value pairs read straight out of
        # levels/practicearena/commondata/stringhashlibrary.xml.
        self.assertEqual(
            solo_modes.level_name_hash('slices/player/marine/marine_character'),
            9256072)
        self.assertEqual(solo_modes.level_name_hash('math_node_test'), 259982)

    def test_computed_hashes_are_flagged_as_computed(self):
        for profile in (solo_modes.HOTH_02, solo_modes.TUTORIAL_02,
                        solo_modes.TUTORIAL_03):
            with self.subTest(level=profile.level):
                self.assertEqual(profile.level_hash_source, 'computed')
                self.assertEqual(profile.level_hash,
                                 solo_modes.level_hash(profile.level))
        for profile in (solo_modes.PRACTICE_ARENA, solo_modes.APRIL2018):
            with self.subTest(level=profile.level):
                self.assertEqual(profile.level_hash_source, 'shipped')

    def test_case_folding_matches_the_library(self):
        # The library stores lowercase paths; the installed directory is
        # HotH_02, so the hash must be case insensitive.
        self.assertEqual(solo_modes.level_hash('HotH_02'),
                         solo_modes.level_hash('hoth_02'))

    def test_mixed_case_shipped_entries_pin_the_lowercasing(self):
        """The only shipped pairs that can tell lowercased from verbatim.

        Every path-like Value in the libraries is already lowercase, so those
        pairs fit either reading. These twelve are the exceptions: the only
        mixed-case Values shipped, all from
        levels/tutorial_0{2,3}/commondata/stringhashlibrary.xml. Each is
        recorded here with its shipped Key and with the value a verbatim
        (non-lowercasing) hash would produce. This is what decides HotH_02's
        key, so a regression to verbatim hashing must fail loudly.
        """
        shipped_and_verbatim = (
            ('GameModeEntity', 1308711974, 981657145),
            ('DialogueMatchEntity', 3964390961, 216694779),
            ('ParticlesCoordinatorEntity', 662212455, 3321349490),
            ('TutorialObjectives', 2373800588, 2278911964),
            ('R_wrist_JNT', 3489153273, 803660853),
            ('Speed', 4187447851, 2054727018),
            ('StandaloneAutoStruct.WeaponParams.Human.Rifle',
             3152421969, 963005760),
            ('StandaloneAutoStruct.WeaponParams.Human.RocketLauncher',
             2892908496, 332345875),
            ('StandaloneAutoStruct.ClientEffect.Impact.Rifle',
             69491423, 4271036982),
            ('StandaloneAutoStruct.ClientEffect.Impact.Rocket',
             3769309770, 1735117255),
            ('StandaloneAutoStruct.ClientEffect.Damage.Rifle',
             2941161597, 3052919238),
            ('StandaloneAutoStruct.ClientEffect.Activate.Rifle',
             3591207792, 2959555569),
        )
        for value, shipped, verbatim in shipped_and_verbatim:
            with self.subTest(value=value):
                self.assertEqual(solo_modes.level_name_hash(value), shipped)
                self.assertNotEqual(shipped, verbatim)

    def test_hoth_02_uses_the_lowercased_path_hash(self):
        # 1770556851 = hash('levels/hoth_02/commondata/level'); the
        # case-preserving alternative 4142557249 is the documented retry only.
        self.assertEqual(solo_modes.level_hash('HotH_02'), 1770556851)
        self.assertNotEqual(solo_modes.level_hash('HotH_02'), 4142557249)


class MapProfileTests(unittest.TestCase):

    def test_every_map_reports_its_asset_entity_counts(self):
        expected = {'practicearena': 1198, 'april2018': 19529,
                    'HotH_02': 21510, 'tutorial_02': 2303, 'tutorial_03': 2400}
        for level, count in expected.items():
            with self.subTest(level=level):
                self.assertEqual(
                    solo_modes.MAPS[level.lower()].entity_array_length, count)

    def test_baked_entries_are_inside_the_entity_array(self):
        for profile in solo_modes.MAPS.values():
            with self.subTest(level=profile.level):
                for entry in (profile.phase_entry, profile.mode_entry,
                              profile.store_entry,
                              profile.match_components_entry,
                              profile.player_drop_entry,
                              *profile.bot_drop_entries):
                    self.assertLess(entry, profile.entity_array_length)
                self.assertLess(profile.phase_entry, profile.mode_entry)
                self.assertLess(profile.mode_entry, profile.store_entry)

    def test_only_measured_maps_claim_a_live_bias(self):
        measured = {level for level, profile in solo_modes.MAPS.items()
                    if profile.baked_counter_bias_source == 'live'}
        self.assertEqual(measured, {'practicearena', 'april2018'})
        self.assertEqual(solo_modes.PRACTICE_ARENA.baked_counter_bias, 2)
        self.assertEqual(solo_modes.APRIL2018.baked_counter_bias, 787)

    def test_only_hoth02_has_authored_hive_spawns(self):
        self.assertEqual(len(solo_modes.HOTH_02.hive_spawns), 6)
        self.assertEqual(
            {name for name, _, _ in solo_modes.HOTH_02.hive_spawns},
            {'SWAMP', 'LOG', 'RESEARCH', 'GROTTO', 'FALLS', 'SPACEPORT'})
        self.assertEqual(solo_modes.APRIL2018.hive_spawns, ())

    def test_authored_spawn_transforms_are_distinct_and_finite(self):
        for profile in solo_modes.MAPS.values():
            with self.subTest(level=profile.level):
                places = (profile.player_position,) + profile.bot_positions
                self.assertEqual(len(set(places)), len(places))
                self.assertEqual(len(profile.bot_positions),
                                 len(profile.bot_drop_entries))
                for place in places:
                    self.assertEqual(len(place), 3)
                    self.assertTrue(all(abs(v) < 5000 for v in place[:2]))
                    self.assertTrue(abs(place[2]) < 1300)


class ModeProfileTests(unittest.TestCase):
    """design/gamemodes/gamemodes.gamemodeconfiglist, in shipped array order."""

    SHIPPED_LIST = {
        0: 'SOLO_CASUAL', 1: 'DOUBLES_CASUAL', 2: 'SOLO_BOTS',
        3: 'CRUCIHEARTS', 4: 'CONQUEST_8V8', 5: 'TUTORIAL', 6: 'PRACTICE',
        7: '1v1', 8: 'HEART_OF_THE_HIVES', 9: 'HOTH_GUIDE',
        10: 'HOTH_MINIMAL', 11: 'TUTORIAL2',
    }

    def test_configuration_indices_match_the_shipped_list(self):
        actual = {mode.configuration: mode.name
                  for mode in solo_modes.MODES.values()}
        self.assertEqual(actual, self.SHIPPED_LIST)

    def test_minimum_players_are_the_shipped_values(self):
        expected = {'SOLO_CASUAL': 8, 'DOUBLES_CASUAL': 16, 'SOLO_BOTS': 8,
                    'CRUCIHEARTS': 8, 'CONQUEST_8V8': 16, 'TUTORIAL': 1,
                    'PRACTICE': 1, '1v1': 3, 'HEART_OF_THE_HIVES': 8,
                    'HOTH_GUIDE': 1, 'HOTH_MINIMAL': 8, 'TUTORIAL2': 1}
        for mode in solo_modes.MODES.values():
            with self.subTest(mode=mode.name):
                self.assertEqual(mode.minimum_players, expected[mode.name])

    def test_hoth_guide_is_the_only_single_player_hives_configuration(self):
        hives = [m for m in solo_modes.MODES.values()
                 if m.ui_mode_override == 'HEART_OF_THE_HIVES']
        self.assertEqual({m.name for m in hives},
                         {'HEART_OF_THE_HIVES', 'HOTH_GUIDE', 'HOTH_MINIMAL'})
        solo = [m for m in hives if m.minimum_players == 1]
        self.assertEqual([m.name for m in solo], ['HOTH_GUIDE'])
        self.assertIn('GuideHeartOfTheHives', solo_modes.MODE_HOTH_GUIDE.flags)

    def test_hoth_phase_order_is_the_shipped_nine_phase_sequence(self):
        self.assertEqual(
            [name for name, _, _ in
             solo_modes.MODE_HEART_OF_THE_HIVES.phase_sequence],
            ['WaitForPlayers', 'PerkPreview', 'TeamInfo', 'PerkSelect',
             'DropPreview', 'DropSelect', 'GamePlay', 'GameOver', 'Shutdown'])

    def test_phase_enum_values_agree_with_the_retail_name_table(self):
        self.assertEqual(solo_modes.MATCH_PHASE['GamePlay'], 8)
        self.assertEqual(solo_modes.SOLO_PHASE, 8)
        for mode in solo_modes.MODES.values():
            with self.subTest(mode=mode.name):
                for name, value, _ in mode.phase_sequence:
                    self.assertEqual(solo_modes.MATCH_PHASE[name], value)

    def test_match_score_data_for_the_scoring_modes(self):
        for name, score, threshold in (('HEART_OF_THE_HIVES', 3, 2),
                                       ('HOTH_GUIDE', 3, 2),
                                       ('HOTH_MINIMAL', 3, 2),
                                       ('CRUCIHEARTS', 3, 2),
                                       ('1v1', 10, 9),
                                       ('PRACTICE', 0, 0)):
            mode = next(m for m in solo_modes.MODES.values() if m.name == name)
            with self.subTest(mode=name):
                self.assertEqual(mode.target_score, score)
                self.assertEqual(mode.match_point_threshold, threshold)

    def test_modes_needing_the_solo_phase_bypass_are_identified(self):
        bypass = {m.name for m in solo_modes.MODES.values()
                  if m.waits_for_human_players}
        self.assertNotIn('PRACTICE', bypass)
        self.assertNotIn('TUTORIAL', bypass)
        self.assertNotIn('TUTORIAL2', bypass)
        self.assertIn('HEART_OF_THE_HIVES', bypass)
        self.assertIn('DOUBLES_CASUAL', bypass)
        # HOTH_GUIDE still has a WaitForPlayers phase but needs one player.
        self.assertNotIn('HOTH_GUIDE', bypass)

    def test_practice_mode_is_authored_only_for_the_arena(self):
        self.assertEqual(solo_modes.MODE_PRACTICE.authored_maps,
                         ('practicearena',))
        self.assertEqual(solo_modes.MODE_HEART_OF_THE_HIVES.authored_maps,
                         ('HotH_02',))


class SelectionTests(unittest.TestCase):

    def test_every_named_selection_composes(self):
        for name in solo_modes.SELECTIONS:
            with self.subTest(mode=name), \
                 patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE': name}):
                profile = solo_modes.selected()
                self.assertIn(profile.level.lower(), solo_modes.MAPS)
                self.assertEqual(
                    shard.build_practice_gamemode_probe().payload[-2:],
                    (32768 + profile.configuration).to_bytes(2, 'big'))
                counter, offset = shard.decode_entity_id(
                    shard.build_practice_gamemode_probe().header)
                self.assertEqual(counter,
                                 profile.mode_entry + profile.baked_counter_bias)

    def test_named_selections_cover_every_shipped_mode(self):
        selected = {mode.configuration
                    for _, mode in solo_modes.SELECTIONS.values()}
        self.assertEqual(selected, set(range(12)))

    def test_explicit_level_and_mode_pairs(self):
        with patch.dict('os.environ',
                        {'CRUCIBLE_SOLO_MODE': 'HotH_02:hoth-guide'}):
            profile = solo_modes.selected()
        self.assertEqual(profile.level, 'HotH_02')
        self.assertEqual(profile.configuration, 9)
        self.assertEqual(profile.phase_entry, 617)

    def test_explicit_pairs_accept_the_shipped_mode_name(self):
        with patch.dict('os.environ',
                        {'CRUCIBLE_SOLO_MODE': 'tutorial_03:TUTORIAL2'}):
            profile = solo_modes.selected()
        self.assertEqual(profile.level, 'tutorial_03')
        self.assertEqual(profile.configuration, 11)

    def test_unknown_level_and_unknown_mode_both_raise(self):
        for value in ('nope', 'nope:practice', 'HotH_02:nope'):
            with self.subTest(value=value), \
                 patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE': value}):
                with self.assertRaises(ValueError):
                    solo_modes.selected()

    def test_bias_override_applies_without_touching_anything_else(self):
        with patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE': 'hoth-guide',
                                       'CRUCIBLE_BAKED_BIAS': '787'}):
            profile = solo_modes.selected()
        self.assertEqual(profile.baked_counter_bias, 787)
        self.assertEqual(profile.phase_entry, 617)
        self.assertEqual(profile.level_hash, solo_modes.level_hash('HotH_02'))

    def test_level_hash_override_supports_the_hash_safe_retry(self):
        with patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE': 'hoth-guide',
                                       'CRUCIBLE_LEVEL_HASH': '161882647'}):
            profile = solo_modes.selected()
        self.assertEqual(profile.level_hash, 161882647)
        self.assertEqual(profile.level, 'HotH_02')

    def test_roster_size_bounds_the_spawned_bot_count(self):
        for name, bots in (('practice', 0), ('one-v-one', 1),
                           ('alpha-hunters', 3), ('hoth-guide', 3),
                           ('tutorial', 3)):
            with self.subTest(mode=name), \
                 patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE': name}):
                profile = solo_modes.selected()
                self.assertEqual(len(profile.bot_positions), bots)
                self.assertEqual(profile.roster_size, bots + 1)

    def test_new_map_profiles_drive_the_baked_probe_headers(self):
        for name, phase, mode, store in (('hoth-guide', 617, 621, 627),
                                         ('tutorial', 57, 61, 67),
                                         ('tutorial2', 57, 60, 66)):
            with self.subTest(mode=name), \
                 patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE': name}):
                profile = solo_modes.selected()
                self.assertEqual(
                    (profile.phase_entry, profile.mode_entry,
                     profile.store_entry), (phase, mode, store))
                message = shard.build_practice_match_store_probe()
                self.assertEqual(
                    shard.decode_entity_id(message.header)[0],
                    store + profile.baked_counter_bias)
                self.assertEqual(
                    int.from_bytes(message.header[
                        shard.decode_entity_id(message.header)[1] + 1:
                        shard.decode_entity_id(message.header)[1] + 5], 'big'),
                    profile.level_hash)

    def test_teams_are_separated_for_every_multi_entry_roster(self):
        for name, mode in solo_modes.SELECTIONS.values():
            with self.subTest(mode=mode.name):
                if mode.roster_size > 1:
                    self.assertNotEqual(mode.local_team, mode.bot_team)


class BotSlotTests(unittest.TestCase):
    """One slot list feeds the roster, the spawns, the arena and the squad."""

    def test_the_default_slots_are_the_marine_baseline(self):
        for name in ('alpha-hunters', 'hoth-guide', 'hoth-guide-april2018', 'practice'):
            with self.subTest(mode=name), \
                    patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE': name}, clear=True):
                profile = solo_modes.selected()
                slots = shard.solo_bot_slots()
                self.assertEqual([s.entity for s in slots],
                                 list(shard.HUNTER_BOT_IDS[:len(profile.bot_positions)]))
                self.assertEqual([s.position for s in slots], list(profile.bot_positions))
                self.assertTrue(all((s.hero, s.team, s.health_max) == ('marine', 2, 800.)
                                    for s in slots))

    def test_bot_hero_names_only_bots(self):
        with patch.dict('os.environ', {'CRUCIBLE_SOLO_MODE': 'alpha-hunters'}, clear=True):
            self.assertEqual(shard.bot_hero(0x01000003), 'marine')
            self.assertIsNone(shard.bot_hero(shard.PLAYER_CHARACTER_ID))
