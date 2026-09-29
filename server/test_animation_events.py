import json
import math
from pathlib import Path
import unittest

import animation_events
import hero_loadout


CAPTURE_DIR = Path(__file__).resolve().parents[1] / 'snapshots' / 'systems-20260922'
CAPTURES = ('live-animation-20260922T174627Z.json',
            'live-animation-20260922T174732Z.json')

# Name calls in retail shared serializer 0x7e3a10, independently joined to the
# captured property descriptor vector. AnimationSyncInfo occupies record 21
# and its six children occupy 22..27; registration order alone misses this.
SCALARS = (
    ('melee_swipe', 1, 'float'), ('emote', 2, 'float'),
    ('is_crouching', 3, 'bool'), ('is_dead', 4, 'bool'),
    ('using_medpack', 5, 'bool'), ('using_quick_melee', 6, 'bool'),
    ('using_primary2', 7, 'bool'), ('using_primary1', 8, 'bool'),
    ('stance', 9, 'float'), ('using_ability3', 10, 'bool'),
    ('using_ability2', 11, 'bool'), ('using_ability1', 12, 'bool'),
    ('is_active', 13, 'bool'), ('is_interacting', 14, 'bool'),
    ('time_since_movement_input', 16, 'float'), ('is_aiming', 17, 'bool'),
    ('ammo_ratio', 18, 'float'), ('spawn_ratio', 19, 'float'),
    ('using_aim_ik', 20, 'bool'), ('turn_angle', 28, 'float'),
    ('resource1_ratio', 29, 'float'), ('is_falling', 30, 'bool'),
    ('using_reload', 31, 'bool'),
)


class AnimationEncodingTests(unittest.TestCase):
    def build(self, **changes):
        arguments = dict(entity_id=0x01000003, shared_types=(13,),
                         values={'using_primary1': True,
                                 'time_since_movement_input': 1.5,
                                 'is_dead': False})
        arguments.update(changes)
        return animation_events.build_animation_update(**arguments)

    def test_property_contract_matches_the_verified_static_serializer_map(self):
        evidence = json.loads((CAPTURE_DIR / 'animation-wire-map.json').read_text())
        self.assertEqual(evidence['executable_sha256'],
                         'e28643747df4a4eccf6a0f5490a3a1c4ab0a6a75b2cee62d3c62f2b191d18203')
        self.assertEqual(evidence['shared_serializer_rva'], '0x7e3a10')
        self.assertEqual(tuple((row['name'], row['record_index'], row['kind'])
                               for row in evidence['properties']),
                         animation_events.ANIMATION_PROPERTIES)

    def test_scalar_selections_follow_both_saved_graphs_on_all_four_entities(self):
        matched = 0
        for capture in CAPTURES:
            for entity in json.loads((CAPTURE_DIR / capture).read_text())['entities']:
                for component in entity['components']:
                    if not component.get('getter_bytes', '').startswith('b00dc3'):
                        continue
                    matched += 1
                    graph = component['replication_graph']
                    self.assertEqual(len(graph), 34)
                    self.assertEqual(component['property_record_indices'],
                                     list(range(1, 22)) + list(range(28, 33)))
                    for selection in tuple((field,) for field in SCALARS) + (SCALARS,):
                        selected = {0, *(field[1] for field in selection)}
                        expected = []

                        def visit(node):
                            while node:
                                enabled = graph[node][0] in selected
                                expected.append(enabled)
                                if enabled and graph[node][2]:
                                    visit(graph[node][2])
                                node = graph[node][1]

                        visit(1)
                        self.assertEqual(len(expected), 27)
                        self.assertEqual(animation_events.animation_traversal_bits(
                            (13,), [field[0] for field in selection]), expected)
        self.assertEqual(matched, 8)

    def test_literal_three_property_update_uses_descriptor_order(self):
        message = self.build()
        self.assertEqual(message.header, bytes.fromhex('0c 01 000a'))
        self.assertEqual(message.payload, bytes.fromhex(
            '1b 11010100 02 3fc00000'))
        # Caller insertion order cannot change the native property order.
        self.assertEqual(message, self.build(values={
            'is_dead': False, 'time_since_movement_input': 1.5,
            'using_primary1': True}))

    def test_bool_byte_is_reused_across_two_intervening_float_fields(self):
        message = self.build(values={
            'is_dead': True, 'stance': -2., 'using_ability3': False,
            'time_since_movement_input': 1.5, 'is_falling': True,
            'using_reload': True})
        self.assertEqual(message.payload, bytes.fromhex(
            '1b 11060103 0d c0000000 3fc00000'))

    def test_ninth_selected_bool_allocates_after_the_intervening_float(self):
        values = {name: True for name, _, kind in SCALARS if kind == 'bool'}
        values['stance'] = 80.
        message = self.build(values=values)
        self.assertEqual(message.payload, bytes.fromhex(
            '1b f97f1203 ff 42a00000 7f'))

    def test_single_float_has_no_boolean_storage_and_preserves_signed_zero(self):
        self.assertEqual(self.build(values={'time_since_movement_input': 2.5}).payload,
                         bytes.fromhex('1b 01000100 40200000'))
        self.assertEqual(self.build(values={'turn_angle': -0.0}).payload,
                         bytes.fromhex('1b 01004000 80000000'))

    def test_unselected_components_are_absent_and_local_headers_chain(self):
        types = hero_loadout.SHARED_TYPES
        bits = animation_events.animation_traversal_bits(types, ('is_dead',))
        start = types.index(13)
        self.assertFalse(any(bits[:start]))
        self.assertFalse(any(bits[start + 27:]))
        self.assertEqual(len(bits), len(types) + 26)
        message = self.build(entity_id=0xfe00004b, shared_types=types,
                             local=True, previous_id=7)
        self.assertEqual(message.header[:3], bytes.fromhex('9202 01'))
        self.assertEqual(int.from_bytes(message.header[-2:], 'big'), len(message.payload))

    def test_values_have_no_implicit_defaults(self):
        with self.assertRaises(TypeError):
            animation_events.build_animation_update(0x01000003, (13,))

    def test_empty_unknown_and_unimplemented_properties_are_rejected(self):
        for values in ({}, {'velocity': (0., 0.)}, {'AnimationSyncInfo': ()},
                       {'ability_target1': (0., 0., 0.)}, {'AimDirection': 0},
                       {'IS_DEAD': False}, {1: 0}, None, [], [('is_dead', False)]):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    self.build(values=values)

    def test_float_fields_require_finite_representable_numbers(self):
        for name, _, kind in SCALARS:
            if kind != 'float':
                continue
            for value in (math.nan, math.inf, -math.inf, 1e40, 10 ** 1000,
                          True, '0', None):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        self.build(values={name: value})

    def test_bool_fields_reject_truthy_coercion(self):
        for name, _, kind in SCALARS:
            if kind != 'bool':
                continue
            for value in (0, 1, 2, 'false', None, math.nan):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        self.build(values={name: value})

    def test_component_layout_must_be_ascending_unique_and_include_animation(self):
        for types in ((), (10, 24), (13, 13), (24, 13), (-1, 13),
                      (13, 256), (13, 80.5), (True, 13)):
            with self.subTest(types=types):
                with self.assertRaises(ValueError):
                    self.build(shared_types=types)

    def test_entity_ids_and_category_are_validated(self):
        for changes in (dict(entity_id=0), dict(entity_id=0x01000000),
                        dict(entity_id=0x01ffffff), dict(entity_id=3),
                        dict(entity_id=0x02000003), dict(entity_id=0xfe000003),
                        dict(entity_id=0x01000003, local=True), dict(local=1),
                        dict(entity_id=True), dict(entity_id=-1),
                        dict(entity_id=0x100000000), dict(previous_id=-1),
                        dict(previous_id=0xffffff), dict(previous_id=True)):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.build(**changes)


if __name__ == '__main__':
    unittest.main()
