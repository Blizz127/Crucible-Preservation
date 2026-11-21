# Crucible Revival

This repository collects original code and design notes from the Crucible Revival preservation project. This source collection is intended for studying and testing individual components.

## Source components

- [Navigation](server/navigation.py). Routes through a triangle mesh and projects waypoints onto its surface.
- [Combat state](server/combat.py). Tracks health and the outcome of hits.

## Design notes

- [Routing across a triangle mesh](docs/21-hunter-ai.md)
- [Shared match state](docs/52-multiplayer-plan.md)

## Patrol motion

Successive accepted position samples provide an estimate of speed. Discard sudden position jumps, then record the duration of each moving or resting interval. Keep bounded samples of those durations. Each simulated patrol can select its own pause from an accepted range and fall back to the default when the input is invalid.

This project distributes its original material under the [MIT License](LICENSE).
