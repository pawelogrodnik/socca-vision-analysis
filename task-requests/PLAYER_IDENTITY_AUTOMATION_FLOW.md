# Player Identity Automation and Minimal-Review Flow

## Status i relacja do istniejących dokumentów

```text
UZUPEŁNIENIE task-requests/PLAYER_IDENTITY_STABILIZATION_ROADMAP.md
UZUPEŁNIENIE task-requests/JERSEY_NUMBER_IDENTITY_ANCHORS.md
OBOWIĄZUJE RAZEM Z AGENTS.md — Mandatory human-audit and operator UX contract
SHADOW/CANDIDATE FIRST
PRODUCTION APPLY POZOSTAJE ZABLOKOWANY DO ODPOWIEDNIEGO MILESTONE'U
```

Istniejące roadmapy opisują osobno:

- stabilizację trackletów i stable subjects;
- whole-subject review;
- candidate identity i bezpieczną promocję;
- jersey-number evidence;
- future roster-confirmed ReID.

Brakowało jednak jednego jawnego dokumentu opisującego, jak te elementy mają docelowo działać jako jeden produktowy flow i jak wraz z rozwojem automatyzacji ma maleć liczba ręcznych audytów.

Ten dokument definiuje ten brakujący przepływ.

---

# 1. Cel produktowy

Docelowy produkt nie może wymagać od użytkownika:

```text
setek ręcznie oznaczanych cropów
przeglądania wszystkich stable subjects
powtarzania tego samego assignmentu w kilku ekranach
ręcznego wpisywania bbox coordinates
ręcznego podawania confidence lub technicznych ID
ręcznej anotacji numerów po każdym meczu
```

Docelowy model pracy:

```text
system wykonuje maksymalnie dużo automatycznie
→ użytkownik dostarcza kilka pewnych informacji, których model nie może znać
→ resolver skaluje te informacje na cały mecz
→ użytkownik przegląda wyłącznie konflikty i przypadki istotne dla statystyk
```

Najważniejsze KPI produktu:

```text
minimalny active operator time
minimalna liczba decyzji użytkownika
maksymalna bezpieczna resolved coverage
0 znanych false assignments po finalnym review
0 ukrytych cross-team lub parallel-position conflicts
```

Manualna praca jest dopuszczalna tylko wtedy, gdy:

- dostarcza wysokowartościowy gold anchor;
- rozwiązuje konflikt, którego automat nie może bezpiecznie rozstrzygnąć;
- waliduje wynik mający istotny wpływ na statystyki;
- rozwija model w osobnym, ograniczonym workflow badawczym.

---

# 2. Docelowy end-to-end flow

```text
1. Upload i konfiguracja rosteru
2. Kalibracja boiska
3. Player/ball detection i tracking
4. Tracklet splitting, team candidates i stable subjects
5. Automatyczny wybór kilku najlepszych klatek do Initial Identity Audit
6. Szybki audit: kilka pewnych player/team observation seeds
7. Seed-aware identity resolve bez ponownego YOLO
8. Automatyczne zbudowanie match-specific appearance galleries
9. Automatyczne jersey-number episodes i roster lookup
10. Fusion wszystkich dowodów w candidate identity resolverze
11. Opcjonalny krótki second-half re-anchor dla nowego capture domain
12. Exception-only review
13. Candidate player timeline i stats validation
14. Jawne finalne zatwierdzenie/promotion w dozwolonym milestone
```

Ciężki etap detekcji ma zostać wykonany raz. Zmiany decyzji operatora mają przebudowywać wyłącznie downstream identity artifacts.

```text
operator decision changed
→ no full-match YOLO rerun
→ rebuild candidate identity / recommendations / timeline / stats diff
```

---

# 3. Dwa capture domains w obecnym materiale

Aktualnie dostępny jest jeden fizyczny mecz, ale dwa istotnie różne capture domains:

```text
H1:
- słońce
- pierwszy kąt i nachylenie kamery
- kamera po jednej stronie boiska
- pierwszy background

H2:
- brak bezpośredniego słońca
- inne nachylenie kamery
- kamera po przeciwnej stronie boiska
- inny background i appearance distribution
```

System i raporty mają jawnie rozróżniać:

```text
distinct physical matches = 1
distinct capture domains = 2
```

Nie wolno przedstawiać tego jako cross-match generalization. Można natomiast walidować:

```text
within-match cross-capture-domain robustness
H1 → H2
H2 → H1
```

Initial Identity Audit powinien głównie pokryć H1, a krótki re-anchor powinien dostarczyć kilka potwierdzonych tożsamości w H2.

---

# 4. Initial Identity Audit — szybki gold-anchor step

## 4.1. Cel

Initial Identity Audit nie służy do ręcznego opisania całego meczu. Służy do dostarczenia niewielkiej liczby bardzo pewnych observation-level identity seeds.

```text
kilka pewnych observation seeds
→ wiele automatycznie rozwiązanych trackletów i subjects
```

## 4.2. Budżet interakcji

Domyślnie:

```text
5–8 automatycznie wybranych klatek
maksymalnie 10 klatek bez jawnego wyboru użytkownika
około 8–12 pewnych assignmentów jako target, nie obowiązek
wcześniejszy stop, gdy brak kolejnego łatwego/high-value przypadku
```

Nie wolno przekształcić tego etapu w obowiązkowe oznaczanie dziesiątek lub setek cropów.

## 4.3. UI

Każda klatka pokazuje pełny kontekst meczu z klikalnymi wykrytymi bboxami.

Po kliknięciu bboxa dostępne są proste akcje:

```text
konkretny zawodnik z rosteru Team A
Team A — zawodnik nieznany
Team B — zawodnik nieznany
sędzia
fałszywa detekcja
pomiń / nie wiem
```

Wybranie zawodnika z rosteru automatycznie ustawia:

```text
player_id
team_id
roster jersey number, jeżeli istnieje
source = operator_initial_identity_audit
certainty = certain
frame / timestamp / bbox / tracklet / subject provenance
```

Użytkownik nie podaje confidence i nie wpisuje współrzędnych.

## 4.4. Team correction

Przypisanie:

```text
Roman #6 · Team A
```

na bboxie automatycznie sklasyfikowanym jako Team B oznacza jednocześnie:

```text
operator-confirmed player = Roman
operator-confirmed team = A
automatic team B rejected for this observation
team contradiction recorded
```

Nie wolno wymagać osobnego formularza do zmiany teamu.

## 4.5. Jednostka seeda

Seed początkowo oznacza:

```text
na tej konkretnej obserwacji to na pewno Roman
```

Nie oznacza automatycznie:

```text
cały raw tracker_id przez cały mecz to Roman
```

Rozszerzenie seeda musi przejść przez:

```text
local tracklet continuity
lineage freshness
team consistency
temporal overlap constraints
parallel-position constraints
structural blockers
```

---

# 5. Automatyczny wybór klatek

Klatki mają być dobierane pod maksymalny expected information gain przy minimalnym koszcie użytkownika.

Pozytywne sygnały:

```text
dużo widocznych zawodników
nowi, jeszcze niezakotwiczeni zawodnicy
wysokiej jakości detekcje
duże bboxy
niski overlap
ciągłość trackletu przed i po klatce
mało edge-cut detections
niski motion blur
różnorodność czasowa i capture-domain diversity
```

Negatywne sygnały:

```text
duży overlap zawodników
podejrzany ID switch
wielu graczy w jednym bboxie
bardzo krótki tracklet
niemal identyczna klatka już pokazana
mała szansa na nową identity informację
```

Każda kolejna klatka powinna idealnie:

- pokazać nowego startera;
- dostarczyć lepszy widok wcześniej niepewnego zawodnika;
- potwierdzić H2 appearance dla zawodnika zakotwiczonego w H1.

---

# 6. Wspólny identity evidence graph

Wszystkie źródła informacji mają trafiać do jednego explainable resolvera.

## 6.1. Źródła dowodów

```text
operator-confirmed observation seed
safe local tracklet continuity
accepted stable-subject lineage
jersey-number visibility episode
same-team unique roster lookup
match-specific appearance/ReID similarity
automatic team classification
role evidence
motion / time-gap / spatial continuity
capture-domain information
```

## 6.2. Priorytet dowodów

Rekomendowana hierarchia:

```text
1. operator-confirmed observation
2. hard structural and temporal safety constraints
3. safe tracklet continuity
4. accepted subject lineage
5. trusted jersey-number episode + same-team unique roster lookup
6. roster-confirmed match-specific ReID
7. automatic team/role evidence
8. motion and positional context
```

Operator seed jest najsilniejszym pozytywnym dowodem dla wskazanej obserwacji, ale nie może omijać twardych konfliktów dla propagacji na inne fragmenty.

## 6.3. Fusion example

```text
operator seed: Roman #6 Team A on frame 1200
+ safe tracklet continuity
+ jersey episode reads #6
+ roster says Team A #6 = Roman
+ appearance matches approved Roman gallery
+ no temporal/parallel conflict
→ high-confidence Roman candidate
```

Konflikt example:

```text
operator seed: Roman #6
jersey episode: #15
→ do not silently choose one
→ create explainable conflict review item
```

---

# 7. Match-specific appearance/ReID gallery

Po operator-confirmed seed system ma automatycznie wybierać reliable crops z bezpiecznych fragmentów.

```text
operator-confirmed Roman observation
→ safe local tracklet segment
→ automatic reliable-crop selection
→ Roman H1 appearance gallery
```

Po H2 re-anchor:

```text
Roman confirmed in H2
→ Roman H2 appearance gallery
→ cross-domain match-specific prototype
```

Użytkownik nie oznacza ręcznie każdego appearance cropa.

System sam wybiera próbki reprezentujące, o ile są dostępne:

```text
front / back / side
near / far
sun / shade
H1 / H2
low occlusion
valid visual content
```

Tylko automatycznie wybrane reliable crops mogą zasilać roster-confirmed ReID prototype.

ReID na tym etapie służy do:

```text
ranking unresolved fragments
candidate suggestions
cross-half matching
```

Nie może samodzielnie wykonywać nieodwracalnego cross-subject merge.

---

# 8. Jersey recognition w docelowym flow

## 8.1. Rola jersey recognition

Jersey recognition nie jest osobnym końcowym produktem ani jedyną drogą do identity.

Ma działać jako dodatkowy high-value evidence source:

```text
team + jersey number
→ same-team roster lookup
→ identity candidate
→ confirmation or conflict against operator seed / lineage / ReID
```

Nawet umiarkowany recall może być użyteczny, jeżeli precision pozostaje bardzo wysoka.

## 8.2. Po Initial Identity Audit

Przypisanie:

```text
Roman #6
```

zapewnia automatycznie label rosterowy numeru `6`. System może następnie szukać czytelnych jersey panels na bezpiecznych fragmentach Romana.

Ważne:

```text
identity label Roman #6
≠
każdy crop Romana jest poprawnym jersey training sample
```

Candidate jersey panel jest użyteczny dopiero, gdy automat oceni m.in.:

```text
plecy/panel widoczne
wystarczająca wielkość
niski overlap/occlusion
poprawny panel crop
wystarczająca czytelność
```

## 8.3. Zestawienie dowodów w review

Po uruchomieniu użytecznego jersey recognizera review card może pokazać jedną spójną sugestię:

```text
Suggestion: Roman #6
Evidence:
- operator seed in H1
- jersey #6 episode
- Team A roster uniqueness
- appearance match in H2
- safe lineage
```

Nie tworzyć osobnych obowiązkowych audytów dla każdego evidence source.

---

# 9. Polityka anotowania cropów

## 9.1. Product/user workflow

W normalnym flow meczu użytkownik nie powinien:

```text
oznaczać setek appearance crops
oznaczać setek jersey panels
wpisywać bbox coordinates
oceniać blur/perspective/IoU/confidence
```

Initial Identity Audit dostarcza kilka gold identity labels. Reszta crop selection ma być automatyczna.

## 9.2. Research/admin workflow

Obecny J8.3 panel dataset closeout nadal ma sens jako ograniczona, jednorazowa praca potrzebna do uruchomienia pierwszego PanelDigitNet experiment.

To jest osobny workflow:

```text
curated research subset
→ panel montage
→ minimal human approval
→ model experiment
```

Nie jest częścią obowiązkowego per-match user flow.

## 9.3. Docelowe active learning

Po uruchomieniu modelu system powinien automatycznie zbierać candidate samples:

```text
operator-confirmed identity
+ roster number
+ safe tracklet lineage
+ automatically selected readable panel
→ candidate labeled sample
```

Człowiek ma oglądać wyłącznie mały, zróżnicowany zestaw:

```text
model conflicts
new visual conditions
uncertain but high-value panels
false-positive candidates
rare digits/numbers
```

Nie pokazywać setek redundantnych sąsiednich klatek z jednego visibility episode.

## 9.4. Kiedy ręczna anotacja przestaje być potrzebna

Ręczny panel-labeling można ograniczyć lub wyłączyć z bieżącego rozwoju, gdy:

```text
panel recognizer przechodzi defined precision/specificity gates
real fixtures są poprawnie rozpoznawane
plain-shirt false confirmed reads = 0 w audytowanym zbiorze
operator-confirmed identities dostarczają wystarczające auto-labeled samples
kolejne ręczne sample nie poprawiają worst-domain metrics
```

---

# 10. Second-half re-anchor

Ponieważ H2 ma inny capture domain, system może pokazać 2–3 dodatkowe łatwe klatki.

Nie jest to pełny drugi lineup audit.

UI powinno głównie pokazywać gotowe sugestie:

```text
Roman #6
[Potwierdź] [Inny zawodnik] [Team B] [Pomiń]
```

Cel:

```text
3–5 zawodników potwierdzonych w H2
→ cross-domain appearance prototypes
→ mocniejsze H1 ↔ H2 matching
```

Gdy H1 evidence już jednoznacznie rozwiązuje H2 bez konfliktów, re-anchor może zostać skrócony albo pominięty.

---

# 11. Review po automatycznym resolve

## 11.1. Whole-subject review zmienia rolę

Obecny whole-subject review ma ewoluować z:

```text
review every card
```

w:

```text
exception-only review
```

Po seed-aware resolve zwykła kolejka nie powinna ponownie pokazywać bezpiecznie rozwiązanych subjectów.

## 11.2. Co trafia do review

```text
operator seed vs jersey conflict
operator/team contradiction wymagający szerszej propagacji
parallel distant same-player candidate
cross-team candidate link
structural-conflict subject
possible ID switch boundary
long unresolved interval
possible substitution/new player
low-confidence fragment o dużym wpływie na stats
H1/H2 appearance conflict
```

## 11.3. Priorytetyzacja

Najpierw:

```text
hard safety conflicts
large stats impact
long duration
ball/event involvement, jeśli dostępne
substitution boundaries
```

Na końcu lub poza domyślną kolejką:

```text
krótkie noise fragments
low-impact unresolved detections
redundant crops z tego samego episode
```

## 11.4. Brak powtarzania pracy

Assignment wykonany w Initial Identity Audit musi zasilać późniejsze rekomendacje i resolved state.

Nie wolno wymagać:

```text
Roman assigned in Initial Audit
→ Roman assigned again in whole-subject review
→ Roman assigned again in jersey review
```

Jeden operator seed może być ponownie pokazany tylko wtedy, gdy istnieje konkretny explainable conflict.

---

# 12. Progressive reduction of manual work

## Etap A — szybki human seeding

```text
5–8 klatek
8–12 pewnych assignmentów
krótki H2 re-anchor
exception review
```

## Etap B — assisted confirmation

Po stabilnym jersey/ReID:

```text
system sugeruje nazwę na klatce
użytkownik głównie potwierdza
mniej ręcznego wyszukiwania w rosterze
```

## Etap C — adaptive audit

```text
system ocenia, których zawodników nadal potrzebuje
pokazuje tylko klatki maksymalizujące nową informację
kończy audit automatycznie po osiągnięciu wystarczającego safe coverage
```

## Etap D — exception-only product

```text
większość identity rozwiązana automatycznie
użytkownik widzi tylko kilka konfliktów lub nowych zawodników
```

## Etap E — near-automatic target

```text
roster + historical approved gallery, jeżeli bezpieczna
+ jersey recognition
+ match-specific re-anchor
+ safe identity optimizer
→ użytkownik zatwierdza finalny wynik i nieliczne wyjątki
```

Cross-match gallery pozostaje późniejszą sugestią i nie może być wdrażana, dopóki single-match gallery jest podatna na false merges.

---

# 13. Zmiany zawodników

Initial Identity Audit skupia się na starterach.

Nie wymagać ręcznej stop-klatki przy każdej zmianie.

Docelowy flow:

```text
nowy unresolved player-like subject
+ brak bezpiecznego dopasowania do aktywnego startera
+ czas/pozycja wskazują możliwą zmianę
→ substitution/new-player review candidate
```

Użytkownik wybiera dopiero w review:

```text
nowy zawodnik z rosteru
opcjonalnie: zastąpił zawodnika X
nie wiem / unresolved
```

Do czasu implementacji niezawodnego substitution logic nowy zawodnik nie może zostać agresywnie przypisany do istniejącego startera tylko na podstawie podobnego appearance.

---

# 14. Artefakty i provenance

Proponowane rozdzielenie:

```text
identity_initial_audit.json
identity_initial_audit_decisions.json
identity_operator_seeds.json
identity_seeded_candidate_assignments.json
identity_evidence_fusion_report.json
identity_exception_review.json
identity_review_reduction_report.json
```

Każdy propagated assignment ma wskazywać:

```text
source operator seed
source frame and bbox
tracklet path
subject/fragment lineage
jersey episodes used
appearance prototype version
team/roster constraints
accepted and rejected evidence
blockers
algorithm version and digests
```

UI ma pokazywać prosty explanation summary. Pełne szczegóły pozostają w developer/debug view i JSON artifacts.

---

# 15. Telemetry i KPI automatyzacji

Minimalne metryki Initial Audit:

```text
audit_frames_shown
audit_crops_clicked
audit_actions
active_operator_seconds
unique_players_seeded
H1_players_seeded
H2_players_reanchored
team_assignments_corrected
false_detections_marked
```

Efekt downstream:

```text
tracklets_resolved_after_seeding
subjects_resolved_after_seeding
frames_resolved_after_seeding
review_cards_before_seeding
review_cards_after_seeding
manual_decisions_before_seeding estimate
manual_decisions_after_seeding
unresolved_time_coverage
conflicts_created
false_assignments_found
```

Jersey/ReID contribution:

```text
subjects_resolved_by_operator_seed_only
subjects_resolved_with_jersey_support
subjects_resolved_with_reid_support
subjects_resolved_with_combined_evidence
jersey_conflicts
reid_conflicts
```

Success nie oznacza tylko utworzenia nowego UI.

Feature musi wykazać co najmniej jeden zysk bez pogorszenia bezpieczeństwa:

```text
fewer later review cards
fewer later manual decisions
lower active review time
higher safe resolved coverage
better H1 ↔ H2 continuity
```

---

# 16. Safety i activation gates

Automatyczny candidate assignment może być utworzony tylko przy braku:

```text
cross-team conflict
parallel distant same-player observations
stale lineage
structural blocker
same observation assigned to multiple players
trusted jersey contradiction without review
operator-seed contradiction
```

Przy konflikcie wynik:

```text
needs_review
```

Nie:

```text
aggressive automatic merge
```

Produkcja pozostaje niezmieniona do jawnego controlled apply.

---

# 17. Scope MVP

Najbliższy MVP obejmuje:

```text
automatyczny wybór 5–8 klatek
klikalne bboxy
roster/team/referee/false/skip actions
observation-level operator seeds
team contradiction correction
seed-aware downstream re-resolve bez YOLO
review-card reduction report
opcjonalny 2–3 frame H2 re-anchor
```

Poza MVP:

```text
named MP4 export
pełny timeline editor
manualne rysowanie wszystkich missed detections
automatyczne retraining podczas audytu
cross-match persistent gallery
pełna automatyczna obsługa zmian
production auto-apply
```

---

# 18. Kolejność implementacji

```text
IA0  Contracts and frozen-artifact frame selection prototype
IA1  Initial Identity Audit read-only UI
IA2  Atomic operator-seed store and telemetry
IA3  Seed-aware candidate identity re-resolve
IA4  Existing whole-subject review integration and card reduction
IA5  H2 capture-domain re-anchor
IA6  Automatic approved appearance gallery
IA7a Core evidence fusion report
IA7b Optional jersey evidence (frozen until new independent capture domain)
IA8  Exception-only review queue
IA9  Adaptive audit and manual-work reduction benchmark
```

IA7a nie czeka na jersey recognizer i korzysta z operator seeds, hard
constraints, safe lineage, appearance/ReID advisory oraz team/capture context.
IA7b jest opcjonalne i FROZEN_UNTIL_NEW_INDEPENDENT_CAPTURE_DOMAIN. IA0–IA6
nie musza czekac na J8.4.

---

# 19. Acceptance criteria docelowego flow

> Status tej checklisty jest historyczną specyfikacją docelowego UX. Bieżące
> rozróżnienie `implementation complete`, `automated validation complete`,
> `ready for operator`, `operator benchmark passed` i `ready for IA7a` jest
> utrzymywane wyłącznie w
> `task-requests/PLAYER_IDENTITY_DEVELOPMENT_PLAN.md`.

## Initial Audit

- [ ] domyślnie maksymalnie 5–8 klatek;
- [ ] brak obowiązkowego oznaczenia wszystkich bboxów;
- [ ] `Pomiń / Nie wiem` zawsze dostępne;
- [ ] brak raw coordinates i numeric confidence w operator UI;
- [ ] assignment gracza automatycznie ustawia team i roster number;
- [ ] błędny automatic team assignment można poprawić jednym wyborem gracza;
- [ ] każda akcja zapisuje observation-level provenance;
- [ ] audit można zakończyć wcześniej.

## Downstream integration

- [ ] brak full-match YOLO rerun po operator seed;
- [ ] seeded identity zasila subject recommendations;
- [ ] bezpiecznie rozwiązane karty nie wymagają ponownego assignmentu;
- [ ] conflicts pozostają jawne;
- [ ] production artifacts pozostają niezmienione;
- [ ] raport pokazuje review cards przed i po seeding.

## Jersey i crop automation

- [ ] operator seed może zasilać automatic appearance gallery;
- [ ] IA7a zestawia operator/team/lineage/ReID advisory bez jersey evidence;
- [ ] IA7b doklada jersey evidence tylko gdy jest dostepne po nowym capture domain;
- [ ] identity label nie jest automatycznie traktowany jako readable jersey sample;
- [ ] per-match user flow nie wymaga ręcznego labelowania jersey panels;
- [ ] research annotations pozostają osobnym curated workflow;
- [ ] active learning deduplikuje sąsiednie klatki jednego episode.

## Target minimal-review state

- [ ] whole-subject review działa jako exception queue;
- [ ] operator nie powtarza tego samego assignmentu w wielu ekranach;
- [ ] manual work jest mierzona i maleje wraz z kolejnymi etapami;
- [ ] automatyzacja nie obniża hard safety gates;
- [ ] finalny wynik pozostaje explainable i audytowalny.

---

# 20. Instrukcja dla następnego agenta

Przed implementacją:

1. przeczytaj `AGENTS.md`;
2. przeczytaj `PLAYER_IDENTITY_STABILIZATION_ROADMAP.md`;
3. przeczytaj `JERSEY_NUMBER_IDENTITY_ANCHORS.md`;
4. przeanalizuj aktualny whole-subject review flow i artifacts;
5. nie twórz kolejnego niezależnego audytu wymagającego powtórzenia istniejącej pracy;
6. nie uruchamiaj ponownie YOLO dla zmian downstream identity;
7. nie wymagaj od użytkownika coordinate/confidence/internal-ID inputs;
8. zacznij od IA0/IA1 i frozen artifacts;
9. zakończ pierwszy cykl działającym krótkim UI na kilku klatkach, nie kompletnym autonomicznym resolverem;
10. raportuj realny operator interaction count i przewidywany wpływ na późniejsze review.

Najważniejsza reguła:

> Użytkownik ma powiedzieć wyłącznie to, co wie jako człowiek znający mecz i zawodników. Aplikacja ma samodzielnie wykonać całą pracę techniczną i wykorzystać tę wiedzę możliwie szeroko, bez bezpiecznego omijania konfliktów.
