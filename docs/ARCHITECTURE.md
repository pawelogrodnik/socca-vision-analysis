# Architecture

## High-level flow

```text
client upload video
→ backend stores match under storage/matches/<match_id>
→ client requests calibration frame
→ user clicks 4 pitch corners
→ backend stores pitch_config.json
→ backend runs analysis adapter
→ backend exports artifacts
→ client displays overlay, heatmap, JSON
```

## Why manual pitch calibration first

Automatic pitch detection is possible, but it is a separate CV problem. For MVP, one manual calibration per match is cheaper and more reliable.

The pitch config is used for:

- filtering detections by `footpoint-in-pitch`,
- homography from image pixels to pitch meters,
- heatmaps,
- distance/speed calculations later,
- minimap and zones later.

## Adapters

### motion

Fallback adapter. It uses OpenCV background subtraction and a tiny centroid tracker. It exists only to verify that the app pipeline works without YOLO.

### yolo

YOLO/Ultralytics adapter. It currently tracks `person` class from COCO and outputs raw tracker IDs as `P<ID>` on `overlay_preview.mp4`.

This is for ID flickering tests. Raw `tracker_id` is not final `player_id`.

## Next architectural layer

The next important layer is identity resolution:

```text
raw tracker_id / tracklet
→ player_id
→ stint
→ match stats
```

A player can have multiple tracklets and multiple stints because orlik substitutions are dynamic.
