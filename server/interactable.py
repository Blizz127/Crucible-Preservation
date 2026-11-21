"""Track state changes that need to be sent for several successive ticks."""

SENDS = 5


class Tracker:
    def __init__(self, initial_state=(True, None)):
        self.initial_state = initial_state
        self.sent = {}
        self.owed = {}

    def forget(self, entity):
        self.sent.pop(entity, None)
        self.owed.pop(entity, None)

    def want(self, entity, state):
        baseline = self.sent.setdefault(entity, self.initial_state)
        if state != baseline:
            self.sent[entity] = state
            self.owed[entity] = SENDS

    def due(self, entity):
        if self.owed.get(entity):
            self.owed[entity] -= 1
            return self.sent[entity]
        return None


def tracker(state):
    book = state.setdefault('interactable', {})
    return book.setdefault('tracker', Tracker())
