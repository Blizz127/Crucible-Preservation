"""Buffer incoming damage notifications in a caller-sized ring."""


class IndicatorRing:
    def __init__(self, size):
        self.size = size
        self.last_index = 0
        self.pending = {}

    def hit(self, damage, source_position):
        self.last_index = (self.last_index + 1) % self.size
        self.pending[self.last_index] = (damage, tuple(source_position))

    def take(self):
        if not self.pending:
            return None
        taken, self.pending = (self.pending, self.last_index), {}
        return taken
