import json
import math
import tempfile
import unittest
from pathlib import Path

import dit_motion_policy


class DitMotionPolicyTests(unittest.TestCase):
    def write_document(self, root, tendencies):
        path = root / 'knowledge' / 'shared' / 'human-tendencies.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            'schemaVersion': 1,
            'source': 'dit-neuronetwork',
            'tendencies': tendencies,
        }), encoding='utf-8')

    @staticmethod
    def tendency(kind, *, approval='active', provisional=False,
                 games=None, distribution=None):
        return {
            'key': 'motion/%s-bout-seconds/field' % kind,
            'approval': approval,
            'tendency': {
                'provisional': provisional,
                'evidenceGames': ['crucible', 'other'] if games is None else games,
                'distribution': ({'p25': 1., 'median': 2., 'p75': 3.}
                                 if distribution is None else distribution),
            },
        }

    def test_reads_only_active_nonprovisional_crucible_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_document(root, [
                self.tendency('pause', approval='candidate'),
                self.tendency('pause', provisional=True),
                self.tendency('pause', games=['other']),
                self.tendency('pause'),
                self.tendency('move'),
            ])
            self.assertEqual(dit_motion_policy.approved_patrol_motion_ranges(root),
                             ((1., 3.), (1., 3.)))

    def test_missing_and_malformed_documents_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(dit_motion_policy.approved_patrol_motion_ranges(''),
                             (None, None))
            self.assertEqual(dit_motion_policy.approved_patrol_motion_ranges(root),
                             (None, None))
            path = root / 'knowledge' / 'shared' / 'human-tendencies.json'
            path.parent.mkdir(parents=True)
            path.write_text('[]', encoding='utf-8')
            self.assertEqual(dit_motion_policy.approved_patrol_motion_ranges(root),
                             (None, None))
            path.write_text(json.dumps({'schemaVersion': 1,
                                        'source': 'dit-neuronetwork',
                                        'tendencies': [None, {'key': 'x'}]}),
                            encoding='utf-8')
            self.assertEqual(dit_motion_policy.approved_patrol_motion_ranges(root),
                             (None, None))

    def test_rejects_out_of_order_nonfinite_and_out_of_range_values(self):
        invalid = [
            {'p25': 4., 'median': 3., 'p75': 5.},
            {'p25': .1, 'median': 1., 'p75': 2.},
            {'p25': 1., 'median': 2., 'p75': 21.},
            {'p25': 1., 'median': math.nan, 'p75': 3.},
            {'p25': True, 'median': 2., 'p75': 3.},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_document(root, [self.tendency('pause', distribution=value)
                                       for value in invalid]
                                + [self.tendency('move')])
            self.assertEqual(dit_motion_policy.approved_patrol_motion_ranges(root),
                             (None, (1., 3.)))


if __name__ == '__main__':
    unittest.main()
