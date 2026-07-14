# Attacking Momentum / Attacking Pressure — zaktualizowany plan implementacji

## 0. Baseline i cel aktualizacji

Plan został dopasowany do stanu repozytorium z commita:

```text
2388d0b2a51108cd479d812fcc39bfca6c49c29f
```

W aktualnym kodzie znajdują się już poprawki, które wcześniej były opisane jako prace lokalne:

- possession posiada fly-through suppression oraz jawne statusy `controlled`, `contested`, `free`, `unknown`;
- pass candidates mają outcome model: `completed_pass`, `failed_pass`, `excluded_non_pass`, `unknown_pass_attempt`;
- pass candidates korzystają z possession frames do budowania release/trajectory evidence;
- restarty są dopisywane do `pass_candidates.json` jako pass attempts z `from_restart=true`;
- istnieje `pass_quality.py`, goldset oraz skrypt `evaluate_pass_candidates.py`;
- ball tracks są przeliczane po camera-motion compensation i mogą być dodatkowo refinowane podczas stabilization;
- `build_ball_possession_analysis(...)` jest wspólnym punktem używanym przez single-pass, chunked analysis i post-YOLO reprocess.

Najważniejsza zmiana względem poprzedniego planu:

> Momentum należy zintegrować przede wszystkim wewnątrz `build_ball_possession_analysis(...)`, a nie implementować trzy osobne wywołania w `analysis.py`, `chunked_analysis.py` i `post_yolo_reprocess.py`.

---

## 1. Cel feature

Zaimplementuj eksperymentalny wykres **attacking momentum**, pokazujący chwilową przewagę ofensywną drużyn:

- wartości dodatnie — Team A;
- wartości ujemne — Team B;
- okolice zera — brak wyraźnej przewagi albo niewystarczające dane.

Momentum nie jest possession percentage. Ma odpowiadać na pytanie:

```text
Która drużyna w ostatnich około 30 sekundach generowała większy nacisk ofensywny?
```

Powinno uwzględniać:

- kto kontroluje piłkę;
- gdzie znajduje się piłka względem kierunku ataku;
- czy akcja przesuwa się do przodu;
- pass attempts i ich outcome;
- progressive passes;
- restarty w niebezpiecznych strefach;
- confidence i coverage danych.

Semantyka techniczna:

```text
attacking_momentum
relative_attacking_pressure_estimate_not_official_stat
```

Nazwa w UI:

```text
Momentum (experimental)
```

Nie przedstawiaj wyniku jako modelu FIFA, Opta, xG albo prawdopodobieństwa zdobycia gola.

---

## 2. Aktualny pipeline i miejsce feature

Aktualny flow:

```text
video
→ player YOLO + ball YOLO
→ camera motion compensation
→ chunk merge
→ stable players / stabilization
→ refined ball tracks
→ possession candidates
→ contact candidates
→ event candidates
→ pass candidates + outcomes
→ restart candidates
→ possession report
→ stable overlay
→ match package
→ public report
```

Canonical integration point:

```text
backend/app/services/ball_possession.py
build_ball_possession_analysis(...)
```

Ta funkcja posiada jednocześnie:

- `possession_candidates`;
- `match_phase_config`;
- `pass_candidates` po dodaniu restartów;
- `restart_candidates`;
- wymiary boiska;
- finalne, post-stabilization ball positions.

Dlatego nowy dokument momentum powinien powstawać właśnie tam.

---

## 3. Zakres MVP

MVP ma:

- generować `attacking_momentum.json`;
- używać post-stabilization possession frames;
- respektować zmianę stron z `match_phase_config`;
- używać outcome-based pass candidates;
- unikać podwójnego liczenia restartów;
- dodać artefakt do analysis report i analysis runs;
- dodać dokument do match package jako optional;
- dodać uproszczoną timeline do public report;
- pokazać osobny wykres w lokalnym i publicznym raporcie;
- zachować istniejący possession chart;
- działać bez pass/restart candidates;
- działać dla starszych meczów bez momentum.

MVP nie ma:

- zmieniać possession detection;
- zmieniać pass detection;
- zmieniać camera-motion/stabilization;
- liczyć xG;
- wykrywać strzałów;
- rysować momentum w stable overlay;
- tworzyć porównywalnego między meczami ratingu;
- traktować momentum jako finalnej statystyki.

---

## 4. Nowy serwis backendowy

Dodaj:

```text
backend/app/services/attacking_momentum.py
```

Główna publiczna funkcja:

```python
def build_attacking_momentum_document(
    possession_candidates_doc: dict[str, Any],
    match_phase_config_doc: dict[str, Any] | None,
    *,
    pitch_width_m: float,
    pitch_length_m: float,
    pass_candidates_doc: dict[str, Any] | None = None,
    restart_candidates_doc: dict[str, Any] | None = None,
    bin_sec: float = 5.0,
    smoothing_window_sec: float = 30.0,
) -> dict[str, Any]:
    ...
```

Serwis ma być możliwie pure i niezależny od filesystemu.

Nie dodawaj obowiązkowo osobnego `write_attacking_momentum_artifact(...)`. Aktualny kod zapisuje zestaw possession artifacts centralnie przez `_write_possession_artifacts(...)`; należy wykorzystać ten wzorzec.

Wszystkie heurystyki mają być nazwanymi stałymi na początku modułu i muszą pojawić się w `document["parameters"]`.

---

## 5. Canonical inputs

### 5.1. Possession candidates

Główne źródło:

```text
possession_candidates.json
```

Aktualny frame zawiera m.in.:

```json
{
  "frame": 123,
  "time_sec": 4.1,
  "status": "controlled",
  "team_label": "A",
  "ball_position_m": [14.2, 31.5],
  "confidence": 0.74,
  "ball_confidence": 0.68,
  "ball_source": "detected",
  "nearest_player_source": "detected",
  "reason": "nearest_player_within_control_distance"
}
```

Zasady:

- score drużyny powstaje tylko dla `status == "controlled"`;
- `team_label` musi być `A` albo `B`;
- `free`, `contested` i `unknown` nie otrzymują właściciela;
- `fly_through_no_close_control` nie może tworzyć momentum;
- brak `ball_position_m` oznacza brak positional score;
- nie próbuj ponownie klasyfikować possession.

### 5.2. Ważna zasada: nie transformuj pozycji ponownie

`ball_position_m` w possession candidates pochodzi z ball tracks po camera-motion compensation i po opcjonalnym refinement w stabilization.

Momentum ma traktować je jako canonical pitch coordinates.

Nie wolno:

- ponownie stosować homografii;
- ponownie stosować camera-motion matrix;
- czytać pozycji bezpośrednio z bbox/obrazu;
- liczyć score z `position_px`.

### 5.3. Match phase config

Użyj istniejącej funkcji:

```python
from app.services.match_phase_config import direction_for_team_at_time
```

```python
direction_for_team_at_time(
    match_phase_config_doc,
    team_label,
    time_sec,
)
```

Obsługiwane kierunki:

```text
towards_y_min
towards_y_max
towards_x_min
towards_x_max
unknown
```

Nie implementuj osobnej logiki połówek ani zmiany stron.

### 5.4. Pass candidates

Aktualny `pass_candidates.json` posiada pola, których momentum powinno używać:

```text
outcome
count_for_team_label
completed
failed
from_restart
excluded_reason
rejection_reasons
confidence
forward_progress_m
is_progressive
review_status
final_stat_eligible
release_evidence
trajectory_evidence
```

Canonical team dla bonusu:

```python
candidate.get("count_for_team_label")
```

Fallback wyłącznie dla backward compatibility:

```python
candidate.get("from_team_label")
```

Outcome rules:

```text
completed_pass      → pełny pass-attempt bonus
failed_pass         → mały pressure-attempt bonus
excluded_non_pass   → zero
unknown_pass_attempt→ zero w MVP
```

Nie używaj `pass_type == same_team_pass` jako jedynej definicji poprawnego podania. Aktualny model posiada jawne `outcome`.

### 5.5. Restart candidates i deduplication

Restarty są już dopisywane do `pass_candidates.json` z:

```text
from_restart = true
restart_candidate_id
restart_type
```

Nie wolno naliczyć jednocześnie:

- pełnego pass bonus;
- pełnego restart bonus;

za ten sam event.

Zasada:

1. jeśli restart posiada odpowiadający pass candidate z `from_restart=true`, canonical event bonus pochodzi z pass candidate;
2. `restart_candidates.json` może dać tylko setup bonus dla restartu, który nie ma odpowiadającego pass attempt;
3. deduplikuj po `restart_candidate_id` / `candidate_id`.

---

## 6. Normalizacja pozycji względem kierunku ataku

Przelicz pozycję na:

```text
attack_progress ∈ [0, 1]
```

- `0` — własna linia końcowa;
- `1` — bramka przeciwnika.

```python
def normalized_attack_progress(
    position_m: list[float],
    attack_direction: str,
    pitch_width_m: float,
    pitch_length_m: float,
) -> float | None:
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

Nie zakładaj na sztywno, że atak zawsze odbywa się po osi Y, mimo że obecny default używa `pitch_y`.

---

## 7. Scoring positional pressure

Proponowane stałe MVP:

```python
POSITION_BASE_SCORE = 0.10
POSITION_WEIGHT = 0.90
POSITION_EXPONENT = 1.8
PROGRESSION_LOOKBACK_SEC = 1.0
PROGRESSION_MAX_GAP_SEC = 1.5
PROGRESSION_FULL_BONUS_M = 6.0
PROGRESSION_MAX_BONUS = 0.30
```

### 7.1. Zone score

```python
zone_score = attack_progress ** POSITION_EXPONENT
position_score = POSITION_BASE_SCORE + POSITION_WEIGHT * zone_score
```

Wysokie posiadanie w final third ma mieć znacznie większą wartość niż posiadanie przy własnej bramce.

### 7.2. Confidence

Aktualny `frame["confidence"]` już uwzględnia:

- ball confidence;
- detected/interpolated ball source;
- odległość zawodnika od piłki;
- detected/interpolated player source.

Dlatego nie należy drugi raz agresywnie mnożyć tych samych kar.

Canonical weight:

```python
confidence_weight = clamp01(frame.get("confidence") or 0.0)
```

Dopuszczalny fallback przy starszym dokumencie:

```python
confidence_weight = clamp01(frame.get("ball_confidence") or 0.0)
```

Nie stosuj ponownie `sqrt(possession_confidence * ball_confidence)`, ponieważ prowadziłoby to do podwójnego obniżenia confidence.

### 7.3. Progression bonus

Nie licz progresji jako różnicy między dwiema sąsiednimi klatkami. To byłoby zbyt podatne na jitter ball track.

Użyj causal lookback około 1 sekundy:

1. znajdź wcześniejszy controlled frame tej samej drużyny;
2. różnica czasu powinna być bliska `PROGRESSION_LOOKBACK_SEC`;
3. nie używaj punktów rozdzielonych zbyt dużym gapem;
4. oblicz progresję w metrach wzdłuż osi ataku.

```python
progress_m = (
    current_attack_progress - previous_attack_progress
) * attack_axis_length_m
```

```python
progression_bonus = (
    clamp01(progress_m / PROGRESSION_FULL_BONUS_M)
    * PROGRESSION_MAX_BONUS
)
```

Cofnięcie piłki nie generuje ujemnej kary — po prostu nie dostaje bonusu.

### 7.4. Frame score

```python
frame_pressure = confidence_weight * (
    position_score + progression_bonus
)
```

Score przypisz wyłącznie właścicielowi controlled possession.

---

## 8. Pass-attempt event bonuses

Proponowane stałe:

```python
COMPLETED_PASS_BASE_BONUS = 0.10
FAILED_PASS_BASE_BONUS = 0.035
PROGRESSIVE_PASS_MAX_BONUS = 0.25
PROGRESSIVE_PASS_FULL_BONUS_M = 10.0
```

Review multipliers:

```python
PASS_REVIEW_MULTIPLIERS = {
    "accepted": 1.00,
    "needs_review": 0.70,
    "uncertain": 0.45,
    "rejected": 0.00,
}
```

`needs_review` nie może mieć wagi zero, ponieważ automatycznie wygenerowane pass attempts domyślnie nie są manualnie zaakceptowane.

### Completed pass

```python
bonus = COMPLETED_PASS_BASE_BONUS
```

### Failed pass

Failed progressive attempt nadal może być oznaką presji, ale powinien ważyć dużo mniej:

```python
bonus = FAILED_PASS_BASE_BONUS
```

### Progressive bonus

```python
progressive_bonus = min(
    PROGRESSIVE_PASS_MAX_BONUS,
    max(0.0, forward_progress_m)
    / PROGRESSIVE_PASS_FULL_BONUS_M
    * PROGRESSIVE_PASS_MAX_BONUS,
)
```

Dodawaj go tylko, gdy:

- `is_progressive == true` albo `forward_progress_m > 0`;
- outcome jest `completed_pass` albo `failed_pass`;
- candidate nie jest excluded/rejected.

### Final event weight

```python
event_bonus = (
    (base_bonus + progressive_bonus)
    * clamp01(candidate.get("confidence") or 0.0)
    * review_multiplier
)
```

`final_stat_eligible` może zwiększyć wiarygodność/debug evidence, ale nie może być jedynym warunkiem wykorzystania eventu w eksperymentalnym momentum.

---

## 9. Bucketing

Domyślnie:

```text
bin_sec = 5
```

Dla każdego binu zachowaj:

- liczbę wszystkich possession samples;
- controlled samples Team A/B;
- sumę frame pressure Team A/B;
- event bonus Team A/B;
- controlled coverage;
- direction coverage;
- mean confidence;
- evidence counters.

Ważne: nie licz `mean()` wyłącznie po controlled frames konkretnej drużyny.

Poprawna normalizacja powinna uwzględniać czas trwania kontroli:

```python
team_a_positional = (
    sum(team_a_frame_scores)
    / max(all_samples_in_bin, 1)
)

team_b_positional = (
    sum(team_b_frame_scores)
    / max(all_samples_in_bin, 1)
)
```

Następnie:

```python
team_a_raw = team_a_positional + team_a_event_bonus
team_b_raw = team_b_positional + team_b_event_bonus
signed_raw = team_a_raw - team_b_raw
```

Dzięki temu pojedyncza klatka wysokiego possession nie waży tyle samo co długi okres nacisku.

---

## 10. Causal smoothing

Domyślnie:

```text
smoothing_window_sec = 30
```

Preferowana implementacja:

```text
causal EMA
```

```python
window_bins = max(1, round(smoothing_window_sec / bin_sec))
alpha = 2.0 / (window_bins + 1.0)

smoothed = alpha * current_raw + (1.0 - alpha) * previous_smoothed
```

Nie korzystaj z przyszłych punktów. Feature ma być kompatybilny z potencjalnym live processingiem.

---

## 11. Skalowanie do zakresu wykresu

Publiczny score:

```text
[-100, 100]
```

Użyj odpornej normalizacji wewnątrz meczu:

```python
robust_scale = percentile(
    abs(non_zero_smoothed_signed_raw),
    95,
)

scale = max(
    robust_scale,
    MIN_NORMALIZATION_SCALE,
)
```

```python
signed_score = clamp(
    smoothed_signed_raw / scale * 100.0,
    -100.0,
    100.0,
)
```

`MIN_NORMALIZATION_SCALE` ma zapobiec rozciąganiu bardzo słabego, niskiej jakości sygnału do ±100.

Dodatkowo:

```python
team_a_value = max(0.0, signed_score)
team_b_value = min(0.0, signed_score)
intensity = abs(signed_score) / 100.0
```

`dominant_team_label` ustawiaj tylko, gdy:

- `abs(signed_score)` przekracza mały dead zone, np. 5;
- point confidence przekracza minimalny próg.

W przeciwnym razie ustaw `null`.

---

## 12. Kontrakt `attacking_momentum.json`

```json
{
  "schema_version": "0.2.0",
  "generated_at": "ISO-8601",
  "source": "attacking_momentum_v1",
  "status": "completed",
  "experimental": true,
  "semantics": "relative_attacking_pressure_estimate_not_official_stat",
  "parameters": {
    "bin_sec": 5.0,
    "smoothing_window_sec": 30.0,
    "smoothing_method": "causal_ema",
    "normalization": "robust_abs_p95_with_floor",
    "position_exponent": 1.8,
    "progression_lookback_sec": 1.0,
    "uses_possession_candidates": true,
    "uses_pass_outcomes": true,
    "uses_restart_candidates": true,
    "camera_motion_reapplied": false
  },
  "summary": {
    "points": 240,
    "duration_sec": 1200.0,
    "known_possession_coverage": 0.71,
    "controlled_coverage": 0.43,
    "direction_coverage": 1.0,
    "scored_controlled_frames": 16400,
    "pass_attempts_used": 38,
    "completed_passes_used": 25,
    "failed_passes_used": 13,
    "excluded_non_pass_ignored": 9,
    "restart_passes_used": 5,
    "restart_setup_bonuses": 2,
    "normalization_scale": 0.42,
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
      "all_samples": 150,
      "team_a_controlled_samples": 86,
      "team_b_controlled_samples": 14,
      "team_a_positional_raw": 0.31,
      "team_b_positional_raw": 0.04,
      "team_a_event_bonus": 0.10,
      "team_b_event_bonus": 0.0,
      "team_a_raw": 0.41,
      "team_b_raw": 0.04,
      "signed_raw": 0.37,
      "smoothed_signed_raw": 0.32,
      "signed_score": 42.8,
      "team_a_value": 42.8,
      "team_b_value": 0.0,
      "dominant_team_label": "A",
      "confidence": 0.72,
      "controlled_coverage": 0.66,
      "direction_coverage": 1.0,
      "intensity": 0.428,
      "evidence": {
        "completed_passes": 1,
        "failed_passes": 0,
        "progressive_passes": 1,
        "restart_passes": 0,
        "restart_setup_bonuses": 0
      }
    }
  ],
  "warnings": [],
  "notes": [
    "Momentum is relative and normalized within this match.",
    "Values from different matches are not directly comparable in v1.",
    "Possession, passes and momentum remain experimental candidate layers."
  ]
}
```

---

## 13. Quality model

Wylicz:

```text
high
medium
low
```

Podstawowe sygnały:

- `known_possession_coverage`;
- `controlled_coverage`;
- `direction_coverage`;
- liczba scored controlled frames;
- czy `match_phase_config.summary.needs_review == true`;
- udział interpolated ball/player positions;
- czy istnieją pass candidates.

Proponowane reguły:

### High

- known possession coverage >= 0.75;
- controlled coverage >= 0.35;
- direction coverage >= 0.95;
- match phase config nie wymaga review;
- wystarczająca liczba scored samples.

### Medium

- known possession coverage >= 0.50;
- controlled coverage >= 0.20;
- direction coverage >= 0.80.

### Low

- wartości poniżej progów medium;
- bardzo mało controlled samples;
- brak kierunku ataku;
- prawie cały sygnał pochodzi z interpolacji.

Jeśli `match_phase_config.summary.needs_review == true`, maksymalna jakość momentum to `medium`.

Brak pass candidates nie powoduje automatycznie `low`. Wtedy model działa jako positional momentum i dodaje warning.

Warnings:

```text
Known possession coverage is below 50%.
Controlled possession coverage is too low for a stable momentum signal.
Attack direction is unknown for part of the match.
Match phase direction still uses an unconfirmed default.
Pass candidates were missing; momentum used positional possession only.
A high share of scored samples uses interpolated positions.
```

---

## 14. Integracja w `ball_possession.py`

W `build_ball_possession_analysis(...)` utwórz momentum po:

```python
_append_restart_pass_candidates(...)
event_docs["pass_review_report"] = ...
```

Dzięki temu momentum zobaczy pass candidates zawierające także restarty.

Przykładowa kolejność:

```python
restart_doc = build_restart_candidates_document(...)
_append_restart_pass_candidates(event_docs["pass_candidates"], restart_doc)
event_docs["pass_review_report"] = build_pass_review_report(...)

momentum_doc = build_attacking_momentum_document(
    candidates_doc,
    match_phase_config,
    pitch_width_m=parameters["pitch_width_m"],
    pitch_length_m=parameters["pitch_length_m"],
    pass_candidates_doc=event_docs["pass_candidates"],
    restart_candidates_doc=restart_doc,
)

report_doc = build_possession_report(...)
```

Rozszerz `_write_possession_artifacts(...)` o zapis:

```python
(match_dir / "attacking_momentum.json").write_text(
    json.dumps(momentum_doc, indent=2),
    encoding="utf-8",
)
```

Rozszerz `artifacts`:

```python
"attacking_momentum": "attacking_momentum.json"
```

Rozszerz return value:

```python
"attacking_momentum": momentum_doc
```

Momentum jest warstwą eksperymentalną, ale nie powinno być łapane w osobnym wewnętrznym `try/except` wewnątrz buildera. Błędy danych należy obsłużyć defensywnie i zwrócić pusty/low-quality dokument. Prawdziwy błąd programistyczny powinien być widoczny w testach.

Główna analiza już posiada outer try/except dla całej experimental possession layer.

---

## 15. Integracja z analysis paths

### `backend/app/services/analysis.py`

Dodaj do `BALL_ARTIFACT_FILENAMES`:

```python
"attacking_momentum": "attacking_momentum.json"
```

Nie wywołuj buildera drugi raz.

W finalnym report dodaj:

```python
"attacking_momentum_summary": (
    (possession or {})
    .get("attacking_momentum", {})
    .get("summary")
),
```

### `backend/app/services/chunked_analysis.py`

Nie generuj momentum osobno per chunk.

`build_ball_possession_analysis(...)` po merge wygeneruje je automatycznie.

Zmień komunikat progress na:

```text
Building possession, pass and attacking momentum candidate layers.
```

Dodaj `attacking_momentum_summary` do końcowego analysis report.

### `backend/app/services/post_yolo_reprocess.py`

Nie wywołuj buildera osobno.

Reprocess korzysta z `_build_ball_possession_artifacts(...)`, więc momentum powinno zostać przeliczone automatycznie bez ponownego YOLO.

Dodaj summary do reprocess report.

### `backend/scripts/reprocess_analysis.py`

Nie jest potrzebny nowy argument CLI. Momentum powinno powstawać zawsze wtedy, gdy:

```text
build_possession = true
```

---

## 16. Match package i API lokalnego meczu

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

W `GET /api/matches/{match_id}` dopisz:

```text
attacking_momentum.json
```

do jawnej listy optional JSON files.

Nie dodawaj momentum do:

```text
PACKAGE_REQUIRED_KEYS
```

Starsze package bez momentum muszą pozostać obsługiwane.

---

## 17. Public report

W:

```text
backend/app/services/public_match_report.py
```

dodaj:

```python
def _public_momentum_timeline(
    package: dict[str, Any],
) -> list[dict[str, Any]]:
    ...
```

Publiczny raport nie powinien kopiować pełnych raw/debug fields.

Minimalny punkt publiczny:

```json
{
  "index": 0,
  "minute": 1,
  "label": "0:05",
  "start_time_sec": 0.0,
  "end_time_sec": 5.0,
  "signed_score": 42.8,
  "team_a_value": 42.8,
  "team_b_value": 0.0,
  "dominant_team_label": "A",
  "confidence": 0.72,
  "controlled_coverage": 0.66,
  "intensity": 0.428
}
```

Do sekcji `ball` dodaj:

```json
{
  "attacking_momentum": {
    "experimental": true,
    "quality": "medium",
    "warnings": [],
    "timeline": []
  }
}
```

Nie mieszaj momentum z:

```text
possession_timeline
```

Possession i momentum mają odpowiadać na inne pytania.

---

## 18. Frontend types

W `client/src/types.ts` dodaj:

```typescript
export type AttackingMomentumPoint = {
  index: number;
  time_sec: number;
  start_time_sec: number;
  end_time_sec: number;
  all_samples?: number;
  team_a_controlled_samples?: number;
  team_b_controlled_samples?: number;
  team_a_positional_raw?: number;
  team_b_positional_raw?: number;
  team_a_event_bonus?: number;
  team_b_event_bonus?: number;
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
- `AnalysisReport.attacking_momentum_summary`;
- `PublicMatchReport.ball`.

Public report może mieć osobny uproszczony typ `PublicAttackingMomentumPoint`.

Nie modyfikuj `PassCandidate` w ramach tego zadania — aktualne pola outcome/evidence już istnieją.

---

## 19. Współdzielony komponent wykresu

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

Użyj Recharts, które już jest zależnością repo.

Preferowany wykres:

```text
BarChart
```

Wymagania:

- Y domain `[-100, 100]`;
- `ReferenceLine y={0}`;
- Team A nad osią;
- Team B pod osią;
- kolory z `display_color` drużyn;
- fallback colors tylko gdy brak konfiguracji;
- tooltip z czasem, dominant team, score, confidence i coverage;
- ograniczona liczba X ticks;
- czytelny render dla 200–500 punktów;
- `low quality` badge i warnings.

Opis:

```text
Eksperymentalna estymacja nacisku ofensywnego.
Nad osią: Team A. Pod osią: Team B.
```

Nie używaj cumulative stacking jak w aktualnym possession chart.

---

## 20. Lokalny raport

W:

```text
client/src/components/MatchReportContent.tsx
```

Rozszerz `MatchReportSource`:

```typescript
attackingMomentum?: GenericRow;
```

Uzupełnij:

```text
sourceFromLocalMatch
sourceFromPublishedPackage
```

Dodaj kartę po sekcji `Posiadanie i podania`.

Lokalny raport może pokazywać pełniejszy tooltip i quality warnings.

Nie usuwaj istniejących statystyk possession/pass.

---

## 21. Publiczny raport

W:

```text
client/src/components/PublicMatchReportContent.tsx
```

Aktualny wykres w sekcji `Przebieg meczu` pokazuje cumulative possession.

Zmień jego tytuł na:

```text
Posiadanie w czasie
```

Następnie dodaj osobną kartę:

```text
Momentum
```

Użyj współdzielonego `AttackingMomentumChart`.

Nie zastępuj possession chart momentum chartem.

---

## 22. Backend tests

Dodaj:

```text
backend/tests/test_attacking_momentum.py
```

Wymagane testy pure model:

1. final third daje większy positional pressure niż własna połowa;
2. ten sam punkt daje odwrotny wynik dla przeciwnego kierunku;
3. druga połowa używa kierunku z właściwego periodu;
4. camera motion nie jest ponownie nakładany — builder używa wyłącznie `ball_position_m`;
5. progression over lookback zwiększa pressure;
6. sąsiedni jitter nie daje dużego progression bonus;
7. completed pass daje większy bonus niż failed pass;
8. progressive completed pass daje większy bonus niż zwykły completed pass;
9. `excluded_non_pass` daje zero;
10. rejected candidate daje zero;
11. needs_review candidate nadal może dać ograniczony bonus;
12. canonical team pochodzi z `count_for_team_label`;
13. restart pass nie jest liczony podwójnie;
14. free/contested/unknown nie tworzą score Team A/B;
15. high confidence daje większy raw score niż low confidence;
16. dłuższy okres nacisku waży więcej niż pojedyncza klatka;
17. `signed_score` zawsze mieści się w `[-100, 100]`;
18. smoothing jest causal;
19. wynik jest deterministyczny i posortowany;
20. brak pass/restart docs nie powoduje błędu;
21. pusty input zwraca `points: []`, warning i quality `low`.

Dodaj również test integracyjny do istniejącego `test_ball_possession.py`:

- wynik `build_ball_possession_analysis` zawiera `attacking_momentum`;
- artifacts zawierają `attacking_momentum.json`;
- plik jest zapisany.

Rozszerz odpowiednie testy package/public report:

- momentum jest optional;
- starszy package bez momentum działa;
- public report zawiera uproszczoną timeline.

---

## 23. Pass quality regression check

Nowy kod nie może zmieniać pass detectora ani goldsetu.

Przed i po implementacji uruchom istniejący evaluator na tym samym materiale:

```bash
cd backend
python scripts/evaluate_pass_candidates.py \
  --match-id <MATCH_ID> \
  --goldset tests/fixtures/pass_goldset_1st_analysis.json
```

Cel tego kroku:

- upewnić się, że momentum tylko konsumuje outcome model;
- nie maskować regresji pass detection zmianą wag momentum;
- sprawdzić, ile `excluded_non_pass` jest poprawnie ignorowanych.

Nie ustawiaj sztywnego globalnego progu precision/recall w acceptance criteria dla momentum, ponieważ goldset jest jeszcze mały. Zapisz wynik before/after w podsumowaniu zadania.

---

## 24. Walidacja

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

Post-YOLO reprocess:

```bash
cd backend
python scripts/reprocess_analysis.py \
  --source-dir <MATCH_OR_REPROCESS_DIR> \
  --build-possession
```

Ręcznie sprawdź:

- starszy mecz bez momentum;
- nowy lokalny mecz z momentum;
- pierwszą i drugą połowę;
- zmianę stron;
- okresy wysokiego possession bez progresji;
- szybki atak z małym possession;
- progressive completed pass;
- failed progressive attempt;
- restart pass;
- low coverage warning;
- match package;
- public report;
- czy momentum nie jest tylko kopią possession.

---

## 25. Acceptance criteria

- [ ] istnieje `backend/app/services/attacking_momentum.py`;
- [ ] builder jest pure i nie czyta video/filesystemu;
- [ ] momentum powstaje w `build_ball_possession_analysis(...)`;
- [ ] nie ma osobnego liczenia per chunk;
- [ ] generowany jest `attacking_momentum.json`;
- [ ] używane są post-stabilization `ball_position_m`;
- [ ] camera motion nie jest nakładany drugi raz;
- [ ] score mieści się w `[-100, 100]`;
- [ ] Team A jest nad osią, Team B pod osią;
- [ ] używany jest `direction_for_team_at_time(...)`;
- [ ] działa zmiana stron;
- [ ] final third daje większy pressure;
- [ ] progresja jest liczona przez causal lookback, nie frame-to-frame;
- [ ] pass bonus używa `outcome` i `count_for_team_label`;
- [ ] `excluded_non_pass` nie wpływa na momentum;
- [ ] completed pass waży więcej niż failed pass;
- [ ] progressive pass może podnieść pressure;
- [ ] restart nie jest liczony podwójnie;
- [ ] unknown/free/contested nie dostają właściciela;
- [ ] brak pass candidates nie psuje feature;
- [ ] artefakt trafia do analysis report/run artifacts;
- [ ] artefakt trafia do match package jako optional;
- [ ] GET match zwraca momentum, jeśli plik istnieje;
- [ ] public report zawiera uproszczoną timeline;
- [ ] lokalny i publiczny raport pokazują wykres;
- [ ] possession chart pozostaje osobno;
- [ ] UI pokazuje `experimental` i quality;
- [ ] backend tests przechodzą;
- [ ] pass goldset wynik before/after jest zapisany;
- [ ] `pnpm typecheck` przechodzi;
- [ ] `pnpm build` przechodzi;
- [ ] starsze raporty nadal działają.

---

## 26. Kolejność implementacji

### Milestone 1 — pure model

- nowy serwis;
- normalizacja inputów;
- positional score;
- progression lookback;
- pass outcome bonuses;
- restart deduplication;
- bucketing, EMA, normalization;
- unit tests.

### Milestone 2 — central pipeline integration

- `ball_possession.py`;
- artifact writer;
- return document;
- `BALL_ARTIFACT_FILENAMES`;
- analysis/chunked/reprocess summaries.

### Milestone 3 — package i public report

- optional package field;
- embedded JSON;
- GET match optional file;
- public timeline;
- backward compatibility tests.

### Milestone 4 — frontend

- types;
- `AttackingMomentumChart`;
- local report;
- public report;
- CSS;
- typecheck/build.

### Milestone 5 — sample validation

- reprocess bez YOLO;
- pass goldset before/after;
- pierwsza/druga połowa;
- porównanie momentum z possession;
- ręczne porównanie z video.

---

## 27. Reguły dla agenta

1. Przeczytaj pełne aktualne wersje plików przed zmianą.
2. Nie używaj poprzedniego założenia, że pass/possession fixes są poza repo — są już częścią baseline.
3. Nie refaktoryzuj pass detectora, possession detectora ani camera motion w ramach tego zadania.
4. Nie licz momentum z raw bbox, raw tracker IDs ani image pixels.
5. Nie stosuj homografii/camera-motion po raz drugi.
6. Nie dodawaj osobnego momentum runnera do każdej ścieżki analizy.
7. Nie generuj momentum per chunk.
8. Nie dodawaj momentum do required package fields.
9. Nie usuwaj possession timeline.
10. Nie używaj `review_status == accepted` jako jedynego warunku event bonus.
11. Ignoruj `excluded_non_pass`.
12. Deduplikuj restarty już obecne w pass candidates.
13. Każda heurystyka ma być nazwaną stałą, zapisaną w `parameters` i pokrytą testem.
14. Kod musi działać przy brakujących optional documents.
15. Nie commituj generated reports, video, heatmap PNG, Playwright logs ani backend storage.
16. Na końcu wypisz:
    - zmienione pliki;
    - nowe artefakty;
    - uruchomione testy;
    - wynik pass goldset before/after;
    - znane ograniczenia;
    - przykładowy fragment `attacking_momentum.json`.

---

## 28. Definition of Done

MVP jest zakończone, gdy wykres potrafi odróżnić:

```text
Długie, bezpieczne possession przy własnej bramce
```

od:

```text
Krótkiego, szybkiego ataku zakończonego progresywnym podaniem w wysokiej strefie
```

Drugi przypadek powinien generować wyższe attacking momentum nawet wtedy, gdy drużyna ma mniejszy całkowity possession share.

Model nadal nie odpowiada na pytanie:

```text
Jakie jest prawdopodobieństwo zdobycia następnego gola?
```

To będzie osobny etap wymagający shot candidates, xG/EPV i oznaczonego zbioru danych.
