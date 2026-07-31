# Player Identity Development Plan

## 0. Rola dokumentu

Ten plik jest nadrzędnym planem **kolejności developmentu** dla obszaru player identity.

Nie zastępuje szczegółowych roadmap:

```text
task-requests/JERSEY_NUMBER_IDENTITY_ANCHORS.md
task-requests/PLAYER_IDENTITY_STABILIZATION_ROADMAP.md
task-requests/PLAYER_IDENTITY_AUTOMATION_FLOW.md
AGENTS.md
```

Ich role są różne:

```text
PLAYER_IDENTITY_STABILIZATION_ROADMAP
→ fundament identity, safety, candidate artifacts, review i controlled apply

JERSEY_NUMBER_IDENTITY_ANCHORS
→ percepcja numerów koszulek i jersey-number evidence

PLAYER_IDENTITY_AUTOMATION_FLOW
→ docelowy minimal-review product flow i fusion dowodów

PLAYER_IDENTITY_DEVELOPMENT_PLAN
→ dokładna kolejność wykonywania milestone'ów oraz zależności między roadmapami
```

Jeżeli szczegółowa roadmapa opisuje własną kolejność lokalnych kroków, agent wykonuje ją **wewnątrz aktualnej fazy z tego dokumentu**.

Jeżeli dokumenty wydają się sprzeczne:

1. nie zgadywać;
2. sprawdzić aktualny `HEAD` i faktycznie istniejące artifacts;
3. preferować bezpieczeństwo oraz minimalny koszt pracy operatora;
4. zaktualizować ten plan albo jawnie zgłosić konflikt przed implementacją.

---

# 1. Najważniejsza decyzja o kolejności

Nie wykonywać trzech roadmap sekwencyjnie w taki sposób:

```text
całe jersey anchors
→ cała player stabilization
→ cała player automation
```

Prawidłowa kolejność:

```text
1. domknąć tylko bieżący J8.3 jersey panel closeout
2. zbudować Initial Identity Audit i seed-aware automation IA0–IA6
3. wrócić do J8.4 i uzyskać użyteczny jersey recognizer
4. połączyć operator seeds + lineage + jersey + ReID w IA7
5. przebudować review do exception-only w IA8–IA9
6. ponownie zwalidować candidate stats i dopiero potem rozważyć P1.24 controlled apply
```

W skrócie:

```text
J8.3
→ IA0–IA6
→ J8.4 + jersey validation
→ IA7–IA9
→ P1.23 revalidation
→ P1.24 controlled production apply
→ P1.25 tylko według realnych braków z benchmarku
```

Powód:

- stabilization foundation już w dużej mierze istnieje;
- obecny whole-subject review jest zbyt kosztowny i nadal generuje false assignments;
- Initial Identity Audit może dostarczyć realny zysk bez gotowego OCR numerów;
- operator seeds i appearance galleries mogą później poprawić dataset oraz użyteczność jersey recognition;
- jersey jest silnym dodatkowym dowodem, ale nie powinien blokować pierwszej automatyzacji;
- production apply nie może nastąpić przed wspólnym benchmarkiem nowego flow.

---

# 2. Zasady obowiązujące w każdej fazie

## 2.1. Agent zawsze zaczyna od aktualnego stanu

Przed rozpoczęciem milestone'u agent ma:

1. pobrać aktualny `HEAD`;
2. przeczytać `AGENTS.md`;
3. przeczytać ten development plan;
4. przeczytać szczegółową roadmapę aktualnej fazy;
5. sprawdzić istniejące schema versions, artifacts, UI oraz testy;
6. nie zakładać, że status zapisany wcześniej nadal jest aktualny;
7. zaktualizować status milestone'u na podstawie kodu i realnych artifacts, nie samej nazwy commitu.

## 2.2. Jeden główny milestone na cykl

Agent nie powinien w jednym dużym cyklu jednocześnie:

```text
budować nowego UI
+ zmieniać resolver
+ trenować nowy jersey model
+ wykonywać production apply
```

Każdy cykl powinien kończyć się:

```text
what changed
what was tested
real artifact/demo result
measured operator impact, jeśli dotyczy
known limitations
next allowed milestone
explicit stop/go decision
```

## 2.3. Shadow/candidate first

Do czasu jawnego P1.24:

```text
production player identity pozostaje niezmienione
production stats pozostają niezmienione
public package nie używa candidate artifacts
```

Manualny operator seed może wpływać na shadow/candidate resolver i rekomendacje, ale nie wykonuje automatycznego production promotion.

## 2.4. Nie uruchamiać ponownie ciężkiego pipeline'u bez potrzeby

Zmiany dotyczące:

```text
operator decisions
identity seeds
jersey evidence
appearance galleries
candidate assignments
review queue
```

mają korzystać z istniejących detekcji i tracków.

```text
no full-match YOLO rerun
```

chyba że milestone jawnie dotyczy player detectora lub istniejące artifacts są niekompatybilne/stale.

## 2.5. Minimalny koszt operatora

Każdy audyt i review podlega kontraktowi z `AGENTS.md`:

```text
user supplies human knowledge
application supplies all technical metadata
```

Agent nie może wymagać od użytkownika:

```text
x1/y1/x2/y2
normalized coordinates
tracklet_id / subject_id
hashes i digests
numeric confidence
IoU / blur / perspective scores
ręcznej listy setek samples
```

Normalny product flow ma wymagać małej liczby high-value decyzji. Research dataset workflow musi być oddzielony od per-match user workflow.

---

# 3. Stan wejściowy i istniejący fundament

Przed rozpoczęciem planu repo posiada już między innymi:

```text
raw tracking
tracklet splitting
stable subjects
team candidates
lineage i structural blockers
whole-subject review
operator decision store
promotion safety planning
partial candidate assignments
candidate timeline/stats artifacts
review telemetry
jersey-number evidence contracts i dataset infrastructure
```

Ten fundament nie jest końcowym produktem.

Obecny benchmark pokazał, że:

```text
review-every-card jest zbyt kosztowny
liczba kart jest wysoka
false identity assignments nadal występują
production promotion nie jest jeszcze bezpieczne
```

Dlatego najbliższy produktowy cel to nie dalsze zwiększanie liczby formularzy review, lecz:

```text
kilka pewnych operator seeds
→ maksymalne bezpieczne wykorzystanie automatyczne
→ review tylko wyjątków
```

---

# 4. Faza A — J8.3 Jersey Panel Closeout

## Status wejściowy

```text
J8.1 freeze recognizers: closed
J8.2 panel annotation contract: closed
J8.3 panel audit implementation: closed
J8.3 real dataset run + human approval + findings: closed
J8.3 final decision: READY_FOR_J8_4_DIAGNOSTIC
J8.4 PanelDigitNetV1: R1-R3 complete / diagnostic only / not eligible
```

Agent ma najpierw zweryfikować, czy ten status nadal odpowiada aktualnemu `HEAD`.

Checkpoint 2026-07-27:

```text
J8.3 implementation + canonical subset: complete
bounded panel-box audit package: generated (58 samples)
operator panel-box review: complete (58/58)
confirmed panel boxes: 19
canonical experiment: 19/19 audited, 0 invalid
readable panels: 16 / 50
readable visibility episodes: 13 / 20
absent/unreadable negatives: 3 / 30
human montage approval: approved and digest-bound
final decision: AVAILABLE_DATA_NOT_SUFFICIENT
J8.4: blocked until dataset readiness improves
```

Refresh 2026-07-28 (after applying the 19 saved panel decisions):

```text
selected panel definitions: 58 total, 19 audited, 39 still missing
readable confirmed panels: 16 / required 50
readable visibility episodes: 13 / required 20
absent/unreadable negatives: 3 / required 30
all currently known confirmed labels in the dataset: 28 across 24 visibility episodes
even annotating every remaining known confirmed label cannot reach the 50-crop threshold
minimum new readable confirmed examples required from a broader source: 22
J8.4: still blocked; prepare a small, high-value discovery/recovery package instead of asking for exhaustive re-annotation
```

Recovery package 2026-07-28:

```text
source: first-half match 7655bf7c, Team A only
package size: 65 independent visibility episodes (bounded below the 80-card cap)
target: 22 new certain readable panels and 27 useful negative panels
purpose: panel_readiness_recovery
operator may Skip / finish early; this is an offline research package, not normal match review
artifact: backend/storage/benchmarks/player_identity/j8-4-number-recovery-first-half-20260728-v1/index.html
next: apply the reviewed manifest, rebuild readiness and decide whether J8.4 can start
```

The readiness artifact can temporarily report `FIX_PANEL_PIPELINE_FIRST` while the
selected canonical subset has missing panel definitions. That status only describes
the incomplete subset. The operational J8.3 decision remains
`AVAILABLE_DATA_NOT_SUFFICIENT`: completing that subset alone cannot reach the
required number of readable positive examples.

Recovery refresh 2026-07-29:

```text
first recovery manifest applied: 10 confirmed, 3 absent, 1 unreadable, 29 skipped
readable confirmed panels: 24 / 50
readable visibility episodes: 23 / 20 (requirement met)
absent/unreadable negatives: 30 / 30 (requirement met)
remaining data gap: 26 certain readable number panels
skipped samples are persisted and excluded from later recovery queues
second package: 64 fresh, independent visibility episodes
artifact: backend/storage/benchmarks/player_identity/
  j8-4-number-recovery-second-followup-first-half-20260729-v1/index.html
J8.4 remains blocked until the 26 additional positive labels are collected
```

Second recovery refresh 2026-07-29:

```text
second reviewed manifest: 14 additional confirmed readable numbers
readable confirmed panels: 38 / 50
readable visibility episodes: 33 / 20 (requirement met)
absent/unreadable negatives: 30 / 30 (requirement met)
remaining data gap: 12 certain readable number panels
final bounded follow-up: 48 fresh independent episodes
artifact: backend/storage/benchmarks/player_identity/
  j8-4-number-recovery-final-followup-first-half-20260729-v1/index.html
J8.4 remains blocked only by these 12 positive labels
```

## Cel fazy

Odpowiedzieć na pytanie:

> Czy tight number-panel crops po finalnym preprocessingu są wystarczająco czytelne, spójne i poprawnie opisane, aby rozpocząć jeden prosty model panelowy?

## Zakres

Wykonać wyłącznie closeout z `JERSEY_NUMBER_IDENTITY_ANCHORS.md`, w szczególności:

```text
J8.3a annotation semantics
J8.3b canonical panel subset
J8.3c readiness gates
J8.3d digit-height diagnostics
real montage
human approval tied to digests
J8.3 findings
```

## Ograniczenie pracy ręcznej

To jest ograniczony research/admin workflow, nie docelowy per-match flow.

Agent ma:

- automatycznie wybrać i przygotować samples;
- pokazać montage;
- wymagać tylko prostego approve/reject oraz krótkich findings;
- nie wymagać ręcznego wpisywania bbox coordinates;
- nie rozszerzać pracy na wszystkie dostępne cropy, gdy canonical subset wystarcza.

## Gate końcowy

Faza kończy się dokładnie jedną decyzją:

```text
PROCEED_TO_J8_4_LATER
FIX_PANEL_PIPELINE_FIRST
AVAILABLE_DATA_NOT_SUFFICIENT
```

Ważne:

```text
PROCEED_TO_J8_4_LATER
```

nie oznacza, że agent natychmiast zaczyna J8.4 w tym samym cyklu.

Po J8.3 należy przejść do Fazy B.

---

# 5. Faza B — Initial Identity Automation Core, IA0–IA4

Ta faza ma najwyższy priorytet produktowy po J8.3.

Nie wymaga działającego jersey recognizera.

## IA0 — Frozen-artifact frame selection prototype

### Cel

Automatycznie wybrać małą liczbę łatwych i wysokowartościowych klatek z istniejących artifacts.

### Domyślny budżet

```text
5–8 frames
10 frames hard default maximum
stop earlier when no new easy/high-value identity is available
```

### Scoring powinien uwzględniać

```text
visible player count
new/unseeded player coverage
bbox size
low overlap
tracklet continuity
low edge cutting
low blur
low ID-switch suspicion
time diversity
capture-domain diversity
```

### Output

Read-only, deterministyczny artifact z:

```text
selected frame
reason/score components
visible detections
tracklet/subject provenance
thumbnail/full-frame artifact references
selection digest
```

### Gate

- klatki faktycznie są łatwiejsze niż losowy baseline;
- nie ma wielu prawie identycznych klatek;
- output jest deterministyczny;
- żadnego UI wymagającego raw coordinates.

### Status 2026-07-27

```text
CLOSED — IA0_ACCEPTED
```

Zaimplementowano:

- read-only selektor pracujący na frozen `global_identity.json`,
  `tracklets.json`, `analysis_report.json` i opcjonalnym camera motion;
- deterministyczny scoring jakości, różnorodności czasowej i pokrycia
  dotychczas niewidzianych subjectów;
- twardy limit 10 klatek oraz domyślny budżet 8 klatek;
- eksport pełnych stop-klatek i lekkich miniaturek bez ponownego YOLO;
- canonical digests wejść, `selection_digest`, provenance i safety contract;
- testy budżetu, deterministyczności, odstępów czasowych, jakości,
  provenance oraz braku mutacji wejściowego identity.

Akceptacja na frozen easy90:

```text
run: 20260715T111009Z-yolo-ultralytics-chunked-598d1dee
artifact: backend/storage/benchmarks/player_identity/ia0-frame-selection-easy90-20260727-v1
candidate frames: 180
selected frames: 8
unique visible subjects: 19
selected mean intrinsic score: 0.839489
random baseline mean intrinsic score: 0.745798
near-duplicate pairs: 0
selection digest: a8095f335a09fd3d6123c518b78fc713caf97c493e0ddbb237cdc006fe807820
```

Powtórne uruchomienie wygenerowało identyczne selected rows i ten sam
`selection_digest`. Kontrola wizualna montage potwierdziła czytelne,
zróżnicowane czasowo klatki bez masowego overlapu. IA1 może korzystać z
pełnych klatek i detection provenance zapisanych przez IA0.

## IA1 — Initial Identity Audit read-only UI

### Cel

Pokazać pełną stop-klatkę z klikalnymi bboxami.

### Akcje

```text
named Team A roster player
Team A — unknown player
Team B — unknown player
referee
false detection
skip / not sure
```

### UX

```text
click bbox → choose action/player
or
click roster player → click bbox
```

Kliknięcie może pokazać powiększony crop i lekki neighboring-frame strip, ale nie może wymuszać długiego clip review.

### Gate

- użytkownik rozumie akcję bez dokumentacji technicznej;
- brak pól coordinate/confidence/internal ID;
- `Skip / Nie wiem` zawsze dostępne;
- widoczny progress;
- możliwość zakończenia przed pełnym pokryciem.

### Status 2026-07-27

```text
CLOSED — IA1_ACCEPTED
```

Zaimplementowano:

- read-only endpoint i cache per match, korzystające wyłącznie z frozen
  artifacts oraz stop-klatek wybranych przez IA0;
- pełną stop-klatkę z klikalnymi bboxami i czytelnym cropem wybranej
  obserwacji;
- obie szybkie ścieżki operatora: `bbox → zawodnik/akcja` oraz
  `zawodnik/akcja → bbox`;
- akcje roster player, nieznany Team A/B, sędzia, false detection oraz
  `Pomiń / nie wiem`;
- widoczny postęp, licznik decyzji, nawigację między klatkami i możliwość
  zakończenia audytu w dowolnym momencie;
- jednorazowe uzbrajanie akcji, żeby wybór osoby nie został przypadkiem
  przeniesiony na kolejny bbox;
- automatyczny reset read-only dokumentu i lokalnych decyzji po zmianie
  meczu;
- operator-safe public contract bez coordinates, confidence i internal IDs;
  techniczne provenance pozostaje w artifacts backendu.

Walidacja lokalna:

```text
match: 46904e8c
selected frames: 8
visible observations: 94
YOLO rerun: no
backend focused tests: 11 passed
frontend strict typecheck: passed
frontend build: passed
operator flow: bbox→player, player→bbox, skip, navigation, early finish passed
```

IA1 zamknęło read-only kontrakt interakcji. Zapis decyzji i telemetry został
następnie dołączony w IA2; propagacja i seed-aware rebuild nadal należą do
IA3–IA4.

## IA2 — Atomic operator-seed store and telemetry

### Cel

Zapisać observation-level gold seeds bez modyfikacji production identity.

### Seed oznacza

```text
na tej konkretnej obserwacji to na pewno Roman
```

Nie oznacza:

```text
cały raw tracker_id to Roman
```

### Minimalny zapis

System sam zapisuje:

```text
frame/timestamp
bbox
track_id / tracklet_id / subject_id
player_id
team_id
roster number
source/provenance
capture domain
digests/schema version
```

### Telemetry

```text
audit_frames_shown
audit_crops_clicked
audit_actions
active_operator_seconds
unique_players_seeded
team_assignments_corrected
false_detections_marked
```

### Gate

- atomic save;
- resume works;
- stale artifact detection;
- production hashes unchanged;
- duplicate assignment conflict na tej samej klatce jest blokowany lub jawnie obsługiwany.

### Status 2026-07-27

```text
CLOSED — IA2_ACCEPTED
```

Zaimplementowano:

- osobny `identity_operator_seeds.json` z observation-level decisions,
  technicznym provenance, capture domain, roster metadata i source digests;
- autosave po każdej akcji, idempotentne `update_id`, zapis atomowy przez
  plik tymczasowy oraz wznowienie audytu po ponownym otwarciu;
- publiczny kontrakt bez bboxów i internal provenance przy zachowaniu pełnych
  danych technicznych w artefakcie backendu;
- telemetry sesji, pokazanych klatek, kliknięć cropów, decyzji i aktywnego
  czasu operatora;
- stale selection detection, która nie przyjmuje decyzji dla zmienionego
  zestawu klatek;
- blokadę przypisania jednego realnego zawodnika do dwóch obserwacji w tej
  samej klatce;
- snapshot i kontrolę hashy production identity przed i po zapisie;
- integrację UI z autosave, widocznym stanem zapisu, resume i bezpiecznym
  komunikatem błędu.

Walidacja lokalna:

```text
backend focused tests: 16 passed
frontend strict typecheck: passed
frontend production build: passed
atomic temporary files after save: 0
production identity mutation during seed save: 0
YOLO rerun: no
downstream identity rebuild: no
```

## IA3 — Seed-aware candidate identity re-resolve

### Cel

Wykorzystać operator seeds do automatycznego rozwiązania większej liczby trackletów/subjects bez ponownego YOLO.

### Propagacja musi przejść przez

```text
local tracklet continuity
lineage freshness
team consistency
temporal overlap constraints
parallel-position constraints
structural blockers
```

Operator seed ma najwyższy priorytet dla wskazanej obserwacji, ale nie omija twardych safety gates przy propagacji.

### Output

```text
identity_operator_seeds.json
identity_seeded_candidate_assignments.json
seed propagation provenance
accepted/rejected propagation reasons
conflicts
```

### Gate

- 0 ukrytych cross-team links;
- 0 impossible parallel same-player assignments;
- unresolved pozostaje jawne;
- production identity unchanged;
- deterministyczny rebuild z frozen tracks.

### Status 2026-07-27

```text
CLOSED — IA3_ACCEPTED
```

Zaimplementowano:

- osobny shadow-only artefakt
  `identity_seeded_candidate_assignments.json`, który nie jest wejściem do
  statystyk ani production identity;
- propagację named operator seed wyłącznie przez dokładne lineage
  `frame + tracklet_id` do jednego candidate subjectu;
- twarde gate'y drużyny, świeżości selection, blockerów strukturalnych i
  równoległych obserwacji tej samej realnej osoby;
- jawne `accepted_assignments`, `rejected_propagations`, `conflicts` oraz
  `unresolved_subjects` wraz z przyczynami i provenance;
- deterministyczne łączenie wielu zgodnych seedów jednego subjectu oraz
  blokadę sprzecznych nazwisk dla tego samego subjectu;
- automatyczny tani rebuild po zapisie IA2 seeds oraz osobne endpointy
  odczytu i ręcznego rebuilda;
- atomowy zapis i kontrolę hashy production identity przed i po rebuildzie.

Walidacja lokalna:

```text
backend focused tests: 12 passed
deterministic repeated build/rebuild: passed
cross-team propagation: blocked and reported
parallel same-player assignment: blocked and reported
hard structural blocker: blocked and reported
atomic temporary files after rebuild: 0
production identity mutation during rebuild: 0
YOLO rerun: no
overlay render: no
```

## IA4 — Existing whole-subject review integration and reduction

### Cel

Nowy audit ma skrócić późniejszy review, a nie dodać kolejny obowiązkowy ekran.

Po IA3:

- seeded/bezpiecznie rozwiązane karty mają zniknąć z normalnej kolejki albo zostać oznaczone jako completed;
- rekomendacje whole-subject review mają wykorzystywać operator seeds;
- tego samego zawodnika nie wolno przypisywać ponownie bez konkretnego konfliktu;
- konflikty i unresolved mają pozostać widoczne.

### Wymagany raport

```text
review_cards_before_seeding
review_cards_after_seeding
subjects_resolved_after_seeding
tracklets_resolved_after_seeding
frames_resolved_after_seeding
manual decisions before/after
active time before/after, jeżeli mierzalne
conflicts created
false assignments found
```

### Gate Fazy B

Faza B jest zakończona tylko wtedy, gdy pełny przepływ działa:

```text
selected frames
→ intuitive audit
→ saved seeds
→ downstream candidate resolve
→ reduced/prioritized existing review
```

Samo utworzenie UI bez downstream reduction nie zamyka Fazy B.

### Status implementacji — 2026-07-27

`CLOSED`.

Zaimplementowano:

- fresh IA3 seeded assignments są automatycznie włączane do whole-subject review;
- bezpiecznie rozwiązane karty dostają `completed_by_initial_audit` i znikają z domyślnej kolejki `Do review`;
- konflikty seed/manual oraz seed/seed pozostają widoczne jako `blocked_seed_conflict` i można je rozstrzygnąć;
- zawodnik zajęty przez nakładający się seeded subject nie może zostać ponownie wybrany bez jawnego konfliktu;
- zapis Initial Identity Audit przebudowuje IA3 i od razu odświeża redukcję whole-subject review, bez YOLO;
- `identity_seeded_review_reduction_report.json` zapisuje wymagane metryki redukcji, konflikty i false assignments;
- frontend rozróżnia decyzje operatora od kart rozwiązanych przez initial audit i pokazuje efekt redukcji;
- production identity, statystyki i heatmapy pozostają nietknięte.

Gate Fazy B potwierdzony testami kontraktowymi:

```text
selected frames
→ saved seeds
→ downstream candidate resolve
→ reduced/prioritized existing review
```

Live-check na `461e4dd9` (`20260727T132244Z-yolo-ultralytics-chunked-725dbdd1`):

```text
operator actions: 50
unique named players: 7
safe subjects resolved: 12
tracklets resolved: 13
frames resolved: 2692
review cards: 109 -> 97
parallel same-player conflicts detected and blocked: 3
unsafe accepted parallel assignments: 0
production identity mutations: 0
YOLO / tracking / overlay rerun: no
```

Naprawiono również bootstrap fresh-match review oraz freshness operator seeds:

- whole-subject review jest budowany po pierwszym audycie nawet wtedy, gdy wcześniej nie istniał;
- telemetry i techniczne timestampy nie unieważniają decyzji operatora;
- zmiana merytorycznej decyzji nadal poprawnie oznacza downstream jako stale;
- bezpiecznie zablokowany konflikt jest zapisywany do review, a nie traktowany jako awaria rebuilda.

---

# 6. Faza C — Cross-half Anchoring and Automatic Appearance, IA5–IA6

## IA5 — Second-half capture-domain re-anchor

### Cel

Dostarczyć kilka potwierdzonych identities w H2, ponieważ H2 różni się kątem, stroną kamery i światłem.

### Budżet

```text
2–3 easy H2 frames
3–5 confirmed players as target
skip entirely when H1 evidence already resolves H2 safely
```

UI powinno głównie potwierdzać sugestie:

```text
Roman #6
[Confirm] [Different player] [Team B] [Skip]
```

Nie wykonywać pełnego drugiego lineup audit.

### Status 2026-07-27

```text
CLOSED — IA5_ACCEPTED
```

Zaimplementowano:

- jawne wykrywanie początku H2 wyłącznie z `match_phase_config.json`;
- świadome `not_applicable`, gdy druga połowa nie jest skonfigurowana,
  bez zgadywania jej początku z połowy długości materiału;
- automatyczne pominięcie re-anchor, jeżeli co najmniej trzech graczy
  posiada już bezpieczne pokrycie H2;
- wybór maksymalnie trzech łatwych i zróżnicowanych klatek H2;
- confirmation-first UI z akcjami `Potwierdź`, `Inny zawodnik`, `Team B`
  oraz `Pomiń / nie wiem`;
- współdzielony zapis operator seeds i downstream rebuild z frozen
  detections/tracks, bez ponownego YOLO;
- deterministyczny limit klatek także na granicy kontraktu dokumentu,
  niezależnie od zawartości cache;
- testy explicit-phase, limitu trzech klatek, aktualnych sugestii,
  braku mutacji wejścia i współdzielonego seed-aware rebuilda.

Akceptacja na runie:

```text
20260727T132244Z-yolo-ultralytics-chunked-725dbdd1
match: 461e4dd9
status: not_applicable
reason: second_half_not_configured
frames: 0
```

To jest oczekiwany wynik dla 90-sekundowego klipu bez jawnej H2.

## IA6 — Automatic approved appearance gallery

### Cel

Po operator-confirmed seed automatycznie wybrać reliable appearance crops.

```text
Roman H1 seed
→ safe local fragment
→ automatic reliable crop selection
→ Roman H1 gallery

Roman H2 re-anchor
→ Roman H2 gallery
→ match-specific cross-domain prototype
```

Użytkownik nie oznacza każdego appearance cropa.

System sam selekcjonuje, o ile dostępne:

```text
front/back/side
near/far
sun/shade
H1/H2
low occlusion
valid visual content
```

### Zastosowanie

Na tym etapie ReID służy do:

```text
ranking unresolved fragments
candidate suggestions
cross-half matching
```

Nie wykonuje nieodwracalnego cross-subject merge.

### Gate Fazy C

- potwierdzony cross-half prototype dla części graczy;
- measurable top-k usefulness na unresolved fragments;
- brak automatycznych false merges;
- crop selection automatyczne;
- operator work nie rośnie względem IA0–IA4.

### Status 2026-07-28

```text
IMPLEMENTED — IA6_CODE_COMPLETE
VALIDATED_ADVISORY_ONLY — CROSS_ANALYSIS_BASELINE_PASSED
```

Zaimplementowano:

- automatyczną galerię appearance wyłącznie z operator-confirmed subjects;
- limit i selekcję reliable cropów per gracz oraz jawna domenę H1/H2;
- automatyczne ponowne wykorzystanie cropów whole-subject review,
  bez dodatkowej pracy operatora;
- robust prototype per subject i per real player;
- leave-one-subject-out evaluation oraz advisory top-3 ranking dla
  nierozwiązanych subjectów;
- przenośny fallback appearance dla Apple Silicon, gdy model OpenVINO
  nie może zostać uruchomiony;
- jawne oznaczenie fallbacku jako `baseline_fallback`, aby nie mieszać
  jego wyników z docelowym modelem ReID;
- cache embeddingów oraz osobne shadow artifacts i quality report;
- integrację z frozen downstream rebuildem, bez YOLO, trackingu
  i renderowania pełnego overlayu;
- fail-open warning contract oraz test niezmienności production identity;
- testy deterministyczności, separacji H1/H2, rankingu, braku
  automatycznych merge oraz pełnej integracji z IA4/IA5.

Live-check:

```text
run: 20260727T132244Z-yolo-ultralytics-chunked-725dbdd1
match: 461e4dd9
gallery players: 6
accepted candidate subjects: 12
selected gallery crops: 42
embedded crops: 268
subject prototypes: 89
player prototypes: 6
unresolved subjects ranked: 25
leave-one-subject-out queries: 12
top-1 accuracy: 33.3%
top-3 accuracy: 50.0%
automatic merges: 0
operator actions required: 0
production identity changed: no
```

Wynik potwierdza działający kontrakt i mierzalny baseline, ale nie
zamyka jeszcze pełnego gate'u Fazy C:

- klip nie posiada H2, więc `cross_domain_players=0`;
- portable descriptor jest tylko bezpiecznym rankingiem bazowym;
- przed promocją IA6 potrzeba potwierdzenia, że top-k przewyższa prosty
  baseline na nierozwiązanych fragmentach w osobnych analizach tego samego
  fizycznego meczu.

Cross-analysis live validation, 2026-07-28:

```text
source analysis: 7655bf7c
target analysis: 343980c8
source manual assignments: 162
target manual assignments: 150
source player profiles: 10
target subject queries: 125
top-1 accuracy: 37.6%
top-3 accuracy: 60.0%
same-team random top-1: 10.0%
same-team random top-3: 30.0%
top-1 lift vs random: 3.76x
top-3 lift vs random: 2.00x
automatic merges: 0
production identity changed: no
```

To jest właściwy kontrakt dla produktu: połówki są osobnymi uploadami i
osobnymi analizami. Benchmark może wykorzystywać je jako różne domeny
capture, ale nie scala ich automatycznie ani nie wymaga stworzenia jednego
plikowego "pełnego meczu". Wynik jest wystarczający dla advisory ranking,
ale za słaby dla automatycznego przypisania realnego zawodnika.

Gate porównawczy IA6 jest zatem spełniony: ranking pokonuje prosty losowy
baseline tej samej drużyny. Nie jest to jednak zgoda na automatyczny merge;
każda sugestia pozostaje decyzją review lub seedem o jawnej proweniencji.

Po Fazie C przejść do Fazy D.

---

# 7. Faza D — J8.4 PanelDigitNetV1 and Jersey Validation

Do tej fazy przechodzimy dopiero po:

```text
J8.3 closeout
IA0–IA6 working on frozen/current match artifacts
```

## Cel

Uzyskać pierwszy rzeczywiście użyteczny, high-precision jersey recognizer, który dostarcza identity evidence, a nie tylko safe abstention.

## Zakres

Wykonać J8.4 zgodnie z `JERSEY_NUMBER_IDENTITY_ANCHORS.md`:

```text
tight panel input
small PanelDigitNetV1
readable/absent/unreadable handling
three digit positions
small-set overfit proof
real fixture validation
plain-shirt specificity
cross-capture-domain evaluation
```

Nie wracać do rozszerzania:

```text
OpenCV template baseline
whole-number centroid baseline
CRNN-CTC tuning
```

chyba że szczegółowa roadmapa zostanie jawnie zmieniona na podstawie nowych danych.

## Dane z Initial Audit

Operator-confirmed identity może dostarczyć roster number, ale:

```text
Roman #6 identity label
≠
every Roman crop is a readable number-6 sample
```

Candidate training sample wymaga automatycznej oceny panel visibility/readability i safe lineage.

## Walidacja

Przy jednym fizycznym meczu raportować jawnie:

```text
physical matches = 1
capture domains = 2
```

Mierzyć co najmniej:

```text
H1 → H2
H2 → H1
pooled result
worst-domain result
crop precision/recall
episode precision/recall
false confirmed reads
real fixtures
plain-shirt negatives
```

Nie nazywać tego cross-match generalization.

## Gate Fazy D

Jersey recognizer jest gotowy do IA7 dopiero gdy:

- daje co najmniej jeden realny poprawny jersey episode/anchor;
- precision/specifity spełniają zdefiniowane safety gates;
- plain-shirt false confirmed reads pozostają na wymaganym poziomie;
- real fixture nie kończy wyłącznie safe abstention;
- wynik jest stabilny przynajmniej w jednym cross-half direction i jawnie raportowany w drugim;
- model pozostaje shadow evidence i nie mutuje production identity.

Jeżeli gate nie przechodzi:

```text
fix data/crops/calibration
or
pause jersey work
```

Nie tworzyć kolejnej architektury bez nowego dowodu diagnostycznego.

---

# 8. Faza E — Evidence Fusion, IA7

IA7 dzieli sie na dwa niezalezne zakresy:

```text
IA7a core evidence fusion
  operator seeds
  hard constraints
  safe lineage
  appearance/ReID advisory top-3
  team/capture context

IA7b optional jersey evidence
  niedostepne i FROZEN_UNTIL_NEW_INDEPENDENT_CAPTURE_DOMAIN
```

IA7a nie czeka na jersey recognizer. IA7b nie moze mutowac candidate ani
production identity i nie jest blokada produktu.

## Cel

Połączyć w jednym explainable resolverze:

```text
operator-confirmed observation
hard structural/temporal constraints
safe tracklet continuity
accepted subject lineage
trusted jersey episode
same-team unique roster lookup
roster-confirmed match-specific ReID
automatic team/role evidence
motion/spatial context
capture-domain context
```

## Priorytet

```text
1. operator-confirmed observation for exact observation
2. hard safety constraints
3. safe continuity/lineage
4. trusted jersey + roster uniqueness
5. roster-confirmed ReID
6. weaker automatic context
```

## Konflikty

Przykład:

```text
operator seed: Roman #6
jersey episode: #15
```

Wynik:

```text
needs_review
```

Nie:

```text
silent automatic choice
```

## Output

```text
identity_evidence_fusion_report.json
per assignment accepted evidence
rejected evidence
blockers
confidence/calibration source
operator-friendly explanation summary
full developer provenance
```

## Gate

- każdy candidate assignment jest explainable;
- hard conflicts blokują;
- evidence fusion poprawia coverage lub ranking;
- nie zwiększa known false assignments;
- review nie jest dzielony na osobne obowiązkowe jersey/ReID/operator audits.

---

# 9. Faza F — Exception-only Product, IA8–IA9

## IA8 — Exception-only review queue

Whole-subject review ma zmienić rolę z:

```text
review every card
```

na:

```text
review only high-value exceptions
```

Domyślna kolejka obejmuje:

```text
operator vs jersey conflict
cross-team link
parallel distant same-player candidate
structural conflict
possible ID switch boundary
long unresolved interval
possible substitution/new player
H1/H2 appearance conflict
low-confidence fragment with large stats impact
```

Poza domyślną kolejką lub na końcu:

```text
short noise fragments
low-impact unresolved detections
redundant crops from one episode
already safely resolved subjects
```

## IA9 — Adaptive audit and manual-work benchmark

System powinien dynamicznie wybierać kolejne klatki tylko wtedy, gdy oczekiwany information gain jest wysoki.

Audit może zakończyć się automatycznie, gdy:

```text
no new easy player is available
safe coverage gain becomes negligible
remaining cases belong to exception review
```

## Wymagany benchmark

Porównać stary i nowy flow:

```text
active operator time
number of actions
frames shown
players seeded
review cards before/after
manual assignments before/after
safe resolved coverage
unresolved time coverage
known false assignments
parallel/cross-team conflicts
H1↔H2 continuity
```

## Gate Fazy F

Nowy flow jest gotowy do finalnej rewalidacji, gdy wykazuje co najmniej jeden znaczący zysk:

```text
fewer review cards
fewer manual actions
lower active time
higher safe resolved coverage
better cross-half continuity
```

bez pogorszenia:

```text
known false assignments
cross-team conflicts
parallel-position conflicts
structural safety
```

---

# 10. Faza G — Stabilization Revalidation and Controlled Apply

Po IA8–IA9 wrócić do końcowych milestone'ów `PLAYER_IDENTITY_STABILIZATION_ROADMAP.md`.

## P1.23 — Candidate Stats Revalidation

Nie wystarczy stary raport sprzed Initial Audit/fusion.

Ponownie sprawdzić:

```text
player timeline
playing intervals
longest gaps
large spatial jumps
parallel observations
playing time
distance
heatmap
possession/events, jeśli dostępne
candidate vs production diff
feature readiness
```

Każda duża stat delta musi prowadzić do źródłowych seeds, subjects, fragments i evidence.

## P1.24 — Controlled Production Apply

Dopiero po pozytywnej rewalidacji:

```text
review completeness understood
hard conflicts = 0
known false assignments after final review = 0
candidate artifacts deterministic
production diff reviewed
backup/transaction/rollback ready
downstream rebuild tested
```

Production apply pozostaje:

```text
explicit
transactional
reversible
auditable
```

Nie wdrażać auto-apply.

## P1.25 — Advanced orphan/event review

P1.25 nie jest obowiązkowym kolejnym dużym projektem.

Implementować wyłącznie przypadki potwierdzone przez IA9/P1.23 benchmark jako istotne, np.:

```text
identity switch boundaries
long unresolved fragments
substitution boundaries
orphan fragments affecting stats/events
```

Nie budować rozbudowanego edytora dla edge cases, które nie wpływają na wynik.

---

# 11. Zadania dozwolone równolegle

Można równolegle wykonywać lekkie prace, które nie zmieniają kolejności gate'ów:

```text
testy kontraktów
CI dla frozen artifact evaluators
telemetry improvements
deterministic artifact generation
UI accessibility/keyboard shortcuts
documentation/status updates
small dataset inspection tools
```

Nie wolno równolegle odblokowywać fazy zależnej od niespełnionego gate'u.

Przykłady:

```text
IA1 UI może powstawać po IA0 contract
IA0–IA6 nie muszą czekać na J8.4
IA7a core evidence fusion nie czeka na jersey recognizer
IA7b optional jersey evidence pozostaje zamrozone do nowego capture domain
P1.24 musi czekać na IA9 + P1.23 revalidation
```

---

# 12. Zadania obecnie zabronione lub odłożone

Do czasu przejścia odpowiednich gate'ów nie implementować jako core requirement:

```text
production auto-apply
cross-match persistent player gallery
face recognition
full autonomous substitution assignment
full timeline editor
named MP4 generation
mandatory per-match jersey panel labeling
manual annotation of hundreds of appearance crops
new large jersey architecture without diagnostic evidence
full-match YOLO rerun after every operator correction
```

Named MP4 może wrócić później jako opcjonalny validation/export feature, ale nie jest częścią najbliższego development path.

---

# 13. Status board do utrzymywania przez agenta

Agent ma aktualizować tę tabelę po zamknięciu fazy lub zmianie kolejności.

```text
A  J8.3 panel closeout                         CLOSED — READY_FOR_J8_4_DIAGNOSTIC
B  IA0–IA4 Initial Audit core                 CLOSED
C  IA5–IA6 re-anchor + appearance gallery     IA5 CLOSED — IA6 VALIDATED, ADVISORY ONLY
D  J8.4 useful jersey recognizer              FROZEN_UNTIL_NEW_INDEPENDENT_CAPTURE_DOMAIN
E  Product-flow benchmark                     H1 OPERATOR COMPLETE + VERIFIED — H2_READY
F  IA7a core evidence fusion                  BLOCKED UNTIL OPERATOR BENCHMARK REACHES REPORT_READY AND PASSES GATES
G  IA7b optional jersey evidence              FROZEN UNTIL NEW INDEPENDENT CAPTURE DOMAIN
H  IA8–IA9 exception-only/adaptive review     BLOCKED BY IA7a
I  P1.23 revalidation + P1.24 apply            BLOCKED BY IA8–IA9
```

## Product-flow benchmark after J8.4

Implementation and automated validation were completed on 2026-07-30. The
operator preflight correctness cycle was completed on 2026-07-30. Required
final saves now gate H1/H2 transitions, H2 requires a fresh receipted reduction
report, and ReID telemetry counts only displayed cross-analysis advisory
suggestions with explicit provenance. The
milestone remains open until a real operator completes the new sequential
session and its `REPORT_READY` artifact passes the gates below. The flow is
measurement-only and must not rerun YOLO or apply candidate/production identity
mutations.

Canonical operator session:

```text
backend/storage/benchmarks/player_identity/product-flow-20260730-v4
implementation HEAD: 99f119c142cc36c4049adf8ad3422607162eaa9f
state: REPORT_READY
H1 frames: 8
H1 active decisions: 12
H1 unique players seeded / safely resolved: 7 / 7
H1 review cards before / after: 25 / 18
H1 conflicts / false assignments: 0 / 0
H2 frames / active decisions: 3 / 5
H2 visible observations / H1-lineage suggestions: 38 / 9
H2 H1-lineage suggestions reviewed / accepted / rejected: 3 / 0 / 3
H2 H1-lineage top-1 accuracy on named decisions: 0%
operator-confirmed H1-lineage errors: 7 / 9 displayed suggestions
operator-confirmed H1-lineage accuracy: 0 / 7 (2 suggestions unreviewed)
H2 safely resolved players: 1
H2 review cards before / after: 1346 / 1345
cross-analysis ReID suggestions displayed: 0
operator team corrections saved in audit: 0
operator-reported team errors in reviewed bboxes: 0
cross-team name-suggestion violations: 2 (H2 frame 3, bbox 8 and 13)
operator findings: missed player detections and one player-shadow bbox
source inventory mutations after final report: 0
```

The operator confirmed that frame 3 bbox 8 and bbox 13 were correctly labelled
Team B. The error was strictly in identity suggestions: the system attached
Krzysiek and Mati GK from the Team A/Corgi roster to Team B observations. This
is a hard cross-team invariant violation, not a team-classification error.
Named roster suggestions must be filtered by the observation team on both the
backend contract and the UI boundary.

Gate outcome: `REPORT_READY_NEGATIVE_IDENTITY_EVIDENCE`. The benchmark is
technically valid and preserved all safety invariants, but it does not pass the
safe-workload-reduction quality gate. H1 and H2 came from independent capture
domains and independent tracker runs. Their local `tracklet_id` values are not
cross-domain identity keys; matching them produced three wrong suggestions in
three reviewed cases. Cross-capture H1 safe-lineage suggestions are therefore
blocked. The team classifier remains promising, while missed detections and
shadow boxes require a separate detector-quality path. IA7a remains blocked
until real cross-domain evidence exists and a new bounded benchmark improves
safe review reduction.

### ReID diagnostic correctness closeout (2026-07-30)

The separate, read-only `cross_capture_reid_diagnostic/` follow-up for canonical
`product-flow-20260730-v4` repaired runtime selection, separated portable and
preferred-model evidence, and made the H1→H2 ground-truth mapping exact
observation based. It did not alter this historical benchmark outcome.

```text
diagnostic status: PREFERRED_REID_RUNTIME_BLOCKED_USER_APPROVAL_REQUIRED
operator names: OPERATOR_NAMES_REMAIN_HIDDEN
preferred model files: present
OpenCV DNN OpenVINO: load failed (backend plugin unavailable)
OpenVINO Runtime CPU: load failed (internal runtime error)
portable baseline: diagnostic only; never operator-visible
automatic assignments / source mutations: 0 / 0
IA7a: BLOCKED
```

### Isolated preferred-runtime repair gate (2026-07-30)

An explicitly approved, separate `backend/.venv-reid-probe` was created with
OpenVINO `2025.4.1`, NumPy and OpenCV only. It did not modify
`backend/.venv-mps`. The isolated runtime can create `ov.Core()`, see `CPU`,
and read the existing IR v10 XML/BIN, but `compile_model(..., "CPU")` still
returns OpenVINO's internal runtime error. The IR files are readable and have
recorded SHA-256 digests, so there is no evidence to request a new model.

```text
preferred runtime: PREFERRED_REID_RUNTIME_BLOCKED
preferred read-only replay: NOT_STARTED — blocked by runtime
evidence collection readiness: NOT_READY — preferred ranking unavailable
bounded H2 session: NOT_CREATED
cross-capture gate: NOT_EVALUATED
IA7a: BLOCKED
```

The cycle stops at this gate. No H2 operator decisions, new benchmark session,
identity mutation, YOLO or tracking rerun occurred. A separate approval is
needed for further local runtime investigation or an alternate compatible
OpenVINO package/version.

### Rosetta preferred-ReID productionization and bounded H2 gate (2026-07-30)

Native macOS ARM64 OpenVINO remains blocked in the CPU plugin during model
compilation. A dedicated Rosetta x86_64 subprocess runtime now has an explicit
candidate/probe/activation contract and a single-compile batch worker. Runtime
availability requires a real x86_64 handshake, model digest verification,
synthetic inference, two real-crop inferences, embedding validation and
repeatability.

```text
native arm64 OpenVINO: BLOCKED — CPU compile internal error
Rosetta x86 runtime: ROSETTA_REID_RUNTIME_AVAILABLE
Python: 3.9.6 x86_64
OpenVINO: 2025.3.0
NumPy: 2.0.2
real runtime integration test: PASSED
preferred v4 replay: PREFERRED_REID_REPLAY_COMPLETE
H1 internal queries / top-1 / top-3: 21 / 0.0476 / 0.1429
valid historical H2 queries: 1
bounded H2 session: product-flow-20260730-v5-reid-followup
bounded H2 cards: 5
bounded H2 decisions: 5 real named operator decisions, session finished
cross-capture queries / top-1 / top-3: 6 / 0.3333 / 0.6667
cross-capture mean / median truth rank: 1.5 / 2
cross-team violations / invalid ranked players: 0 / 0
bounded H2 status: CROSS_CAPTURE_REID_QUALITY_GATE_FAILED
operator names: OPERATOR_NAMES_REMAIN_HIDDEN
IA7a: NOT_STARTED — internal and cross-capture preferred quality gates failed
```

The v5 follow-up froze preferred rankings before the five operator decisions.
Selection was independent of ground-truth identity and excluded the one
previously valid H2 query. The final evaluation used those frozen rankings;
ground truth did not influence their generation. Portable rankings remain
absent from the operator surface. Historical v4 source artifacts, production
identity, candidate identity, YOLO and tracking remain unchanged.

Persistent state machine:

```text
CREATING
→ H1_READY
→ H1_FINISHED
→ H1_REBUILT
→ H2_READY
→ H2_FINISHED
→ REPORT_READY

any in-progress state → FAILED
```

The historical `product-flow-20260729-v1` session is not resumable evidence for
this gate. It prepared H1 and H2 concurrently, used the generic
`READY_FOR_OPERATOR` status and exceeded the intended operator budgets
(`112` H1 decisions and `20` H2 decisions). Treat it as historical diagnostic
input only, not as a passed benchmark.

The superseded `product-flow-20260730-v2` session is also marked `FAILED`.
It was used for a browser smoke test that exposed and verified a symlink
workspace-context bug; its non-operator telemetry is intentionally excluded
from benchmark evidence. The fix is covered by the product-flow regression
suite.

The former canonical `product-flow-20260730-v3` is marked `FAILED` and is now a
historical preflight session, not valid final operator evidence after the
preflight code change. The separate `product-flow-20260730-v4-smoke` session
was used only to read `H1_READY`, eight visible frames, the `0/12` limit and
the absence of H2. Opening its audit generated session telemetry, so it was
also marked `FAILED` and excluded from evidence. Canonical `v4` was created
after that smoke test and has never been opened by the agent.

```text
1. Show at most 8-12 Initial Identity Audit decisions.
2. Ask at most 3-5 H2 re-anchor confirmations.
3. Keep appearance/ReID as advisory top-3 recommendations only.
4. Run a seed-aware downstream rebuild from frozen artifacts.
5. Report review cards before/after, active operator time, players/subjects/
   tracklets/frames safely resolved, unresolved coverage, false assignments,
   and parallel/cross-team/structural conflicts.
6. Confirm automatic assignments = 0 and production apply = 0.
```

The benchmark succeeds by proving lower safe manual workload, not by forcing
coverage or substituting jersey evidence for operator knowledge.

Player-observation QA correctness was also closed on 2026-07-30 at the
implementation and automated-validation level:

```text
shared renderer/QA visible-observation projection
freshness-checked source lineage
visible → clean → rejected → raw → no-match waterfall
team-safe deterministic one-to-one matching
visual-only unresolved rows excluded from identity and trusted stats
offline editor with delete/undo/team-toggle/local restore/reset
```

This does not claim full raw YOLO recall. Its conclusion is limited to
freshness-verified downstream observation coverage.

`BLOCKED BY A CLOSEOUT` oznacza zakończenie J8.3 decyzją, niekoniecznie pozytywny wynik modelowy.

Jeżeli J8.3 kończy się `AVAILABLE_DATA_NOT_SUFFICIENT`, Fazy B i C nadal mogą być wykonywane. Faza D zostaje wstrzymana do poprawy danych, a IA7 może zostać częściowo przygotowane kontraktowo, lecz nie zamknięte bez realnego jersey evidence.

---

# 14. Następne zadanie dla agenta

Przy aktualnym planie następne zadanie to:

```text
H1 approved appearance gallery jest naprawiona. Portable i preferred ReID mają
osobne diagnostyczne artifacty, a wyłącznie preferred model może kiedyś stać
się active operator source po przejściu własnego gate'u. Lokalny preferred
runtime jest obecnie zablokowany; nazwy pozostają ukryte. Naprawa runtime'u
wymaga jawnej zgody użytkownika, po której należy uruchomić wyłącznie
read-only probe i ponownie ocenić gate. IA7a pozostaje zablokowane.
```

J8.3 zostało zamknięte decyzją:

```text
READY_FOR_J8_4_DIAGNOSTIC
```

Nie promować PanelDigitNetV1 ani nie rozpoczynać kolejnej architektury bez
nowego materiału walidacyjnego. IA7b pozostaje zamrozone; IA7a moze korzystac
wyłącznie z operator seeds, hard constraints, safe lineage, advisory ReID oraz
team/capture context.

Po zamkniętym IA0 następne zadania są wykonywane dokładnie w kolejności:

```text
IA1
→ IA2
→ IA3
→ IA4
→ IA5
→ IA6
→ J8.4
→ product-flow benchmark
→ IA7a
→ IA8
→ IA9
→ P1.23 revalidation
→ P1.24
```

Każdy krok może być podzielony na mniejsze commity, ale nie wolno ominąć gate'u poprzedniej fazy.

---

# 15. Definition of Done całego planu

Plan jest zakończony dopiero, gdy normalny flow użytkownika wygląda w przybliżeniu tak:

```text
upload + roster
→ automatic analysis
→ a few easy identity confirmations
→ automatic seed propagation
→ automatic appearance/jersey/ReID evidence fusion
→ a small exception queue
→ candidate stats validation
→ explicit final approval
```

Docelowy użytkownik nie:

```text
labels hundreds of crops
reviews every stable subject
repeats the same player assignment
calculates coordinates
enters confidence values
runs developer scripts manually
```

Końcowy sukces jest mierzony przez:

```text
minimal active operator time
minimal manual decisions
high safe resolved coverage
0 known false assignments after review
0 hidden structural/cross-team/parallel conflicts
explainable provenance for every promoted assignment
```

Najważniejsza zasada wykonawcza:

> Najpierw budujemy niewielką liczbę bardzo mocnych human anchors i system, który potrafi je bezpiecznie skalować. Dopiero potem dokładamy jersey recognition jako dodatkowy dowód, łączymy źródła i redukujemy review do wyjątków. Production apply jest ostatnim krokiem, nie skrótem.
