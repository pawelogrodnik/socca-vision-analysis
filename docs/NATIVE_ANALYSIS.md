# Native Analysis And Performance Benchmark

Ten tryb jest przeznaczony do porównania wydajności lokalnych maszyn bez Dockera, szczególnie:

- MacBook Apple Silicon z `mps`,
- Windows/Linux z NVIDIA CUDA,
- CPU fallback.

Docker nadal zostaje poprawnym trybem uruchomienia aplikacji, ale na macOS Docker zwykle nie daje backendowi dostępu do Apple MPS. Do pomiaru MacBooka M4 uruchamiaj backend/analyzer natywnie.

## 1. Przygotowanie środowiska

### macOS / Apple Silicon

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

Sprawdź, czy Torch widzi MPS:

```bash
npm run runtime:info
```

Wynik powinien zawierać:

```json
"mps_available": true
```

### Windows / NVIDIA

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
```

Jeśli instalacja Torch z `requirements.txt` nie widzi CUDA, zainstaluj wariant CUDA zgodny z lokalnym sterownikiem/PyTorch. Następnie sprawdź:

```powershell
npm run runtime:info
```

Wynik powinien zawierać:

```json
"cuda_available": true
```

## 2. Uruchomienie backendu natywnie

```bash
npm run backend:native
```

Frontend może dalej działać normalnie przez Vite. W panelu analizy zobaczysz sekcję `Backend runtime` oraz selektor device: `Auto`, `CPU`, `CUDA / NVIDIA GPU 0`, `Apple MPS`.

## 3. Benchmark bez nadpisywania analizy meczu

Benchmark tworzy osobny katalog pod:

```text
backend/storage/benchmarks/<timestamp-label>/
```

Nie nadpisuje głównych artefaktów w `backend/storage/matches/<match_id>/`.

### MacBook M4 / MPS

```bash
npm run benchmark:analysis -- --match-id 031e4e6d --label macbook-m4-mps --device mps --max-seconds 60 --frame-stride 2 --yolo-imgsz 960
```

### Dell / NVIDIA CUDA

```powershell
npm run benchmark:analysis -- --match-id 031e4e6d --label dell-gtx1650-cuda --device 0 --max-seconds 60 --frame-stride 2 --yolo-imgsz 960
```

### CPU fallback

```bash
npm run benchmark:analysis -- --match-id 031e4e6d --label cpu-baseline --device cpu --max-seconds 60 --frame-stride 2 --yolo-imgsz 960
```

## 4. Jak porównywać wyniki

Każdy benchmark zapisuje:

- `performance_report.json`,
- `benchmark_input.json`,
- normalne artefakty analizy w katalogu benchmarku.

Najważniejsze pola w `performance_report.json`:

- `elapsed_wall_sec` - realny czas wykonania,
- `processed_frames_per_wall_sec` - ile przetworzonych klatek na sekundę,
- `video_seconds_per_wall_second` - ile sekund filmu analizuje się w sekundę zegarową,
- `estimated_40_min_wall_min` - szacowany czas analizy 40 minut przy tych parametrach.

Do uczciwego porównania używaj tych samych parametrów: `max_seconds`, `frame_stride`, `yolo_imgsz`, `yolo_model`, `yolo_conf` i `yolo_tracker`.
