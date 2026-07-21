# Player Identity Stabilization Roadmap

## 0. Cel dokumentu

Celem tego dokumentu jest doprowadzenie systemu identyfikacji zawodników do stanu, w którym operator nie przypisuje ręcznie setek pojedynczych cropów po każdym meczu.

Docelowy workflow produktowy:

```text
analiza meczu
→ automatyczne candidate stable subjects
→ whole-subject review
→ przypisanie subjectów do rosteru
→ minimalne review konfliktów i nierozstrzygniętych fragmentów
→ candidate player timeline i statystyki
→ kontrolowana promocja do produkcji
```

Najważniejsza zasada:

> Brak przypisania jest bezpieczniejszy niż błędne przypisanie. System może pozostawić fragment jako `unresolved`, ale nie może ukrywać niepewności przez agresywny merge, deduplikację albo interpolację.

---

# 1. Aktualny baseline

Dokument został zaktualizowany po zakończeniu P1.20 i późniejszym safety review względem aktualnego flow z commita:

```text
c04ebf31c7822315c08a200533ca38c42b0d4077
```

Repo posiada działający human-in-the-loop pipeline:

```text
P1.15 roster-anchor shadow
→ P1.16 representative anchor crops
→ P1.17 whole-subject review contract
→ P1.18 review API/store
→ P1.19 local operator UI
→ P1.20 controlled promotion plan dry-run
```

Historia szczegółowych implementacji P0–P1.20 pozostaje dostępna w Git history. Ten dokument koncentruje się na aktualnym stanie i kolejnych krokach.

Przed rozpoczęciem każdego kolejnego milestone agent ma:

1. pobrać aktualny `HEAD`;
2. zweryfikować istniejące artefakty, funkcje i schema versions;
3. nie zakładać, że prywatne helpery zachowały stare nazwy;
4. utrzymać backward compatibility z aktualnym review flow;
5. nie modyfikować produkcyjnego identity bez jawnego milestone promotion;
6. nie uruchamiać ponownie YOLO, jeżeli zadanie dotyczy wyłącznie downstream identity artifacts;
7. zachować shadow/candidate separation do czasu P1.24.

---

# 2. Stan po P1.20

## 2.1. Co działa

### Shadow diagnostics i resolver research

Repo posiada między innymi:

- tracklet quality classification;
- occlusion i footpoint reliability;
- stitching candidates;
- joint assignment po overlapie;
- offline identity shadow resolver;
- shadow timeline z `detected`, `predicted`, `occluded`, `missing`;
- fragment consolidation;
- visual-content gates;
- same-match ReID;
- active-roster shadow view;
- frozen benchmarki i goldsety.

### Whole-subject operator workflow

Operator może przeglądać cały candidate stable subject jako jedną jednostkę zamiast przypisywać pojedyncze cropy.

P1.19 dostarcza:

- modal whole-subject review;
- anchor cropy;
- Team A/B/Unknown filters;
- roster select;
- rekomendację, blockery i quality flags;
- decyzje `confirm_recommended_player`, `assign_roster_player`, `mark_unresolved`;
- atomowy shadow store;
- freshness decyzji względem review artifact digest.

Pierwszy audyt Team A dla `published-46904e8c`:

```text
45/45 reviewed cards
31 subjects assigned
14 subjects unresolved
7 real players covered
12/12 recommendations accepted
recommendation precision = 1.0
```

### P1.20 promotion plan

P1.20 buduje read-only plan promocji decyzji whole-subject review do dokładnych obserwacji klatkowych zawodników.

Dry-run:

```text
source observations:       18 402
canonical observations:    18 247
duplicates removed:           155
hard conflicts:                  0
players with coverage:           7
blocking errors:                 0
```

P1.20 nadal:

- nie zapisuje `player_identity_assignments.json`;
- nie modyfikuje produkcyjnego identity;
- nie przebudowuje statystyk;
- nie przebudowuje heatmap;
- wymaga osobnego apply step.

## 2.2. Co jeszcze nie zostało udowodnione

P1.20 potwierdza poprawność kontraktu i lineage dla jednego audytu, ale nie potwierdza jeszcze:

- że 155 deduplikacji jest bezpiecznych semantycznie;
- że ten sam realny zawodnik nie występuje równocześnie w dwóch odległych miejscach;
- że liczba aktywnych zawodników na klatkę nie przekracza składu na boisku;
- że unresolved subjects stanowią mały procent czasu, a nie znaczną część meczu;
- że coverage każdego zawodnika jest wystarczające do statystyk;
- że whole-subject review skaluje się do pełnego meczu;
- że review trwa mniej niż 15 minut;
- że candidate stats są lepsze od obecnych statystyk produkcyjnych;
- że system generalizuje na inne mecze i warunki.

Dlatego następny etap nie powinien bezpośrednio nadpisywać produkcyjnych assignments.

---

# 3. Zasady gate’ów i KPI

## 3.1. Hard safety gates

Hard safety gates blokują promotion/candidate apply:

```text
stale lineage
cross-team player conflict
same source observation assigned to multiple players
parallel distant observations of the same player
structural-conflict subject promoted without remediation
sustained team on-pitch limit overflow
trusted multiple-goalkeeper conflict
```

## 3.2. Readiness metrics

Readiness metrics opisują jakość, ale nie muszą blokować pierwszych candidate artifacts:

```text
resolved coverage
unresolved coverage
review time
number of decisions
player timeline gaps
feature availability
```

## 3.3. Docelowe KPI produktu

```text
median manual review time < 15 minut na pełny mecz
resolved detected coverage > 95% dla analizowanej drużyny
0 znanych false assignments po review
0 równoległych, odległych obserwacji tego samego zawodnika
0 cross-team identity links
0 unresolved structural conflicts w produkcji
```

`95% coverage` i `<15 minut review` są KPI docelowymi do kalibracji w P1.22, a nie bieżącymi warunkami ukończenia P1.20A lub utworzenia pierwszego partial candidate.

Na etapie walidacji wynik:

```text
80% bezpiecznie przypisane
20% jawnie unresolved
0 false assignments
```

jest lepszy niż:

```text
97% przypisane
ukryte false merges
```

---

# 4. P1.20A — Promotion Safety Audit

## Status

```text
NEXT / MUST IMPLEMENT BEFORE CANDIDATE APPLY
```

## 4.1. Cel

Rozszerzyć `identity_roster_subject_promotion` tak, aby plan jawnie rozróżniał:

```text
safe observations
safe duplicates
review warnings
structural conflicts
blocking safety violations
```

`ready_for_controlled_apply` nie może oznaczać wyłącznie pustej listy błędów strukturalnych.

## 4.2. Klasyfikacja duplikatów

Obecne obserwacje usuwane przez deduplikację trzeba sklasyfikować.

Nowe klasy:

```text
same_source_duplicate
boundary_split_duplicate
near_identical_spatial_duplicate
parallel_nearby_duplicate
parallel_distant_conflict
structural_subject_conflict
unknown_duplicate
```

### Safe duplicates

Automatyczna deduplikacja jest dozwolona, gdy zachodzi co najmniej jeden warunek:

- ten sam `tracklet_id` i ta sama klatka;
- praktycznie identyczny bbox lub pitch position;
- dokładna granica dwóch subjectów wynikająca ze splitu;
- ten sam source observation występujący w dwóch reprezentacjach tego samego player ID.

### Blocking conflicts

Plan ma zostać zablokowany, gdy dwa różne tracklety przypisane temu samemu zawodnikowi:

- występują w tej samej klatce;
- są przestrzennie oddalone ponad konfigurowalny próg;
- nie są duplicate detections tej samej osoby;
- nie mają jawnego boundary/lineage explanation.

Reason code:

```text
same_player_parallel_spatial_conflict
```

Nie wolno wybierać jednego zwycięzcy i ukrywać drugiej obserwacji zwykłym warningiem.

## 4.3. Structural conflict gate

Podzielić blockery na:

### Review conflicts

Mogą zostać ręcznie rozstrzygnięte przez whole-subject assignment:

```text
missing recommendation
weak ranking
insufficient visual evidence
no reliable ReID
```

### Structural conflicts

Nie mogą zostać promowane jako cały subject bez wcześniejszej remediation:

```text
merges_production_subjects
merges_multiple_production_subjects
cross_production_transition
uncertain_transition
parallel_roster_candidate_conflict
parallel_subject_observations
mixed_team_evidence
structural_identity_conflict
```

Dla structural conflict dozwolone powinno być wyłącznie:

```text
mark_unresolved
split_subject
assign_fragment
open_event_review
```

P1.20A ma blokować promocję całego structural-conflict subjectu do jednego roster playera.

Jednocześnie P1.20A nie może tworzyć deadlocku całego pipeline. Nierozstrzygnięty structural fragment może zostać wykluczony z partial candidate po P1.20B zamiast blokować wszystkie bezpieczne obserwacje meczu.

## 4.4. Coverage — poprawne mianowniki

Nie wolno liczyć:

```text
player detected frames / full match frames
```

bez wiarygodnego on-pitch interval zawodnika.

Raportować osobno:

### Team assignment coverage

```text
promoted reliable detected team observations
/
all reliable detected observations audytowanej drużyny
```

### Review resolution ratio

```text
promoted detected frames
/
promoted + unresolved detected frames objęte review
```

### Player confirmed-interval coverage

Tylko gdy istnieje ręcznie potwierdzony on-pitch interval:

```text
promoted detected frames playera
/
frames w potwierdzonym on-pitch interval
```

### Unknown denominator

Gdy nie znamy czasu wejścia/zejścia:

```json
{
  "detected_coverage_ratio": null,
  "coverage_denominator": "unknown",
  "reason": "on_pitch_interval_not_confirmed"
}
```

Dodatkowo raportować:

```text
potential_player_gaps
team_level_unresolved_frames
reviewed_detected_frames
promoted_detected_frames
unresolved_detected_frames
promoted_detected_ratio
unresolved_detected_ratio
longest_unresolved_interval_sec
unresolved_intervals_over_1s
unresolved_intervals_over_3s
```

## 4.5. Konserwatywny per-frame roster validation

Do twardego limitu aktywnych zawodników liczyć wyłącznie obserwacje spełniające wszystkie warunki:

```text
operator-confirmed player_id
status = detected
inside_play
po bezpiecznej deduplikacji
unikalny player_id
```

Nie liczyć automatycznie jako hard conflict:

```text
predicted
occluded
missing
unresolved subjects
outside_play
boundary/bench observations
niepewne momenty zmiany
```

Rozróżnić:

```text
team_active_player_limit_spike
team_active_player_limit_sustained
```

Pojedynczy krótkotrwały spike powinien być warningiem. Dopiero sustained overflow przez konfigurowalny czas/klatki jest błędem blokującym.

Expected player count powinien pochodzić z match/team configuration z bezpiecznym fallbackiem.

## 4.6. Bramkarze — trusted role only

`multiple_goalkeepers_active` może być hard blockiem tylko, gdy rola GK pochodzi z:

```text
explicit roster role
operator-confirmed role
trusted match configuration
```

Nie używać jako hard gate wyłącznie:

```text
kolorystycznego outliera
pozycji na boisku
visual guess
team appearance clustering
```

Semantyka:

```text
2 explicit confirmed goalkeepers active
→ block

1 confirmed GK + 1 visual GK candidate
→ warning

role unknown
→ no goalkeeper hard gate
```

## 4.7. Unresolved weighted coverage i downstream impact

Core identity safety nie może zależeć od dostępności artefaktów piłki.

### Core identity report

Zawsze raportować:

```text
unresolved detected frames
unresolved ratios
longest intervals
identity/stat eligibility flags
```

### Optional downstream impact

Jeżeli artefakty piłki/eventów istnieją, można dodatkowo raportować:

```text
unresolved_during_ball_possession
unresolved_during_player_event
unresolved_affects_passes
unresolved_affects_turnovers
```

Brak ball artifacts nie może blokować identity promotion/candidate apply.

Powinien dawać statusy w rodzaju:

```text
player_identity: ready_with_review
possession_readiness: not_available
passes_readiness: not_available
```

## 4.8. Per-player readiness

Dodać raport per roster player:

```json
{
  "player_id": "...",
  "detected_frames": 0,
  "distance_eligible_frames": 0,
  "heatmap_eligible_frames": 0,
  "coverage_denominator": "confirmed_on_pitch|review_scope|unknown",
  "detected_coverage_ratio": null,
  "distance_eligible_ratio": null,
  "heatmap_eligible_ratio": null,
  "subject_fragments": 0,
  "timeline_gaps": 0,
  "longest_gap_sec": 0.0,
  "parallel_conflicts": 0,
  "readiness": "ready|ready_with_review|experimental|not_available",
  "reasons": []
}
```

Nie uznawać zawodnika za gotowego tylko dlatego, że ma co najmniej jedną obserwację.

## 4.9. Pełne lineage digests

Review artifact i promotion plan mają zawierać digests:

```text
candidate identity artifact
shadow timeline artifact
anchor crops artifact
roster/match configuration
team configuration
review contract
operator decisions
algorithm parameters
```

Zmiana któregokolwiek z tych źródeł ma oznaczać decyzje lub plan jako `stale`.

Nie wystarczy porównanie samych IDs, zakresów i trackletów.

## 4.10. Nowe artefakty

Proponowane:

```text
identity_roster_subject_promotion_safety_report.json
identity_roster_subject_duplicate_audit.json
identity_roster_subject_readiness.json
```

Nazwy mogą zostać dostosowane do istniejących conventions, ale odpowiedzialności muszą pozostać rozdzielone.

## 4.11. Gate P1.20A

P1.20A przechodzi, gdy system poprawnie wykrywa i raportuje:

```text
stale lineage
structural conflicts
safe vs unsafe duplicates
parallel spatial conflicts
conservative active-player overflow
trusted goalkeeper conflicts
coverage with explicit denominator semantics
optional downstream readiness
```

P1.20A nie wymaga jeszcze:

```text
95% coverage
review < 15 minutes
0 unresolved fragments
```

## 4.12. Testy

Dodać minimum:

- same tracklet boundary duplicate is safe;
- near-identical duplicate is safe;
- distant simultaneous observations block;
- structural-conflict subject assignment blocks;
- review-only conflict can be assigned;
- one-frame active player spike warns;
- sustained active player overflow blocks;
- substitution without simultaneous overflow passes;
- two explicit trusted goalkeepers block;
- visual GK guess does not hard-block;
- stale candidate/timeline digest blocks;
- player coverage denominator can be unknown;
- team/review coverage is correct;
- missing ball artifacts do not block identity;
- deterministic output;
- production artifacts remain unchanged.

---

# 5. P1.20B — Minimal Structural Conflict Remediation

## Status

```text
MUST IMPLEMENT BEFORE P1.21 WHEN STRUCTURAL CONFLICTS EXIST
```

## 5.1. Cel

Zapewnić minimalny sposób naprawy konfliktów wykrytych przez P1.20A, aby pipeline nie był zablokowany do czasu zaawansowanego P1.25.

To nie jest jeszcze pełny event-level editor.

## 5.2. Minimalne akcje

```text
split subject at tracklet boundary
split subject at transition frame
assign one fragment to roster player
mark one fragment unresolved
exclude structural fragment from candidate promotion
clear remediation decision
```

## 5.3. Reguły

- split musi działać na stabilnych frame/tracklet keys;
- decyzja ma zapisywać source digest;
- po zmianie candidate/timeline decyzja staje się stale;
- jeden fragment może pozostać unresolved;
- bezpieczne fragmenty tego samego meczu mogą przejść dalej;
- remediation nie zapisuje produkcyjnego identity.

## 5.4. Artefakty

Proponowane:

```text
identity_roster_subject_remediation_decisions_shadow.json
identity_roster_subject_remediation_plan.json
```

## 5.5. Definition of Done

```text
structural subject can be split or partially excluded
unsafe whole-subject promotion is blocked
safe observations remain eligible for partial candidate
production hashes remain unchanged
```

---

# 6. P1.21 — Partial Candidate Apply

## Cel

Zastosować zatwierdzony i bezpieczny plan wyłącznie do równoległych candidate artifacts.

Nierozstrzygnięte lub structural-conflict fragmenty mogą zostać pominięte jako `unresolved`, zamiast blokować wszystkie poprawne obserwacje meczu.

Nie nadpisywać jeszcze produkcyjnych:

```text
player_identity_assignments.json
resolved_player_stats.json
player_heatmaps.json
```

## 6.1. Candidate artifacts

Wygenerować:

```text
player_identity_assignments_candidate_v2.json
resolved_player_timeline_candidate_v2.json
resolved_player_stats_candidate_v2.json
player_heatmaps_candidate_v2.json
identity_candidate_apply_manifest.json
```

Opcjonalnie:

```text
player_events_candidate_v2.json
player_passes_candidate_v2.json
```

jeżeli obecna architektura pozwala je przebudować bez zmiany produkcji.

## 6.2. Zasady candidate timeline

```text
detected + operator-confirmed
→ pełne candidate identity

predicted / occluded
→ zachowanie ciągłości, ale bez observed distance

unresolved / missing / excluded structural fragment
→ brak player identity contribution
```

Nie używać predicted positions jako rzeczywistych obserwacji do distance i heatmap.

## 6.3. Partial candidate status

Candidate manifest ma jawnie wskazywać:

```text
complete_candidate
partial_candidate
blocked
```

`partial_candidate` jest poprawnym wynikiem do benchmarku i walidacji statystyk, ale nie jest automatycznie production-ready.

## 6.4. Candidate vs production diff

Dodać raport:

```text
identity_candidate_vs_production_diff.json
```

Per player:

```text
playing time delta
detected coverage delta
distance delta
heatmap coverage delta
subject count delta
longest gap delta
identity switch boundaries
new unresolved intervals
removed/added observations
coverage denominator status
```

Globalnie:

```text
production assigned frames
candidate assigned frames
production ambiguous frames
candidate unresolved frames
parallel conflicts
cross-team conflicts
excluded structural fragments
```

## 6.5. Safety

Candidate apply:

- zapisuje pliki atomowo;
- nie modyfikuje produkcji;
- posiada manifest z input hashes;
- jest powtarzalny;
- może zostać bezpiecznie usunięty i przebudowany;
- nie publikuje candidate stats w public package;
- nie wymaga 95% coverage;
- nie obniża hard constraints dla większego coverage.

## Definition of Done

```text
candidate artifacts generated
production hashes unchanged
candidate timeline validates with 0 hard conflicts
unresolved fragments remain explicit
candidate stats can be compared with production
```

---

# 7. P1.22 — Full-Match Operator Benchmark

## Cel

Sprawdzić prawdziwy koszt pracy operatora i generalizację workflow oraz skalibrować docelowe KPI.

## 7.1. Materiał

Minimum:

```text
Match A — obecny mecz / znany materiał
Match B — inne światło, stroje lub ustawienie kamery
Match C — held-out, bez strojenia parametrów pod wynik
```

Nie ograniczać oceny do `easy90` i `hard3m`.

## 7.2. Review session telemetry

UI/store ma zapisywać:

```text
review_session_started_at
review_session_completed_at
active_review_seconds
cards_opened
cards_decided
cards_reopened
decisions_changed
confirm_recommendation_count
manual_assignment_count
unresolved_count
remediation_actions_count
average_seconds_per_card
cards_per_minute
```

`active_review_seconds` powinno ograniczać naliczanie długich okresów bez aktywności operatora.

## 7.3. Metryki pełnego meczu

```text
manual review time
manual decisions
candidate subjects reviewed
subjects assigned
subjects unresolved
promoted detected ratio
unresolved detected ratio
false assignment count
parallel conflict count
player coverage denominator distribution
player coverage distribution where denominator is known
longest player gap
```

## 7.4. Human audit sample

Po review ręcznie sprawdzić co najmniej:

- wszystkie structural conflicts;
- wszystkie dalekie parallel duplicates;
- wszystkie granice subjectów dla jednego player ID;
- wszystkie długie unresolved intervals;
- wszystkie duże skoki pozycji;
- początek i koniec timeline każdego zawodnika;
- okresy zmian zawodników;
- fragmenty z posiadaniem piłki i player events, jeżeli te artefakty istnieją.

## 7.5. Kalibracja KPI

Po pierwszym pełnym meczu raportować wyniki bez wymuszania docelowych progów.

Po co najmniej trzech meczach, w tym jednym held-out, ocenić realność:

```text
median review time < 15 min
resolved detected coverage > 95%
0 known false assignments after review
```

KPI mogą zostać doprecyzowane na podstawie rzeczywistego denominator coverage i udziału rezerwowych/zmian.

## Gate P1.22

```text
telemetry available
at least three matches evaluated
at least one held-out match
0 hidden structural conflicts
0 impossible parallel player positions
human-audited false assignments reported explicitly
```

---

# 8. P1.23 — Candidate Stats Validation

## Cel

Sprawdzić, czy candidate identity daje sensowne statystyki zawodników i nie tylko ładniejsze przypisania.

## 8.1. Walidacja timeline

Dla każdego zawodnika sprawdzić:

```text
first observation
last observation
playing intervals
known/unknown on-pitch denominator
substitution boundaries
number of fragments
longest gap
large spatial jumps
parallel observations
predicted/occluded share
```

## 8.2. Walidacja statystyk

Porównać candidate vs production:

```text
playing time
distance
heatmap shape
possession involvement
passes
turnovers
player events
```

Brak ball artifacts nie może obniżać identity readiness. Powinien ustawić zależne feature readiness na `not_available`.

## 8.3. Explainable deltas

Każda duża zmiana powinna mieć możliwość przejścia do źródłowych subjectów i decyzji operatora.

Przykład:

```text
Player A distance +620 m
→ 3 nowe subject fragments
→ frames 12000–14800
→ review cards X/Y/Z
```

## 8.4. Readiness per feature

Przykład:

```json
{
  "player_identity": "ready",
  "playing_time": "ready_with_review",
  "heatmap": "ready_with_review",
  "distance": "experimental",
  "player_possession": "not_available",
  "player_passes": "not_available"
}
```

Feature może być niedostępny mimo gotowego identity, jeżeli wymagane dane wejściowe są zbyt słabe lub nie istnieją.

## Gate P1.23

```text
0 known false assignments in audited sample
0 impossible spatial jumps affecting stats
predicted/occluded excluded from observed distance
large stat deltas manually explained
candidate output reviewed on held-out match
feature readiness independent from unavailable optional inputs
```

---

# 9. P1.24 — Controlled Production Apply

## Cel

Dopiero po pozytywnym P1.20A–P1.23 umożliwić jawne zastosowanie candidate identity do produkcji.

## 9.1. Apply UX

Operator musi zobaczyć:

```text
input plan and digests
review completeness
unresolved coverage
coverage denominator semantics
blocking warnings
candidate vs production diff
files to be replaced
```

Apply wymaga jawnego potwierdzenia.

## 9.2. Backup i transaction manifest

Przed zapisem:

```text
backup current assignments
backup resolved timeline/stats/heatmaps
write transaction manifest
mark downstream package stale
```

Zapis atomowy lub kontrolowana transakcja plikowa.

Manifest:

```json
{
  "transaction_id": "...",
  "source_commit": "...",
  "input_digests": {},
  "backups": [],
  "written_files": [],
  "rebuild_steps": [],
  "validation": {},
  "status": "prepared|applied|validated|rolled_back|failed"
}
```

## 9.3. Rebuild downstream

Po apply przebudować co najmniej:

```text
player identity assignments
resolved player timeline
resolved player stats
player heatmaps
player-level events/passes, jeśli zależne i dostępne
analysis readiness
package/publication freshness
```

Nie pozostawiać nowego identity z poprzednimi statystykami.

## 9.4. Post-apply validation

```text
0 hard identity conflicts
0 stale downstream artifacts
all output hashes recorded
readiness gates recalculated
package remains unpublished/stale until rebuild completes
```

## 9.5. Rollback

Rollback ma:

- przywracać backup;
- przebudowywać zależne artefakty;
- zapisywać status i reason;
- nie usuwać audytu operatora ani remediation decisions.

---

# 10. P1.25 — Advanced Event-Level and Orphan Review

P1.25 pozostaje etapem zaawansowanym. Minimalny split/remediation wymagany do odblokowania candidate flow znajduje się już w P1.20B.

P1.25 ma zostać zaprojektowany na podstawie realnych danych z P1.22.

## 10.1. Priorytetowe przypadki

```text
structural-conflict subjects requiring richer context
long unresolved fragments
identity switch boundaries
overlap exits
parallel subject conflicts
orphan fragments affecting possession/events
```

## 10.2. Jednostka review

Nie pojedynczy crop.

Preferowane:

```text
clip before
transition/overlap
clip after
incoming identities
outgoing candidates
recommended mapping
```

Akcje:

```text
keep
swap
split subject
assign fragment
mark unresolved
mark noise
```

## 10.3. Priorytetyzacja

Najpierw pokazywać fragmenty o największym wpływie:

```text
long duration
structural conflict
large candidate stat impact
ball possession involvement, jeśli dostępne
player event involvement, jeśli dostępne
```

Krótkie noise tracklety bez wpływu na wynik nie powinny zaśmiecać operatora.

---

# 11. P2 — Automatyzacja po stabilnym workflow

Dopiero po pozytywnym pełnomaczowym benchmarku rozważyć:

## P2.1. Roster-confirmed ReID prototypes

```text
real player
→ operator-approved anchor crops
→ robust prototype
→ unresolved fragment ranking
```

Prototype może zawierać tylko zatwierdzone reliable crops.

## P2.2. Anchor-conditioned offline optimizer

Koszt:

```text
motion
+ time gap
+ team
+ trusted role
+ occlusion context
+ footpoint reliability
+ visual-content validity
+ approved roster ReID
```

Hard constraints zawsze mają pierwszeństwo.

## P2.3. Persistent gallery między meczami

Dopiero gdy single-match gallery nie jest zatruwana false merges.

Cross-match identity pozostaje sugestią wymagającą potwierdzenia operatora.

## P2.4. Targeted player YOLO improvement

Trenować tylko po benchmarku pokazującym, że głównym źródłem fragmentacji są:

```text
missed detections
merged player detections
severe overlap recall
bbox instability
```

Nie trenować dużego modelu wyłącznie dlatego, że liczba raw tracków jest wysoka.

---

# 12. Data contracts

## 12.1. Stable keys

Każdy review subject, fragment, decision, promotion row i apply transaction musi posiadać stabilny klucz niezależny od kolejności listy.

## 12.2. Exact source observations

Promocja musi wskazywać dokładnie:

```text
frame
tracklet_id
candidate_subject_id
fragment_id, jeśli subject został podzielony
player_id
source review/remediation decision
```

## 12.3. Reliability

Każda obserwacja powinna zachować:

```text
status
confidence
footpoint_reliable
appearance_reliable
play_area_status
position_source
eligible_for_distance
eligible_for_heatmap
```

## 12.4. Freshness

Zmiana któregokolwiek wejścia identity powoduje stale downstream artifacts:

```text
candidate graph
shadow timeline
team config
roster
review contract
operator decisions
remediation decisions
manual splits
algorithm version
parameters
```

---

# 13. Quality i readiness

Minimalny dokument readiness:

```text
identity_roster_subject_readiness.json
```

Statusy:

```text
ready
ready_with_review
experimental
not_available
```

Zakres:

```text
team-level identity
player-level identity
playing time
distance
heatmaps
player possession
player passes
player events
```

Nie przenosić logiki readiness do komponentów React.

Readiness ma rozróżniać:

```text
hard safety
coverage/readiness
optional feature availability
```

---

# 14. CI i testy

Repo nie powinno polegać wyłącznie na lokalnej deklaracji agenta.

Dodać lub rozszerzyć GitHub Actions:

```text
backend unit tests
identity contract tests
promotion safety tests
candidate apply tests
client typecheck
client production build
```

Benchmarki z ciężkimi modelami mogą pozostać osobnym manual workflow, ale lekkie frozen artifact evaluators powinny działać w CI.

Każdy milestone ma potwierdzić:

```text
deterministic output
no unexpected production mutations
stale input detection
atomic writes
failure rollback or safe abort
```

---

# 15. KPI

## Główne KPI produktowe

```text
manual review time per match
manual decisions per match
team assignment coverage
review resolution ratio
player confirmed-interval coverage, jeśli denominator jest znany
unresolved detected coverage
false assignments after review
parallel spatial conflicts
player timeline gaps
candidate vs production stat deltas
```

## KPI diagnostyczne

```text
raw tracklets per player
candidate subjects per player
subjects assigned per player
structural conflicts
safe duplicates
unsafe duplicates
ReID suggestion precision
coverage denominator unknown count
```

Nie uznawać za sukces samego spadku liczby subjectów, jeżeli wynika z false merges.

---

# 16. Zmieniona kolejność implementacji

```text
P1.20A  Promotion Safety Audit
P1.20B  Minimal Structural Conflict Remediation
P1.21   Partial Candidate Apply
P1.22   Full-Match Operator Benchmark and KPI Calibration
P1.23   Candidate Stats Validation
P1.24   Controlled Production Apply
P1.25   Advanced Event-Level / Orphan Review driven by benchmark evidence
P2      Approved-anchor automation and cross-match assistance
```

Najbliższy task dla agenta:

```text
Implement P1.20A Promotion Safety Audit
```

Jeżeli P1.20A wykryje structural conflicts w aktualnym audycie, następnym zadaniem jest P1.20B przed P1.21.

Nie implementować jeszcze produkcyjnego apply adaptera.

---

# 17. Acceptance Criteria całej zmienionej roadmapy

## P1.20A

- [ ] wszystkie duplikaty są sklasyfikowane;
- [ ] distant parallel observations blokują plan;
- [ ] structural-conflict whole-subject assignments są blokowane;
- [ ] aktywni zawodnicy są liczeni tylko z confirmed detected inside-play observations;
- [ ] sustained team active-player overflow jest blokowany;
- [ ] pojedynczy overflow spike jest warningiem;
- [ ] goalkeeper hard gate używa tylko trusted role;
- [ ] pełne lineage digests są sprawdzane;
- [ ] coverage ma jawny denominator;
- [ ] unresolved time-weighted coverage jest raportowane;
- [ ] brak ball artifacts nie blokuje identity;
- [ ] per-player readiness jest raportowane;
- [ ] produkcyjne artefakty pozostają bez zmian.

## P1.20B

- [ ] structural subject można podzielić na fragmenty;
- [ ] fragment można przypisać lub pozostawić unresolved;
- [ ] remediation posiada freshness digest;
- [ ] safe fragments mogą przejść do partial candidate;
- [ ] produkcyjne identity pozostaje bez zmian.

## P1.21

- [ ] candidate assignments/timeline/stats/heatmaps powstają obok produkcji;
- [ ] partial candidate jest obsługiwany jawnie;
- [ ] candidate vs production diff jest dostępny;
- [ ] predicted/occluded nie są liczone jako observed distance;
- [ ] unresolved/excluded fragments nie zasilają player stats;
- [ ] output jest atomowy i deterministyczny;
- [ ] public package nie używa candidate artifacts.

## P1.22

- [ ] co najmniej trzy mecze są ocenione;
- [ ] co najmniej jeden mecz jest held-out;
- [ ] review time jest mierzone;
- [ ] false assignments są audytowane;
- [ ] unresolved coverage jest mierzona czasowo, nie tylko liczbą kart;
- [ ] KPI 95% i 15 minut są ocenione na danych pełnomaczowych.

## P1.23

- [ ] candidate timeline każdego zawodnika jest sprawdzony;
- [ ] duże stat deltas mają explainable source;
- [ ] brak impossible spatial jumps wpływających na statystyki;
- [ ] feature-level readiness jest wyliczane;
- [ ] brak optional input nie blokuje identity readiness.

## P1.24

- [ ] apply wymaga jawnego potwierdzenia;
- [ ] istnieje backup i transaction manifest;
- [ ] zapis jest atomowy;
- [ ] downstream artifacts są przebudowane;
- [ ] package freshness jest poprawne;
- [ ] rollback został przetestowany.

## Cel końcowy

- [ ] median manual review time < 15 minut;
- [ ] resolved detected coverage > 95% dla analizowanej drużyny albo jawnie skalibrowany równoważny KPI;
- [ ] 0 znanych false assignments po review;
- [ ] 0 unresolved structural conflicts w produkcji;
- [ ] 0 równoległych odległych obserwacji jednego zawodnika;
- [ ] player-level stats posiadają jawny readiness status.

---

# 18. Anti-goals

W kolejnych milestone’ach nie należy:

- nadpisywać produkcji bez candidate stage;
- ukrywać konfliktów przez wybór obserwacji o wyższym confidence;
- traktować wszystkich duplikatów jako bezpieczne;
- wymuszać przypisania unresolved fragmentu;
- używać predicted positions jako observed distance;
- liczyć player coverage względem pełnego meczu bez potwierdzonego on-pitch interval;
- używać visual GK guess jako hard gate;
- blokować identity z powodu braku ball artifacts;
- publikować candidate stats;
- obniżać hard constraints, aby zwiększyć coverage;
- optymalizować wyłącznie pod `easy90` i `hard3m`;
- budować persistent gallery przed full-match validation;
- dodawać kolejnych warstw research bez wpływu na review time lub correctness.

---

# 19. Raport końcowy agenta po każdym milestone

Agent ma podać:

## Baseline

```text
input commit
output commit
benchmark matches
schema versions
```

## Zmienione pliki

Lista plików.

## Flow

Krótki diagram wejścia → output.

## Safety

```text
hard conflicts
stale inputs
production hashes
atomicity
rollback behavior
```

## Coverage semantics

```text
team assignment coverage
review resolution ratio
player coverage denominator status
unresolved detected coverage
```

## Wyniki

```text
review time
assigned coverage
unresolved coverage
false assignments
parallel conflicts
candidate vs production deltas
```

## Testy

```text
backend unit tests
integration tests
client typecheck/build
frozen benchmark evaluators
```

## Znane ograniczenia

Bez ukrywania przypadków, których system nie rozwiązuje.

## Następna rekomendacja

Agent nie może automatycznie przechodzić do produkcyjnego apply tylko dlatego, że testy jednostkowe są zielone. Każdy promotion milestone wymaga jawnego spełnienia odpowiednich safety gate’ów z tego dokumentu.
