# Shared match state

The multiplayer design gives the match one clock and one simulation. A tick advances the shared world once. Each connection then gets its own view of that result. Adding a second viewer or changing the order in which clients poll should leave the elapsed time and world state unchanged.

Keep a player's identity in the shared roster. A connection uses its own roster entry to identify which character it controls. Both viewers should agree on the identity of every participant and the match outcome.

Test the boundary with two simulated viewers. Request both views for the same tick, reverse the request order, and compare the shared state. Check the controlled character separately in each view. This catches accidentally advancing timers while constructing an update for a viewer.
