# MacBook Benchmark Guide

Ten guide służy do uruchomienia porównywalnego benchmarku na MacBooku. Celem jest porównanie czasu analizy pełnego flow z wynikiem Windows/XPS:

```text
Windows baseline:
machine_benchmark_summary.json
backend/storage/benchmarks/20260706T101049Z-windows-xps-gtx1650-cuda-benchmark-video-fullflow-60s/
```

Porównywalny benchmark musi używać tego samego video, pitch configu, modeli i parametrów.

## Wymagane pliki

Po sklonowaniu repo upewnij się, że istnieją:

```text
scripts/benchmark_video.mp4
scripts/benchmark_video_pitch_config.json
backend/models/best-model-with-ball-and-players-500-frames.pt
backend/models/best-balls-only-800-frames.pt
```

Jeśli `scripts/benchmark_video.mp4` nie jest w repo, skopiuj dokładnie ten sam plik z Windowsa. Benchmark nie będzie porównywalny na innym video.

## Setup macOS

Zainstaluj systemowe zależności:

```bash
brew install python@3.11 ffmpeg
```

Utwórz środowisko Pythona:

```bash
cd /path/to/orlik-vision-app
python3.11 -m venv backend/.venv-mps
source backend/.venv-mps/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r backend/requirements.txt
```

Sprawdź PyTorch/MPS:

```bash
python - <<'PY'
import torch
print("torch", torch.__version__)
print("mps_available", torch.backends.mps.is_available())
print("mps_built", torch.backends.mps.is_built())
PY
```

Oczekiwane:

```text
mps_available True
mps_built True
```

Jeśli MPS nie jest dostępny, benchmark pójdzie na CPU i nie będzie uczciwym porównaniem z CUDA.

## Benchmark 60s Full Flow

Uruchom z katalogu repo:

```bash
export PYTHONPATH=backend
export YOLO_CONFIG_DIR="$(pwd)/.cache/ultralytics"
export MPLCONFIGDIR="$(pwd)/.cache/matplotlib"
mkdir -p "$YOLO_CONFIG_DIR" "$MPLCONFIGDIR"

backend/.venv-mps/bin/python backend/scripts/benchmark_analysis.py \
  --video scripts/benchmark_video.mp4 \
  --pitch-config scripts/benchmark_video_pitch_config.json \
  --label macbook-m4-mps-benchmark-video-fullflow-60s \
  --max-seconds 60 \
  --frame-stride 1 \
  --device mps \
  --include-ball \
  --yolo-imgsz 1280 \
  --ball-yolo-imgsz 1280 \
  --ball-yolo-conf 0.03
```

Jeśli chcesz jednocześnie widzieć log w terminalu i zapisać go do pliku:

```bash
mkdir -p backend/storage/benchmarks/manual-performance-runs

backend/.venv-mps/bin/python backend/scripts/benchmark_analysis.py \
  --video scripts/benchmark_video.mp4 \
  --pitch-config scripts/benchmark_video_pitch_config.json \
  --label macbook-m4-mps-benchmark-video-fullflow-60s \
  --max-seconds 60 \
  --frame-stride 1 \
  --device mps \
  --include-ball \
  --yolo-imgsz 1280 \
  --ball-yolo-imgsz 1280 \
  --ball-yolo-conf 0.03 2>&1 | tee backend/storage/benchmarks/manual-performance-runs/macbook-m4-mps-60s.log
```

Parametry muszą zostać takie same jak na Windowsie:

```text
max_seconds: 60
frame_stride: 1
include_ball: true
yolo_imgsz: 1280
ball_yolo_imgsz: 1280
ball_yolo_conf: 0.03
player model: models/best-model-with-ball-and-players-500-frames.pt
ball model: models/best-balls-only-800-frames.pt
```

Różni się tylko:

```text
device: mps
label: macbook-m4-mps-benchmark-video-fullflow-60s
```

## Monitorowanie postępu

Aktualny benchmark nie pokazuje jeszcze procentowego progress bara per etap, ale można śledzić postęp po artefaktach zapisywanych na dysk. Najważniejsze etapy kończą się mniej więcej w tej kolejności:

```text
benchmark_input.json
tracks.json
camera_motion_report.json
overlay_preview.mp4
stable_overlay_preview.mp4
global_identity.json
frame_detection_counts.json
movement_stats.json
ball_candidates.json
ball_tracks.json
ball_tracking_report.json
ball_overlay_preview.mp4
possession_candidates.json
possession_report.json
possession_overlay_preview.mp4
analysis_report.json
performance_report.json
```

W drugim terminalu możesz obserwować najnowszy katalog benchmarku:

```bash
watch -n 5 'ls -lh backend/storage/benchmarks | tail'
```

Gdy pojawi się katalog z nazwą `macbook-m4-mps-benchmark-video-fullflow-60s`, monitoruj jego pliki:

```bash
WATCH_DIR="backend/storage/benchmarks/<TU_WKLEJ_KATALOG_BENCHMARKU>"
watch -n 5 "ls -lh \"$WATCH_DIR\" | sort"
```

Jeżeli uruchomiłeś wariant z `tee`, możesz śledzić log:

```bash
tail -f backend/storage/benchmarks/manual-performance-runs/macbook-m4-mps-60s.log
```

Szybki check, czy benchmark już zakończony:

```bash
test -f "$WATCH_DIR/performance_report.json" && echo "DONE" || echo "RUNNING"
```

Po zakończeniu możesz od razu podejrzeć kluczowe metryki:

```bash
python - <<'PY'
import json
from pathlib import Path

output_dir = Path(input("output_dir: ").strip())
perf = json.loads((output_dir / "performance_report.json").read_text(encoding="utf-8"))
print("elapsed_wall_sec:", perf["elapsed_wall_sec"])
print("video_seconds_per_wall_second:", perf["throughput"]["video_seconds_per_wall_second"])
print("estimated_40_min_wall_min:", perf["throughput"]["estimated_40_min_wall_min"])
print("processed_frames:", perf["throughput"]["processed_frames"])
PY
```

## Wynik

Po zakończeniu benchmark wypisze JSON z polem:

```text
artifacts.output_dir
```

W tym katalogu znajdziesz:

```text
performance_report.json
analysis_report.json
stable_overlay_preview.mp4
ball_overlay_preview.mp4
possession_overlay_preview.mp4
```

Jeśli chcesz mieć taki sam skrócony plik jak na Windowsie, uruchom:

```bash
python - <<'PY'
import json
from pathlib import Path

output_dir = Path(input("output_dir: ").strip()).expanduser()
perf = json.loads((output_dir / "performance_report.json").read_text(encoding="utf-8"))
analysis = json.loads((output_dir / "analysis_report.json").read_text(encoding="utf-8"))

summary = {
    "schema_version": "0.1.0",
    "label": perf.get("label"),
    "machine": {
        "system": ((perf.get("runtime") or {}).get("platform") or {}).get("system"),
        "platform": ((perf.get("runtime") or {}).get("platform") or {}).get("platform"),
        "processor": ((perf.get("runtime") or {}).get("platform") or {}).get("processor"),
        "python": ((perf.get("runtime") or {}).get("python") or {}).get("version"),
        "torch": ((perf.get("runtime") or {}).get("torch") or {}).get("version"),
        "mps_available": ((perf.get("runtime") or {}).get("torch") or {}).get("mps_available"),
        "mps_built": ((perf.get("runtime") or {}).get("torch") or {}).get("mps_built"),
    },
    "input": {
        "video": "scripts/benchmark_video.mp4",
        "pitch_config": "scripts/benchmark_video_pitch_config.json",
        "max_seconds": (perf.get("parameters") or {}).get("max_seconds"),
        "frame_stride": (perf.get("parameters") or {}).get("frame_stride"),
        "include_ball": (perf.get("parameters") or {}).get("include_ball"),
        "player_model": (perf.get("parameters") or {}).get("yolo_model"),
        "ball_model": (perf.get("parameters") or {}).get("ball_yolo_model"),
        "yolo_imgsz": (perf.get("parameters") or {}).get("yolo_imgsz"),
        "ball_yolo_imgsz": (perf.get("parameters") or {}).get("ball_yolo_imgsz"),
        "device": perf.get("normalized_yolo_device"),
    },
    "performance": {
        "elapsed_wall_sec": perf.get("elapsed_wall_sec"),
        "elapsed_wall_min": round(float(perf.get("elapsed_wall_sec") or 0) / 60.0, 3),
        "processed_frames": ((perf.get("throughput") or {}).get("processed_frames")),
        "processed_frames_per_wall_sec": ((perf.get("throughput") or {}).get("processed_frames_per_wall_sec")),
        "analyzed_video_sec": ((perf.get("throughput") or {}).get("analyzed_video_sec")),
        "video_seconds_per_wall_second": ((perf.get("throughput") or {}).get("video_seconds_per_wall_second")),
        "estimated_40_min_wall_min": ((perf.get("throughput") or {}).get("estimated_40_min_wall_min")),
        "estimated_40_min_wall_hours": round(float(((perf.get("throughput") or {}).get("estimated_40_min_wall_min")) or 0) / 60.0, 3),
    },
    "analysis_summary": perf.get("analysis_summary"),
    "warnings": analysis.get("warnings") or [],
    "artifacts": {
        "output_dir": str(output_dir.resolve()),
        "performance_report": str((output_dir / "performance_report.json").resolve()),
        "analysis_report": str((output_dir / "analysis_report.json").resolve()),
        "stable_overlay_preview": str((output_dir / "stable_overlay_preview.mp4").resolve()),
        "ball_overlay_preview": str((output_dir / "ball_overlay_preview.mp4").resolve()),
        "possession_overlay_preview": str((output_dir / "possession_overlay_preview.mp4").resolve()),
    },
}

(output_dir / "machine_benchmark_summary.json").write_text(
    json.dumps(summary, indent=2),
    encoding="utf-8",
)
print(output_dir / "machine_benchmark_summary.json")
PY
```

Wklej `output_dir` z wyniku benchmarku, np.:

```text
backend/storage/benchmarks/20260706TXXXXXXZ-macbook-m4-mps-benchmark-video-fullflow-60s
```

## Co porównać

Porównuj:

```text
performance.elapsed_wall_sec
performance.video_seconds_per_wall_second
performance.estimated_40_min_wall_min
performance.processed_frames_per_wall_sec
```

Najważniejsza metryka:

```text
video_seconds_per_wall_second
```

Im wyższa, tym szybciej analizujemy video. Windows baseline miał:

```text
elapsed_wall_sec: 1002.113
video_seconds_per_wall_second: 0.06
estimated_40_min_wall_min: 668.08
```

## Uwaga o uczciwym porównaniu

Nie porównuj benchmarków, jeśli zmieniłeś którykolwiek z tych parametrów:

```text
frame_stride
max_seconds
yolo_imgsz
ball_yolo_imgsz
include_ball
player model
ball model
camera motion settings
```

Nawet mała zmiana `imgsz` albo `frame_stride` mocno zmienia czas analizy.
