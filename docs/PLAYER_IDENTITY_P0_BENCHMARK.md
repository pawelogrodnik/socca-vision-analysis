# Player Identity P0 Benchmark

P0 identity diagnostics runs in `shadow_read_only` mode after `conservative_identity_v2`.
It does not feed classifications, reliability scores, or occlusion events back into the
resolver, player statistics, heatmaps, or crop review.

## Artifacts

- `identity_tracklet_quality.json`: versioned quality classification and reliability evidence.
- `identity_occlusion_events.json`: grouped bbox-overlap and footpoint-proximity events.
- `identity_fragmentation_report.json`: fragmentation, unresolved time, switch and review-load metrics.
- `identity_stitching_candidates.json`: read-only continuation edges, hard-constraint rejections,
  ranked predecessor/successor candidates and conservative shadow recommendations.

Observation seconds may exceed video duration because multiple players can be unresolved or
ambiguous in the same frame. Timeline seconds count each problematic video frame only once.

## Frozen benchmarks

The manifest is `examples/player_identity_benchmarks.json`:

- `easy90`: the 90-second baseline with A02, A03, and A05 as verified stable subjects;
- `hard3m`: source match `7655bf7c`, frames from 04:30 to 07:30, preserving source timestamps.

Run both without YOLO, ball processing, possession, or overlay rendering:

```bash
PYTHONPATH=backend backend/.venv-mps/bin/python \
  backend/scripts/benchmark_player_identity.py
```

The runner creates immutable baseline/candidate directories under
`backend/storage/benchmarks/player_identity/`. It compares normalized hashes of current
identity, stats, and heatmap artifacts. Any change fails the P0 no-impact gate.
For the verified easy subjects A02, A03, and A05, a recommendation that joins the subject
to a different current identity also fails the benchmark.

Runtime reporting keeps the full baseline/candidate wall-clock delta for visibility, but
the 15% gate uses the median isolated diagnostics-stage cost. This avoids treating normal
camera-motion and filesystem timing variance between two full reprocesses as P0 overhead.

Use `--case easy90` or `--case hard3m` for a single benchmark. Direct reprocessing can disable
the shadow layer with `backend/scripts/reprocess_analysis.py --no-identity-diagnostics`.

## Visual stitching audit

After a passing benchmark, render review cards for every recommended shadow edge without
running YOLO or rebuilding identity:

```bash
PYTHONPATH=backend backend/.venv-mps/bin/python \
  backend/scripts/generate_identity_stitching_audit.py \
  --benchmark-root backend/storage/benchmarks/player_identity/<benchmark-run>
```

Each case receives:

- `audit_manifest.json` with stable candidate keys and pending manual-review fields;
- `cards/*.jpg` with source, transition, target, crops and cost evidence;
- `contact_sheets/*.jpg` for fast scanning;
- `index.html` with same/different/uncertain actions and reviewed JSON export.

Cards render at `2400x1080`. The orange source and blue target crops repeat the exact bbox and
show an arrow above the reviewed person. Click any card in `index.html` to open the full-resolution
lightbox; this is especially important when another player appears inside the same crop context.

The audit is developer tooling. Selecting a decision in the static page does not mutate match
artifacts or production identity. The exported reviewed manifest becomes input to a later,
versioned identity goldset step.

## Versioned stitching goldset

After reviewing and downloading both manifests, build an immutable goldset and evaluate the
current shadow scorer:

```bash
PYTHONPATH=backend backend/.venv-mps/bin/python \
  backend/scripts/build_identity_stitching_goldset.py \
  --reviewed-manifest <reviewed-easy90.json> \
  --reviewed-manifest <reviewed-hard3m.json> \
  --goldset-id player-identity-stitching \
  --version 1.0.0 \
  --output backend/storage/benchmarks/player_identity/goldsets/player-identity-stitching-1.0.0.json \
  --prediction player-identity-easy-90s-v1=<easy90-identity_stitching_candidates.json> \
  --prediction player-identity-hard-3m-v1=<hard3m-identity_stitching_candidates.json>
```

The default conservative gate requires at least 10 labeled edges, precision of at least 0.95,
and zero false-positive merges. `pending` and `uncertain` rows are never guessed or included in
the confusion matrix. Existing goldset versions are never overwritten.
