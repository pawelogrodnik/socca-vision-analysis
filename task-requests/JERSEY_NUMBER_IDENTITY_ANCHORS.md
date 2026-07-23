# Jersey Number Identity Anchors

## Status

```text
UZUPEŁNIENIE task-requests/PLAYER_IDENTITY_STABILIZATION_ROADMAP.md
SHADOW-FIRST / HIGH-CONFIDENCE IDENTITY EVIDENCE
N0-N5 ORAZ INFRASTRUKTURA N5.1-N5.8 ZAIMPLEMENTOWANE
DATASET MANIFEST, SPLITY I EWALUACJA ZAIMPLEMENTOWANE
TEMPLATE I CENTROID POZOSTAJĄ DIAGNOSTYCZNYMI BASELINE'AMI
CRNN-CTC JEST MECHANICZNIE ZAIMPLEMENTOWANY, ALE NIEZWALIDOWANY I NIEZINTEGROWANY
CANDIDATE I PRODUCTION ASSIGNMENTS POZOSTAJĄ ZABLOKOWANE
```

Przeanalizowany aktualny baseline:

```text
e86fcb70f06ae1f1e5347a780fef9ca0eb25f7ce
```

Najważniejsza decyzja po audycie kodu:

> Agent nie powinien dalej tworzyć kolejnych recognizerów, kolejnych schematów ani stroić CTC w ciemno. Aktualny problem nie polega na braku architektury. Problemem jest zbyt szeroki i niesprawdzony number ROI, mała liczba niezależnych readable episodes oraz trenowanie sekwencyjnego modelu od zera na jednym fizycznym meczu.

Docelowy następny krok to jeden prosty model panelowy z trzema pozycjami cyfr, poprzedzony obowiązkowym audytem tight number-panel crops i testem overfit na małej próbce.

Jersey-number work nie blokuje P1.22 Full-Match Operator Benchmark.

---

# 1. Dlaczego ten feature nadal jest bardzo wartościowy

Zaufany numer może utworzyć znacznie silniejszy identity anchor niż ogólne appearance/ReID:

```text
Team A + numer 10
→ jednoznaczny roster player
→ operator-confirmed candidate subject
→ safe intra-subject lineage propagation
→ approved appearance crops
→ roster-confirmed ReID prototype
→ ranking unresolved tracklets/subjects
```

Nawet umiarkowany recall może dać duży zysk:

```text
kilka poprawnie rozpoznanych numerów
→ kilka pewnych real-player anchors
→ więcej trackletów przypisanych przez istniejący safe lineage
→ mniej ręcznych kart do przejrzenia
```

Warunek pozostaje niezmienny:

```text
wysoka precision > wysoki recall
```

Numer nie może omijać:

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

## N0-N5

Gotowe pozostają:

- optional unique jersey number per team;
- duplicate same-team number disables trust;
- `number_absent` różne od `number_unreadable`;
- reliable crop/evidence contract;
- visibility episodes;
- tracklet i subject consensus;
- same-team unique roster lookup;
- whole-subject review suggestion;
- canonical structural blockers;
- full lineage validation;
- separate number/operator seed provenance;
- safe intra-subject propagation;
- brak cross-subject propagation;
- brak automatic assignments;
- candidate i production identity pozostają niezmienione.

## Dataset i ewaluacja

Aktualny kod ma:

- deterministyczny dataset manifest;
- content digests i provenance;
- split bez przecieku jednego subjectu/episode pomiędzy zestawami;
- match-level split przy minimum trzech source matches;
- subject-group fallback dla jednego fizycznego meczu;
- team-scoped roster candidate numbers;
- crop, episode i subject metrics;
- plain-shirt negatives;
- real fixture numeru `10`;
- canonical production artifact comparison;
- held-out multi-match infrastructure.

To jest wystarczająca infrastruktura. Nie należy teraz dodawać kolejnych warstw kontraktowych, dopóki recognizer nie przejdzie prostego testu percepcji.

---

# 3. Aktualny wynik i jego właściwa interpretacja

Najnowszy closeout:

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
precision: obiecująca, ale wynika głównie z abstention
recall: za niski do realnej pomocy
identity gain: obecnie zero
multi-match generalization: niezmierzone
```

Model nie tworzy błędnych identity, ale także nie dostarcza użytecznych number anchors.

---

# 4. Audit aktualnego number-recognition kodu

Aktualnie istnieją trzy różne ścieżki rozpoznawania.

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
```

Nie rozwijać go dalej jako głównej metody.

## 4.2. Whole-number centroid baseline

Plik:

```text
backend/app/services/identity_jersey_number_learned.py
```

Mechanizm:

```text
normalized grayscale/gradient torso feature
→ centroid per pełny numer
→ closed-set cosine similarity
```

Ograniczenia:

- nie uczy się cyfr `0-9`;
- nie potrafi generalizować do nowego numeru;
- może uczyć się wyglądu koszulki lub zawodnika;
- jest roster/closed-set diagnostic baseline;
- realny numer `10` kończy jako safe abstention.

Status:

```text
keep for regression comparison
not a target recognizer
```

## 4.3. CRNN-CTC prototype

Pliki:

```text
backend/app/services/identity_jersey_number_sequence.py
backend/app/services/identity_jersey_number_sequence_training.py
backend/app/services/identity_jersey_number_sequence_evaluation.py
```

Aktualna architektura:

```text
fixed upper-torso ROI
→ grayscale 32x96
→ small CNN
→ BiGRU
→ CTC digits
→ visual-state head
```

To oznacza, że digit-sequence recognizer jest mechanicznie zaimplementowany. Nie jest jednak gotowy produktowo.

Najważniejsze problemy aktualnej implementacji:

1. wejściem jest szeroki, stały torso ROI, a nie tight number panel;
2. po resize do `32x96` cyfry mogą mieć tylko kilka pikseli wysokości;
3. CNN+GRU+CTC jest trenowany od zera;
4. domyślne trenowanie to jeden epoch;
5. brak batchingu, augmentacji i pozytywnego overfit smoke testu na realnych panelach;
6. CTC ma naturalną tendencję do blank collapse przy małym datasecie;
7. obecny model jest tylko diagnostic: public prediction pozostaje `accepted = false`;
8. CRNN nie jest podłączony jako target predictor do głównego recognizer shadow;
9. testy sprawdzają głównie kontrakty i mechanikę na sztucznych tensorach, a nie real-video quality;
10. wszystkie dostępne dane pochodzą z jednego fizycznego meczu.

Wniosek:

> Nie należy próbować naprawić tego przez kolejne warianty GRU, dodatkowe regularizatory blank albo wielokrotne zmiany thresholdów. Najpierw trzeba udowodnić, że model w ogóle dostaje czytelny panel numeru.

---

# 5. STOP-LOOP RULES dla agenta

Agent ma obowiązek zatrzymać iterację, gdy wystąpi którykolwiek z poniższych przypadków.

## Rule A — brak panel audit

```text
brak montage tight panel crops
→ nie wolno zmieniać architektury ani trenować kolejnego modelu
```

## Rule B — brak tiny-set overfit

```text
model nie potrafi osiągnąć >= 95% exact sequence accuracy
na 16 ręcznie wybranych czystych panelach
→ preprocessing, labels lub loss są błędne
→ nie wolno zwiększać datasetu ani stroić confidence
```

## Rule C — overfit działa, validation nie działa

```text
tiny overfit passes
same-match validation fails
→ problemem jest diversity/dataset
→ zbieraj lepsze dane
→ nie twórz kolejnej architektury
```

## Rule D — real10 nadal unreadable

```text
real10 panel jest czytelny w montage
model nadal abstynuje
→ problem panel alignment / model training

real10 panel nie jest czytelny po preprocessing
→ problem crop pipeline
```

## Rule E — plain-shirt false read

```text
jakikolwiek confirmed number na plain-shirt held-out negative
→ blokada activation
→ popraw visual/readability head lub calibration
```

## Rule F — limit eksperymentów

```text
maksymalnie jeden model architecture experiment per benchmark cycle
```

Każdy cykl musi zakończyć się jednym raportem:

```text
what changed
train overfit result
validation result
real10 result
plain-shirt result
decision: keep / reject / collect data
```

---

# 6. Obowiązkowy krok zerowy: tight number-panel dataset

Największym obecnym ryzykiem jest nie model, lecz wejście.

Aktualny fixed torso ROI nie jest wystarczającym kontraktem dla target recognizera.

## 6.1. Nowe opcjonalne pole annotation

Dodać do review crop:

```json
{
  "number_panel_bbox_normalized": [0.22, 0.18, 0.78, 0.62]
}
```

Pole oznacza tight panel numeru względem właściwego person/torso cropa.

Nie trzeba annotować całego datasetu od razu.

Minimum dla pierwszego eksperymentu:

```text
wszystkie high-confidence number_confirmed crops
real10 frames 3509/3510/3512
co najmniej 30 plain-shirt / unreadable negatives
```

## 6.2. Panel artifact

Training sample powinien używać:

```text
number_panel_artifact
```

lub deterministycznie wycinać panel z:

```text
artifact + number_panel_bbox_normalized
```

Do modelu nie podawać całej sylwetki ani szerokiej górnej połowy tułowia jako finalnego inputu.

## 6.3. Readiness report

Przed treningiem wygenerować:

```text
number_panel_dataset_readiness.json
number_panel_montage.jpg
```

Raport musi zawierać:

```text
total panel crops
readable full-number crops
partial-number crops
plain-shirt crops
unreadable crops
unique visibility episodes
unique tracklets
unique subjects
counts per number
counts per digit 0-9
counts per view
panel width/height distribution
estimated digit pixel-height distribution
missing panel bbox count
```

Hard stop:

```text
median digit height < 8 px po preprocessing
→ nie trenować modelu
```

Target dla pierwszego użytecznego eksperymentu:

```text
minimum 50 readable panel crops
minimum 20 independent readable visibility episodes
minimum 30 plain-shirt/unreadable negatives
```

Mniejsze zbiory mogą służyć do overfit smoke testu, ale nie do oceny generalizacji.

---

# 7. Decyzja modelowa: PanelDigitNetV1

Nie implementować kolejnego CRNN jako następnego kroku.

Użyć jednego prostego modelu wielowyjściowego.

## 7.1. Architektura

```text
number panel crop 64x96
→ shared small CNN
├── visual head: readable / absent / unreadable
├── digit position 1: blank + 0-9
├── digit position 2: blank + 0-9
└── digit position 3: blank + 0-9
```

To jest jeden model, nie kilka osobnych modeli.

Można początkowo ponownie użyć prostego CNN z obecnego CRNN:

```text
Conv 1→32
MaxPool
Conv 32→64
MaxPool
Adaptive pooling
shared embedding
```

Bez:

```text
GRU
CTC
beam search
blank regularizer
```

## 7.2. Target encoding

```text
"7"   → [7, blank, blank]
"10"  → [1, 0, blank]
"92"  → [9, 2, blank]
"100" → [1, 0, 0]
```

## 7.3. Loss

```text
total_loss = visual_cross_entropy
```

Dla `number_confirmed` z pełnym numerem:

```text
total_loss += digit_1_ce + digit_2_ce + digit_3_ce
```

Dla `number_partially_readable`:

```text
loss tylko dla jawnie widocznych pozycji cyfr
```

Dla `number_absent` i `number_unreadable`:

```text
bez digit loss
```

## 7.4. Dlaczego ten wariant

- odpowiada dokładnie numerom długości 1-3;
- używa istniejących whole-number labels;
- nie wymaga bboxów pojedynczych cyfr;
- eliminuje CTC blank collapse;
- jest łatwy do overfitowania i debugowania;
- daje per-position confidence;
- nie tworzy nowego skomplikowanego OCR systemu;
- pozostawia możliwość powrotu do CRNN dopiero, gdy fixed-position model przegra z powodu realnego układu cyfr.

## 7.5. CRNN status

Obecny CRNN pozostawić w repo jako:

```text
deferred diagnostic experiment
```

Nie usuwać go i nie integrować teraz z identity pipeline.

Powrót do CRNN jest dozwolony wyłącznie, gdy:

```text
PanelDigitNetV1 overfits clean panels
+ panel crops są poprawne
+ błędy wynikają z variable digit spacing/alignment
```

---

# 8. Obowiązkowa drabina debugowania

## R0 — panel visual audit

Deliverables:

```text
montage z expected number
panel bbox overlay
panel crop po resize
source frame/tracklet/episode
```

Decision:

```text
czy cyfry są czytelne dla człowieka po dokładnie tym samym preprocessing?
```

## R1 — tiny overfit

Dataset:

```text
16 clean readable panel crops
minimum 3 różne numery
minimum 4 różne visibility episodes
```

Training:

```text
no augmentation
same 16 samples
train until overfit or max 500 epochs
```

Pass gate:

```text
exact sequence accuracy >= 0.95
visual-state accuracy = 1.0
0 null predictions
```

Brak pass oznacza błąd kodu, cropów, labeli lub loss.

## R2 — readable-vs-negative overfit

Dataset:

```text
16 readable
16 plain-shirt/unreadable
```

Pass gate:

```text
readable recall >= 0.95
negative specificity >= 0.95
exact sequence accuracy >= 0.90 na readable
```

## R3 — same-match subject-heldout diagnostic

Nie jest to production validation.

Mierzyć:

```text
crop exact sequence accuracy
episode exact sequence accuracy
plain-shirt false confirmed reads
real10 episode result
```

Target diagnostyczny:

```text
real10 = 10
plain-shirt false confirmed = 0
episode precision = 1.0
episode recall > 0
```

## R4 — drugi niezależny mecz

Dopiero drugi source match pozwala ocenić, czy model nauczył się cyfr zamiast strojów/zawodników.

Dopiero wtedy:

```text
confidence calibration
threshold freeze
N5.8 candidate-review experiment
```

---

# 9. Integracja z istniejącym pipeline

PanelDigitNetV1 ma początkowo działać tylko w osobnym offline benchmarku.

Nie integrować go do `identity_jersey_number_recognizer_shadow` przed przejściem R0-R3.

Po przejściu R0-R3:

```text
PanelDigitNetV1 raw prediction
→ episode-level fusion
→ calibrated confidence
→ same-team roster lookup
→ existing evidence contract
→ existing subject consensus
→ operator review suggestion
```

Roster constraint działa po recognition:

```text
raw model: "10"
→ Team A roster lookup
→ unique player or unmatched/conflict
```

Model nie może być trenowany wyłącznie jako klasyfikator aktualnych roster players.

## Confidence

Przed drugim meczem zapisywać tylko:

```text
raw visual probabilities
raw digit probabilities
raw decoded sequence
```

Nie udawać kalibrowanego prawdopodobieństwa.

Po niezależnym validation:

```text
calibrated_read_confidence
calibration_version
threshold_version
```

---

# 10. Konkretne zadania dla agenta

Agent ma wykonać dokładnie poniższą sekwencję.

## J8.1 — freeze existing recognizers

- nie zmieniać template baseline;
- nie zmieniać centroid baseline;
- nie stroić CRNN;
- oznaczyć CRNN jako deferred diagnostic w raportach;
- brak zmian candidate/production identity.

## J8.2 — panel annotation contract

Dodać:

```text
number_panel_bbox_normalized
number_panel_artifact lub deterministic panel extraction
```

Zachować pełne provenance i source digests.

## J8.3 — panel readiness report

Dodać skrypt:

```text
backend/scripts/audit_identity_jersey_number_panels.py
```

Output:

```text
number_panel_dataset_readiness.json
number_panel_montage.jpg
```

Po tym milestone agent ma zatrzymać implementację i zapisać findings.

## J8.4 — PanelDigitNetV1

Dodać jeden moduł, np.:

```text
backend/app/services/identity_jersey_number_panel_digit_model.py
```

Nie tworzyć dodatkowego detectora cyfr, CRNN ani OCR frameworka.

## J8.5 — explicit overfit mode

Training CLI musi obsługiwać:

```text
--mode overfit-smoke
--sample-limit 16
--epochs 500
```

Raportować co najmniej:

```text
exact train sequence accuracy
visual train accuracy
null prediction count
per-position accuracy
```

Checkpoint nie może zostać uznany za udany bez pass gate R1.

## J8.6 — negative overfit i real10

Dodać R2 i R3 jako osobne raporty.

Real10 contract:

```text
frames 3509/3510/3512
→ one visibility episode
→ episode prediction "10"
→ one identity vote
→ no automatic assignment
```

## J8.7 — stop and report

Po R3 agent ma zakończyć pracę nad recognizerem i zwrócić:

```text
panel readiness summary
R1 result
R2 result
R3 result
real10 result
plain-shirt result
recommended next decision
```

Nie może automatycznie przejść do kolejnej architektury.

---

# 11. Testy wymagane w następnym commicie

## Contract tests

- panel bbox musi być znormalizowany i mieścić się w `[0,1]`;
- niepoprawny bbox blokuje sample;
- panel crop jest deterministyczny;
- panel artifact digest jest stabilny;
- visibility episode nie zmienia się przez liczbę adjacent frames;
- Team B/unknown team nie tworzy Team A roster suggestion.

## Model smoke tests

- model ma cztery heads: visual + 3 digit positions;
- output shapes są stabilne;
- one-digit, two-digit i three-digit target encoding;
- digit loss tylko dla readable labels;
- negative sample nie tworzy digit target;
- tiny synthetic overfit mechanicznie działa.

## Real quality regression

- real10 panel fixture jest widoczny w montage;
- R1 wykorzystuje real panel crops, nie font generowany przez ten sam kod;
- plain-shirt fixture nie daje confirmed number;
- accepted identity evidence pozostaje `null` w diagnostic benchmarku.

---

# 12. Czego agent nie ma teraz robić

Nie implementować:

- kolejnego CRNN/Transformer/OCR modelu;
- osobnego digit detectora bez digit bbox datasetu;
- beam search;
- kolejnych CTC regularizerów;
- nowego candidate integration;
- cross-subject propagation;
- production apply;
- kolejnych warstw schema/gate niezwiązanych z R0-R3;
- automatycznego generowania player stats z number predictions;
- confidence calibration na train set;
- losowego frame-level splitu;
- synthetic-font benchmarku jako dowodu jakości.

Nie zmieniać thresholdów tylko po to, aby real10 został zaakceptowany.

---

# 13. Kontynuacja Player ID roadmap

Jersey recognition i Player ID Stabilization mają działać równolegle.

## Teraz

```text
J8.2-J8.7 panel recognizer rescue
+
P1.22 Full-Match Operator Benchmark
```

P1.22 powinno zbierać:

```text
number visibility episodes
plain-shirt negatives
unreadable negatives
operator-confirmed panel bboxes
number suggestions shown/accepted/rejected
subjects resolved by number
tracklets resolved by number
review time spent/saved
```

## Po poprawnym number anchor

```text
operator-confirmed real player
→ approved number panel
→ approved appearance crops
→ roster-confirmed ReID prototype
→ ranking unresolved fragments
```

Na tym etapie nadal:

```text
no automatic cross-subject merge
no production identity write
```

---

# 14. Acceptance criteria

## Recognizer rescue complete

- [ ] tight panel montage zostało ręcznie obejrzane;
- [ ] median digit height po preprocessing wynosi minimum 8 px;
- [ ] real10 panel jest czytelny po dokładnie tym samym preprocessing;
- [ ] PanelDigitNetV1 R1 exact train accuracy >= 0.95;
- [ ] R2 negative specificity >= 0.95;
- [ ] real10 episode prediction = `10`;
- [ ] plain-shirt false confirmed reads = 0;
- [ ] raw prediction nie mutuje identity;
- [ ] agent kończy milestone raportem zamiast automatycznie zmieniać architekturę.

## Candidate review activation

- [ ] minimum 2 independent source matches;
- [ ] minimum 2 positive safe multi-tracklet propagations;
- [ ] 0 false strong subject consensuses;
- [ ] 0 false roster suggestions;
- [ ] 0 unexpected propagations;
- [ ] canonical production digests unchanged;
- [ ] operator review pozostaje wymagane.

## Production use

- [ ] minimum 3 evaluated source matches including held-out;
- [ ] P1.22 completed;
- [ ] candidate-only number assistance measurably reduces review;
- [ ] P1.23 stats validation passes;
- [ ] P1.24 transaction/backup/rollback implemented;
- [ ] no automatic cross-subject or cross-match identity promotion.

---

# 15. Wniosek końcowy

Aktualny agent nie utknął dlatego, że potrzebuje bardziej zaawansowanego OCR.

Utknął, ponieważ równolegle powstały:

```text
template matcher
whole-number centroid baseline
CRNN-CTC prototype
multiple evaluation contracts
```

bez wcześniejszego udowodnienia, że model dostaje tight, czytelny number panel i potrafi overfitować kilkanaście realnych przykładów.

Prawidłowa ścieżka jest teraz znacznie krótsza:

```text
tight panel annotation
→ montage/readiness audit
→ simple PanelDigitNetV1
→ tiny real-data overfit
→ negatives
→ real10
→ stop and report
```

Dopiero po tym można wrócić do generalizacji, confidence calibration i N5.8.

Najważniejsza decyzja:

```text
nie dodawać kolejnej architektury
nie stroić CTC w ciemno
najpierw udowodnić poprawność panel input + tiny overfit
```
