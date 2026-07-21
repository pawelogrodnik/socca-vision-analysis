# Player Identity Stabilization Roadmap

## 0. Cel dokumentu

Celem tego dokumentu jest doprowadzenie systemu identyfikacji zawodników do stanu, w którym operator nie przypisuje ręcznie setek pojedynczych cropów po każdym meczu.

Docelowy workflow produktowy:

```text
analiza meczu
→ automatyczne candidate stable subjects
→ whole-subject review
→ przypisanie subjectów do rosteru
→ review tylko konfliktów i nierozstrzygniętych fragmentów
→ candidate player timeline i statystyki
→ kontrolowana promocja do produkcji
```

Docelowe KPI:

```text
manual review time < 15 minut na pełny mecz
resolved detected coverage > 95%
0 znanych false assignments po review
0 równoległych, odległych obserwacji tego samego zawodnika
0 cross-team identity links
0 niejawnie usuniętych konfliktów strukturalnych
```

Najważniejsza zasada:

> Brak przypisania jest bezpieczniejszy niż błędne przypisanie. System może pozostawić fragment jako `unresolved`, ale nie może ukrywać niepewności przez agresywny merge, deduplikację albo interpolację.

---

# 1. Aktualny baseline

Dokument został zaktualizowany po zakończeniu P1.20 względem commita:

```text
c04ebf31c7822315c08a200533ca38c42b0d4077
```

Aktualny etap projektu nie jest już wyłącznie eksperymentem z trackerem i ReID. Repo posiada działający human-in-the-loop flow:

```text
P1.15 roster-anchor shadow
→ P1.16 representative anchor crops
→ P1.17 whole-subject review contract
→ P1.18 review API/store
→ P1.19 local operator UI
→ P1.20 controlled promotion plan dry-run
```

Historia szczegółowych implementacji P0–P1.20 pozostaje dostępna w Git history. Ten dokument celowo koncentruje się teraz na aktualnym stanie i kolejnych krokach, zamiast utrzymywać wielotysięczną kronikę każdego eksperymentu.

Przed rozpoczęciem każdego kolejnego milestone agent ma:

1. pobrać aktualny `HEAD`;
2. zweryfikować istniejące artefakty, funkcje i schema versions;
3. nie zakładać, że prywatne helpery zachowały stare nazwy;
4. utrzymać backward compatibility z aktualnym review flow;
5. nie modyfikować produkcyjnego identity bez jawnego milestone promotion;
6. nie uruchamiać ponownie YOLO, jeśli zadanie dotyczy wyłącznie downstream identity artifacts.

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

# 3. Zmieniony kierunek roadmapy

Do P1.20 roadmapa koncentrowała się na budowie identity evidence i operator review.

Od teraz priorytet zmienia się na:

```text
bezpieczna walidacja operator-reviewed identity
→ candidate artifacts
→ pełnomaczowy benchmark
→ candidate stats validation
→ kontrolowana produkcyjna promocja
```

Nie należy teraz priorytetowo dodawać:

- kolejnego ogólnego pairwise score;
- kolejnego modelu ReID;
- persistent gallery pomiędzy meczami;
- agresywnego global merge;
- automatycznego progu identity bez human review.

Najpierw trzeba zmierzyć, czy obecny human-in-the-loop flow daje poprawne i użyteczne dane.

---

# 4. P1.20A — Promotion Safety Audit

## Status

```text
NEXT / MUST IMPLEMENT BEFORE ANY APPLY
```

## 4.1. Cel

Rozszerzyć `identity_roster_subject_promotion` tak, aby `ready_for_controlled_apply` oznaczało nie tylko brak błędu strukturalnego, ale także brak konfliktów przestrzennych, rosterowych i jakościowych.

## 4.2. Klasyfikacja duplikatów

Obecne 155 obserwacji usuniętych przez deduplikację trzeba sklasyfikować.

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

Nie mogą zostać promowane jako cały subject:

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

Dla structural conflict dozwolone jest wyłącznie:

```text
mark_unresolved
split_subject
open_event_review
```

P1.20A ma blokować plan, jeżeli operator przypisał cały structural-conflict subject do roster playera bez wcześniejszego splitu albo event-level resolution.

## 4.4. Per-frame roster validation

Dodać validator:

```text
unique active roster players per team <= expected on-pitch count
same player observations per frame <= 1
goalkeepers active per team <= 1
player cannot be active for both teams
```

Expected player count powinien pochodzić z match/team configuration, z bezpiecznym fallbackiem do obecnego formatu meczu.

Zmiany zawodników są dozwolone. Przekroczenie limitu aktywnych osób w tej samej klatce jest błędem blokującym.

Reason codes:

```text
team_active_player_limit_exceeded
multiple_goalkeepers_active
player_active_for_multiple_teams
```

## 4.5. Unresolved weighted coverage

Dla `mark_unresolved` nie wystarczy przechowywać zakresu karty.

Plan ma wyliczać:

```text
reviewed_detected_frames
promoted_detected_frames
unresolved_detected_frames
promoted_detected_ratio
unresolved_detected_ratio
longest_unresolved_interval_sec
unresolved_intervals_over_1s
unresolved_intervals_over_3s
```

Osobno:

- per team;
- per player, jeżeli fragment ma ograniczony candidate set;
- globalnie dla audytowanej części meczu.

Dodatkowe impact flags:

```text
unresolved_during_ball_possession
unresolved_during_player_event
unresolved_affects_distance
unresolved_affects_heatmap
```

## 4.6. Per-player readiness

Dodać raport per roster player:

```json
{
  "player_id": "...",
  "detected_frames": 0,
  "distance_eligible_frames": 0,
  "heatmap_eligible_frames": 0,
  "detected_coverage_ratio": 0.0,
  "distance_eligible_ratio": 0.0,
  "heatmap_eligible_ratio": 0.0,
  "subject_fragments": 0,
  "timeline_gaps": 0,
  "longest_gap_sec": 0.0,
  "parallel_conflicts": 0,
  "readiness": "ready|ready_with_review|experimental|not_available",
  "reasons": []
}
```

Nie uznawać zawodnika za gotowego tylko dlatego, że ma co najmniej jedną obserwację.

## 4.7. Pełne lineage digests

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

## 4.8. Nowe artefakty

Proponowane:

```text
identity_roster_subject_promotion_safety_report.json
identity_roster_subject_duplicate_audit.json
identity_roster_subject_readiness.json
```

Nazwy mogą zostać dostosowane do istniejących conventions, ale odpowiedzialności muszą pozostać rozdzielone.

## 4.9. Gate P1.20A

P1.20A przechodzi tylko, gdy:

```text
0 stale lineage inputs
0 unresolved structural conflicts promoted as whole subjects
0 distant parallel observations for the same player
0 frames above team on-pitch player limit
0 multiple-goalkeeper conflicts
0 cross-team player conflicts
all duplicates classified
all unsafe duplicates block the plan
```

## 4.10. Testy

Dodać minimum:

- same tracklet boundary duplicate is safe;
- near-identical duplicate is safe;
- distant simultaneous observations block;
- structural-conflict subject assignment blocks;
- review-only conflict can be assigned;
- active player limit blocks;
- substitution without simultaneous overflow passes;
- multiple goalkeepers block;
- stale candidate/timeline digest blocks;
- unresolved weighted coverage is correct;
- deterministic output;
- production artifacts remain unchanged.

---

# 5. P1.21 — Candidate Apply

## Cel

Zastosować zatwierdzony plan wyłącznie do równoległych candidate artifacts.

Nie nadpisywać jeszcze produkcyjnych:

```text
player_identity_assignments.json
resolved_player_stats.json
player_heatmaps.json
```

## 5.1. Candidate artifacts

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

## 5.2. Zasady candidate timeline

```text
detected + operator-confirmed
→ pełne candidate identity

predicted / occluded
→ zachowanie ciągłości, ale bez observed distance

unresolved / missing
→ brak player identity contribution
```

Nie używać predicted positions jako rzeczywistych obserwacji do distance i heatmap.

## 5.3. Candidate vs production diff

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
```

Globalnie:

```text
production assigned frames
candidate assigned frames
production ambiguous frames
candidate unresolved frames
parallel conflicts
cross-team conflicts
```

## 5.4. Safety

Candidate apply:

- zapisuje pliki atomowo;
- nie modyfikuje produkcji;
- posiada manifest z input hashes;
- jest powtarzalny;
- może zostać bezpiecznie usunięty i przebudowany;
- nie publikuje candidate stats w public package.

## Definition of Done

```text
candidate artifacts generated
production hashes unchanged
candidate timeline validates with 0 hard conflicts
candidate stats can be compared with production
```

---

# 6. P1.22 — Full-Match Operator Benchmark

## Cel

Sprawdzić prawdziwy koszt pracy operatora i generalizację workflow.

## 6.1. Materiał

Minimum:

```text
Match A — obecny mecz / znany materiał
Match B — inne światło, stroje lub ustawienie kamery
Match C — held-out, bez strojenia parametrów pod wynik
```

Nie ograniczać oceny do `easy90` i `hard3m`.

## 6.2. Review session telemetry

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
average_seconds_per_card
cards_per_minute
```

`active_review_seconds` powinno ograniczać naliczanie długich okresów bez aktywności operatora.

## 6.3. Metryki pełnego meczu

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
player coverage distribution
longest player gap
```

## 6.4. Human audit sample

Po review ręcznie sprawdzić co najmniej:

- wszystkie structural conflicts;
- wszystkie dalekie parallel duplicates;
- wszystkie granice subjectów dla jednego player ID;
- wszystkie długie unresolved intervals;
- wszystkie duże skoki pozycji;
- początek i koniec timeline każdego zawodnika;
- okresy zmian zawodników;
- fragmenty z posiadaniem piłki i player events.

## Gate P1.22

Docelowy gate po zebraniu co najmniej trzech meczów:

```text
median review time < 15 min
0 known false assignments after review
0 unresolved structural conflicts
0 impossible parallel player positions
resolved detected coverage > 95% for reviewed team
held-out match without material regression
```

Pierwszy pełny mecz może służyć kalibracji progów, ale co najmniej jeden mecz musi pozostać held-out.

---

# 7. P1.23 — Candidate Stats Validation

## Cel

Sprawdzić, czy candidate identity daje sensowne statystyki zawodników i nie tylko ładniejsze przypisania.

## 7.1. Walidacja timeline

Dla każdego zawodnika sprawdzić:

```text
first observation
last observation
playing intervals
substitution boundaries
number of fragments
longest gap
large spatial jumps
parallel observations
predicted/occluded share
```

## 7.2. Walidacja statystyk

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

Nie zakładać, że większa wartość oznacza lepszy wynik.

## 7.3. Explainable deltas

Każda duża zmiana powinna mieć możliwość przejścia do źródłowych subjectów i decyzji operatora.

Przykład:

```text
Player A distance +620 m
→ 3 nowe subject fragments
→ frames 12000–14800
→ review cards X/Y/Z
```

## 7.4. Readiness per feature

Przykład:

```json
{
  "player_identity": "ready",
  "playing_time": "ready",
  "heatmap": "ready_with_review",
  "distance": "experimental",
  "player_passes": "not_available"
}
```

Feature może być niedostępny mimo gotowego identity, jeżeli wymagane dane wejściowe są zbyt słabe.

## Gate P1.23

```text
0 known false assignments
0 impossible spatial jumps affecting stats
resolved detected coverage above accepted threshold
predicted/occluded excluded from observed distance
large stat deltas manually explained
candidate output reviewed on held-out match
```

---

# 8. P1.24 — Controlled Production Apply

## Cel

Dopiero po pozytywnym P1.20A–P1.23 umożliwić jawne zastosowanie candidate identity do produkcji.

## 8.1. Apply UX

Operator musi zobaczyć:

```text
input plan and digests
review completeness
unresolved coverage
blocking warnings
candidate vs production diff
files to be replaced
```

Apply wymaga jawnego potwierdzenia.

## 8.2. Backup i transaction manifest

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

## 8.3. Rebuild downstream

Po apply przebudować co najmniej:

```text
player identity assignments
resolved player timeline
resolved player stats
player heatmaps
player-level events/passes, jeśli zależne
analysis readiness
package/publication freshness
```

Nie pozostawiać nowego identity z poprzednimi statystykami.

## 8.4. Post-apply validation

```text
0 hard identity conflicts
0 stale downstream artifacts
all output hashes recorded
readiness gates recalculated
package remains unpublished/stale until rebuild completes
```

## 8.5. Rollback

Rollback ma:

- przywracać backup;
- przebudowywać zależne artefakty;
- zapisywać status i reason;
- nie usuwać audytu operatora.

---

# 9. P1.25 — Event-Level and Orphan Review

Ten milestone ma zostać zaprojektowany na podstawie realnych danych z P1.22, nie z góry.

## 9.1. Priorytetowe przypadki

```text
structural-conflict subjects
long unresolved fragments
identity switch boundaries
overlap exits
parallel subject conflicts
orphan fragments affecting possession/events
```

## 9.2. Jednostka review

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

## 9.3. Priorytetyzacja

Najpierw pokazywać fragmenty o największym wpływie:

```text
long duration
ball possession involvement
player event involvement
large stat impact
structural conflict
```

Krótkie noise tracklety bez wpływu na wynik nie powinny zaśmiecać operatora.

---

# 10. P2 — Automatyzacja po stabilnym workflow

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
+ role
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

# 11. Data contracts

## 11.1. Stable keys

Każdy review subject, decision, promotion row i apply transaction musi posiadać stabilny klucz niezależny od kolejności listy.

## 11.2. Exact source observations

Promocja musi wskazywać dokładnie:

```text
frame
tracklet_id
candidate_subject_id
player_id
source review decision
```

## 11.3. Reliability

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

## 11.4. Freshness

Zmiana któregokolwiek wejścia identity powoduje stale downstream artifacts:

```text
candidate graph
shadow timeline
team config
roster
review contract
operator decisions
manual splits
algorithm version
parameters
```

---

# 12. Quality i readiness

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

---

# 13. CI i testy

Aktualnie lokalne testy są raportowane w roadmapie, ale repo nie powinno polegać wyłącznie na lokalnej deklaracji agenta.

Dodać lub rozszerzyć GitHub Actions:

```text
backend unit tests
identity contract tests
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

# 14. KPI

## Główne KPI produktowe

```text
manual review time per match
manual decisions per match
resolved detected coverage
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
```

Nie uznawać za sukces samego spadku liczby subjectów, jeśli wynika z false merges.

---

# 15. Zmieniona kolejność implementacji

```text
P1.20A  Promotion Safety Audit
P1.21   Candidate Apply
P1.22   Full-Match Operator Benchmark
P1.23   Candidate Stats Validation
P1.24   Controlled Production Apply
P1.25   Event-Level / Orphan Review driven by benchmark evidence
P2      Approved-anchor automation and cross-match assistance
```

Najbliższy task dla agenta:

```text
Implement P1.20A Promotion Safety Audit
```

Nie implementować jeszcze produkcyjnego apply adaptera.

---

# 16. Acceptance Criteria całej zmienionej roadmapy

## P1.20A

- [ ] wszystkie duplikaty są sklasyfikowane;
- [ ] distant parallel observations blokują plan;
- [ ] structural-conflict whole-subject assignments blokują plan;
- [ ] team active-player cap jest walidowany;
- [ ] multiple goalkeeper conflicts są walidowane;
- [ ] pełne lineage digests są sprawdzane;
- [ ] unresolved time-weighted coverage jest raportowane;
- [ ] per-player readiness jest raportowane;
- [ ] produkcyjne artefakty pozostają bez zmian.

## P1.21

- [ ] candidate assignments/timeline/stats/heatmaps powstają obok produkcji;
- [ ] candidate vs production diff jest dostępny;
- [ ] predicted/occluded nie są liczone jako observed distance;
- [ ] output jest atomowy i deterministyczny;
- [ ] public package nie używa candidate artifacts.

## P1.22

- [ ] co najmniej trzy mecze są ocenione;
- [ ] co najmniej jeden mecz jest held-out;
- [ ] review time jest mierzone;
- [ ] false assignments są audytowane;
- [ ] unresolved coverage jest mierzona czasowo, nie tylko liczbą kart.

## P1.23

- [ ] candidate timeline każdego zawodnika jest sprawdzony;
- [ ] duże stat deltas mają explainable source;
- [ ] brak impossible spatial jumps wpływających na statystyki;
- [ ] feature-level readiness jest wyliczane.

## P1.24

- [ ] apply wymaga jawnego potwierdzenia;
- [ ] istnieje backup i transaction manifest;
- [ ] zapis jest atomowy;
- [ ] downstream artifacts są przebudowane;
- [ ] package freshness jest poprawne;
- [ ] rollback został przetestowany.

## Cel końcowy

- [ ] median manual review time < 15 minut;
- [ ] resolved detected coverage > 95% dla analizowanej drużyny;
- [ ] 0 znanych false assignments po review;
- [ ] 0 unresolved structural conflicts w produkcji;
- [ ] 0 równoległych odległych obserwacji jednego zawodnika;
- [ ] player-level stats posiadają jawny readiness status.

---

# 17. Anti-goals

W kolejnych milestone’ach nie należy:

- nadpisywać produkcji bez candidate stage;
- ukrywać konfliktów przez wybór obserwacji o wyższym confidence;
- traktować wszystkie duplikaty jako bezpieczne;
- wymuszać przypisania unresolved fragmentu;
- używać predicted positions jako observed distance;
- publikować candidate stats;
- obniżać hard constraints, aby zwiększyć coverage;
- optymalizować wyłącznie pod `easy90` i `hard3m`;
- budować persistent gallery przed full-match validation;
- dodawać kolejnych warstw research bez wpływu na review time lub correctness.

---

# 18. Raport końcowy agenta po każdym milestone

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

Agent nie może automatycznie przechodzić do produkcyjnego apply tylko dlatego, że testy jednostkowe są zielone. Każdy promotion milestone wymaga jawnego spełnienia gate’ów z tego dokumentu.
