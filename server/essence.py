"""Track individual and team awards using caller-supplied progression thresholds."""

import math
from dataclasses import dataclass, field


U32 = 0xffffffff
START_LEVEL = 1


def marginal(level, table):
    if level < 1:
        raise ValueError('levels start at 1')
    return table[min(level, len(table)) - 1]


def total_for_level(level, table, max_level):
    if not 1 <= level <= max_level:
        raise ValueError('level outside 1..%d' % max_level)
    return sum(marginal(step, table) for step in range(1, level))


def level_for(essence, table, max_level):
    if essence < 0:
        raise ValueError('essence is not negative')
    level, total = START_LEVEL, 0
    while level < max_level and essence >= total + marginal(level, table):
        total += marginal(level, table)
        level += 1
    return level


@dataclass
class Account:
    essence: int = 0
    level: int = START_LEVEL


@dataclass
class Books:
    table: tuple
    max_level: int
    players: dict = field(default_factory=dict)
    teams: dict = field(default_factory=dict)
    levels_reached: list = field(default_factory=list)

    def grant(self, player, team, amount, reason, now):
        if (isinstance(amount, bool) or not isinstance(amount, (int, float))
                or not math.isfinite(amount) or amount <= 0 or amount != int(amount)):
            raise ValueError('an essence grant is a positive whole number')
        if (isinstance(now, bool) or not isinstance(now, (int, float))
                or not math.isfinite(now)):
            raise ValueError('a grant is booked at a finite time')
        amount = int(amount)
        account = self.players.setdefault(player, Account())
        account.essence = min(account.essence + amount, U32)
        self.teams[team] = min(self.teams.get(team, 0) + amount, U32)
        reached = []
        level = level_for(account.essence, self.table, self.max_level)
        while account.level < level:
            account.level += 1
            reached.append(account.level)
            self.levels_reached.append((player, account.level, account.essence, now))
        return reached
