# Ball Active Learning Guide

Ten proces służy do generowania lepszych klatek do anotacji piłki niż losowe próbkowanie. Zamiast brać przypadkowe frame'y, skrypt wybiera klatki, na których aktualne modele mają problem: brak detekcji, niską pewność, wiele kandydatów, niezgodę między modelami albo piłkę poza standardowym ROI boiska.

Aktualny domyślny model piłki:

```text
models/best-balls-only-800-frames.pt
```

## Kiedy generować problematyczne klatki

Użyj tego flow, gdy:

- overlay piłki ma długie `unknown` ranges,
- model gubi piłkę przy wysokich wybiciach albo poza górną linią boiska,
- model daje kilka kandydatów piłki na jednej klatce,
- po nowym treningu chcesz celowo poprawić najgorsze przypadki, zamiast dodawać losowe klatki.

Losowe klatki są dobre na pierwszą wersję datasetu. Później lepsze są problematyczne klatki, bo szybciej poprawiają realne błędy modelu.

## Wymagania

Potrzebujesz:

- finalnego lub testowego video,
- pasującego `pitch_config.json`,
- aktualnych modeli w `backend/models/`,
- lokalnego backendowego środowiska CUDA, jeśli chcesz szybciej generować paczkę.

Domyślnie skrypt korzysta z:

```text
video:        matches_video/corgi_verisk_2_3.mp4
pitch config: backend/storage/matches/682c5606/pitch_config.json
teacher:      models/best-balls-only-800-frames.pt
custom:       models/best-model-with-ball-and-players-500-frames.pt
output:       training_frames/
```

`teacher` to aktualny najlepszy ball-only model. `custom` jest drugim modelem referencyjnym do wykrywania niezgodności. Możesz nadpisać oba parametry, jeśli testujesz nową wersję.

## Komenda CUDA

Przykład dla fragmentu od 1. do 19. minuty, 500 klatek:

```powershell
$env:YOLO_CONFIG_DIR = (Resolve-Path .).Path + "\.cache\ultralytics"
$env:MPLCONFIGDIR = (Resolve-Path .).Path + "\.cache\matplotlib"

backend\.venv-cuda\Scripts\python.exe backend\scripts\export_ball_active_learning_frames.py `
  --video matches_video\corgi_verisk_2_3.mp4 `
  --pitch-config backend\storage\matches\682c5606\pitch_config.json `
  --start-sec 60 `
  --end-sec 1140 `
  --sample-every-sec 2 `
  --target-count 500 `
  --device 0 `
  --imgsz 960 `
  --name corgi_verisk_ball_problem_m01_m19_v2 `
  --overwrite
```

Wynik powstanie w:

```text
training_frames/<name>/
```

Najważniejsze pliki:

```text
images/               JPG do uploadu
labels/               prelabel YOLO TXT
data.yaml             konfiguracja Roboflow/YOLO
roboflow_yolo.zip     paczka ZIP, jeśli Roboflow pozwala ją wgrać
problem_frames.csv    lista frame'ów i powodów wyboru
problem_frames.json   pełne metadane wyboru
annotated_preview/    podgląd z narysowanymi prelabelami
```

Jeśli Roboflow nie przyjmuje ZIP-a, wskaż cały katalog z obrazami i labelkami albo wgraj `images/` razem z odpowiadającymi plikami `labels/*.txt`.

## Jak skrypt wybiera klatki

Kategorie w `problem_frames.csv`:

- `teacher_detected_custom_missing` - ball-only widzi piłkę, drugi model nie.
- `custom_detected_teacher_missing` - drugi model widzi piłkę, ball-only nie.
- `model_disagreement` - oba modele widzą coś innego.
- `teacher_low_confidence` - piłka wykryta, ale z niską pewnością.
- `multi_candidate_noise` - za dużo kandydatów piłki w jednej klatce.
- `outside_pitch_projection` - piłka/kandydat wychodzi poza zwykły polygon boiska; to ważne przy wysokich wybiciach.
- `no_ball_model_gap` - żaden model nie daje dobrej detekcji przez dłuższy fragment.
- `agreement_high_confidence` - mała domieszka łatwych klatek kontrolnych.

Te kategorie są celowo mieszane, żeby dataset nie składał się tylko z najtrudniejszych, szumowych przypadków.

## Zasady eksportu z jednego meczu

Jeśli dostępny jest tylko jeden mecz, nie należy zwiększać datasetu przez eksport wielu niemal identycznych sąsiednich klatek. Taki dataset może wyglądać dobrze w walidacji, ale model uczy się konkretnego boiska, oświetlenia i ustawienia kamery zamiast generalizować.

Przy każdym kolejnym eksporcie należy:

- celować łącznie w około `1500-2000` zróżnicowanych klatek z jednego meczu,
- nie przekraczać `2500-3000` klatek bez wyraźnego uzasadnienia nowymi typami błędów,
- preferować nowe hard cases zamiast kolejnych podobnych klatek z tego samego zdarzenia,
- deduplikować sąsiednie klatki i zachowywać tylko reprezentatywne przykłady z jednego krótkiego zdarzenia,
- mieszać zasłonięcia, motion blur, daleką piłkę, okolice nóg, linie/bramki, wiele kandydatów oraz prawdziwe klatki negatywne,
- sprawdzać, czy nowa paczka nie powiela klatek ani bardzo bliskich timestampów z wcześniejszych eksportów.

Domyślnie po przekroczeniu około `1000` poprawnie oznaczonych klatek z jednego meczu następna paczka powinna zawierać przede wszystkim `500-800` nowych, najbardziej problematycznych przypadków.

### Holdout czasowy

Walidacji i testu nie wolno budować przez losowe rozdzielenie sąsiednich klatek z tego samego zdarzenia. Należy odłożyć całe, ciągłe przedziały czasu, na przykład kilka osobnych fragmentów po `30-90` sekund, i nie eksportować ich do treningu.

Przy generowaniu paczek active learning:

- klatki z przedziałów holdout nie trafiają do train,
- kolejne klatki tego samego zagrania nie mogą być rozdzielone między train i valid/test,
- wynik nowego modelu porównujemy na tych samych zamrożonych przedziałach,
- po pozyskaniu nowych meczów co najmniej jeden cały mecz powinien zostać niezależnym testem generalizacji.

Losowy split Roboflow `80/15/5` jest dopuszczalny dopiero po wcześniejszym pogrupowaniu danych według meczu i ciągłych fragmentów. Sam losowy split pojedynczych klatek może znacząco zawyżyć wyniki.

## ROI dla piłki

Dla piłki nie używamy tak twardego ROI jak dla zawodników. Piłka może być wizualnie nad boiskiem albo poza górną granicą polygonu, gdy jest kopnięta wysoko.

Domyślne marginesy w eksporcie:

```text
--roi-side-margin-px 160
--roi-top-margin-px 420
--roi-bottom-margin-px 100
```

Jeśli paczka ma za dużo śmieci po bokach, zmniejsz `--roi-side-margin-px`. Jeśli brakuje wysokich wybić, zwiększ `--roi-top-margin-px`.

## Jak anotować w Roboflow

Dataset powinien mieć jedną klasę:

```text
ball
```

Zasady:

- Oznaczaj piłkę, jeśli jest widoczna choćby częściowo i człowiek jest w stanie ją wskazać.
- Jeśli prelabel jest przesunięty, popraw bbox zamiast usuwać obraz.
- Usuń prelabel, jeśli oznacza refleks, linię, but, głowę albo inny mały obiekt.
- Jeśli piłka jest widoczna, ale nie ma prelabela, dodaj bbox ręcznie.
- Jeśli piłki naprawdę nie widać, zostaw obraz bez labela jako negatywny przykład.
- Nie oznaczaj zawodników w ball-only datasacie.

Negatywne klatki są przydatne, ale tylko wtedy, gdy piłki faktycznie nie da się zobaczyć. Nie zostawiaj nieoznaczonej klatki, na której piłka jest widoczna.

## Split i preprocessing w Roboflow

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

Unikaj `Stretch to`, bo może zniekształcać proporcje piłki. Augmentacje dodawaj ostrożnie; dla małej piłki zbyt agresywne blur/crop może pogorszyć dataset.

## Trening w Colab

Bezpieczny start dla T4:

```python
from ultralytics import YOLO

model = YOLO("yolov8n.pt")

model.train(
    data=dataset.location + "/data.yaml",
    epochs=100,
    imgsz=1280,
    batch=4,
    device=0,
    name="ball_yolov8n_1280_v_next",
    patience=30,
    cache="disk",
    workers=2,
    seed=42,
    amp=True,
    plots=True,
    save_period=10,
)
```

Po treningu pobierz `best.pt`, nadaj mu wersjonowaną nazwę i wrzuć do:

```text
backend/models/
```

Przykład nazwy:

```text
best-balls-only-1200-frames.pt
```

Nie nadpisuj starego modelu bez powodu. Łatwiej porównywać regresje, gdy każdy model ma osobną nazwę.

## Porównanie nowego modelu

Najpierw zrób krótki test jakości i czasu na tym samym fragmencie:

```powershell
backend\.venv-cuda\Scripts\python.exe backend\scripts\benchmark_analysis.py `
  --match-id 682c5606 `
  --max-seconds 60 `
  --frame-stride 2 `
  --device 0 `
  --include-ball `
  --ball-yolo-model models/best-balls-only-1200-frames.pt `
  --ball-yolo-imgsz 1280 `
  --ball-yolo-conf 0.03
```

Porównuj przede wszystkim:

- `detected_coverage`,
- `known_coverage`,
- `mean_detected_confidence`,
- `multi_candidate_ratio`,
- `unknown_ranges_count`,
- `longest_unknown_streak_sec`,
- manualny overlay video.

Dobra zmiana to nie tylko więcej detekcji. Lepszy model powinien mieć mniej wielu kandydatów na jednej klatce i wyższy confidence, nawet jeśli czasem ma trochę więcej `unknown`.

## Kiedy aktualizować default

Zmień domyślny model piłki dopiero gdy nowy model:

- poprawia albo utrzymuje `detected_coverage`,
- ma mniej fałszywych kandydatów,
- nie zwiększa znacząco najdłuższych `unknown` streaków,
- wygląda lepiej na overlayu wideo,
- działa stabilnie na co najmniej dwóch różnych fragmentach meczu.

Aktualne defaulty są zapisane w:

```text
backend/app/model_defaults.py
client/src/lib/modelDefaults.ts
README.md
AGENTS.md
backend/AGENTS.md
```
