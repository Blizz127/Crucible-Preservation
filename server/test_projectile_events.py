import json
import math
from pathlib import Path
import unittest

import hero_loadout
import projectile_events


CAPTURE = (Path(__file__).resolve().parents[1] / 'snapshots' /
           'gamemode-20260919' / 'live-combat-layout.json')


class HybridActivationTests(unittest.TestCase):
    def build(self, **changes):
        arguments = dict(
            entity_id=0x01000003, shared_types=(115,), slot=0,
            allowed_projectile_index=4, fire_origin=(-4500., -4500., -1300.),
            ideal_origin=(0., 0., 0.), ideal_target_position=(4500., 4500., 1300.),
            relative_time_ms=0, has_first_update_hit=False, deflected=False,
            attachment_id=0x1234)
        arguments.update(changes)
        return projectile_events.build_hybrid_activation_update(**arguments)

    def test_every_slot_traversal_matches_the_captured_shared_graph(self):
        matched = 0
        for entity in json.loads(CAPTURE.read_text())['entities']:
            for component in entity['components']:
                if not component.get('getter_bytes', '').startswith('b073c3'):
                    continue
                matched += 1
                graph = component['replication_graph']
                self.assertEqual(len(graph), 43)
                self.assertEqual(component['property_record_indices'], [1])
                for slot in range(10):
                    selected = {0, 1, *range(2 + 4 * slot, 6 + 4 * slot)}
                    expected = []

                    def visit(node):
                        while node:
                            enabled = graph[node][0] in selected
                            expected.append(enabled)
                            if enabled and graph[node][2]:
                                visit(graph[node][2])
                            node = graph[node][1]

                    visit(1)
                    self.assertEqual(len(expected), 15)
                    self.assertEqual(projectile_events.activation_traversal_bits(
                        (115,), slot), expected)
        self.assertGreater(matched, 1)

    def test_literal_slot_zero_bytes_have_no_size_and_one_boolean_byte(self):
        message = self.build()
        self.assertEqual(message.header, bytes.fromhex('0c 01 0024'))
        self.assertEqual(message.payload, bytes.fromhex(
            '0f 3f00 04 0000000000000000 8000020000080000 ffffffffffffffff '
            '00 00 1234 01000003'))

    def test_literal_last_slot_nonzero_time_and_both_boolean_bits(self):
        message = self.build(slot=9, relative_time_ms=64,
                             has_first_update_hit=True, deflected=True)
        self.assertEqual(message.header, bytes.fromhex('0c 01 0025'))
        self.assertEqual(message.payload, bytes.fromhex(
            '0f 0378 04 0000000000000000 8000020000080000 ffffffffffffffff '
            '8001 03 1234 01000003'))

    def test_signed_time_uses_zigzag_uleb128(self):
        for delta, wire in ((0, '00'), (1, '02'), (-1, '01'), (63, '7e'),
                            (64, '8001'), (-65, '8101'),
                            (0x7ffffffe, 'fcffffff0f'),
                            (-0x7ffffffe, 'fbffffff0f')):
            with self.subTest(delta=delta):
                self.assertEqual(self.build(relative_time_ms=delta).payload[28:-7],
                                 bytes.fromhex(wire))

    def test_booleans_have_separate_bits_in_the_same_byte(self):
        for hit, deflected, value in ((False, False, 0), (True, False, 1),
                                      (False, True, 2), (True, True, 3)):
            message = self.build(has_first_update_hit=hit, deflected=deflected)
            self.assertEqual(message.payload[-7], value)
            self.assertEqual(len(message.payload), 36)

    def test_other_components_are_absent_and_entity_headers_chain(self):
        types = hero_loadout.SHARED_TYPES
        bits = projectile_events.activation_traversal_bits(types, 3)
        start = types.index(115)
        self.assertFalse(any(bits[:start]))
        self.assertFalse(any(bits[start + 15:]))
        self.assertEqual(len(bits), len(types) + 14)
        message = self.build(entity_id=0xfe00004b, shared_types=types,
                             local=True, previous_id=7)
        self.assertEqual(message.header[:3], bytes.fromhex('9202 01'))
        self.assertEqual(message.payload[-4:], bytes.fromhex('fe00004b'))
        self.assertEqual(int.from_bytes(message.header[-2:], 'big'), len(message.payload))

    def test_unresolved_field_values_have_no_implicit_defaults(self):
        with self.assertRaises(TypeError):
            projectile_events.build_hybrid_activation_update(0x01000003, (115,))

    def test_integer_fields_reject_wrong_types_and_ranges(self):
        for field, maximum in (('slot', 9), ('allowed_projectile_index', 15),
                               ('attachment_id', 0xffff), ('entity_id', 0xffffffff),
                               ('previous_id', 0xfffffe)):
            for value in (-1, maximum + 1, 1.5, True, '1'):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        self.build(**{field: value})

    def test_relative_time_rejects_invalid_sentinel_and_unsupported_ranges(self):
        for value in (-0x80000000, -0x7fffffff, 0x7fffffff, 0x80000000,
                      math.nan, math.inf, 1.5, True, '0'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.build(relative_time_ms=value)

    def test_position_vectors_are_finite_and_inside_the_packed_domain(self):
        for field in ('fire_origin', 'ideal_origin', 'ideal_target_position'):
            for vector in ((math.nan, 0., 0.), (0., math.inf, 0.),
                           (0., 0., -math.inf), (4500.01, 0., 0.),
                           (0., -4500.01, 0.), (0., 0., 1300.01),
                           (True, 0., 0.), ('0', 0., 0.), (), (0., 0.),
                           (0., 0., 0., 0.), None):
                with self.subTest(field=field, vector=vector):
                    with self.assertRaises(ValueError):
                        self.build(**{field: vector})

    def test_boolean_fields_reject_truthy_coercion(self):
        for field in ('has_first_update_hit', 'deflected', 'local'):
            for value in (0, 1, 2, 'false', None):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        self.build(**{field: value})

    def test_only_valid_ordered_component_layouts_are_accepted(self):
        for types in ((), (13, 24), (115, 115), (145, 115), (-1, 115),
                      (115, 256), (115, 120.5), (True, 115)):
            with self.subTest(types=types):
                with self.assertRaises(ValueError):
                    self.build(shared_types=types)

    def test_owner_id_must_agree_with_the_entity_category(self):
        for changes in (dict(entity_id=0), dict(entity_id=0x01000000),
                        dict(entity_id=0x01ffffff), dict(entity_id=3),
                        dict(entity_id=0x02000003), dict(entity_id=0xfe000003),
                        dict(entity_id=0x01000003, local=True)):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.build(**changes)


if __name__ == '__main__':
    unittest.main()
