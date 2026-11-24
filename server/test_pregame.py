import unittest

import pregame
import shard_stub


class ScheduleTests(unittest.TestCase):
    def test_a_table_without_a_requested_phase_runs_none(self):
        phases = [("first", 1, 1.0), ("last", 2, 2.0)]
        self.assertEqual(pregame.schedule(phases, ["first", "middle", "last"]), [])


class RosterClassTests(unittest.TestCase):
    def test_first_team_card_gets_the_selected_class_before_spawn(self):
        assigned = object()
        unassigned = object()
        team_card = object()
        another_phase = object()
        for spawned, enabled, initial, expected in [
            (False, False, team_card, unassigned),
            (False, True, another_phase, unassigned),
            (False, True, team_card, assigned),
            (True, False, another_phase, assigned),
        ]:
            with self.subTest(spawned=spawned, enabled=enabled, initial=initial):
                result = shard_stub.roster_class(
                    spawned, pregame_enabled=enabled, first_phase=lambda: initial,
                    team_info_phase=team_card, selected_class=lambda: assigned,
                    empty_class=unassigned,
                )
                self.assertIs(result, expected)

    def test_team_card_keeps_an_unknown_class_unassigned(self):
        def unknown_class():
            raise ValueError

        team_card = object()
        unassigned = object()
        result = shard_stub.roster_class(
            False, pregame_enabled=True, first_phase=lambda: team_card,
            team_info_phase=team_card, selected_class=unknown_class, empty_class=unassigned,
        )
        self.assertIs(result, unassigned)


if __name__ == "__main__":
    unittest.main()
