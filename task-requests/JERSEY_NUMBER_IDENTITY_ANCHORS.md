# Jersey Number Identity Anchors

## Status

```text
UZUPEŁNIENIE task-requests/PLAYER_IDENTITY_STABILIZATION_ROADMAP.md
SHADOW-FIRST / HIGH-CONFIDENCE IDENTITY EVIDENCE
N0-N5 ORAZ INFRASTRUKTURA N5.1-N5.8 ZAIMPLEMENTOWANE
J8.1 FREEZE EXISTING RECOGNIZERS: CLOSED
J8.2 PANEL ANNOTATION CONTRACT: CLOSED
J8.3 PANEL AUDIT IMPLEMENTATION: CLOSED
J8.3 REAL DATASET RUN + HUMAN MONTAGE APPROVAL + FINDINGS: OPEN
J8.4 PANELDIGITNETV1: NOT STARTED
CANDIDATE I PRODUCTION ASSIGNMENTS POZOSTAJĄ ZABLOKOWANE
```

Aktualny baseline po mergu:

```text
826616090bcb1db050fd36ec70ee2e7d052af39b
```

Najważniejsza decyzja:

> Nie budować teraz kolejnego recognizera ani nie wracać do strojenia CRNN. Najpierw należy operacyjnie domknąć J8.3: wybrać jawny subset paneli, wygenerować realne montage, ręcznie je zatwierdzić, naprawić finalne readiness gates i zapisać findings. Dopiero potem wolno rozpocząć J8.4.

Jersey-number work nie blokuje równoległego P1.22 Full-Match Operator Benchmark.

---

# 1. Dlaczego feature pozostaje bardzo wartościowy

Zaufany numer może utworzyć silniejszy identity anchor niż ogólne appearance/ReID:

```text
Team A + numer 10
→ jednoznaczny roster player
→ operator-confirmed candidate subject
→ safe intra-subject lineage propagation
→ approved appearance crops
→ roster-confirmed ReID prototype
→ ranking unresolved tracklets/subjects
```

Nawet umiarkowany recall może być użyteczny:

```text
kilka poprawnie rozpoznanych numerów
→ kilka pewnych real-player anchors
→ więcej trackletów przypisanych przez istniejący safe lineage
→ mniej ręcznego review
```

Warunek:

```text
wysoka precision > wysoki recall
```

Number evidence nie może omijać:

```text
team constraints
temporal overlap constraints
parallel-position constraints
structural blockers
lineage freshness
operator review
```

---

# 2. Co jest już poprawnie zaimplementowane

## 2.1. Identity safety N0-N5

Gotowe pozostają:

- optional unique jersey number per team;
- duplicate same-team number disables trust;
- `number_absent` różne od `number_unreadable` w identity/evidence contract;
- visibility episodes;
- tracklet i subject consensus;
- same-team roster lookup;
- whole-subject review suggestions;
- canonical structural blockers;
- full lineage validation;
- separate number/operator seed provenance;
- safe intra-subject propagation;
- brak cross-subject propagation;
- brak automatic assignments;
- candidate i production identity pozostają niezmienione.

## 2.2. Dataset i ewaluacja

Aktualny kod ma:

- deterministyczny dataset manifest;
- content digests i provenance;
- leak-safe subject/episode splits;
- match-level split przy minimum trzech source matches;
- subject-group fallback dla jednego fizycznego meczu;
- team-scoped roster candidate numbers;
- crop, episode i subject metrics;
- plain-shirt negatives;
- real fixture numeru `10`;
- canonical production artifact comparison;
- held-out multi-match infrastructure.

Nie dodawać kolejnych ogólnych schematów ani gate frameworków przed przejściem panel audit i tiny overfit.

---

# 3. Aktualny wynik recognizera

Ostatni dataset closeout:

```text
backend/storage/benchmarks/player_identity/
jersey-number-dataset-closeout-20260723-v3/
```

Wynik:

```text
334 samples
1 physical source match / 2 source videos
crop precision: 1.0000
crop recall:    0.0909
episode recall: 0.0000
subject recall: 0.0000
plain-shirt false reads: 0
real number 10 fixture: FAILED BY SAFE ABSTENTION
production eligible: false
```

Interpretacja:

```text
safety: bardzo dobra
precision: wynika głównie z abstention
recall: za niski do realnej pomocy
identity gain: zero
multi-match generalization: niezmierzone
```

---

# 4. Status istniejących recognizerów

## 4.1. OpenCV template baseline

```text
constrained torso ROI
→ bright jersey component
→ dark digit mask
→ OpenCV font templates
```

Status:

```text
diagnostic baseline only
DO NOT EXTEND
```

## 4.2. Whole-number centroid baseline

```text
normalized torso feature
→ centroid per pełny numer
→ closed-set cosine similarity
```

Status:

```text
regression comparison only
NOT TARGET RECOGNIZER
```

## 4.3. CRNN-CTC prototype

```text
fixed upper-torso ROI
→ grayscale 32x96
→ small CNN
→ BiGRU
→ CTC digits
→ visual-state head
```

Status:

```text
deferred diagnostic experiment
DO NOT TUNE
DO NOT INTEGRATE
```

Problemy:

- szeroki torso ROI zamiast tight number panel;
- zbyt małe cyfry po resize;
- trening od zera;
- blank collapse risk;
- brak real-panel overfit proof;
- jeden fizyczny mecz;
- brak użytecznego recall.

---

# 5. Ocena ostatniego commitu

Commit `8266160` wnosi dużą wartość:

- upraszcza operator annotation flow;
- dodaje `jersey_number` oraz panel bbox do dataset path;
- dodaje wspólne walidatory bbox i safe artifact path;
- generuje `number_panel_dataset_readiness.json`;
- generuje `number_panel_montage.jpg`;
- audytuje panel po finalnym resize `96x64`;
- dodaje testy panel audit;
- ujednolica nazwy outputów z roadmapą.

J8.2 i implementacyjna część J8.3 są zamknięte.

J8.3 nie jest jeszcze zamknięte operacyjnie, ponieważ:

1. nie ma realnego readiness JSON i montage z właściwego dataset subset;
2. nie ma human approval powiązanego z digestem montage;
3. `manual_panel_audit_required` jest obecnie informacją, ale nie uczestniczy w finalnym `ready` status;
4. nie ma jawnego gate dla uszkodzonych lub brakujących paneli w wybranym eksperymentalnym subsecie;
5. puste `jersey_number` może zlewać `number_absent` i `number_unreadable`;
6. digit-height distribution może obejmować negatives i wykrywać przypadkowe kontury zamiast cyfr;
7. nadal istnieją dwa podobne moduły `panel_readiness` i `panel_audit`;
8. nie ma J8.3 findings z decyzją `proceed / fix crops / collect data`.

---

# 6. Minimalny annotation contract po uproszczeniu UI

Nie przywracać pól:

```text
digit_visibility
occlusion_state
blur_level
perspective_state
panel_height_ratio
kit_profile
```

Nie są wymagane do pierwszego PanelDigitNet experiment.

Potrzebny jest jednak jeden jawny stan semantyczny:

```json
{
  "jersey_number_state": "number_confirmed | number_absent | number_unreadable",
  "jersey_number": "10 | null",
  "number_panel_bbox_normalized": [0.22, 0.18, 0.78, 0.62]
}
```

Reguły:

```text
number_confirmed
→ jersey_number wymagany, 1-3 cyfry
→ tight bbox obejmuje cały widoczny numer

number_absent
→ jersey_number = null
→ bbox obejmuje oczekiwany obszar numeru na czystej koszulce

number_unreadable
→ jersey_number = null
→ bbox obejmuje obszar, w którym numer jest potencjalnie obecny, ale nieczytelny
```

Puste pole tekstowe nie może samodzielnie oznaczać zarówno braku numeru, jak i nieczytelnego numeru.

Backward compatibility:

```text
legacy label_state/state
→ mapować do jersey_number_state
→ nie usuwać istniejących manualnych annotations
```

---

# 7. Canonical panel experiment selection

Nie wymagamy bboxa dla wszystkich 334 samples.

Dodać jawny, wersjonowany subset, np.:

```json
{
  "panel_experiment_selection": {
    "selection_version": "j8.3-v1",
    "sample_keys": ["..."],
    "selection_digest": "..."
  }
}
```

Audit ma rozróżniać:

```text
selected sample
→ musi mieć poprawny panel definition i przejść audit

not selected sample
→ nie jest błędem
→ status not_selected_for_panel_experiment
```

Pierwszy subset:

```text
minimum 50 number_confirmed panels
minimum 20 independent readable visibility episodes
minimum 30 negatives łącznie
  - number_absent
  - number_unreadable
real10 frames 3509/3510/3512
```

Sąsiednie real10 frames:

```text
3 frame crops
→ mogą poprawić jeden visual episode
→ nadal 1 independent identity vote
```

---

# 8. J8.3 closeout — dokładne zadania dla następnego agenta

Agent ma wykonać wyłącznie poniższy closeout. Nie rozpoczynać J8.4 w tym samym cyklu.

## J8.3a — fix annotation semantics

- dodać `jersey_number_state` z trzema wartościami;
- walidować zgodność state/number;
- zachować kompatybilność legacy;
- dodać prosty selector/radio w UI;
- nie przywracać dodatkowych jakościowych dropdownów.

Acceptance:

```text
confirmed + brak numeru       → reject
absent + numer                → reject
unreadable + numer            → reject
confirmed + poprawny numer    → accept
absent/unreadable + null      → accept
```

## J8.3b — canonical selection subset

- dodać wersjonowaną listę `sample_keys` do panel experiment;
- audit ma pracować na selection subset;
- sample poza selection nie blokuje readiness;
- każdy selected sample musi być audytowalny.

## J8.3c — repair readiness gates

Dodać metryki:

```text
selected_samples
selected_audited_samples
selected_invalid_samples
audited_panel_coverage
readable_confirmed_panels
number_absent_panels
number_unreadable_panels
readable_visibility_episodes
real10_panels_found
```

Wymagane machine gates:

```text
selected_samples > 0
selected_invalid_samples = 0
audited_panel_coverage = 1.0
readable_confirmed_panels >= 50
readable_visibility_episodes >= 20
number_absent + number_unreadable >= 30
real10_panels_found >= 1
```

Invalid selected statuses obejmują:

```text
missing_panel_bbox
missing_panel_artifact
missing_source_artifact
corrupt_source_artifact
empty_panel_crop
invalid_bbox
stale_panel_definition
```

## J8.3d — digit-height gate

`estimated_digit_height_px` liczyć tylko dla:

```text
jersey_number_state = number_confirmed
```

Nie wliczać:

```text
number_absent
number_unreadable
```

Automatyczny contour estimate jest diagnostyczny, nie ground truth.

Finalny warunek:

```text
median estimated digit height >= 8 px
+
human montage confirms, że resize 96x64 nadal zachowuje czytelne cyfry
```

Jeżeli contour estimate jest niestabilny:

```text
nie pisać bardziej skomplikowanego detectora
→ oznaczyć estimate jako unreliable
→ oprzeć decyzję R0 na montage review
```

## J8.3e — human montage approval contract

Nie używać samego boolean podanego podczas generowania raportu.

Wygenerować osobny approval artifact:

```json
{
  "schema_version": "0.1.0",
  "montage_sha256": "...",
  "dataset_digest": "...",
  "selection_digest": "...",
  "reviewer": "operator",
  "reviewed_at": "...",
  "status": "approved | rejected",
  "notes": "..."
}
```

Przebieg:

```text
1. agent generuje montage i readiness JSON
2. agent STOP
3. człowiek ogląda montage
4. człowiek zatwierdza lub odrzuca konkretny montage digest
5. drugi run weryfikuje approval digest
```

Finalny status:

```text
ready_for_panel_digit_experiment
```

może powstać tylko, gdy:

```text
all machine gates pass
AND approval.status = approved
AND approval montage/dataset/selection digests match
```

Bez approval:

```text
machine_ready_waiting_for_human_review
```

## J8.3f — remove duplicate path ambiguity

Canonical implementation:

```text
backend/app/services/identity_jersey_number_panel_audit.py
backend/scripts/audit_identity_jersey_number_panels.py
```

Stary moduł:

```text
backend/app/services/identity_jersey_number_panel_readiness.py
```

należy:

```text
usunąć
LUB
zamienić w cienki deprecated wrapper do panel_audit
```

Nie utrzymywać dwóch niezależnych gate implementations.

## J8.3g — run on real data and stop

Uruchomić:

```bash
python backend/scripts/audit_identity_jersey_number_panels.py \
  --dataset <identity_jersey_number_dataset.json> \
  --output-root <j8.3-output-directory>
```

Zapisać:

```text
number_panel_dataset_readiness.json
number_panel_montage.jpg
number_panel_montage_approval.json
J8_3_PANEL_READINESS_FINDINGS.md
```

Findings muszą zawierać:

```text
source dataset + digest
selection version + digest
selected sample counts
readable/absent/unreadable counts
visibility episode counts
invalid selected samples
panel coverage
median readable digit height
real10 status
human montage decision
final decision
```

Dozwolone final decisions:

```text
PROCEED_TO_J8_4
FIX_PANEL_ANNOTATIONS
COLLECT_MORE_READABLE_EPISODES
COLLECT_MORE_NEGATIVES
CROP_PIPELINE_NOT_VIABLE
```

Po zapisaniu findings agent kończy pracę.

---

# 9. STOP-LOOP RULES

## Rule A — brak realnego montage

```text
brak realnego number_panel_montage.jpg
→ nie implementować ani nie trenować modelu
```

## Rule B — brak human approval

```text
machine gates pass, ale montage niezatwierdzone
→ status waiting_for_human_review
→ agent STOP
```

## Rule C — invalid selected panels

```text
selected_invalid_samples > 0
→ napraw annotations/artifacts
→ nie rozpoczynać J8.4
```

## Rule D — real10 nieczytelne po resize

```text
real10 nieczytelne w panel 96x64
→ problem crop/input resolution
→ nie zmieniać model architecture
```

## Rule E — brak dataset minimum

```text
<50 confirmed panels
lub <20 readable episodes
lub <30 negatives
→ collect data
→ nie trenować generalization modelu
```

## Rule F — limit architektury

```text
maksymalnie jeden model architecture experiment per benchmark cycle
```

---

# 10. J8.4 — PanelDigitNetV1 dopiero po J8.3

J8.4 może rozpocząć się wyłącznie po:

```text
J8_3_PANEL_READINESS_FINDINGS.md
final decision = PROCEED_TO_J8_4
```

Architektura:

```text
number panel crop 64x96
→ shared small CNN
├── visual head: readable / absent / unreadable
├── digit position 1: blank + 0-9
├── digit position 2: blank + 0-9
└── digit position 3: blank + 0-9
```

To jest jeden model.

Nie dodawać:

```text
GRU
CTC
beam search
osobnego digit detectora
OCR frameworka
```

Target encoding:

```text
7   → [7, blank, blank]
10  → [1, 0, blank]
92  → [9, 2, blank]
100 → [1, 0, 0]
```

Loss:

```text
visual CE dla wszystkich samples
digit CE tylko dla number_confirmed
```

Dla `number_absent` i `number_unreadable` nie liczyć digit loss.

---

# 11. Debug ladder po rozpoczęciu J8.4

## R1 — tiny real-panel overfit

```text
16 clean confirmed panels
minimum 3 różne numery
minimum 4 visibility episodes
no augmentation
max 500 epochs
```

Pass:

```text
exact sequence accuracy >= 0.95
visual-state accuracy = 1.0
0 null predictions
```

Brak pass:

```text
problem code/labels/preprocessing/loss
→ nie zwiększać datasetu
→ nie zmieniać architektury
```

## R2 — readable vs negatives overfit

```text
16 confirmed
16 absent/unreadable
```

Pass:

```text
readable recall >= 0.95
negative specificity >= 0.95
exact sequence accuracy >= 0.90 na confirmed
```

## R3 — same-match heldout diagnostic

Mierzyć:

```text
crop exact sequence accuracy
episode exact sequence accuracy
plain-shirt false confirmed reads
real10 episode result
```

Target:

```text
real10 = 10
plain-shirt false confirmed = 0
episode precision = 1.0
episode recall > 0
```

## R4 — drugi niezależny mecz

Dopiero potem:

```text
confidence calibration
threshold freeze
N5.8 candidate-review experiment
```

---

# 12. Testy wymagane w J8.3 closeout

## Annotation contract

- poprawny `jersey_number_state` jest wymagany dla selected sample;
- confirmed wymaga numeru;
- absent/unreadable zabraniają numeru;
- legacy state mapping nie traci istniejących annotations;
- bbox musi być finite, normalized i spełniać `x1<x2`, `y1<y2`.

## Selection contract

- selected sample bez panel definition blokuje readiness;
- not-selected sample bez bboxa nie jest błędem;
- selection digest jest deterministyczny;
- duplicate sample key jest odrzucany.

## Readiness gates

- invalid selected sample blokuje final ready;
- coverage poniżej 1.0 blokuje final ready;
- digit-height median używa tylko confirmed panels;
- negative contours nie wpływają na digit-height gate;
- brak human approval daje waiting status;
- mismatched montage digest odrzuca approval;
- rejected approval blokuje J8.4.

## Real regression

- real10 znajduje się w selection;
- real10 jest widoczne w montage;
- frames 3509/3510/3512 pozostają jednym visibility episode;
- audit nie mutuje identity;
- candidate/production assignments pozostają zero.

---

# 13. Kontynuacja Player ID roadmap

Działać równolegle:

```text
J8.3 operational closeout
+
P1.22 Full-Match Operator Benchmark
```

P1.22 powinno zbierać:

```text
number visibility episodes
number_absent negatives
number_unreadable negatives
operator-confirmed panel bboxes
number suggestions shown/accepted/rejected
subjects resolved by number
tracklets resolved by number
review time spent/saved
```

Po poprawnym, operator-confirmed number anchor:

```text
real player
→ approved number panel
→ approved appearance crops
→ roster-confirmed ReID prototype
→ ranking unresolved fragments
```

Nadal:

```text
no automatic cross-subject merge
no production identity write
```

---

# 14. Acceptance criteria

## J8.2 closed

- [x] normalized bbox contract;
- [x] safe artifact path validation;
- [x] deterministic panel crop;
- [x] panel annotation persistence;
- [x] dataset manifest integration;
- [x] operator-only panel provenance;
- [x] no production identity mutation.

## J8.3 implementation closed

- [x] canonical panel audit service;
- [x] panel audit CLI;
- [x] `number_panel_dataset_readiness.json` output;
- [x] `number_panel_montage.jpg` output;
- [x] resize preview `96x64`;
- [x] panel audit tests.

## J8.3 operational closeout required

- [ ] explicit three-state jersey annotation;
- [ ] canonical panel experiment selection subset;
- [ ] 100% audit coverage of selected samples;
- [ ] 0 invalid selected samples;
- [ ] minimum 50 confirmed panels;
- [ ] minimum 20 readable visibility episodes;
- [ ] minimum 30 absent/unreadable negatives;
- [ ] digit-height median from confirmed panels only;
- [ ] real10 included and readable after preprocessing;
- [ ] montage approval tied to digest;
- [ ] duplicate panel readiness path removed/deprecated;
- [ ] real readiness artifacts generated;
- [ ] J8.3 findings committed;
- [ ] final decision recorded.

## Candidate review activation

- [ ] minimum 2 independent source matches;
- [ ] minimum 2 positive safe multi-tracklet propagations;
- [ ] 0 false strong subject consensuses;
- [ ] 0 false roster suggestions;
- [ ] 0 unexpected propagations;
- [ ] canonical production digests unchanged;
- [ ] operator review remains required.

## Production use

- [ ] minimum 3 evaluated source matches including held-out;
- [ ] P1.22 completed;
- [ ] candidate-only number assistance measurably reduces review;
- [ ] P1.23 stats validation passes;
- [ ] P1.24 transaction/backup/rollback implemented;
- [ ] no automatic cross-subject or cross-match promotion.

---

# 15. Polecenie wykonawcze dla następnego agenta

```text
Pracuj wyłącznie nad J8.3 operational closeout.

1. Przeczytaj aktualny kod panel annotation, dataset builder i panel audit.
2. Dodaj minimalny jersey_number_state: confirmed / absent / unreadable.
3. Dodaj jawny panel_experiment_selection z deterministycznym digestem.
4. Audytuj wyłącznie selected subset; not-selected nie jest błędem.
5. Final ready wymaga 100% selected coverage i 0 invalid selected samples.
6. Digit-height gate licz tylko z confirmed panels.
7. Dodaj montage approval artifact związany z montage/dataset/selection digest.
8. Usuń lub zdeprecjonuj stary panel_readiness path.
9. Dodaj testy kontraktowe i uruchom je.
10. Wygeneruj real readiness JSON i montage.
11. Zatrzymaj się przed human approval.
12. Po decyzji człowieka zapisz J8_3_PANEL_READINESS_FINDINGS.md.
13. Nie implementuj PanelDigitNetV1, CRNN, kolejnego OCR ani identity integration w tym cyklu.
```

---

# 16. Wniosek końcowy

Ostatni commit prawidłowo uprościł operator flow i zbudował właściwy panel audit. Najważniejsza część infrastruktury jest gotowa.

Następny krok nie polega na napisaniu modelu. Polega na udowodnieniu, że:

```text
wybrany dataset subset
→ ma poprawne tight panels
→ ma jawne confirmed/absent/unreadable labels
→ przechodzi 100% extraction coverage
→ zachowuje czytelne cyfry po 96x64 resize
→ zawiera real10
→ został ręcznie obejrzany i zatwierdzony
```

Dopiero wtedy:

```text
PanelDigitNetV1
→ tiny real-panel overfit
→ negatives
→ real10
→ stop and report
```

Nie dodawać kolejnej architektury i nie stroić CTC w ciemno.