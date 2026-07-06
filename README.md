# Orlik Vision App

Starter aplikacji do analizy meczów orlikowych z nagrania z góry.

Monorepo:

```text
client/   React + Vite UI
backend/  FastAPI + OpenCV + YOLO/Ultralytics analysis API
docs/     opis architektury, model danych, roadmapa
examples/ krótkie demo video i przykładowa kalibracja
```

## Co działa w tej wersji

- upload video,
- wybór meczu,
- podgląd klatki z filmu,
- ręczne kliknięcie 4 rogów boiska,
- zapis `pitch_config.json`,
- analiza adapterem `motion` lub `yolo`,
- output `overlay_preview.mp4` z widocznym `P<ID>` / `T<ID>`,
- output `tracks.json`,
- output `heatmap_all_tracks.png`,
- Docker Compose dla client + backend.

To jest starter techniczny do testowania pipeline'u i flickeringu ID. Nie jest jeszcze finalny system statystyk piłkarskich.

## Szybki start bez Dockera

### Backend

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate   # Git Bash na Windows
# PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Client

W drugim terminalu:

```bash
cd client
npm install
npm run dev
```

Otwórz:

```text
http://localhost:5173
```

## Start w Dockerze

```bash
docker compose up --build
```

Client:

```text
http://localhost:5173
```

Backend API:

```text
http://localhost:8000/docs
```

## Aktualne modele YOLO

Domyślny pipeline analizy jest ustawiony na lokalne modele w `backend/models/`:

```text
player model: models/best-model-with-ball-and-players-500-frames.pt
ball model:   models/best-balls-only-800-frames.pt
```

Te pliki muszą istnieć w `backend/models/` na maszynie, na której uruchamiasz analizę. Przy przenoszeniu pracy na drugi laptop skopiuj cały katalog `backend/models/` razem z repo albo odtwórz te pliki ręcznie z backupu.

Używamy ich w domyślnym UI, payloadach backendu, chunked analysis, prelabelingu i benchmarkach. `yolov8n.pt` zostaje tylko sensownym fallbackiem/testem porównawczym, a nie aktualnym domyślnym modelem produkcyjnym.

Guide generowania kolejnych problematycznych klatek do doskonalenia modelu piłki:

```text
docs/BALL_ACTIVE_LEARNING_GUIDE.md
```

Guide doskonalenia modelu zawodników:

```text
docs/PLAYER_ACTIVE_LEARNING_GUIDE.md
```

Guide porównywalnego benchmarku na MacBooku/MPS:

```text
docs/MACBOOK_BENCHMARK_GUIDE.md
```

## Test YOLO flickeringu ID

1. Uploaduj video.
2. Wybierz mecz.
3. Załaduj klatkę, najlepiej z 1-5 sekundy.
4. Kliknij 4 rogi boiska w kolejności zgodnej z opisem w UI.
5. Zapisz boisko.
6. Wybierz adapter `yolo`.
7. Ustaw:

```text
model: models/best-model-with-ball-and-players-500-frames.pt
tracker: centroid_high_recall
imgsz: 1280
conf: 0.05
max seconds: 20-60
frame stride: 1
```

8. Uruchom analizę i obejrzyj `overlay_preview.mp4`.

Etykiety `P1`, `P2`, `P3` to surowe tracker IDs z Ultralytics. To właśnie służy do oceny, czy ID flickeruje.

## GPU / NVIDIA

Domyślnie backend działa także na CPU. Przy GPU lokalnie możesz w UI ustawić `device = 0`.

Dla Dockera z GPU wymagany jest NVIDIA Container Toolkit. Potem możesz uruchomić backend z dostępem do GPU przez własną konfigurację Compose albo uruchomić backend lokalnie bez Dockera.

## Agent / coding instructions

Repo zawiera instrukcje dla AI/coding-agentów:

```text
AGENTS.md
client/AGENTS.md
backend/AGENTS.md
docs/IMPLEMENTATION_PLAN.md
```

Najważniejsze zasady: nie mieszać UI z logiką, nie używać inline CSS dla normalnych styli, trzymać API client osobno od komponentów, a w backendzie oddzielać route handlery od analizy video/CV/statystyk.

## Ważne założenia

- Polygon boiska robimy ręcznie w MVP, bo to najszybsza droga do stabilnego testu.
- Filtrowanie osób spoza boiska działa przez `footpoint-in-pitch`.
- Statystyki liczymy z pozycji stóp po homografii.
- Raw `tracker_id` nie jest jeszcze finalnym `player_id`.
- Docelowo potrzebny będzie panel `tracklet -> player_id -> stint`.

## Kolejność rozwoju

1. Stabilny tracking zawodników.
2. Panel przypisywania trackletów do zawodników.
3. Czas gry, heatmapy, dystans, sprinty.
4. Ball detector + interpolation.
5. Posiadanie, podania, strzały.
6. Dashboard sezonowy.


## AI implementation plan

Dla progresywnej implementacji używaj:

```text
docs/IMPLEMENTATION_PLAN.md
```

Ten plik zawiera milestone'y, user stories, acceptance criteria i gotowe prompty dla agenta AI, np. „kontynuuj pracę od Milestone 5 zgodnie z `docs/IMPLEMENTATION_PLAN.md`”.

## Troubleshooting: pitch clicks and overlay preview

### Clicks are shifted on the calibration canvas

The click position is mapped from the displayed canvas size to the internal frame size in `client/src/App.tsx` inside `handleCanvasClick`.

The canvas must not be stretched by CSS. The relevant styles are in `client/src/styles.css`:

```css
.pitch-canvas-wrap { overflow: auto; }
.pitch-canvas { display: block; max-width: 100%; height: auto; }
```

Do not add `width: 100%` together with `max-height` / `object-fit` directly on the canvas, because that can distort the aspect ratio and create click drift toward the left/right edges.

### `overlay_preview.mp4` exists but does not play

The backend now writes a temporary MJPEG AVI and converts it with ffmpeg to browser-playable H.264 MP4. The conversion code lives in:

```text
backend/app/services/analysis.py
```

If the browser still cannot play the file:

1. rebuild the backend image: `docker compose up -d --build backend`,
2. rerun analysis to regenerate `overlay_preview.mp4`,
3. check backend logs: `docker compose logs -f backend`,
4. open `analysis_report.json` from the UI — failed analyses now write an error report.

### Docker troubleshooting

If the client exits with `sh: vite: not found`, rebuild the client dependencies and remove the stale node_modules volume:

```bash
docker compose down -v
docker compose up --build
```

The client service mounts `./client` for Vite hot reload and keeps `/app/node_modules` in a Docker named volume, so Windows host `node_modules` will not shadow the container dependencies.


### Windows / npm registry troubleshooting

If `npm install` tries to download packages from an unexpected internal registry or Vite is not found, reset the client dependencies:

```powershell
cd client
npm config set registry https://registry.npmjs.org/
Remove-Item -Recurse -Force node_modules -ErrorAction SilentlyContinue
Remove-Item -Force package-lock.json -ErrorAction SilentlyContinue
npm cache verify
npm install
npm run dev
```

The starter intentionally includes `.npmrc` files that force the public npm registry and disable package-lock generation, because generated lockfiles can contain machine-specific registry URLs.
