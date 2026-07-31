# Player Identity Development Plan

## 0. Rola dokumentu

Ten plik jest nadrzędnym planem **kolejności developmentu** dla obszaru player identity.

Obowiązuje razem z:

```text
task-requests/PLAYER_IDENTITY_AUTOMATION_FLOW.md
task-requests/PLAYER_IDENTITY_STABILIZATION_ROADMAP.md
task-requests/JERSEY_NUMBER_IDENTITY_ANCHORS.md
AGENTS.md
```

Role dokumentów:

```text
PLAYER_IDENTITY_DEVELOPMENT_PLAN
→ aktualna kolejność prac i decyzje stop/go

PLAYER_IDENTITY_AUTOMATION_FLOW
→ docelowy product flow oraz operator UX

PLAYER_IDENTITY_STABILIZATION_ROADMAP
→ safety, candidate artifacts, revalidation i controlled apply

JERSEY_NUMBER_IDENTITY_ANCHORS
→ opcjonalny jersey-number evidence research
```

Jeżeli dokumenty są sprzeczne, obowiązuje kolejność:

1. `AGENTS.md` i hard safety invariants;
2. ten development plan;
3. aktualny kod i rzeczywiste artifacts;
4. szczegółowe roadmapy.

Historyczne milestone logs pozostają dostępne w Git history oraz w katalogach:

```text
backend/storage/benchmarks/player_identity/
```

Ten dokument nie ma być kolejnym długim dziennikiem eksperymentów. Ma mówić agentowi **co robić teraz, czego nie robić i jaki realny rezultat ma zobaczyć użytkownik**.

---

# 1. Decyzja produktowa z 2026-07-31

Aplikacja jest początkowo narzędziem do analizy meczów drużyny Corgi.

Typowy materiał:

```text
jedna połowa około 20 minut
lub
maksymalnie dwa osobne uploady po około 20 minut
```

Manualna praca operatora jest akceptowalna, jeżeli:

- dotyczy kilku lub kilkudziesięciu high-value decyzji;
- nie wymaga oznaczania setek cropów;
- jedna decyzja może rozwiązać wiele klatek/trackletów;
- wynik można szybko zweryfikować na wygenerowanym wideo.

Najbliższym celem nie jest perfekcyjne automatyczne ReID.

Najbliższym celem jest działający przepływ:

```text
upload + analiza
→ krótki identity audit
→ review trackletów/subjectów
→ finalizacja reviewed identity
→ Generate reviewed video
→ imiona tylko dla potwierdzonych przypisań
→ A01/B01 dla niepewnych przypisań
→ minimapa/radar
→ podstawowe statystyki z jawnym coverage
```

ReID, jersey recognition i automatyczny resolver są źródłami sugestii. Nie mogą blokować dostarczenia tego przepływu.

---

# 2. Nienaruszalne zasady

## 2.1. Human-in-the-loop

Użytkownik dostarcza wyłącznie wiedzę, którą zna jako człowiek:

```text
to jest Paweł
to jest inny zawodnik Team A
to jest Team B
to jest sędzia
to jest false detection
nie wiem / pomiń
```

Aplikacja samodzielnie zapisuje:

```text
frame/timestamp
bbox
track_id
tracklet_id
candidate_subject_id
team/player IDs
source digests
provenance
quality metadata
```

## 2.2. Brak wymuszonego assignmentu

```text
unresolved
```

jest prawidłowym wynikiem.

Brak przypisania jest bezpieczniejszy niż błędne imię.

## 2.3. Display policy

W reviewed video i zwykłym product UI:

```text
confirmed
→ imię/nazwa rosterowa

probable
unresolved
conflicted
→ stabilny anonimowy label A01/A02/B01/B02

blocked / invalid detection
→ brak imienia; Unknown albo ukrycie zależnie od kontekstu
```

Nie używać `Paweł?` jako domyślnej etykiety. Imię oznacza potwierdzoną tożsamość.

## 2.4. Candidate/shadow before production

Do jawnego controlled apply:

```text
production identity pozostaje niezmienione
public package nie używa candidate identity
production stats nie są automatycznie zastępowane
```

Lokalne reviewed video i reviewed candidate stats **nie są production apply** i mogą powstać wcześniej.

## 2.5. Brak ciężkich rerunów po review

Zmiana operator decision ma powodować wyłącznie tani downstream rebuild:

```text
no full-match YOLO rerun
no tracking rerun
```

chyba że osobny detector-quality milestone jawnie wymaga rerunu.

## 2.6. Jedno źródło prawdy po review

Nie rozwijać dalej kilku równoległych końcowych resolverów.

Wprowadzić jeden kanoniczny artifact:

```text
reviewed_identity_snapshot.json
```

który łączy:

```text
operator seeds
whole-subject decisions
manual remediation
safe confirmed resolver outcomes
explicit unresolved/conflicted statuses
```

Ten snapshot jest wejściem do:

```text
reviewed video
reviewed timeline
reviewed stats
reviewed heatmaps
minimapy
```

---

# 3. Stan repo na HEAD c4559ff4

## 3.1. Fundament wykonany

Repo posiada już:

```text
player detection i tracking
tracklet splitting
stable/candidate subjects
team candidates
Initial Identity Audit
operator seed store i telemetry
seed-aware candidate resolve
whole-subject review
seed-aware review reduction
promotion safety i remediation
partial candidate assignments
a candidate timeline/stats/heatmaps
candidate-vs-production diff
appearance galleries
preferred ReID runtime i historical H1/H2 gate
OSNet fine-tuning infrastructure
tracklet-level ReID evidence contract
Match Identity Resolver shadow
identity candidate overlay renderer
pitch mapping do współrzędnych boiska
```

## 3.2. Potwierdzone ograniczenia

Historyczny preferred ReID gate:

```text
H1 queries/top-1/top-3: 21 / 0.0476 / 0.1429
H2 queries/top-1/top-3: 6 / 0.3333 / 0.6667
status: CROSS_CAPTURE_REID_QUALITY_GATE_FAILED
operator names: hidden
```

Aktualny kod zawiera poprawiony training/evaluation pipeline, ale repo nie zawiera jeszcze wiarygodnego końcowego raportu z rzeczywiście wykonanych runów:

```text
Run A pretrained full-body
Run B fine-tuned full-body
Run C fine-tuned torso
H1 winner
frozen H2 replay
Resolver A/B/C comparison
```

Nie traktować samego istnienia skryptów jako dowodu poprawy jakości.

## 3.3. Największa luka produktowa

Brakuje zamkniętego flow:

```text
review complete
→ finalize reviewed identity snapshot
→ generate reviewed video
→ generate reviewed stats with coverage
```

Istniejące elementy są nadal rozproszone pomiędzy:

```text
operator seed pipeline
whole-subject review
promotion/remediation pipeline
new match identity resolver
candidate overlay renderer
candidate stats generator
```

Najbliższe prace mają je połączyć, a nie tworzyć piąty równoległy resolver.

---

# 4. Dwa tory dalszego developmentu

Od tego miejsca development dzieli się na:

```text
TOR P — Product delivery
TOR A — Automation/research
```

Tor P ma pierwszeństwo.

Tor A nie może blokować Toru P.

---

# 5. TOR P — Product delivery

## P-MVP1 — Finalize reviewed identity snapshot

### Cel

Po zakończeniu review utworzyć jeden deterministyczny snapshot zawierający finalny stan każdego trackletu/subjectu.

### Wejścia

```text
match/roster
tracklets i candidate subjects
Initial Identity Audit decisions
whole-subject review decisions
manual remediation decisions
safe resolver proposals
team constraints
```

### Minimalny rekord

```json
{
  "tracklet_id": "tracklet-123",
  "candidate_subject_id": "subject-17",
  "team_label": "A",
  "canonical_player_id": "player-7",
  "display_label": "Paweł",
  "identity_status": "confirmed",
  "identity_source": "operator_review",
  "eligible_for_player_stats": true,
  "source_digests": {}
}
```

Niepewny rekord:

```json
{
  "tracklet_id": "tracklet-456",
  "candidate_subject_id": "subject-31",
  "team_label": "A",
  "canonical_player_id": null,
  "display_label": "A04",
  "identity_status": "unresolved",
  "identity_source": null,
  "eligible_for_player_stats": false,
  "source_digests": {}
}
```

### Wymagania

- operator-confirmed decisions mają najwyższy priorytet;
- `probable` nie jest automatycznie `confirmed`;
- cross-team assignment jest hard-blocked;
- parallel same-player conflict pozostaje jawny;
- unresolved otrzymuje stabilny fallback label;
- ten sam input daje identyczny snapshot i digest;
- snapshot jest stale po zmianie którejkolwiek decyzji lub źródłowego artifactu;
- brak production mutation.

### Output

```text
reviewed_identity_snapshot.json
reviewed_identity_report.json
```

### Definition of Done

```text
jeden kanoniczny reviewed snapshot istnieje
wszystkie końcowe renderery/statystyki czytają ten sam kontrakt
nie ma dwóch konkurencyjnych źródeł finalnego player_id
```

---

## P-MVP2 — Generate reviewed video

### Cel

Po review użytkownik może kliknąć:

```text
Generate reviewed video
```

### Wideo pokazuje

```text
bbox
kolor drużyny
confirmed name
Axx/Bxx fallback dla reszty
opcjonalny conflict/review marker
czas meczu
opcjonalną piłkę
```

### Zasady

- imię tylko dla `confirmed`;
- label `Axx/Bxx` jest stabilny w obrębie eksportu;
- niepewna sugestia ReID nie jest pokazywana jako imię;
- invalid/false detection nie dostaje player name;
- renderer korzysta z istniejącego H.264/FFmpeg flow;
- rerender po korekcie nie uruchamia YOLO ani trackingu;
- output zawiera snapshot digest i source provenance.

### Output

```text
reviewed_identity_video.mp4
reviewed_identity_video_manifest.json
```

### Rola produktu

Reviewed video jest jednocześnie:

```text
wartością dla użytkownika
QA player identity
narzędziem do wykrycia ID switches
narzędziem do wykrycia false merge/split
podstawą zaufania do statystyk
```

Nie jest już elementem `poza MVP`.

---

## P-MVP3 — Minimap/radar

### Cel

Dodać do reviewed video małe boisko 2D pokazujące aktualne ustawienie zawodników.

### Dane

Korzystać z istniejących:

```text
pitch calibration/homography
pitch_m positions
team labels
ball position, jeżeli dostępna
reviewed identity snapshot
```

### Pierwsza wersja

```text
Team A marker
Team B marker
ball marker
confirmed player initials/number opcjonalnie
unresolved jako anonimowy marker teamu
krótkie wygładzanie pozycji
```

Minimapa nie może wymagać działającego ReID.

### Definition of Done

- orientacja minimapy jest zgodna z pitch config;
- markery nie wychodzą poza boisko bez jawnego clamp/statusu;
- brak pozycji oznacza brak markera, nie wymyśloną interpolację;
- minimapa działa jako opcja renderera.

---

## P-MVP4 — Reviewed stats with coverage

### Cel

Generować podstawowe statystyki z tego samego reviewed snapshotu.

### Pierwszy zakres

```text
playing/detected time
heatmap
average position
observed distance — experimental/readiness-gated
team shape/minimap-derived diagnostics
team possession, jeżeli ball pipeline jest gotowy
individual possession/contact/pass tylko dla potwierdzonych identity windows
```

### Główna zasada

Statystyka bez pełnego identity coverage musi jawnie raportować coverage.

Przykład:

```json
{
  "player_id": "player-7",
  "player_name": "Paweł",
  "identity_coverage": 0.87,
  "heatmap_coverage": 0.84,
  "possession_coverage": 0.71,
  "passes_coverage": 0.63,
  "readiness": {
    "heatmap": "ready_with_review",
    "distance": "experimental",
    "possession": "ready_with_review",
    "passes": "experimental"
  }
}
```

Nie przypisywać unresolved fragmentów do konkretnego zawodnika tylko po to, żeby podnieść coverage.

### Output

```text
reviewed_player_timeline.json
reviewed_player_stats.json
reviewed_player_heatmaps.json
reviewed_stats_readiness.json
```

---

## P-MVP5 — Video-driven correction and cheap rerender

### Cel

Po obejrzeniu reviewed video operator może poprawić konkretny błąd bez powrotu do research tooling.

### Minimalny flow

```text
open reviewed video
→ jump to timestamp/frame
→ open corresponding subject/tracklet review
→ correct assignment / mark unresolved
→ rebuild reviewed snapshot
→ rerender only downstream outputs
```

Nie budować od razu pełnego timeline editora.

Pierwsza wersja może używać timestamp linków i istniejących ekranów review.

---

# 6. TOR A — Automation/research

## A1 — Final bounded ReID decision

Aktualna infrastruktura ReID jest wystarczająca do jednego końcowego, ograniczonego eksperymentu.

Wykonać wyłącznie:

```text
Run A — pretrained OSNet full-body
Run B — fine-tuned OSNet full-body
Run C — fine-tuned OSNet torso
```

Wszystkie na tym samym H1 split, same-team ranking i tracklet-level evaluation.

Przed finalnym runem dopilnować:

- Stage 2 startuje z najlepszego checkpointu Stage 1;
- validation query nie używa własnych cropów w galerii;
- nowe tracklety mogą dostać automatycznie wybrane 2–4 representative crops;
- H2 pozostaje finalnym holdoutem;
- winner jest zamrożony na H1.

### Decyzja po eksperymencie

ReID pozostaje aktywnie rozwijane tylko wtedy, gdy wykazuje praktyczny zysk:

```text
mniej manualnych decyzji
mniej unresolved trackletów
mniej ID switches
bez wzrostu false merges/splits
```

Możliwy wynik:

```text
REID_ELIGIBLE_FOR_SHADOW_SUGGESTIONS
REID_ADVISORY_ONLY
REID_FROZEN_NO_PRODUCT_GAIN
```

Nie uruchamiać kolejnego model bakeoffu bez nowego materiału lub nowego, konkretnego błędu diagnostycznego.

## A2 — Resolver integration, nie drugi finalny pipeline

Match Identity Resolver pozostaje źródłem:

```text
rankingu
explainable evidence
conflict detection
safe continuity proposals
```

Nie jest osobnym finalnym źródłem prawdy obok reviewed snapshotu.

Resolver proposal może zostać:

```text
confirmed przez operatora
odrzucony
pozostawiony unresolved
```

Tylko bezpiecznie potwierdzone wyniki trafiają do `reviewed_identity_snapshot.json` jako `confirmed`.

## A3 — Exception-only review

Po działającym P-MVP1–P-MVP4 kolejkę review redukować do przypadków o największym wpływie:

```text
operator/evidence conflict
cross-team candidate
parallel same-player conflict
possible ID switch boundary
long unresolved interval
possible substitution/new player
low-margin high-duration candidate
fragment z dużym wpływem na statystyki
```

## A4 — Adaptive audit

Dopiero po zebraniu telemetry z prawdziwych reviewed meczów system może dynamicznie wybierać następne klatki na podstawie expected information gain.

Nie optymalizować adaptacyjnego audytu bez pomiaru:

```text
które decyzje faktycznie redukują późniejsze review
```

## A5 — Full-match benchmark

Pełny benchmark służy do decyzji o automatyzacji i produkcyjnym apply.

Nie blokuje lokalnego reviewed MVP.

Mierzyć:

```text
active operator time
manual decisions
confirmed coverage
unresolved coverage
known false assignments
ID switches
false merges
false splits
reviewed video corrections
stats coverage
```

## A6 — Controlled production apply

Pozostaje ostatnim etapem.

Wymaga:

```text
explicit approval
candidate-vs-production diff
backup
transaction manifest
atomic writes
full downstream rebuild
post-apply validation
rollback
```

Nie implementować auto-apply.

---

# 7. Jersey recognition

Jersey recognition pozostaje opcjonalnym evidence source.

Stan:

```text
nie blokuje P-MVP1–P-MVP5
nie blokuje A1–A3
nie wymaga per-match ręcznego panel labeling
```

Wrócić do niego tylko gdy:

- istnieje nowy niezależny capture domain lub nowy materiał;
- readable panel dataset spełnia readiness;
- oczekiwany zysk dotyczy realnego problemu z review;
- model może działać high-precision z bezpiecznym abstention.

Nie rozpoczynać kolejnej architektury jersey bez nowego dowodu diagnostycznego.

---

# 8. Status board

Stan na `c4559ff4`:

```text
FOUNDATION
IA0 frame selection                         CLOSED
IA1 Initial Identity Audit UI               CLOSED
IA2 operator seed store                     CLOSED
IA3 seed-aware candidate resolve            CLOSED
IA4 seeded review reduction                 CLOSED
IA5 optional H2 re-anchor                    IMPLEMENTED
IA6 appearance gallery/advisory ranking      IMPLEMENTED — ADVISORY ONLY
promotion safety/remediation                IMPLEMENTED
partial candidate stats/heatmaps            IMPLEMENTED

AUTOMATION
preferred historical ReID gate              FAILED
OSNet fine-tuning protocol code              IMPLEMENTED
real bounded fine-tuning result              NOT PROVEN IN REPO
Match Identity Resolver shadow contract      IMPLEMENTED / PARTIAL
IA7a evidence-fusion contract                SHADOW IMPLEMENTED / PRODUCT NOT INTEGRATED
IA8 exception-only queue                     PARTIAL
IA9 adaptive audit                           NOT STARTED

PRODUCT DELIVERY
P-MVP1 reviewed identity snapshot            NOT STARTED
P-MVP2 reviewed video                        NOT STARTED
P-MVP3 minimap/radar                         NOT STARTED
P-MVP4 reviewed stats + coverage              PARTIAL FOUNDATIONS ONLY
P-MVP5 correction + cheap rerender            NOT STARTED

PRODUCTION
full-match quality gate                      NOT PASSED
controlled production apply                  NOT STARTED
```

---

# 9. Aktualna kolejność prac

Obowiązująca kolejność:

```text
1. P-MVP1 Finalize reviewed identity snapshot
2. P-MVP2 Generate reviewed video
3. P-MVP3 Add minimap/radar
4. P-MVP4 Reviewed stats with coverage
5. P-MVP5 Video-driven correction and rerender
6. A1 Final bounded ReID decision
7. A2 Integrate useful resolver suggestions into review
8. A3 Exception-only queue
9. A4 Adaptive audit
10. A5 Full-match benchmark
11. A6 Controlled production apply
```

A1 może być wykonywane równolegle, jeżeli nie opóźnia P-MVP1–P-MVP4.

Jersey work pozostaje zamrożone i nie jest elementem critical path.

---

# 10. Jeden główny milestone na cykl

Agent nie powinien w jednym cyklu jednocześnie:

```text
przepisywać review UI
+ trenować nowy model
+ budować minimapę
+ implementować production apply
```

Każdy cykl ma zakończyć się:

```text
what changed
real artifact/demo result
what was tested
operator impact
known limitations
explicit stop/go
next allowed milestone
```

Dla product milestone wymagany jest widoczny output, nie tylko schema i unit tests.

---

# 11. KPI najbliższego MVP

MVP nie wymaga 95% automatycznej identyfikacji.

Wymaga:

```text
reviewed video powstaje po review
confirmed names są poprawne w audytowanym materiale
niepewne tracklety pozostają Axx/Bxx
coverage jest jawne
statystyki nie wykorzystują ukrytych false assignments
rerender nie uruchamia YOLO/tracking
manualna praca nie polega na setkach cropów
```

Mierzyć:

```text
time from upload to reviewed output
active operator seconds
audit decisions
whole-subject decisions
video-driven corrections
confirmed identity coverage
unresolved coverage
known false names in final video
ID switches visible in final video
per-feature stats coverage
```

Pierwszy użyteczny wynik może być:

```text
70–90% bezpiecznie confirmed
10–30% jawnie Axx/Bxx
0 znanych błędnych imion po review
```

To jest lepsze niż agresywne 100% z false assignments.

---

# 12. Anti-goals

Do czasu zebrania realnych wyników nie wykonywać jako critical path:

```text
kolejnych dużych ReID model bakeoffów
persistent gallery między meczami
face recognition
pełnego globalnego timeline editora
pełnej automatycznej obsługi zmian
auto-apply do produkcji
manualnego oznaczania setek appearance crops
mandatory jersey panel audit per match
ukrywania unresolved przez podobne appearance
```

Nie liczyć za sukces:

```text
samej liczby nowych plików
zielonych unit tests bez realnego artifactu
wysokiego train accuracy
spadku liczby subjectów wynikającego z false merge
```

---

# 13. Definition of Done planu

Plan jest zakończony, gdy normalny lokalny flow dla meczu Corgi wygląda tak:

```text
upload + roster + pitch calibration
→ automatic analysis
→ kilka easy identity confirmations
→ review istotnych subjectów/wyjątków
→ finalize reviewed identity
→ reviewed MP4 z imionami i Axx/Bxx fallback
→ minimapa
→ podstawowe statystyki z coverage
→ opcjonalna korekta i tani rerender
```

Użytkownik nie:

```text
oznacza setek cropów
wpisuje coordinates/confidence/internal IDs
powtarza tego samego assignmentu w wielu ekranach
uruchamia developer scripts ręcznie
czeka na perfekcyjne ReID, aby zobaczyć wynik
```

Najważniejsza zasada wykonawcza:

> Najpierw dostarczamy wiarygodny reviewed output i realną wartość dla Corgi. Automatyzację rozwijamy tylko wtedy, gdy mierzalnie zmniejsza liczbę decyzji operatora bez zwiększania błędnych przypisań.
