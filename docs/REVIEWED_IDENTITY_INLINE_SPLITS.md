# Unified Reviewed Identity actions and temporal splits

Reviewed Identity presents one operator vocabulary for every review scope:
whole player fragment, canonical fragment, material continuity and optional MAX
audit. The server decides the exact observation scope and which actions are
available; the browser never sends raw observation ownership.

Primary actions are roster player, known team with unnamed player, **Kilku
zawodników**, referee, false detection, unknown team and `Nie wiem`. Existing stable
slot actions remain under **Zaawansowane** whenever the server marks them safe.
Choosing a roster player is always a deliberate operator action. In particular,
team-attribution evidence does not create an automatic player suggestion, but
an operator can still choose a roster player; roster membership determines the
effective team.

## Staged mixed-player review

`Kilku zawodników` first saves a durable mixed-player marker for the exact
server-authoritative source and advances normal Review. It does not open a
split editor in the narrow correction column. Once normal required cases are
done, the separate full-width **Zmieszani gracze** stage presents the unified
queue. This keeps routine decisions fast while preserving all mixed evidence.

The marker and subsequent split use only its exact `(tracklet_id, frame)`
ownership. Display ranges are presentation only, never ownership. Saving a
normal decision for the same exact source retires only that marker (and any
children), never another segment of the same raw tracker subject.

The editor gives an overview of at most 12 chronological crops. A wide gap can
be refined with denser local crops before choosing a boundary. Every child must
have an explicit decision, or the operator can save `Nie da się bezpiecznie
podzielić czasowo`, which remains a safety blocker.

Existing decisions are restored when the case is reopened. Boundary changes
preserve only unambiguous child assignments; the UI warns before clearing a
decision that cannot be safely mapped. Editing an already saved split requires
its current semantic digest, while an unchanged retry is idempotent. This
prevents a stale browser tab from overwriting a newer operator decision.

Legacy `reviewed_identity_mixed_players.json` cases remain readable. The
existing inline split editor is retained only as a full-width modal for
editing/reopening a saved split. Both legacy and staged cases use the same
versioned artifact and child-target/segment-decision resolver, so they remain
Reviewed Identity overlays and never rewrite raw detector or tracker data.
