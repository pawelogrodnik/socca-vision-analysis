# Unified Reviewed Identity actions and temporal splits

Reviewed Identity presents one operator vocabulary for every review scope:
whole player fragment, canonical fragment, material continuity and optional MAX
audit. The server decides the exact observation scope and which actions are
available; the browser never sends raw observation ownership.

Primary actions are roster player, known team with unnamed player, temporal
split, referee, false detection, unknown team and `Nie wiem`. Existing stable
slot actions remain under **Zaawansowane** whenever the server marks them safe.
Choosing a roster player is always a deliberate operator action. In particular,
team-attribution evidence does not create an automatic player suggestion, but
an operator can still choose a roster player; roster membership determines the
effective team.

## Temporal split

`To kilku zawodników — podziel` opens an inline editor. It derives the parent
set from the server-authoritative review source and its ownership digest. The
saved split contains only boundaries and child decisions; each child receives a
deterministic target derived from its exact `(tracklet_id, frame)` observations.
Display ranges are presentation only, never ownership.

The editor gives an overview of at most 12 chronological crops. A wide gap can
be refined with denser local crops before choosing a boundary. Every child must
have an explicit decision, or the operator can save `Nie da się bezpiecznie
podzielić czasowo`, which remains a safety blocker.

Existing decisions are restored when the case is reopened. Boundary changes
preserve only unambiguous child assignments; the UI warns before clearing a
decision that cannot be safely mapped. Editing an already saved split requires
its current semantic digest, while an unchanged retry is idempotent. This
prevents a stale browser tab from overwriting a newer operator decision.

Legacy `reviewed_identity_mixed_players.json` cases remain readable. New inline
splits use the same versioned artifact and the same child-target/segment-decision
resolver, so they remain Reviewed Identity overlays and never rewrite raw
detector or tracker data.
