# Current Pipeline & Attacking Momentum — post-implementation review

## 0. Cel dokumentu

Ten dokument jest **przeglądem aktualnego flow analizy piłki oraz wdrożenia `attacking_momentum`** po implementacji feature.

Nie jest to polecenie dodawania kolejnych statystyk. Najpierw należy usunąć ryzyko publikowania niespójnych albo nieaktualnych danych po manualnym review.

Dokument ma służyć agentowi jako:

- lista wykrytych problemów w obecnym przepływie danych;
- plan stabilizacji `attacking_momentum`;
- plan centralizacji przebudowy artefaktów zależnych;
- kontrakt freshness i invalidation dla package/public report;
- kolejność prac przed implementacją kolejnych analiz z roadmapy.

Najważniejszy wniosek:

> Sama pierwsza implementacja momentum jest poprawnie wpięta do pełnej analizy, ale obecny flow review nie przebudowuje wszystkich zależnych artefaktów. W efekcie `pass_candidates.json`, `attacking_momentum.json`, package i public report mogą reprezentować różne stany review tego samego meczu.

---

## 1. Baseline repozytorium

Analiza została wykonana dla commita:

```text
2c0188e8cc86c103c433579885dc8f33ba071b2b
```

Commit implementuje między innymi:

- `backend/app/services/attacking_momentum.py`;
- integrację momentum w `build_ball_possession_analysis(...)`;
- zapis `attacking_momentum.json`;
- osadzenie momentum w match package;
- uproszczony public report;
- lokalny i publiczny wykres Recharts;
- backend unit tests dla heurystyk momentum.

Przed realizacją taska agent ma pobrać aktualny `HEAD` i potwierdzić, czy opisane problemy nadal występują.

---

# 2. Ocena obecnej implementacji

## 2.1. Elementy wykonane poprawnie

### Architektura bazowa

Momentum zostało zintegrowane w odpowiednim miejscu:

```text
backend/app/services/ball_possession.py
build_ball_possession_analysis(...)
```

Dzięki temu pełna analiza single-pass, chunked analysis i post-YOLO reprocess korzystają z tego samego canonical flow.

### Pure scoring service

`build_attacking_momentum_document(...)`:

- nie wykonuje I/O;
- używa wejściowych dokumentów;
- nie stosuje ponownie homografii ani camera motion;
- respektuje `match_phase_config`;
- działa bez pass/restart candidates;
- generuje jawny, eksperymentalny dokument;
- zapisuje parametry modelu w output.

### Semantyka produktu

UI wyraźnie pokazuje:

```text
Momentum (experimental)
```

Momentum nie zastępuje possession timeline i nie jest przedstawiane jako xG ani oficjalna statystyka.

### Integracja artefaktów

Momentum zostało dodane do:

- analysis artifacts;
- analysis report summary;
- match package;
- artifact endpoint;
- public report;
- local report;
- public viewer.

### Testy heurystyk

Istnieją testy dla:

- kierunku ataku;
- zmiany stron;
- używania `ball_position_m`;
- progression lookback;
- confidence;
- pass outcomes;
- review multipliers;
- restart deduplication;
- causal smoothing;
- deterministycznego outputu;
- pustego inputu.

To jest dobra baza MVP.

---

## 2.2. Ocena gotowości

Aktualny status feature:

```text
implemented: yes
architecturally integrated: yes
safe after manual review changes: no
safe to publish without freshness verification: no
validated on real-match goldset: no
ready as experimental local visualization: yes
ready as trusted commercial metric: no
```

---

# 3. P0 — niespójny rebuild downstream artifacts

To jest najważniejszy problem obecnego flow.

## 3.1. Obecny canonical build podczas pełnej analizy

Pełna analiza wykonuje poprawną kolejność:

```text
possession_candidates
→ possession_segments
→ contact_candidates
→ event_candidates
→ pass_candidates z possession evidence
→ restart_candidates
→ append restart pass candidates
→ pass_review_report
→ attacking_momentum
→ write all artifacts
```

Problem pojawia się po zakończeniu analizy, gdy operator wykonuje manual review.

---

## 3.2. Pass review nie odświeża momentum

Endpoint:

```text
PUT /api/matches/{match_id}/pass-candidates/review
```

aktualizuje:

```text
pass_candidates.json
pass_review_report.json
```

ale nie przebudowuje:

```text
attacking_momentum.json
analysis_report.json
match_package.json / public_report.json, jeśli istnieją
```

Przykład błędu:

```text
1. pass candidate ma review_status = needs_review
2. momentum używa multiplier 0.70
3. operator zmienia pass na rejected
4. pass_candidates.json ma rejected
5. attacking_momentum.json nadal zawiera bonus z poprzedniego stanu
6. package może opublikować oba dokumenty jednocześnie
```

To tworzy wewnętrznie sprzeczny raport.

### Wymagana poprawka

Po zapisaniu pass review należy zawsze przebudować co najmniej:

```text
pass_review_report
attacking_momentum
momentum summary w analysis report, jeśli jest przechowywany jako snapshot
publish/package readiness
```

Nie należy ponownie uruchamiać YOLO ani possession.

---

## 3.3. Match phase update wykonuje częściowy i niepełny rebuild

`save_match_phase_config(...)` wywołuje obecnie prywatne:

```text
_refresh_pass_candidates(...)
```

które buduje pass candidates z `event_candidates.json`.

Problemy:

1. rebuild nie przekazuje `possession_candidates.json` do pass buildera;
2. release/trajectory evidence może więc przejść na fallback `event_endpoints_only`;
3. restart pass candidates nie są ponownie dopisywane;
4. attacking momentum nie jest ponownie generowane;
5. package/public report nie są oznaczane jako stale;
6. `match_phase_config.py` posiada side effect należący do orchestration layer.

### Przykładowa konsekwencja

```text
operator ustawia poprawny second_half_start_time_sec
→ regular passes dostają nowy direction/progression
→ restart passes mogą zniknąć z pass_candidates.json
→ momentum nadal pokazuje stary kierunek drugiej połowy
```

### Wymagana poprawka

`match_phase_config.py` powinien odpowiadać wyłącznie za:

```text
build
normalize
load
save configuration
resolve direction
```

Przebudową downstream artifacts powinien zarządzać osobny application/orchestration service.

---

## 3.4. Contact review może nadpisać pass review i usunąć restarty

`save_contact_candidate_reviews(...)` zapisuje contact review, a następnie wywołuje:

```text
write_event_candidate_artifacts(...)
```

Obecny writer:

- buduje ponownie event candidates;
- buduje ponownie pass candidates;
- nie przekazuje possession document;
- zapisuje pass candidates bez jawnego zachowania istniejącego manual review;
- nie dopisuje restart pass candidates;
- nie przebudowuje momentum.

To jest bardziej niebezpieczne niż zwykły stale artifact.

Możliwy scenariusz:

```text
1. operator przejrzał 30 podań
2. później odrzucił jeden błędny contact candidate
3. event/pass artifacts zostały wygenerowane ponownie
4. manualne review podań może zostać utracone albo przypisane niepoprawnie
5. restart passes mogą zniknąć
6. momentum pozostaje ze starego stanu
```

### Wymagana poprawka

Zmiana contact review musi uruchomić pełny, kontrolowany rebuild downstream:

```text
contact_candidates
→ event_candidates
→ pass_candidates with possession evidence
→ append restart passes
→ restore valid manual pass reviews
→ pass review report
→ attacking momentum
→ freshness/readiness
```

---

# 4. P0 — stabilne identyfikatory eventów i review

## 4.1. Problem z indeksowymi ID

Regular pass candidates otrzymują ID w stylu:

```text
pass-0001
pass-0002
```

Event candidates również otrzymują sekwencyjne ID:

```text
event-0001
event-0002
```

Po odrzuceniu albo dodaniu wcześniejszego contact candidate kolejność może się przesunąć.

Obecne zachowanie manual review używa pary:

```text
source_event_id
target_event_id
```

To jest lepsze niż samo `candidate_id`, ale event IDs także są generowane sekwencyjnie.

### Ryzyko

Manual review może:

- zniknąć po rebuildzie;
- zostać podpięte do innej pary kontaktów;
- przetrwać tylko przypadkiem, gdy kolejność eventów się nie zmieni.

---

## 4.2. Canonical stable key

Każdy pass candidate powinien mieć stabilne pole:

```text
candidate_key
```

Dla regularnego podania:

```text
contact-pair:{source_contact_candidate_id}:{target_contact_candidate_id}
```

Dla restartu:

```text
restart:{restart_candidate_id}
```

Pole powinno bazować na stabilnych source candidate IDs, nie na indeksie wygenerowanego eventu.

Przykład:

```json
{
  "candidate_id": "pass-0007",
  "candidate_key": "contact-pair:contact-0041:contact-0042",
  "source_candidate_id": "contact-0041",
  "target_candidate_id": "contact-0042"
}
```

`candidate_id` może pozostać czytelnym ID prezentacyjnym, ale review persistence ma używać `candidate_key`.

---

## 4.3. Migracja istniejących review

Przy odczycie istniejących pass reviews zastosuj fallback w kolejności:

```text
1. candidate_key
2. source_candidate_id + target_candidate_id
3. restart_candidate_id
4. legacy source_event_id + target_event_id
5. legacy candidate_id tylko jako ostatni fallback
```

Nie stosuj legacy review, jeśli więcej niż jeden nowy candidate pasuje do tego samego klucza.

W takim przypadku:

```text
review_status = needs_review
review_migration_warning = ambiguous_existing_review
```

---

# 5. Proponowany canonical rebuild service

## 5.1. Nowy moduł

Dodaj:

```text
backend/app/services/ball_event_rebuild.py
```

Nazwa może zostać zmieniona, ale odpowiedzialność ma pozostać wyraźna.

Główna funkcja:

```python
def rebuild_ball_event_dependents(
    match_path: Path,
    *,
    trigger: str,
    rebuild_events: bool,
    rebuild_passes: bool,
    rebuild_momentum: bool,
    preserve_manual_reviews: bool = True,
) -> dict[str, Any]:
    ...
```

Dopuszczalna jest również prostsza wersja z enum/planem przebudowy.

---

## 5.2. Canonical inputs z filesystemu

Service ma ładować aktualne canonical documents:

```text
match.json
possession_candidates.json
contact_candidates.json
event_candidates.json
restart_candidates.json
match_phase_config.json
pass_candidates.json jako źródło istniejącego review
```

Nie może korzystać z przypadkowo przekazanych, niepełnych dokumentów z endpointu.

---

## 5.3. Rebuild po contact review

```text
load reviewed contact candidates
→ build event candidates
→ build pass candidates z possession candidates
→ append restart pass candidates
→ restore manual pass reviews by candidate_key
→ update pass summary
→ build pass review report
→ build attacking momentum
→ write all outputs atomically
```

---

## 5.4. Rebuild po match phase update

```text
load current event candidates
→ rebuild pass candidates z current possession candidates i new phase config
→ append restart passes
→ restore manual pass reviews
→ build attacking momentum with new phase config
→ write outputs atomically
```

Match phase update nie powinien przebudowywać contact candidates ani possession.

---

## 5.5. Rebuild po pass review

```text
save pass reviews
→ update pass summary/report
→ rebuild attacking momentum only
→ update freshness/readiness
```

Nie generuj na nowo kandydatów podań w tym flow.

---

## 5.6. Rebuild po post-YOLO reprocess

Pełny shared flow może nadal korzystać z:

```text
build_ball_possession_analysis(...)
```

Jednak końcowy etap zapisu powinien używać tych samych helperów dokumentów i freshness metadata co review rebuild.

Nie utrzymuj dwóch różnych definicji tego, jak powstaje finalny zestaw:

```text
event + pass + restart + momentum
```

---

# 6. P0 — freshness i lineage artefaktów

## 6.1. Problem

Obecny package build ma specjalne odświeżanie dla:

```text
resolved_player_stats
```

ale nie posiada odpowiednika dla:

```text
event_candidates
pass_candidates
attacking_momentum
possession_report
```

Package osadza dokument, jeżeli plik istnieje, niezależnie od tego, czy powstał przed czy po ostatnim review inputu.

---

## 6.2. `generated_from`

Każdy derived artifact powinien zapisywać lineage.

Przykład dla momentum:

```json
{
  "generated_at": "...",
  "generated_from": {
    "possession_candidates": {
      "updated_at": "...",
      "content_hash": "sha256:..."
    },
    "pass_candidates": {
      "updated_at": "...",
      "content_hash": "sha256:..."
    },
    "restart_candidates": {
      "generated_at": "...",
      "content_hash": "sha256:..."
    },
    "match_phase_config": {
      "updated_at": "...",
      "content_hash": "sha256:..."
    }
  },
  "algorithm": {
    "name": "attacking_momentum_v1",
    "version": "0.2.0"
  }
}
```

Preferowanym source of truth jest hash z canonical JSON po stabilnej serializacji, nie samo porównanie timestamp stringów.

---

## 6.3. Freshness status

Dodaj helper:

```python
def artifact_freshness(match_path: Path, artifact_name: str) -> dict[str, Any]:
    ...
```

Output:

```json
{
  "status": "fresh",
  "stale_inputs": [],
  "missing_inputs": []
}
```

Statusy:

```text
fresh
stale
missing_inputs
legacy_unknown
```

Dla starszych dokumentów bez lineage:

```text
legacy_unknown
```

Nie oznaczaj ich automatycznie jako fresh.

---

## 6.4. Package behavior

Przed build/publish package:

```text
ensure_ball_event_artifacts_fresh(match_path)
```

Dopuszczalne zachowania:

### Opcja rekomendowana

Automatycznie przebuduj tanie derived artifacts:

```text
passes
reports
momentum
readiness
```

bez ponownego YOLO.

### Gdy rebuild nie jest możliwy

- nie publikuj stale artifact jako aktualnego;
- ustaw package warning;
- ustaw optional feature status na unavailable/stale;
- nie blokuj całego package, jeżeli feature jest eksperymentalny i optional.

Przykład:

```json
{
  "analytics_readiness": {
    "attacking_momentum": {
      "status": "not_available",
      "reason": "stale_after_pass_review"
    }
  }
}
```

---

# 7. P1 — poprawki do algorytmu attacking momentum

## 7.1. Progression bonus musi respektować ciągłość possession

### Obecne zachowanie

Historia progression jest przechowywana osobno dla Team A i Team B.

Wcześniejszy controlled frame tej samej drużyny może zostać użyty po krótkiej przerwie, nawet gdy pomiędzy wystąpiło:

```text
opponent controlled
free
contested
unknown
possession loss
```

Jeżeli luka mieści się w `1.5 s`, algorytm może przyznać progression bonus za dwa punkty należące do różnych akcji.

### Wymagana zmiana

Progression history ma być związana z ciągłą sekwencją possession.

Możliwe rozwiązania:

#### Preferowane

Użyj `possession_segments.json` albo nadaj frame’om `segment_id`.

Progression może porównywać tylko klatki:

```text
same segment_id
same team_label
same match phase period
same attack_direction
```

#### Minimalne

Resetuj historię, gdy:

- status nie jest `controlled`;
- controlling team się zmienia;
- czas od poprzedniej klatki przekracza limit;
- period ID się zmienia;
- attack direction się zmienia.

### Testy

Dodaj testy:

```text
A controlled → B controlled → A controlled within 1.5 s = no A progression carryover
A controlled → contested → A controlled = no progression carryover
A controlled across second-half boundary = no progression carryover
same continuous A segment = progression bonus retained
```

---

## 7.2. Failed progressive pass jest obecnie zbyt wysoko premiowany

Aktualny wzór dodaje ten sam progressive bonus dla:

```text
completed_pass
failed_pass
```

Różni się tylko base bonus:

```text
completed = 0.10
failed = 0.035
```

Przy dużym `forward_progress_m` failed pass może otrzymać prawie cały bonus progresji i zbliżyć się do wartości udanego podania.

### Problem semantyczny

Nieudana długa piłka lub wybicie zaklasyfikowane jako failed pass może wygenerować silny peak momentum mimo natychmiastowej straty.

### Proponowana zmiana

Dodaj outcome multiplier dla progressive component:

```python
PROGRESSIVE_OUTCOME_MULTIPLIERS = {
    "completed_pass": 1.0,
    "failed_pass": 0.20,
}
```

Wersja bardziej konserwatywna:

```text
failed pass dostaje tylko FAILED_PASS_BASE_BONUS
bez progressive bonus
```

Wybór ma zostać sprawdzony na realnych klipach.

### Acceptance

Failed progressive pass nie może mieć wartości większej niż completed regular pass przy tym samym confidence, chyba że zostanie to świadomie uzasadnione w modelu.

---

## 7.3. Restart setup bonus powinien respektować review multipliers

Aktualny restart setup bonus:

- odrzuca tylko `rejected`;
- dla `needs_review` i `uncertain` używa pełnego confidence;
- nie korzysta z `PASS_REVIEW_MULTIPLIERS` ani osobnych restart multipliers.

### Proponowana zmiana

Dodaj:

```python
RESTART_REVIEW_MULTIPLIERS = {
    "accepted": 1.0,
    "needs_review": 0.6,
    "uncertain": 0.35,
    "rejected": 0.0,
}
```

Setup bonus powinien również wymagać sensownego typu restartu:

```text
corner
kick_in
```

Nie premiuj:

```text
ignored_goal_line_restart
restart_unknown_actor_team
```

---

## 7.4. Event-only bin ma score, ale confidence równe zero

Point confidence jest obecnie wyliczany z:

```text
mean controlled possession confidence
direction coverage
controlled coverage
```

Bin zawierający pass/restart bonus bez controlled samples może mieć:

```text
signed_score != 0
confidence = 0
```

Chart nadal rysuje pełny obszar score.

### Proponowana zmiana

Zbieraj osobno:

```text
positional_confidence
event_confidence
```

Następnie:

```python
combined_confidence = weighted_confidence(
    positional_contribution,
    event_contribution,
)
```

Jeśli event contribution tworzy większość score, confidence musi pochodzić z candidate confidence i review multiplier.

W JSON zachowaj:

```json
{
  "confidence": 0.62,
  "confidence_components": {
    "positional": 0.0,
    "events": 0.62
  }
}
```

---

## 7.5. EMA powinno resetować się na granicy okresu i długiej przerwy

Aktualne causal EMA przechodzi przez wszystkie biny jednym ciągiem.

Może to powodować:

- przeniesienie przewagi z pierwszej połowy na początek drugiej;
- powolne wygaszanie przez halftime;
- bleed po długim fragmencie bez danych;
- sztuczny ramp-up od zera na początku meczu.

### Proponowana zmiana

Każdy bin powinien znać:

```text
period_id
has_data
```

Resetuj smoothing, gdy:

- zmienia się `period_id`;
- luka bez wiarygodnych danych przekracza np. `15–20 s`;
- zaczyna się nowy configured period.

Pierwszy valid point okresu inicjalizuj:

```python
previous = current
```

zamiast:

```python
previous = 0
```

---

## 7.6. Duration i bin boundaries

Aktualny duration jest wyprowadzany z ostatniego frame/eventu użytego przez momentum.

Skutki:

- wykres może kończyć się przed końcem meczu;
- końcowe fragmenty `unknown` mogą zniknąć;
- event dokładnie na granicy binu może trafić do wcześniejszego binu przez clamp;
- local i public timeline mogą mieć inną długość niż video.

### Proponowana zmiana

Rozszerz builder o:

```python
match_duration_sec: float | None = None
```

Canonical duration:

```text
configured in-play period end
fallback: video duration
fallback: last possession sample + sample interval
```

Bin count:

```python
floor(duration_sec / bin_sec) + 1
```

lub jawna logika half-open ranges:

```text
[start, end)
```

z wyjątkiem ostatniego binu.

---

## 7.7. Event bonus cap per bin

Positional contribution jest średnią po samples, a event bonus jest dodawany jako suma.

W binie z wieloma false-positive pass candidates eventy mogą zdominować całość.

### Proponowana zmiana

Dodaj:

```python
MAX_EVENT_BONUS_PER_TEAM_PER_BIN
```

oraz debug fields:

```text
uncapped_event_bonus
capped_event_bonus
capped_events_count
```

Alternatywnie zastosuj diminishing returns:

```python
normalized_bonus = max_bonus * (1 - exp(-raw_bonus / scale))
```

Na MVP prosty cap jest bardziej czytelny.

---

## 7.8. Quality vs product readiness

Aktualne `quality = high` może zostać osiągnięte przy controlled coverage około `35%`.

To może być wystarczające dla jakości samego sygnału eksperymentalnego, ale nie odpowiada wymaganiom produktu opisanym w analytics readiness roadmap.

### Wymagana zmiana

Rozdziel:

```text
signal_quality
product_readiness
```

Przykład:

```json
{
  "signal_quality": "high",
  "product_readiness": "experimental",
  "readiness_reasons": [
    "team possession coverage below 75%",
    "pass review incomplete"
  ]
}
```

Statusy readiness:

```text
ready
ready_with_review
experimental
not_available
```

Momentum może mieć dobry lokalny signal quality i jednocześnie pozostać `experimental` do publikacji.

---

## 7.9. Pressure share jest podatne na błędną interpretację

Summary zawiera:

```text
team_a_pressure_share
team_b_pressure_share
```

Ponieważ momentum jest normalizowane wewnątrz meczu i łączy różne komponenty, share nie jest odpowiednikiem possession ani oficjalnej przewagi ofensywnej.

### Proponowana zmiana

- nie eksponuj share jako głównej metryki;
- zmień nazwę techniczną na:

```text
raw_modeled_pressure_share_within_match
```

- dodaj notatkę, że nie jest porównywalne między meczami;
- nie używaj go do rankingów drużyn przed kalibracją.

---

# 8. P1 — UI i public report

## 8.1. Low-confidence points

Wykres rysuje wszystkie punkty podobnie, niezależnie od confidence.

### Propozycja

- dla confidence poniżej progu zmniejsz opacity;
- dla `dominant_team_label = null` pokazuj neutralny segment;
- opcjonalnie ukrywaj score, jeśli confidence jest bardzo niskie;
- w tooltipie pokaż źródła evidence.

Minimalny wariant:

```text
confidence < 0.15 → opacity 0.2
confidence 0.15–0.40 → opacity 0.5
confidence > 0.40 → normal
```

---

## 8.2. Click-to-video

Momentum powinno być użyteczne jako nawigacja do sytuacji.

Dodaj opcjonalny callback:

```ts
onPointSelect?: (point: AttackingMomentumPoint) => void
```

Kliknięcie powinno otwierać video:

```text
point.time_sec - 5 s
```

Docelowo wybrany zakres:

```text
start = max(0, point.start_time_sec - 5)
end = point.end_time_sec + 5
```

To ma większą wartość produktową niż kolejne summary ratio.

---

## 8.3. Publicowanie low-quality momentum

Public report obecnie może zawierać timeline niezależnie od quality.

### Proponowana zmiana

Public report powinien mieć:

```json
{
  "attacking_momentum": {
    "status": "available",
    "signal_quality": "medium",
    "product_readiness": "experimental",
    "timeline": []
  }
}
```

Jeśli stale albo niewystarczające dane:

```json
{
  "status": "not_available",
  "reasons": ["stale_after_pass_review"],
  "timeline": []
}
```

Nie publikuj starego wykresu tylko dlatego, że plik nadal istnieje.

---

## 8.4. User-facing warnings

Backend warnings są techniczne i angielskie.

W UI należy mapować je na stabilne warning codes:

```text
low_known_coverage
low_controlled_coverage
unknown_attack_direction
phase_needs_review
missing_pass_candidates
high_interpolation_share
stale_inputs
```

JSON może zawierać:

```json
{
  "code": "low_controlled_coverage",
  "details": {"value": 0.18, "required": 0.20}
}
```

UI lokalizuje komunikat po kodzie.

---

# 9. P1 — quality dashboard i readiness flow

Obecny `analysis_quality_report` koncentruje się głównie na:

```text
tracking
identity stability
movement stats
team assignment
```

Nie posiada pełnego kontraktu dla:

```text
ball tracking
possession
passes
momentum
artifact freshness
```

## 9.1. Rozszerzenie quality report

Dodaj komponenty:

```text
ball_tracking
possession
pass_candidates
attacking_momentum
artifact_freshness
```

Przykładowe metryki:

### Ball

```text
detected_coverage_in_play
known_coverage_in_play
longest_unknown_gap_sec
suspected_hijack_count
```

### Possession

```text
known_state_coverage
team_owner_coverage
controlled_coverage
short_switch_count
transition_goldset_precision
```

### Passes

```text
precision
recall
goldset_events
needs_review_count
manual_review_count
restart_pass_count
```

### Momentum

```text
signal_quality
freshness
controlled_coverage
direction_coverage
interpolated_share
event_share_of_total_score
```

---

## 9.2. `analytics_readiness.json`

Dodaj osobny dokument:

```text
analytics_readiness.json
```

Przykład:

```json
{
  "schema_version": "0.1.0",
  "generated_at": "...",
  "features": {
    "attacking_momentum": {
      "status": "experimental",
      "fresh": true,
      "must_have": {
        "match_phase_confirmed": true,
        "possession_available": true,
        "minimum_controlled_coverage": false
      },
      "warnings": ["low_controlled_coverage"]
    },
    "turnover_map_team": {
      "status": "not_available",
      "missing": ["validated_possession_transitions"]
    }
  }
}
```

Nie wkładaj całej logiki readiness do komponentów React.

---

# 10. P1 — testy integracyjne i CI

## 10.1. Brak pełnej walidacji CI

Dla analizowanego commita dostępny jest deploy Vercel, ale brak widocznego workflow uruchamiającego backend test suite.

Frontend deploy success nie potwierdza poprawności Python pipeline.

### Dodaj workflow

```text
.github/workflows/ci.yml
```

Minimum:

```text
backend: python -m unittest discover -s tests
client: pnpm install --frozen-lockfile
client: pnpm typecheck
client: pnpm build
```

Jeżeli backend zależy od ciężkich modeli, unit tests nie mogą pobierać modeli ani odpalać YOLO.

---

## 10.2. Wymagane testy flow

### Pass review cascade

```text
save pass as rejected
→ attacking_momentum changes
→ momentum generated_from hash matches pass file
```

### Match phase cascade

```text
change second-half direction
→ pass directions updated
→ possession release evidence retained
→ restart passes retained
→ momentum second half updated
```

### Contact review cascade

```text
reject contact
→ event count changes
→ pass candidates rebuilt
→ manual pass review preserved by stable key
→ restart passes retained
→ momentum rebuilt
```

### Package freshness

```text
manually modify reviewed pass input
→ old momentum becomes stale
→ package builder rebuilds or excludes it
```

### Atomicity

Symuluj błąd podczas rebuild:

```text
old complete artifacts remain available
or
new complete artifacts replace them together
```

Nie pozostawiaj zestawu, w którym pass file jest nowy, a momentum stary.

---

## 10.3. Dodatkowe momentum unit tests

Dodaj:

- no progression across opponent possession;
- no progression across contested/free gap;
- no smoothing across period boundary;
- event-only confidence;
- failed progressive multiplier;
- restart review multiplier;
- exact bin boundary;
- match duration trailing bins;
- event bonus cap;
- stale lineage detection;
- deterministic hash/lineage output excluding `generated_at`.

---

# 11. P2 — kalibracja na realnych meczach

Po naprawie flow należy sprawdzić zachowanie modelu na kilku meczach.

## 11.1. Nie buduj od razu ML modelu

Pierwszy etap to manual calibration:

```text
3–5 meczów
10–20 ręcznie wskazanych okresów presji na mecz
10 okresów neutralnych na mecz
```

Dla każdego fragmentu operator oznacza:

```text
Team A pressure
Team B pressure
neutral
insufficient data
```

Opcjonalnie intensywność:

```text
low
medium
high
```

---

## 11.2. Momentum review goldset

Nowy dokument testowy może mieć format:

```json
{
  "match_id": "...",
  "windows": [
    {
      "start_time_sec": 120.0,
      "end_time_sec": 150.0,
      "expected_dominant_team": "A",
      "expected_intensity": "medium",
      "notes": "sustained final-third possession"
    }
  ]
}
```

Evaluator powinien mierzyć:

```text
dominant-team accuracy
neutral false-positive rate
coverage of labeled windows
peak timing error
```

Nie optymalizuj wyłącznie pod jeden mecz.

---

## 11.3. Parametry do kalibracji

Dopiero po przygotowaniu goldsetu dostrajaj:

```text
POSITION_BASE_SCORE
POSITION_WEIGHT
POSITION_EXPONENT
PROGRESSION_MAX_BONUS
COMPLETED_PASS_BASE_BONUS
FAILED_PASS_BASE_BONUS
PROGRESSIVE_PASS_MAX_BONUS
EMA window
normalization floor
dead zone
minimum confidence
```

Wszystkie zmiany parametrów muszą zachować algorithm version.

---

# 12. Proponowana architektura po poprawkach

```text
full analysis / reprocess
        │
        ▼
canonical source artifacts
ball_tracks
stable_players
possession_candidates
contact_candidates
restart_candidates
match_phase_config
        │
        ▼
ball_event_rebuild service
        │
        ├── event_candidates
        ├── pass_candidates + reviews + restarts
        ├── pass_review_report
        ├── attacking_momentum
        ├── analytics_readiness
        └── lineage/freshness
        │
        ▼
package readiness
        │
        ├── local report
        └── public report
```

Review endpoints nie powinny samodzielnie implementować fragmentów tego DAG.

Powinny wykonywać:

```text
save source review
→ call canonical rebuild service
→ return rebuilt relevant documents/readiness
```

---

# 13. Kolejność implementacji

## Milestone 1 — P0 review consistency

1. Dodać stable `candidate_key`.
2. Zmienić review preservation na stable key.
3. Dodać canonical `ball_event_rebuild` service.
4. Przepiąć contact review.
5. Przepiąć match phase update.
6. Przepiąć pass review.
7. Zachować possession evidence i restart passes w każdym rebuildzie.
8. Dodać integration tests.

### Definition of Done

Po każdej zmianie review wszystkie downstream JSON-y reprezentują ten sam stan.

---

## Milestone 2 — freshness i package safety

1. Dodać `generated_from`.
2. Dodać content hashes.
3. Dodać artifact freshness helper.
4. Dodać `analytics_readiness.json`.
5. Dodać ensure-fresh przed package/publish.
6. Nie publikować stale momentum.

### Definition of Done

Nie można zbudować public reportu zawierającego stare momentum i nowe pass review bez jawnego statusu stale/not available.

---

## Milestone 3 — momentum correctness v1.1

1. Segment-aware progression.
2. Failed-pass progression multiplier.
3. Restart review multiplier.
4. Event confidence.
5. EMA reset.
6. Match duration/bin boundaries.
7. Event bonus cap.
8. Rozdzielenie signal quality i readiness.

### Definition of Done

Nowe testy jednostkowe przechodzą, a model nie generuje oczywistych progression/halftime artifacts.

---

## Milestone 4 — UI review usability

1. Confidence-aware rendering.
2. Click-to-video.
3. Warning codes i lokalizacja.
4. Freshness/readiness badge.
5. Public unavailable state.

### Definition of Done

Trener może kliknąć peak, obejrzeć sytuację i zobaczyć, czy wykres opiera się na wiarygodnych danych.

---

## Milestone 5 — real-match calibration

1. Momentum goldset.
2. Evaluator.
3. Test 3–5 meczów.
4. Parametry v1.1.
5. Dokument wyników i ograniczeń.

### Definition of Done

Parametry nie są wybrane wyłącznie „na oko” na jednym meczu.

---

# 14. Rekomendowany następny task dla agenta

Nie implementuj teraz kolejnej statystyki takiej jak Turnover Map.

Następny task powinien mieć tytuł:

```text
Centralize ball-event downstream rebuild and artifact freshness
```

Zakres pierwszego PR/tasku:

```text
stable pass candidate keys
canonical rebuild service
contact review cascade
match phase cascade
pass review → momentum refresh
restart preservation
possession evidence preservation
integration tests
```

Po tym tasku wykonaj osobno:

```text
Attacking Momentum v1.1 correctness fixes
```

Nie mieszaj obu etapów w jeden ogromny refactor, jeśli utrudni to review.

---

# 15. Acceptance Criteria całego planu

- [ ] `match_phase_config.py` nie przebudowuje samodzielnie pass candidates;
- [ ] contact review używa centralnego rebuild service;
- [ ] pass review odświeża momentum;
- [ ] match phase review odświeża pass direction i momentum;
- [ ] possession release evidence nie znika po review rebuild;
- [ ] restart passes nie znikają po contact/phase update;
- [ ] manual pass reviews są zachowywane przez stable candidate key;
- [ ] ambiguous review migration jest raportowana, nie zgadywana;
- [ ] derived artifacts mają lineage;
- [ ] stale momentum jest wykrywane;
- [ ] package rebuilds albo wyklucza stale optional artifacts;
- [ ] progression nie przechodzi między possession sequences;
- [ ] smoothing nie przechodzi przez halftime/period boundary;
- [ ] failed progressive pass nie jest prawie równy completed progressive pass;
- [ ] restart bonuses respektują review status;
- [ ] event-only bins mają sensowne confidence;
- [ ] public report posiada available/unavailable/readiness status;
- [ ] backend tests uruchamiają się w CI;
- [ ] istnieją integration tests review cascade;
- [ ] istnieje real-match momentum goldset przed uznaniem feature za trusted.

---

# 16. Anti-goals

W ramach stabilizacji nie należy:

- zmieniać YOLO modeli;
- przepisywać ball tracking;
- dodawać shot detectora;
- dodawać xG;
- dodawać Turnover Map;
- budować ML momentum model;
- usuwać istniejącego eksperymentalnego wykresu;
- przedstawiać momentum jako oficjalnej statystyki;
- wykonywać pełnego video analysis po każdej zmianie review;
- opierać freshness wyłącznie na istnieniu pliku.

---

# 17. Finalny raport agenta

Po implementacji agent ma podać:

## Zmienione pliki

Lista plików.

## Nowy canonical rebuild flow

Krótki opis:

```text
contact review
match phase review
pass review
package build
```

## Review preservation

```text
stable key format
legacy fallback
ambiguous migration behavior
```

## Freshness

```text
artifacts with lineage
stale detection method
package behavior
```

## Testy

```text
backend unit tests
integration tests
client typecheck/build
```

## Znane ograniczenia

W szczególności:

- brak shot modelu;
- momentum nadal eksperymentalne;
- brak porównywalności score między meczami;
- real-match calibration pozostaje osobnym etapem.
