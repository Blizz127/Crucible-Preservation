import unittest

import health_plant as plant


class HealthPlantTests(unittest.TestCase):
    def test_overlapping_heals_run_side_by_side(self):
        amount, rate = 23., 2.5
        heals = [[amount, 0.], [amount, 3.]]
        first = plant.heal_owed(heals, 5., rate=rate)
        self.assertAlmostEqual(first, rate * 5 + rate * 2)
        self.assertAlmostEqual(plant.heal_owed(heals, 21., rate=rate), 2 * amount - first)
        self.assertEqual(heals, [])

    def test_field_identifiers_rest_before_reuse(self):
        fields = {'fields': {}, 'rested': {}}
        bounds = {'first_id': 31, 'count': 2, 'rest_seconds': 7}
        self.assertEqual(plant._field_id(fields, 0., **bounds), 31)
        fields['rested'][31] = 4.
        self.assertEqual(plant._field_id(fields, 6., **bounds), 32)
        self.assertEqual(plant._field_id(fields, 11., **bounds), 31)
        fields['fields'].update({31: {}, 32: {}})
        self.assertIsNone(plant._field_id(fields, 50., **bounds))


if __name__ == '__main__':
    unittest.main()
