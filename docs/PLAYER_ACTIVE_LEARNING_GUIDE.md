# Player Active Learning Guide

Ten guide opisuje, jak przygotowywać lepsze klatki do doskonalenia modelu zawodników. Cel jest podobny jak przy piłce: nie dodawać losowych frame'ów bez potrzeby, tylko zbierać klatki, na których aktualny pipeline realnie ma problem.

Aktualny domyślny model zawodników:

```text
models/best-model-with-ball-and-players-500-frames.pt
```

## Kiedy generować nowe klatki

Dodawaj nowe klatki do datasetu zawodników, gdy widzisz:

- zawodników gubionych przy bocznych liniach,
- zawodników daleko od kamery, szczególnie przy górnej części boiska,
- zbyt duże lub zbyt małe bboxy,
- bboxy na cieniach, ławkach, bramkach, słupkach albo osobach poza boiskiem,
- identity switch po kolizji lub minięciu się dwóch zawodników,
- spadki `visible_stable_boxes` w `frame_detection_counts.json`,
- zakresy `ambiguous` albo `blocked_switches` w `global_identity_report.json`.

Przy zawodnikach nie chodzi tylko o więcej detekcji. Model powinien stabilnie łapać zawodników na boisku i ograniczać false-positive poza boiskiem, bo false-positive potrafią potem niszczyć identity resolver.

## Najlepsze źródła problematycznych klatek

### 1. Stable overlay

Oglądaj `stable_overlay_preview.mp4` i notuj frame number, gdy:

- zawodnik znika mimo że jest widoczny,
- bbox przeskakuje na inną osobę,
- bbox obejmuje cień albo obiekt,
- zawodnik przy linii bocznej jest systematycznie pomijany.

To jest nadal najlepszy manualny sygnał jakości.

### 2. Frame detection counts

Po analizie sprawdzaj:

```text
backend/storage/matches/<match_id>/frame_detection_counts.json
```

Szukaj klatek z niskim:

```text
trusted_detected
visible_stable_boxes
```

Dla 7v7 bez zmian i z obiema drużynami na boisku oczekujemy orientacyjnie 14 widocznych zawodników. Krótkie spadki są akceptowalne, ale długie zakresy są dobrym materiałem do datasetu.

### 3. Identity report

Sprawdzaj:

```text
backend/storage/matches/<match_id>/global_identity_report.json
```

Najbardziej wartościowe są:

```text
ambiguous_frame_ranges
blocked_switches
rejected_candidates
visible_bbox_count_per_frame
```

Te frame'y często pokazują dokładnie przypadki, których model lub resolver nie rozumie.

## Eksport klatek z video

Na start możesz wyciągnąć równomiernie rozłożone klatki z wybranego zakresu video. Jeżeli skrypt `extract:frames` nie obsługuje jeszcze `start/end`, użyj całego krótkiego sample albo przygotuj wcześniej sample 30-120 sekund.

```powershell
npm run extract:frames -- `
  --video matches_video\corgi_verisk_2_3.mp4 `
  --frames 400 `
  --out training_frames `
  --quality 2 `
  --overwrite
```

Wynik:

```text
training_frames/<video-name>_400frames/
  frame_000001.jpg
  frame_000002.jpg
  metadata.json
```

Do doskonalenia modelu nie musisz zawsze brać 400 klatek. Lepsze jest 100-200 dobrze dobranych problematycznych klatek niż 1000 bardzo podobnych ujęć.

## Prelabel player + ball

Do szybszej anotacji użyj aktualnych modeli jako prelabelingu:

```powershell
$env:PYTHON = "backend\.venv-cuda\Scripts\python.exe"

npm run prelabel:player-ball -- `
  --frames-dir training_frames\<video-name>_400frames `
  --out training_frames\<video-name>_400frames_player_prelabels `
  --player-model models/best-model-with-ball-and-players-500-frames.pt `
  --ball-model models/best-balls-only-800-frames.pt `
  --player-conf 0.05 `
  --ball-conf 0.03 `
  --player-imgsz 1280 `
  --ball-imgsz 1280 `
  --device 0 `
  --max-ball-detections 1 `
  --overwrite
```

Jeżeli tworzysz dataset tylko dla zawodników, w Roboflow zostaw klasę `player`, a labelki piłki usuń lub nie importuj ich do tej wersji datasetu.

## ROI boiska dla prelabelingu

Skrypt `prelabel:player-ball` obsługuje polygon boiska:

```powershell
--roi-polygon "x1,y1;x2,y2;x3,y3;x4,y4"
--roi-margin-px -20
```

Zasada:

- dla zawodników zwykle lepiej mieć lekko ujemny margines boczny niż usuwać 5-10 osób poza boiskiem na każdej klatce,
- ale nie przesadzaj z ujemnym marginesem, bo zawodnicy przy samej linii bocznej mogą zostać pominięci,
- jeśli dataset ma uczyć model ludzi na boisku, usuń osoby za linią, trenerów, ławkę i widzów.

Jeśli potrzebujesz innego marginesu po bokach niż góra/dół, na dziś zrób prelabeling zwykłym ROI, a potem popraw w Roboflow. Warto później rozbudować skrypt o osobne marginesy `side/top/bottom`, tak jak mamy w eksporcie problematycznych klatek piłki.

## Jak wybierać klatki do Roboflow

Najbardziej wartościowe typy:

- daleka kamera, zawodnicy mali,
- górna część boiska,
- zawodnicy przy linii bocznej,
- kilku zawodników blisko siebie,
- zawodnik częściowo zasłonięty,
- bramkarze w innych kolorach,
- szybki bieg z motion blur,
- cień zawodnika dłuższy niż sylwetka,
- osoby poza boiskiem podobne rozmiarem do zawodników.

Mniej wartościowe:

- 50 prawie identycznych klatek z tego samego ustawienia,
- klatki, gdzie wszyscy są idealnie widoczni i model już działa dobrze,
- frame'y z przerwą, ławką, rozgrzewką poza boiskiem, jeśli nie chcesz tego wykrywać jako `player`.

## Jak anotować zawodników

Dataset zawodników powinien mieć jedną klasę:

```text
player
```

Zasady:

- Oznaczaj tylko zawodników na boisku, których chcesz analizować.
- Oznaczaj bramkarzy jako `player`, nie jako osobną klasę, chyba że świadomie budujemy multi-class model.
- Nie oznaczaj sędziów, trenerów, rezerwowych, widzów i osób poza boiskiem.
- Bbox powinien obejmować ciało zawodnika, bez długiego cienia.
- Jeśli zawodnik jest częściowo poza kadrem, oznacz tylko widoczną część.
- Jeśli dwie osoby się stykają, dawaj dwa osobne bboxy, jeśli człowiek jest w stanie je rozdzielić.
- Usuń prelabel na cieniu albo obiekcie, nawet jeśli wygląda podobnie do sylwetki.

Nie oznaczaj teamu ani koloru koszulki w modelu detekcji. Team assignment zostaje osobną warstwą po detekcji.

## Roboflow split i preprocessing

Rekomendowany split:

```text
train: 80%
valid: 15%
test: 5%
```

Preprocessing:

```text
Auto-Orient: on
Resize: Fit within 1280 x 1280
```

Unikaj `Stretch to`, bo zmienia proporcje sylwetek. Przy zawodnikach można testować lekkie augmentacje jasności/kontrastu, ale ostrożnie z dużym blur i crop, bo mamy stałą perspektywę drona i zależy nam na małych obiektach.

## Trening w Colab

Bezpieczny start:

```python
from ultralytics import YOLO

model = YOLO("yolov8n.pt")

model.train(
    data=dataset.location + "/data.yaml",
    epochs=100,
    imgsz=1280,
    batch=4,
    device=0,
    name="players_yolov8n_1280_v_next",
    patience=30,
    cache="disk",
    workers=2,
    seed=42,
    amp=True,
    plots=True,
    save_period=10,
)
```

Jeśli Colab daje mocniejsze GPU i nie ma OOM, można spróbować `batch=8`. Nie schodź z `imgsz=1280` bez testu, bo zawodnicy w górnej części boiska są mali.

Po treningu pobierz `best.pt`, nazwij wersję jawnie i wrzuć do:

```text
backend/models/
```

Przykład:

```text
best-players-only-1000-frames.pt
```

## Porównanie nowego player modelu

Porównuj na tym samym krótkim fragmencie meczu:

```powershell
backend\.venv-cuda\Scripts\python.exe backend\scripts\benchmark_analysis.py `
  --match-id 682c5606 `
  --max-seconds 60 `
  --frame-stride 2 `
  --device 0 `
  --yolo-model models/best-players-only-1000-frames.pt `
  --yolo-imgsz 1280 `
  --yolo-conf 0.05
```

Porównuj z aktualnym defaultem:

```text
models/best-model-with-ball-and-players-500-frames.pt
```

Najważniejsze metryki:

- `detections_kept`,
- `detections_rejected_outside_pitch`,
- `stable_players_count`,
- średni `visible_stable_boxes`,
- liczba `ambiguous` ranges,
- liczba `blocked_switches`,
- manualny overlay: czy znikają ghost bboxy i aggressive switches.

Nie wybieraj modelu tylko po liczbie detekcji. Model, który wykrywa więcej ludzi poza boiskiem, może pogorszyć stabilizację ID.

## Kiedy aktualizować default

Zmień domyślny player model dopiero gdy nowy model:

- ma mniej zgubionych zawodników w problematycznych fragmentach,
- nie dodaje ghost bboxów na cieniach i osobach poza boiskiem,
- poprawia albo utrzymuje stabilność `A01-A07` / `B01-B07`,
- nie zwiększa liczby aggressive switchy,
- wygląda lepiej na overlayu w co najmniej dwóch różnych fragmentach meczu.

Aktualne defaulty są zapisane w:

```text
backend/app/model_defaults.py
client/src/lib/modelDefaults.ts
README.md
AGENTS.md
backend/AGENTS.md
```
