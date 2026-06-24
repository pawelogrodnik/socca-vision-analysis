# Data model draft

## Match

```json
{
  "id": "abc123",
  "title": "Team A vs Team B",
  "video_filename": "match.mp4",
  "video": {
    "fps": 30,
    "width": 1920,
    "height": 1080,
    "duration_sec": 2400
  }
}
```

## PitchConfig

```json
{
  "image_points": [[100, 100], [1800, 100], [1850, 980], [80, 980]],
  "width_m": 26,
  "length_m": 56,
  "source": "manual"
}
```

## Track

`tracks.json` currently stores raw tracking output:

```json
{
  "track_id": 7,
  "start_time_sec": 1.2,
  "end_time_sec": 10.4,
  "positions": [
    {
      "frame": 36,
      "time_sec": 1.2,
      "bbox_xyxy": [100, 200, 140, 280],
      "footpoint": [120, 280],
      "pitch_m": [11.2, 35.8],
      "confidence": 0.81,
      "source": "yolo-person"
    }
  ]
}
```

## Future entities

```text
Team
Player
Tracklet
Stint
IdentityAssignment
SubstitutionEvent
PlayerMatchStats
TeamMatchStats
SeasonAggregate
```

Important rule:

```text
tracker_id != player_id
```

A real player may be composed of many tracklets.
