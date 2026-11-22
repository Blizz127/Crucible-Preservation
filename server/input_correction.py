"""Schedule retries of a requested boolean state."""

BEGIN_BURST = 3
END_BURST = 10
REFRESH_SECONDS = 1.0


def refresh_request(state, suppress, now, send):
    """Call send(suppress) on a transition and periodically while it persists."""
    previous = state.get('input_suppress')
    if previous is None or previous['suppress'] != suppress:
        pending = state['input_suppress'] = {'suppress': suppress, 'sends': 0, 'last': now}
    else:
        pending = previous
    burst = BEGIN_BURST if suppress else END_BURST
    if pending['sends'] >= burst and now - pending['last'] < REFRESH_SECONDS:
        return
    send(suppress)
    pending['sends'] += 1
    pending['last'] = now
