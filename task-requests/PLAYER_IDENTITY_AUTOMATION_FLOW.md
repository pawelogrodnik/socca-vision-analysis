# Player Identity Automation and Minimal-Review Flow

## 0. Status i relacja do innych dokumentów

Ten dokument definiuje **product flow i operator UX** dla player identity.

Obowiązuje razem z:

```text
task-requests/PLAYER_IDENTITY_DEVELOPMENT_PLAN.md
task-requests/PLAYER_IDENTITY_STABILIZATION_ROADMAP.md
AGENTS.md
```

Nadrzędna kolejność prac znajduje się w:

```text
task-requests/PLAYER_IDENTITY_DEVELOPMENT_PLAN.md
```

Ten dokument nie odblokowuje production apply. Wszystkie automatyczne assignmenty pozostają candidate/shadow do jawnego controlled apply.

---

# 1. Cel produktowy

Docelowy produkt nie może wymagać od użytkownika:

```text
setek ręcznie oznaczanych cropów
przeglądania każdego raw trackletu
powtarzania tego samego assignmentu w kilku ekranach
ręcznego wpisywania coordinates/confidence/internal IDs
ręcznej anotacji numerów koszulek po każdym meczu
czekania na perfekcyjne ReID przed zobaczeniem wyniku
```

Dopuszczalny model pracy dla pierwszego MVP:

```text
kilka lub kilkadziesiąt high-value decyzji
→ system skaluje je na wiele klatek
→ użytkownik dostaje reviewed video i statystyki
```

Manualna praca jest częścią produktu, jeżeli:

- jest ograniczona;
- jest zrozumiała bez wiedzy technicznej;
- ma duży wpływ na coverage;
- można łatwo zweryfikować wynik na finalnym wideo.

Najważniejsze KPI:

```text
active operator time
manual decisions
confirmed identity coverage
unresolved coverage
known false names after review
ID switches/false merges visible in final output
stats coverage
```

---

# 2. Aktualny end-to-end target

```text
1. Upload wideo i rosteru
2. Kalibracja boiska
3. Player/ball detection i tracking
4. Tracklet splitting, team candidates i candidate subjects
5. Automatyczny wybór kilku łatwych klatek
6. Initial Identity Audit — observation-level seeds
7. Seed-aware candidate resolve i redukcja whole-subject review
8. Whole-subject/exception review
9. Finalize reviewed identity snapshot
10. Generate reviewed video
11. Opcjonalna minimapa/radar
12. Reviewed timeline/stats/heatmaps z coverage
13. Korekta wykryta na wideo i tani downstream rerender
14. Dopiero później pełny benchmark i controlled production apply
```

Zmiana operator decision:

```text
no full-match YOLO rerun
no tracking rerun
→ rebuild reviewed identity
→ rebuild video/stats/minimap
```

---

# 3. Jednostki tożsamości

System rozróżnia:

```text
detection_id
track_id
tracklet_id
candidate_subject_id
canonical_player_id
```

## `track_id`

Lokalny identyfikator trackera. Może się zmienić po zgubieniu zawodnika. Nie jest `player_id`.

## `tracklet_id`

Ciągła obserwacja jednej osoby w ograniczonym czasie.

## `candidate_subject_id`

Hipoteza grupująca tracklety. Może być błędna, conflicted lub unresolved.

## `canonical_player_id`

Trwały identyfikator rosteru. Może pojawić się w finalnym reviewed snapshot tylko po spełnieniu kontraktu potwierdzenia.

---

# 4. Initial Identity Audit

## 4.1. Cel

Initial Identity Audit dostarcza niewielką liczbę bardzo pewnych observation-level anchors.

```text
kilka pewnych obserwacji
→ wiele downstream suggestions/resolved fragments
```

Seed oznacza:

```text
na tej obserwacji to na pewno Paweł
```

Nie oznacza automatycznie:

```text
cały tracker/subject to Paweł
```

## 4.2. Budżet

Domyślnie:

```text
5–8 wybranych klatek
maksymalnie 10 bez jawnego rozszerzenia
około 8–12 aktywnych decyzji jako target
możliwość wcześniejszego zakończenia
```

## 4.3. Akcje

```text
konkretny gracz z rosteru Team A
Team A — unknown
Team B — unknown
referee
false detection
skip / not sure
```

Operator nie podaje confidence ani technicznych IDs.

## 4.4. Automatyczny zapis

Aplikacja zapisuje:

```text
frame/timestamp
bbox
tracklet/subject provenance
player/team IDs
roster number
capture domain
source digests
schema/algorithm version
```

---

# 5. Seed-aware resolve i review reduction

Operator seed może propagować się tylko przez:

```text
exact observation lineage
safe local continuity
team consistency
temporal overlap constraints
parallel same-player constraints
structural blockers
fresh source digests
```

Po bezpiecznej propagacji:

- rozwiązane karty znikają z domyślnej kolejki;
- conflicts pozostają widoczne;
- nie wolno ponownie wymagać tego samego assignmentu bez konkretnego konfliktu;
- unresolved pozostaje legalne.

Wymagany raport:

```text
review_cards_before
review_cards_after
subjects_resolved
tracklets_resolved
frames_resolved
manual decisions before/after
active operator time
conflicts
known false assignments
```

---

# 6. Reviewed identity snapshot

Po zakończeniu review aplikacja tworzy jeden kanoniczny artifact:

```text
reviewed_identity_snapshot.json
```

Jest to jedyne finalne candidate/reviewed źródło tożsamości dla eksportów i lokalnych statystyk.

## 6.1. Źródła

```text
Initial Identity Audit decisions
whole-subject review decisions
manual remediation
safe resolver proposals
team/temporal/structural constraints
```

## 6.2. Statusy

```text
confirmed
probable
unresolved
conflicted
blocked
```

## 6.3. Display contract

```text
confirmed
→ roster name

probable/unresolved/conflicted
→ stable Axx/Bxx label

blocked/invalid
→ Unknown albo brak renderowania
```

Imię nie może pochodzić wyłącznie z niesprawdzonego ReID top-1.

## 6.4. Stats contract

```text
confirmed
→ eligible for player-specific stats

probable/unresolved/conflicted
→ excluded from named player stats
→ może zasilać team-level stats, jeśli team jest pewny
```

---

# 7. Reviewed video

Named reviewed MP4 jest częścią najbliższego MVP.

## 7.1. Trigger

Po review użytkownik ma przycisk:

```text
Generate reviewed video
```

## 7.2. Overlay

```text
bbox
team color
confirmed name
Axx/Bxx fallback
conflict/review marker
match time
optional ball marker
optional minimap
```

## 7.3. Safety

- imię tylko dla `confirmed`;
- probable ReID nie może być pokazane jako imię;
- fallback ID jest stabilny w obrębie eksportu;
- false detections nie są traktowane jako gracze;
- output zapisuje snapshot digest;
- rerender jest downstream-only.

## 7.4. QA role

Reviewed video służy do wykrywania:

```text
wrong confirmed player
ID switch
false merge
false split
bbox przypisanego do niewłaściwej osoby
zbyt agresywnej propagacji
```

Korekta wykryta na wideo musi prowadzić do źródłowej karty/trackletu, a następnie do taniego rerenderu.

---

# 8. Minimap/radar

Minimapa wykorzystuje istniejące mapowanie pozycji na boisko.

## 8.1. Pierwsza wersja

```text
Team A markers
Team B markers
ball marker, jeśli dostępny
confirmed initials/number opcjonalnie
unresolved jako anonimowe team markers
```

## 8.2. Zasady

- nie wymaga ReID;
- nie wymyśla pozycji przy braku wiarygodnej obserwacji;
- jawnie respektuje clamp/outside-play status;
- stosuje lekkie wygładzanie, bez agresywnej interpolacji;
- korzysta z tej samej osi/orientacji co heatmapy.

---

# 9. Reviewed stats i coverage

Każda statystyka indywidualna musi mieć jawne coverage/readiness.

## 9.1. Pierwszy zakres

```text
playing/detected time
heatmap
average position
observed distance
team shape
team possession, jeśli ball pipeline jest gotowy
player possession/contact/pass tylko dla confirmed windows
```

## 9.2. Coverage

Raportować co najmniej:

```text
confirmed identity coverage
unresolved identity coverage
heatmap coverage
observed-distance coverage
possession attribution coverage
pass attribution coverage
```

Nie wypełniać luk błędnym assignmentem tylko po to, aby wynik wyglądał kompletnie.

## 9.3. Readiness

```text
ready
ready_with_review
experimental
not_available
```

Brak ball artifacts nie może obniżać gotowości identity/heatmap.

---

# 10. Evidence graph i resolver

Wszystkie automatyczne źródła trafiają do jednego explainable resolvera.

## 10.1. Evidence

```text
operator-confirmed observation
team constraints
safe continuity
accepted subject lineage
jersey episode, jeśli trusted
match-specific ReID
motion/spatial context
capture domain
```

## 10.2. Priorytet

```text
1. exact operator confirmation
2. hard safety constraints
3. safe continuity/lineage
4. trusted jersey + unique same-team roster lookup
5. gated match-specific ReID
6. weaker context
```

## 10.3. Rola resolvera

Resolver generuje:

```text
suggestions
rankings
explanations
conflicts
abstentions
```

Resolver nie jest drugim finalnym źródłem prawdy obok reviewed snapshotu.

## 10.4. Conflicts

Przykład:

```text
operator: Paweł
ReID: Bartek
```

Wynik:

```text
operator retained
conflict recorded
manual review if propagation is affected
```

Nie silent override.

---

# 11. ReID policy

ReID jest advisory evidence.

## 11.1. Dozwolone użycie

```text
ranking unresolved tracklets
suggestion in review
cross-capture comparison
operator workload reduction
```

## 11.2. Niedozwolone użycie

```text
automatic confirmed name after failed gate
irreversible cross-subject merge
name on final video without confirmation
training/model selection on H2 holdout
```

## 11.3. Product metric

Najważniejsze pytanie:

```text
ile decyzji operatora ReID oszczędziło
bez zwiększenia false merges/splits?
```

Model nie musi być perfekcyjny. Musi być praktycznie użyteczny.

## 11.4. Stop rule

Po jednym końcowym bounded eksperymencie:

```text
wyraźny zysk
→ shadow suggestions

brak zysku
→ freeze ReID
→ rozwój produktu trwa dalej
```

---

# 12. Jersey recognition policy

Jersey recognition jest opcjonalnym high-precision evidence source.

Nie jest warunkiem reviewed MVP.

Per-match user flow nie wymaga ręcznej anotacji paneli.

Research/admin workflow może używać ograniczonych curated subsets, ale tylko gdy istnieje konkretny cel diagnostyczny.

Jersey evidence może zostać użyte, gdy:

```text
panel jest czytelny
precision/specificity spełniają gate
same-team roster number jest jednoznaczny
brak konfliktu operatora
```

Przy konflikcie wynik to `needs_review`, nie automatyczny wybór.

---

# 13. Exception-only review

Po wdrożeniu reviewed output normalny review ma ewoluować w stronę wyjątków.

Priorytetowe przypadki:

```text
hard safety conflicts
possible ID switch
long unresolved interval
large stats impact
possible substitution/new player
operator/ReID conflict
cross-team proposal
parallel same-player proposal
```

Niskoprioritetowe:

```text
krótkie noise fragments
low-impact unresolved detections
redundant crops jednego episode
```

Assignment wykonany w Initial Audit nie może być powtarzany bez konkretnego konfliktu.

---

# 14. Adaptive audit

Adaptive audit jest późniejszym etapem.

System może wybierać kolejną klatkę tylko na podstawie zmierzonego expected information gain:

```text
nowy zawodnik
coverage gain
rozwiązanie długiego unresolved interval
rozstrzygnięcie wysokiego stats impact
cross-domain re-anchor
```

Nie optymalizować adaptive audit przed zebraniem telemetry z kilku realnych reviewed exports.

---

# 15. Telemetry i KPI

## Operator

```text
audit_frames_shown
audit_actions
active_operator_seconds
unique_players_seeded
whole_subject_decisions
exception_decisions
video_driven_corrections
```

## Identity

```text
confirmed tracklets
probable tracklets
unresolved tracklets
conflicted tracklets
confirmed time coverage
unresolved time coverage
ID switches
false merges
false splits
cross-team violations
```

## Automation contribution

```text
resolved by operator seed
resolved with lineage/continuity
suggested by ReID
ReID suggestions accepted/rejected
jersey-supported assignments
manual decisions saved
```

## Product output

```text
reviewed video generation time
rerender time
named-label errors found
minimap coverage
stats coverage per feature
```

---

# 16. Scope najbliższego MVP

W scope:

```text
Initial Identity Audit
whole-subject/exception review
reviewed identity snapshot
reviewed video
confirmed name vs Axx/Bxx policy
minimapa/radar
reviewed stats with coverage
cheap correction/rerender
```

Poza najbliższym MVP:

```text
production auto-apply
persistent cross-match gallery
face recognition
pełny timeline editor
pełna automatyczna obsługa zmian
mandatory jersey workflow
ciągłe retraining podczas review
```

---

# 17. Acceptance criteria

## Review

- [x] bounded Initial Audit istnieje;
- [x] skip/not sure jest dostępne;
- [x] operator nie wpisuje technicznych danych;
- [x] seed-aware review reduction istnieje;
- [ ] końcowy review tworzy jeden canonical snapshot;
- [ ] conflict/unresolved pozostają jawne.

## Reviewed output

- [ ] przycisk `Generate reviewed video`;
- [ ] imiona tylko dla confirmed;
- [ ] Axx/Bxx fallback dla pozostałych;
- [ ] snapshot digest w video manifest;
- [ ] minimapa jako opcjonalny overlay;
- [ ] korekta i downstream-only rerender.

## Stats

- [x] candidate stats/heatmap foundations istnieją;
- [ ] reviewed snapshot jest ich kanonicznym wejściem;
- [ ] per-feature coverage/readiness jest widoczne;
- [ ] unresolved nie zasila named player stats.

## Automation

- [x] appearance/ReID advisory infrastructure istnieje;
- [x] resolver shadow contract istnieje;
- [ ] realny bounded ReID result jest zapisany;
- [ ] ReID contribution jest mierzona liczbą oszczędzonych decyzji;
- [ ] resolver suggestions są częścią wspólnego review, nie osobnym finałem.

## Safety

- [ ] 0 cross-team confirmed assignments;
- [ ] 0 ukrytych parallel same-player conflicts;
- [ ] 0 znanych błędnych imion po finalnym review;
- [ ] production identity pozostaje niezmienione;
- [ ] rerender nie uruchamia YOLO/tracking.

---

# 18. Instrukcja dla agenta

Przed każdym identity milestone:

1. przeczytaj `AGENTS.md`;
2. przeczytaj `PLAYER_IDENTITY_DEVELOPMENT_PLAN.md`;
3. sprawdź aktualny HEAD i istniejące artifacts;
4. ustal, który pojedynczy product milestone realizujesz;
5. nie dodawaj nowego równoległego resolvera;
6. nie uruchamiaj YOLO/tracking dla downstream identity zmian;
7. zakończ realnym artifactem/demo;
8. raportuj operator impact i known limitations;
9. nie promuj do produkcji bez controlled apply milestone;
10. nie pozwól, aby research opóźnił reviewed MVP.

Najważniejsza reguła:

> System ma wykorzystać niewielką liczbę pewnych decyzji operatora do stworzenia wiarygodnego, łatwego do zweryfikowania wideo i statystyk. Niepewność ma być widoczna jako Axx/Bxx lub unresolved, a nie ukrywana pod błędnym imieniem.
