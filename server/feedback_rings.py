"""Buffer incoming damage notifications in a caller-sized ring."""


RESEND_SECONDS = 0.5


RESEND_ENTRIES = 8


class _Ring:
    def __init__(self, size):
        self.size = size
        self.last_index = 0
        self.written = 0
        self.recent = {}
        self.unsent = set()

    def _write(self, entry, now):
        self.last_index = (self.last_index + 1) % self.size
        self.recent[self.last_index] = (entry, now)
        self.unsent.add(self.last_index)
        self.written += 1

    def take(self, now):
        fresh = sorted(((written, slot) for slot, (_, written) in self.recent.items()
                        if now - written <= RESEND_SECONDS), reverse=True)
        keep = {slot for _, slot in fresh[:RESEND_ENTRIES]} | self.unsent
        self.recent = {slot: value for slot, value in self.recent.items() if slot in keep}
        self.unsent = set()
        if self.last_index not in self.recent:
            return None
        return {slot: entry for slot, (entry, _) in self.recent.items()}, self.last_index


class IndicatorRing(_Ring):
    def hit(self, damage, source_position, now):
        self._write((damage, tuple(source_position)), now)
