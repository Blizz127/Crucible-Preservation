import json
import math
from pathlib import Path
import unittest

import hero_loadout
import hit_reaction_events


CAPTURE_DIR = Path(__file__).resolve().parents[1] / 'snapshots' / 'systems-20260922'
CAPTURES = ('live-animation-20260922T174627Z.json',
            'live-animation-20260922T174732Z.json')


class HitReactionEncodingTests(unittest.TestCase):
    def build(self, **changes):
        arguments = dict(entity_id=0x01000003, shared_types=(80,),
                         index=0xa7, yaw=-2.5)
        arguments.update(changes)
        return hit_reaction_events.build_hit_reaction_update(**arguments)

    def test_both_fields_follow_the_saved_graphs_on_all_eight_entities(self):
        matched = 0
        for capture in CAPTURES:
            for entity in json.loads((CAPTURE_DIR / capture).read_text())['entities']:
                for component in entity['components']:
                    if not component.get('getter_bytes', '').startswith('b050c3'):
                        continue
                    matched += 1
                    graph = component['replication_graph']
                    self.assertEqual(len(graph), 4)
                    self.assertEqual(component['property_record_indices'], [1, 2])
                    selected = {0, 1, 2}
                    expected = []

                    def visit(node):
                        while node:
                            enabled = graph[node][0] in selected
                            expected.append(enabled)
                            if enabled and graph[node][2]:
                                visit(graph[node][2])
                            node = graph[node][1]

                    visit(1)
                    self.assertEqual(expected, [True, True, True])
                    self.assertEqual(hit_reaction_events.hit_reaction_traversal_bits(
                        (80,)), expected)
        self.assertEqual(matched, 8)

    def test_literal_payload_has_one_index_byte_then_big_endian_float(self):
        message = self.build()
        self.assertEqual(message.header, bytes.fromhex('0c 01 0007'))
        self.assertEqual(message.payload, bytes.fromhex('03 07 a7 c0200000'))

    def test_full_wire_range_and_explicit_zero_have_no_assigned_index_meaning(self):
        self.assertEqual(self.build(index=0, yaw=0.).payload,
                         bytes.fromhex('03 07 00 00000000'))
        self.assertEqual(self.build(index=255, yaw=-0.).payload,
                         bytes.fromhex('03 07 ff 80000000'))
        # This helper establishes wire representation, not angle units/range.
        self.assertEqual(self.build(index=1, yaw=1024).payload,
                         bytes.fromhex('03 07 01 44800000'))

    def test_unselected_components_and_relative_local_entity_header(self):
        types = hero_loadout.SHARED_TYPES
        bits = hit_reaction_events.hit_reaction_traversal_bits(types)
        start = types.index(80)
        self.assertFalse(any(bits[:start]))
        self.assertEqual(bits[start:start + 3], [True, True, True])
        self.assertFalse(any(bits[start + 3:]))
        self.assertEqual(len(bits), len(types) + 2)
        message = self.build(entity_id=0xfe00004b, shared_types=types,
                             previous_id=7, local=True)
        self.assertEqual(message.header[:3], bytes.fromhex('9202 01'))
        self.assertEqual(int.from_bytes(message.header[-2:], 'big'), len(message.payload))
        self.assertEqual(message.payload[-5:], bytes.fromhex('a7 c0200000'))

    def test_both_fields_are_required_without_defaults(self):
        for arguments in ({}, {'index': 1}, {'yaw': 0.}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(TypeError):
                    hit_reaction_events.build_hit_reaction_update(
                        0x01000003, (80,), **arguments)

    def test_index_rejects_values_outside_uint8_and_implicit_coercion(self):
        for index in (-1, 256, 1.5, True, '1', None, math.nan):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    self.build(index=index)

    def test_yaw_must_fit_a_finite_float32(self):
        for yaw in (math.nan, math.inf, -math.inf, 1e40, 10 ** 1000,
                    True, '0', None):
            with self.subTest(yaw=yaw):
                with self.assertRaises(ValueError):
                    self.build(yaw=yaw)

    def test_layout_must_be_ascending_unique_and_include_hit_reaction(self):
        for types in ((), (13, 24), (80, 80), (115, 80), (-1, 80),
                      (80, 256), (80, 120.5), (True, 80)):
            with self.subTest(types=types):
                with self.assertRaises(ValueError):
                    self.build(shared_types=types)

    def test_entity_id_and_category_validation(self):
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
