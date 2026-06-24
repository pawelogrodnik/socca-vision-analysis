# Tracklet to player identity resolver

The detector/tracker output is not the final player identity. Raw Ultralytics tracker IDs can flicker, split and reappear. The app therefore uses a manual approval layer before publishing stats.

## Concepts

```text
tracker_id / tracklet -> team_id -> player_id -> published stats
```

- `tracklet_id`: a raw continuous track from YOLO/BoT-SORT/ByteTrack.
- `team_id`: selected from the match roster.
- `player_id`: selected from the team roster.
- `status`: decision made by the operator.

Supported statuses:

```text
unassigned      needs review
assigned        accepted and linked to a roster player
unknown         keep for later, not enough confidence
false_positive  detector mistake
opponent        outside roster / another pitch / not relevant
referee         official or technical person
```

## Current workflow

1. Run analysis.
2. Open the `Akceptacja trackletów i player_id` section in `/admin-panel`.
3. Review the raw tracklet list sorted by duration.
4. For each useful tracklet, choose team and player.
5. Mark bad tracklets as `false_positive`, `opponent`, `referee`, or `unknown`.
6. Save assignments.
7. Generate `match_package.json`.
8. Publish/import the package to SQLite.

The backend saves local review decisions in:

```text
player_assignments.json
```

This file is intentionally ignored by Git because it is generated match data.

## What the UI reports

The panel shows:

- raw tracklet count,
- assigned tracklet count,
- unassigned tracklet count,
- ignored tracklet count,
- unique assigned players total,
- unique assigned players per team compared with roster size.

This answers the MVP question: not “how many players did the model automatically identify by team”, but “how many real roster players have been accepted after analysis review”. Automatic team/color classification can be added later as a suggestion layer, not as the source of truth.

## Why this approach

For amateur wide-angle football footage, raw tracker IDs are not stable enough to directly become player IDs. A player can be split into multiple tracklets, and false positives can appear. The review layer gives us a stable contract:

```text
many tracklets -> one real player_id
bad tracklets -> ignored
unknown tracklets -> saved for later
```

## Future improvements

- show thumbnail/frame crop per tracklet,
- draw selected tracklet path on the pitch map,
- auto-suggest team by kit color,
- auto-suggest player when a new tracklet starts near the previous end of the same player,
- support splitting one tracklet if the tracker switches people mid-track,
- support multiple stints/substitutions for the same player,
- store reviewed player stats in normalized DB tables.
