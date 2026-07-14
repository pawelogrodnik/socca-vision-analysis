# Attacking Momentum / Attacking Pressure — plan implementacji

## Cel

Zaimplementuj eksperymentalny wykres **attacking momentum**, który pokazuje chwilową przewagę ofensywną drużyn:

- wartości dodatnie: Team A;
- wartości ujemne: Team B;
- okolice zera: brak wyraźnej przewagi albo za mało wiarygodnych danych.

Momentum nie jest possession i nie może być narastającym procentem possession. Ma estymować ostatni nacisk ofensywny na podstawie:

- drużyny kontrolującej piłkę;
- pozycji piłki względem kierunku ataku;
- progresji akcji;
- kandydatów podań;
- opcjonalnych restartów;
- jakości i pokrycia danych.

Semantyka techniczna:

```text
attacking_momentum
relative_attacking_pressure_estimate_not_official_stat
```

W UI użyj nazwy `Momentum (experimental)` albo `Attacking pressure (experimental)`. Nie przedstawiaj wyniku jako modelu FIFA/Opta.

---

## Kontekst aktualnego repo

Aktualny pipeline:

```text
video
→ YOLO / ball detection
→ chunk merge
→ stable players
→ possession candidates
→ contact/event candidates
→ pass candidates
→ possession report
→ match package
→ public report
```

Wykorzystaj istniejące moduły:

- `backend/app/services/ball_possession.py`
- `backend/app/services/pass_candidates.py`
- `backend/app/services/match_phase_config.py`
- `backend/app/services/analysis.py`
- `backend/app/services/chunked_analysis.py`
- `backend/app/services/public_match_report.py`
- `backend/app/main.py`
- `client/src/components/MatchReportContent.tsx`
- `client/src/components/PublicMatchReportContent.tsx`
- `client/src/types.ts`

Repo ma już Recharts oraz possession timeline. Momentum ma być osobnym wykresem, nie zamiennikiem possession.

---

## Ważne założenie

Poza repo trwają testy zmian poprawiających:

- wykrywanie eventu podania;
- possession.

Dlatego momentum:

1. nie może przepisywać tych algorytmów;
2. ma przyjmować ich dokumenty jako input;
3. ma tolerować brak opcjonalnych pól;
4. ma mieć lokalną warstwę normalizacji wejścia;
5. nie może rozsiewać zależności od szczegółów obecnego schematu.

Opieraj się głównie na polach:

```text
time_sec
status
team_label
ball_position_m
confidence
ball_confidence
ball_source
from_team_label
to_team_label
start_time_sec
end_time_sec
forward_progress_m
is_progressive
review_status
final_stat_eligible
```

---

## Zakres MVP

MVP ma:

- wygenerować `attacking_momentum.json`;
- dodać artefakt do analysis report;
- dodać go do match package jako optional;
- przenieść uproszczoną timeline do public report;
- pokazać wykres nad/pod osią zero w lokalnym i publicznym raporcie;
- dodać testy backendu;
- działać bez pass/restart candidates.

MVP nie ma:

- odtwarzać proprietary modelu;
- liczyć xG;
- wykrywać strzałów;
- zmieniać possession/pass detection;
- tworzyć finalnej statystyki;
- zastępować possession timeline.

---

## Nowy serwis backendowy

Dodaj:

```text
backend/app/services/attacking_momentum.py
```

Publiczne funkcje:

```python
def build_attacking_momentum_document(
    possession_candidates_doc: dict,
    match_phase_config_doc: dict | None,
    *,
    pitch_width_m: float,
    pitch_length_m: float,
    pass_candidates_doc: dict | None = None,
    restart_candidates_doc: dict | None = None,
    shot_candidates_doc: dict | None = None,
    bin_sec: float = 5.0,
    smoothing_window_sec: float = 30.0,
) -> dict:
    ...
```

```python
def write_attacking_momentum_artifact(
    match_dir: Path,
    possession_candidates_doc: dict,
    match_phase_config_doc: dict | None,
    *,
    pitch_width_m: float,
    pitch_length_m: float,
    pass_candidates_doc: dict | None = None,
    restart_candidates_doc: dict | None = None,
    shot_candidates_doc: dict | None = None,
) -> dict:
    ...
```

Druga funkcja zapisuje:

```text
attacking_momentum.json
```

---

## Źródła danych

### Possession frames

Główne źródło to `possession_candidates.json`.

Przykład:

```json
{
  "frame": 123,
  "time_sec": 4.1,
  "status": "controlled",
  "team_label": "A",
  "ball_position_m": [14.2, 31.5],
  "confidence": 0.74,
  "ball_confidence": 0.68,
  "ball_source": "detected"
}
```

Zasady:

- `controlled` + Team A/B: może generować score;
- `contested`: bez przypisania do drużyny;
- `free`: bez przypisania do drużyny;
- `unknown`: bez przypisania do drużyny;
- brak pozycji lub kierunku ataku: bez positional score;
- nie zgaduj właściciela piłki.

### Kierunek ataku

Użyj istniejącej funkcji:

```python
direction_for_team_at_time(match_phase_config_doc, team_label, time_sec)
```

Nie implementuj osobnej logiki zmiany stron.

### Pass candidates

Momentum musi działać bez podań. Jeżeli dokument istnieje, można dodać niewielki bonus za:

- same-team pass;
- progressive pass;
- accepted/final-stat-eligible pass;
- needs-review candidate z ograniczoną wagą.

Rejected candidate daje zero.

### Restarty

Opcjonalnie:

- corner: większy bonus;
- kick-in w wysokiej strefie: mały bonus;
- nieznana drużyna: zero.

### Shot candidates

MVP nie musi mieć shot detection. Zostaw opcjonalny input jako extension point.

---

## Normalizacja pozycji

Przelicz pozycję piłki na:

```text
attack_progress ∈ [0, 1]
```

`0` = własna bramka, `1` = bramka rywala.

```python
def normalized_attack_progress(
    position_m,
    attack_direction,
    pitch_width_m,
    pitch_length_m,
):
    x, y = position_m

    if attack_direction == "towards_y_min":
        return 1.0 - clamp01(y / pitch_length_m)
    if attack_direction == "towards_y_max":
        return clamp01(y / pitch_length_m)
    if attack_direction == "towards_x_min":
        return 1.0 - clamp01(x / pitch_width_m)
    if attack_direction == "towards_x_max":
        return clamp01(x / pitch_width_m)

    return None
```

Test musi potwierdzić odwrócenie wyniku po zmianie stron.

---

## Scoring MVP

Wszystkie liczby mają być nazwanymi stałymi i trafić do `parameters` dokumentu.

### Position threat

```python
zone_threat = attack_progress ** 1.8
position_score = 0.15 + 0.85 * zone_threat
```

Wysoka strefa ma być znacznie bardziej wartościowa niż własna połowa.

### Confidence

Przykładowo:

```python
possession_confidence = clamp01(frame.get("confidence", 0.0))
ball_confidence = clamp01(
    frame.get("ball_confidence", possession_confidence)
)

source_weight = {
    "detected": 1.0,
    "interpolated": 0.65,
    "unknown": 0.0,
}.get(ball_source, 0.5)

confidence_weight = (
    sqrt(possession_confidence * ball_confidence)
    * source_weight
)
```

Zachowaj zasadę:

```text
detected > interpolated > unknown
```

### Progression bonus

Dla kolejnych kontrolowanych frame tej samej drużyny:

```python
delta_progress = current_progress - previous_progress
```

Bonus tylko gdy:

- poprzedni frame jest tej samej drużyny;
- przerwa nie przekracza np. 2 sekund;
- `delta_progress > 0`.

```python
progression_bonus = clamp01(delta_progress / 0.15) * 0.35
```

Cofnięcie piłki nie daje ujemnej kary; po prostu nie dostaje bonusu.

### Frame pressure

```python
frame_pressure = confidence_weight * (
    position_score + progression_bonus
)
```

Zapisuj osobno Team A i Team B.

### Pass bonus

Waga review:

```text
accepted / final_stat_eligible: 1.00
uncertain:                      0.55
needs_review:                   0.35
rejected:                       0.00
```

Przykładowe bonusy:

```text
same-team pass:    0.05
progressive pass: +0.10 do +0.30
```

```python
progress_bonus = min(
    0.30,
    max(0.0, forward_progress_m) / 10.0 * 0.30,
)
```

W MVP nie dawaj dużego bonusu turnover/interception. Można zachować go w `evidence`.

### Restart bonus

Przykładowo:

```text
corner:  0.25–0.35
kick-in: 0.05–0.15
```

Waż score confidence i pozycją względem kierunku ataku.

---

## Bucketing i smoothing

Domyślnie:

```text
bin_sec = 5
smoothing_window_sec = 30
```

Dla każdego binu:

```python
team_a_raw = mean(team_a_frame_scores) + team_a_event_bonus
team_b_raw = mean(team_b_frame_scores) + team_b_event_bonus
signed_raw = team_a_raw - team_b_raw
```

Nie sumuj wszystkich frame bez normalizacji, bo wynik zależałby od FPS/frame stride.

Użyj trailing rolling weighted average albo EMA. Nie korzystaj z przyszłych punktów.

Skalowanie publiczne:

```text
[-100, 100]
```

Użyj odpornej skali, np. 95 percentyla:

```python
scale = max(
    percentile(abs(smoothed_signed_raw), 95),
    MIN_NORMALIZATION_SCALE,
)

signed_score = clamp(
    smoothed_signed_raw / scale * 100,
    -100,
    100,
)
```

Dodaj:

```python
team_a_value = max(0.0, signed_score)
team_b_value = min(0.0, signed_score)
```

Zachowaj także raw score do debugowania.

---

## Kontrakt JSON

```json
{
  "schema_version": "0.1.0",
  "generated_at": "ISO-8601",
  "source": "attacking_momentum_v1",
  "status": "completed",
  "experimental": true,
  "semantics": "relative_attacking_pressure_estimate_not_official_stat",
  "parameters": {
    "bin_sec": 5.0,
    "smoothing_window_sec": 30.0,
    "normalization": "robust_abs_p95",
    "position_exponent": 1.8,
    "uses_possession": true,
    "uses_pass_candidates": true,
    "uses_restart_candidates": true,
    "uses_shot_candidates": false
  },
  "summary": {
    "points": 240,
    "duration_sec": 1200.0,
    "known_possession_coverage": 0.71,
    "direction_coverage": 1.0,
    "scored_controlled_frames": 16400,
    "pass_event_bonuses": 42,
    "restart_event_bonuses": 8,
    "team_a_pressure_share": 0.54,
    "team_b_pressure_share": 0.46,
    "team_a_peak": 86.4,
    "team_b_peak": -79.1,
    "quality": "medium"
  },
  "points": [
    {
      "index": 0,
      "time_sec": 2.5,
      "start_time_sec": 0.0,
      "end_time_sec": 5.0,
      "team_a_raw": 0.42,
      "team_b_raw": 0.08,
      "signed_raw": 0.34,
      "smoothed_signed_raw": 0.31,
      "signed_score": 42.8,
      "team_a_value": 42.8,
      "team_b_value": 0.0,
      "dominant_team_label": "A",
      "confidence": 0.72,
      "controlled_coverage": 0.66,
      "direction_coverage": 1.0,
      "intensity": 0.5,
      "evidence": {
        "team_a_controlled_samples": 86,
        "team_b_controlled_samples": 14,
        "progressive_pass_candidates": 1,
        "accepted_pass_candidates": 0,
        "restart_candidates": 0
      }
    }
  ],
  "warnings": [],
  "notes": [
    "Momentum is relative and normalized within this match.",
    "Values from different matches are not directly comparable in v1.",
    "This is an experimental pressure estimate."
  ]
}
```

---

## Quality

Wylicz:

```text
high
medium
low
```

Przykładowe reguły:

- high: known coverage >= 0.75, direction coverage >= 0.95, kierunek potwierdzony;
- medium: known coverage >= 0.50 i direction coverage >= 0.80;
- low: gorsze coverage, mało kontrolowanych próbek albo brak kierunku.

Warnings:

```text
Known possession coverage is below 50%.
Attack direction is unknown for part of the match.
Match phase direction uses an unconfirmed default.
Pass candidates were missing; momentum used positional possession only.
```

Brak pass/restart candidates nie blokuje generowania positional momentum.

---

## Integracja backendu

### `backend/app/services/analysis.py`

Po utworzeniu possession/pass/restart docs zbuduj momentum.

Dodaj artefakt:

```text
attacking_momentum.json
```

Do `analysis_report.json` dodaj:

```json
{
  "momentum_summary": {}
}
```

Momentum jest eksperymentalne i nie może wywrócić całej analizy:

```python
try:
    ...
except Exception as exc:
    warnings.append(
        f"Experimental attacking momentum layer failed: {exc}"
    )
```

### `backend/app/services/chunked_analysis.py`

Generuj momentum tylko po merge całego meczu, po possession/pass/restart.

Nie generuj go osobno per chunk.

Użyj obecnego progress stage:

```text
possession_pass_candidates
```

Nie dodawaj nowego stage w MVP.

### Reprocess

Jeżeli reprocess przelicza possession/pass bez YOLO, zaktualizuj:

```text
backend/app/services/post_yolo_reprocess.py
backend/scripts/reprocess_analysis.py
```

Momentum ma być możliwe do odtworzenia z istniejących JSON-ów bez ponownego YOLO.

---

## Match package

W `backend/app/main.py` dodaj:

```python
"attacking_momentum"
```

do `PACKAGE_OPTIONAL_KEYS`.

Dodaj:

```python
("attacking_momentum", "attacking_momentum.json")
```

do `PACKAGE_EMBEDDED_JSON_FILES`.

Jeżeli endpoint lokalnego meczu używa jawnej mapy plików, również ją zaktualizuj.

Nie dodawaj momentum do `PACKAGE_REQUIRED_KEYS`.

Starsze paczki bez tego pola muszą nadal działać.

---

## Public report

W `backend/app/services/public_match_report.py` dodaj:

```python
def _public_momentum_timeline(package: dict) -> list[dict]:
    ...
```

Publiczny punkt powinien być uproszczony:

```json
{
  "index": 0,
  "minute": 1,
  "label": "1",
  "start_time_sec": 0.0,
  "end_time_sec": 5.0,
  "signed_score": 42.8,
  "team_a_value": 42.8,
  "team_b_value": 0.0,
  "dominant_team_label": "A",
  "confidence": 0.72,
  "controlled_coverage": 0.66,
  "intensity": 0.5
}
```

Do `ball` dodaj:

```json
{
  "momentum": {
    "experimental": true,
    "quality": "medium",
    "warnings": [],
    "timeline": []
  }
}
```

Nie mieszaj tego z `possession_timeline`.

---

## Typy frontendowe

W `client/src/types.ts` dodaj:

```typescript
export type AttackingMomentumPoint = {
  index: number;
  time_sec: number;
  start_time_sec: number;
  end_time_sec: number;
  team_a_raw?: number;
  team_b_raw?: number;
  signed_raw?: number;
  smoothed_signed_raw?: number;
  signed_score: number;
  team_a_value: number;
  team_b_value: number;
  dominant_team_label?: 'A' | 'B' | null;
  confidence?: number;
  controlled_coverage?: number;
  direction_coverage?: number;
  intensity?: number;
  evidence?: Record<string, number>;
};
```

```typescript
export type AttackingMomentumDocument = {
  schema_version?: string;
  generated_at?: string;
  source?: string;
  status?: string;
  experimental?: boolean;
  semantics?: string;
  parameters?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  points: AttackingMomentumPoint[];
  warnings?: string[];
  notes?: string[];
};
```

Dodaj optional field do:

- `Match`;
- `MatchPackage`;
- `AnalysisReport.artifacts`;
- `PublicMatchReport.ball`.

---

## Komponent wykresu

Dodaj:

```text
client/src/components/AttackingMomentumChart.tsx
```

Props:

```typescript
type AttackingMomentumChartProps = {
  points: AttackingMomentumPoint[];
  teamAName: string;
  teamBName: string;
  teamAColor?: string;
  teamBColor?: string;
  quality?: string;
  warnings?: string[];
  compact?: boolean;
};
```

Użyj Recharts:

- `BarChart`;
- oś Y `[-100, 100]`;
- `ReferenceLine y={0}`;
- Team A nad osią;
- Team B pod osią;
- tooltip z czasem, score, confidence i coverage.

Nie hardcoduj kolorów, jeśli raport ma `display_color`.

Opis:

```text
Eksperymentalna estymacja nacisku ofensywnego.
Nad osią: Team A. Pod osią: Team B.
```

Low quality ma być widoczne jako badge/warning.

---

## Raport lokalny

W `client/src/components/MatchReportContent.tsx`:

- dodaj `attackingMomentum` do `MatchReportSource`;
- uzupełnij `sourceFromLocalMatch`;
- uzupełnij `sourceFromPublishedPackage`;
- pokaż kartę momentum po sekcji possession/pass.

Nie usuwaj possession.

---

## Raport publiczny

W `client/src/components/PublicMatchReportContent.tsx`:

1. zostaw obecny possession chart;
2. opcjonalnie zmień jego tytuł na `Posiadanie w czasie`;
3. dodaj osobną kartę `Momentum`;
4. użyj `AttackingMomentumChart`.

---

## Testy backendu

Dodaj:

```text
backend/tests/test_attacking_momentum.py
```

Wymagane przypadki:

1. final third daje większy pressure niż własna połowa;
2. odwrócenie kierunku daje odwrotną ocenę;
3. druga połowa używa zmienionego kierunku;
4. progressive pass podnosi score;
5. `accepted > uncertain > needs_review > rejected`;
6. free/contested/unknown nie tworzą Team A/B momentum;
7. high confidence daje większy raw score niż low confidence;
8. `signed_score` zawsze jest w `[-100, 100]`;
9. wynik jest deterministyczny i posortowany po czasie;
10. brak pass/restart/shot docs nie powoduje błędu;
11. pusty input zwraca `points: []`, warning i quality `low`.

---

## Walidacja

Backend:

```bash
cd backend
python -m unittest discover -s tests
```

Frontend:

```bash
cd client
pnpm typecheck
pnpm build
```

Ręcznie sprawdź:

- starszy mecz bez momentum;
- nowy lokalny mecz;
- match package;
- public report;
- zmianę stron;
- low-coverage warning;
- czy momentum nie jest kopią possession.

---

## Acceptance criteria

- [ ] istnieje `attacking_momentum.py`;
- [ ] generowany jest `attacking_momentum.json`;
- [ ] score mieści się w `[-100, 100]`;
- [ ] Team A jest nad osią, Team B pod osią;
- [ ] używany jest kierunek z `match_phase_config`;
- [ ] działa zmiana stron;
- [ ] final third daje większy pressure;
- [ ] progressive pass może podnieść pressure;
- [ ] unknown/free/contested nie dostają właściciela;
- [ ] brak pass candidates nie psuje feature;
- [ ] artefakt trafia do analysis report;
- [ ] artefakt trafia do match package jako optional;
- [ ] public report zawiera uproszczoną timeline;
- [ ] lokalny i publiczny raport pokazują wykres;
- [ ] possession chart pozostaje osobno;
- [ ] UI pokazuje `experimental`;
- [ ] backend tests przechodzą;
- [ ] `pnpm typecheck` przechodzi;
- [ ] `pnpm build` przechodzi;
- [ ] starsze raporty nadal działają.

---

## Kolejność implementacji

### Milestone 1 — pure model

- nowy serwis;
- testy;
- bez integracji z pipeline.

### Milestone 2 — pipeline

- single-pass;
- chunked;
- reprocess;
- analysis report.

### Milestone 3 — package/public report

- optional artifact;
- embedded JSON;
- public timeline;
- backward compatibility.

### Milestone 4 — frontend

- typy;
- współdzielony chart;
- local report;
- public report;
- style;
- typecheck/build.

### Milestone 5 — sample validation

- ręczne porównanie z video;
- sprawdzenie zmiany stron;
- porównanie z possession;
- upewnienie się, że momentum reaguje na wysokość i progresję akcji.

---

## Reguły dla agenta

1. Przeczytaj pełne aktualne pliki przed zmianą.
2. Nie zakładaj, że lokalny pass/possession schema jest identyczny jak na `main`.
3. Nie refaktoryzuj istniejących detektorów w ramach tego feature.
4. Utrzymuj momentum w osobnym serwisie.
5. Nie dodawaj go do required package fields.
6. Nie usuwaj possession timeline.
7. Każda heurystyka ma być nazwaną stałą, widoczną w `parameters` i pokrytą testem.
8. Kod ma działać bez optional documents.
9. Nie commituj generated reports, video, heatmap PNG, Playwright logs ani backend storage.
10. Na końcu wypisz zmienione pliki, testy, ograniczenia i przykład JSON.

---

## Definition of Done

MVP ma odpowiadać na pytanie:

```text
Która drużyna w ostatnich około 30 sekundach generowała większy nacisk ofensywny?
```

Nie musi jeszcze odpowiadać:

```text
Jakie było prawdopodobieństwo zdobycia następnego gola?
```

Drugi problem wymaga później shot candidates, xG, box entries i modelu uczonego na oznaczonych danych.
