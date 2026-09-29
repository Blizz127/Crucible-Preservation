import json
import math
from pathlib import Path
import struct
import unittest

import combat
import death_events
from practice_population import DUMMY_SHARED_TYPES, SPITTER_SHARED_TYPES


CAPTURE = (Path(__file__).resolve().parents[1] / 'snapshots' /
           'gamemode-20260919' / 'live-combat-layout.json')


class DeathEncodingTests(unittest.TestCase):
    def build(self, **changes):
        arguments = dict(entity_id=0xfe00004b, shared_types=DUMMY_SHARED_TYPES,
                         source_name=0x1234, ability_name=0xabcd,
                         killer_id=0x01020304, maximum=600.0, value=0.0,
                         local=True)
        arguments.update(changes)
        return death_events.build_death_update(**arguments)

    def test_traversal_follows_every_captured_health_graph(self):
        # Select source metadata, killer and the health value group. Walk the
        # captured sibling/child links independently of the encoder's tuple.
        selected = {0, 2, 3, 23, 24, 26}
        matched = 0
        for entity in json.loads(CAPTURE.read_text())['entities']:
            for component in entity['components']:
                if not component.get('getter_bytes', '').startswith('b04fc3'):
                    continue
                matched += 1
                graph = component['replication_graph']
                self.assertEqual(component['property_record_indices'],
                                 [1, 2, 3, 4, 5, 23])
                self.assertEqual(graph[3][2], 0, 'metadata is an atomic leaf')
                expected = []

                def visit(node):
                    while node:
                        enabled = graph[node][0] in selected
                        expected.append(enabled)
                        if enabled and graph[node][2]:
                            visit(graph[node][2])
                        node = graph[node][1]

                visit(1)
                self.assertEqual(len(expected), 10)
                self.assertEqual(death_events.death_traversal_bits((79,)),
                                 expected)
                # The old health-only path still selects the same resource
                # branch and never writes either newly supported leaf.
                self.assertEqual([i for i, (a, b) in enumerate(zip(
                    combat.HEALTH_VALUE_BITS, expected)) if a != b], [2, 3])
        self.assertGreater(matched, 1)

    def test_exact_single_component_wire_bytes(self):
        message = self.build(shared_types=(79,))
        # 10 LSB-first decisions = cd 02. Metadata contributes bytes only;
        # adding child bits here shifts KillerId and corrupts the record.
        self.assertEqual(message.payload, bytes.fromhex(
            '0a cd02 1234 abcd 01020304 00000000 44160000 00000000'))

    def test_component_selection_and_relative_headers_for_both_id_kinds(self):
        for entity, types, local in ((0xfe00004b, DUMMY_SHARED_TYPES, True),
                                     (0x01000003, SPITTER_SHARED_TYPES, False)):
            with self.subTest(entity=entity):
                bits = death_events.death_traversal_bits(types)
                start = types.index(79)
                self.assertFalse(any(bits[:start]))
                self.assertFalse(any(bits[start + 10:]))
                self.assertEqual(len(bits), len(types) + 9)
                message = self.build(entity_id=entity, shared_types=types,
                                     local=local, previous_id=7)
                old = combat.build_health_update(entity, types, maximum=600,
                                                  value=0, local=local,
                                                  previous_id=7)
                self.assertEqual(message.header[:-2], old.header[:-2])
                self.assertEqual(int.from_bytes(message.header[-2:], 'big'),
                                 len(message.payload))
                self.assertEqual(len(message.payload), len(old.payload) + 8)

    def test_full_width_metadata_and_killer_are_preserved(self):
        message = self.build(source_name=0xffff, ability_name=0xffff,
                             killer_id=0xffffffff)
        self.assertEqual(message.payload[-20:-12], b'\xff' * 8)

    def test_explicit_zero_metadata_is_encoded_without_assigned_meaning(self):
        message = self.build(source_name=0, ability_name=0, killer_id=0,
                             value=600)
        self.assertEqual(message.payload[-20:],
                         struct.pack('>HHIfff', 0, 0, 0, 0, 600, 600))

    def test_metadata_has_no_implicit_defaults(self):
        with self.assertRaises(TypeError):
            death_events.build_death_update(1, (79,), maximum=600, value=0)

    def test_invalid_integer_fields_are_rejected(self):
        for field, maximum in (('source_name', 0xffff),
                               ('ability_name', 0xffff),
                               ('killer_id', 0xffffffff),
                               ('entity_id', 0xffffffff),
                               ('previous_id', 0xffffff)):
            for value in (-1, maximum + 1, 1.5, True, '1'):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        self.build(**{field: value})

    def test_invalid_health_is_rejected_before_serialization(self):
        for changes in (dict(value=-1), dict(value=601), dict(minimum=1),
                        dict(maximum=-1), dict(value=math.nan),
                        dict(maximum=math.inf), dict(minimum=-math.inf),
                        dict(maximum=1e100), dict(value=True), dict(value='0')):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.build(**changes)

    def test_component_layout_must_be_unique_and_in_ascending_order(self):
        for types in ((), (13, 24), (79, 79), (145, 79), (-1, 79),
                      (79, 256), (79, 80.5), (True, 79)):
            with self.subTest(types=types):
                with self.assertRaises(ValueError):
                    self.build(shared_types=types)

    def test_health_only_output_remains_unchanged(self):
        message = combat.build_health_update(1, (79,), maximum=600, value=0)
        self.assertEqual(message.payload,
                         bytes.fromhex('0a c102 00000000 44160000 00000000'))


if __name__ == '__main__':
    unittest.main()
