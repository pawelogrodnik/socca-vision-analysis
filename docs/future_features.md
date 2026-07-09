# Future Features

This document collects product ideas that are useful, but not part of the
current tracking/statistics foundation work.

## Possession Indicator In Stable Overlay

When the possession layer identifies the current ball carrier, the stable
overlay should draw a clear visual marker above that player's bbox.

Proposed first version:

- draw a small red triangle above the center of the player's bbox, similar to
  classic football game UI;
- show it only when possession confidence is high enough to avoid noisy flicker;
- anchor the marker to the trusted player bbox, not to the ball bbox;
- keep it out of player labels and stats text, so it remains readable during
  crowded moments;
- hide or soften it for contested/unknown possession states.

Acceptance idea:

- in frames where the possession panel says `controlled`, the same player should
  have the triangle above their bbox;
- no triangle should be shown when possession is `free`, `unknown`, or clearly
  contested.

## Passing Lane Preview

When a player has controlled possession, the stable overlay should optionally
draw passing-lane lines from that player to teammates who appear to be open.

Proposed first version:

- candidate receiver = trusted same-team player currently on pitch;
- draw a line from the ball carrier footpoint/bbox-bottom-center to the receiver
  footpoint/bbox-bottom-center;
- mark the lane as open only when no opponent blocks the line;
- approximate blocking with opponent footpoints or the lower part of opponent
  bboxes intersecting a corridor around the passing line;
- keep this diagnostic/visual at first, not a final tactical statistic;
- use subdued lines so the overlay remains readable.

Initial geometry rule:

- create a narrow corridor around the line between passer and receiver;
- for every opponent, test whether their footpoint or lower bbox segment falls
  inside that corridor;
- if an opponent blocks the corridor, do not draw the open passing-lane line, or
  draw it in a blocked style later if useful.

Acceptance idea:

- when the ball carrier has a clear same-team teammate with no opponent between
  them, the overlay shows a passing option line;
- when an opponent's lower bbox/footpoint crosses the lane, that option is not
  shown as open.
