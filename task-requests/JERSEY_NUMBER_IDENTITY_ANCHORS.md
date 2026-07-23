# Jersey Number Identity Anchors

## Status

```text
UZUPEŁNIENIE task-requests/PLAYER_IDENTITY_STABILIZATION_ROADMAP.md
SHADOW-FIRST / HIGH-CONFIDENCE IDENTITY EVIDENCE
N0-N5 ORAZ INFRASTRUKTURA N5.1-N5.8 ZAIMPLEMENTOWANE
DATASET NUMERÓW JEST DOSTĘPNY
LEARNED RECOGNIZER NIE JEST JESZ SKALIBROWANY ANI AKTYWOWANY
CANDIDATE I PRODUCTION ASSIGNMENTS POZOSTAJĄ ZABLOKOWANE
```

Przeanalizowany baseline:

```text
425da4cec50603d87af97fad3e19c1f614a9696f
```

Najważniejsza decyzja:

> Jersey number jest jednym z najsilniejszych dostępnych identity anchors i warto rozwijać go dalej. Nie należy jednak zatrzymywać całej stabilizacji Player ID do czasu osiągnięcia idealnego OCR. Krótki recognizer closeout i trening na istniejącym datasecie powinny działać równolegle z P1.22 Full-Match Operator Benchmark.

Aktualna implementacja jest poprawna jako konserwatywny, bezpieczny pipeline shadow. Nie jest jeszcze domknięta jako automatyczne odczytywanie numerów z realnego wideo.

---

# 1. Założenia domenowe

- niepusty numer jest unikalny w obrębie jednej drużyny;
- ten sam numer może wystąpić w Team A i Team B;
- nie każdy zawodnik ma numer;
- kilku zawodników może grać w koszulkach bez numeru;
- zawodnik z numerem używa tego samego numeru przez cały mecz;
- numer może być widoczny tylko w niewielkiej części trackletów;
- brak odczytu nie oznacza braku numeru;
- jeden poprawny number anchor może być bardziej wartościowy niż wiele słabych pairwise ReID scores.

Przykład:

```text
trusted Team A + numer 10
→ jednoznaczny roster player
→ potwierdzony candidate subject
→ safe lineage propagation wewnątrz subjectu
→ operator-approved crops do przyszłego roster-confirmed ReID prototype
```

Numer nigdy nie omija:

```text
team constraints
temporal overlap constraints
parallel-position constraints
structural blockers
lineage freshness
operator review, gdy wymagane
```

---

# 2. Aktualny stan implementacji

## N0 — roster number registry

Zaimplementowano:

- opcjonalny numer per roster player;
- jawnie potwierdzony brak numeru;
- normalizację numerów;
- unikalność numeru wewnątrz drużyny;
- wyłączenie trust przy duplikacie;
- możliwość tego samego numeru w różnych drużynach;
- brak mutacji rosteru przez evidence.

Status:

```text
IMPLEMENTED / SHADOW-SAFE
```

## N1 — crop evidence i operator audit

Zaimplementowano:

- reliable anchor crops;
- torso ROI;
- operator audit gallery;
- stany `number_confirmed`, `number_absent`, `number_unreadable`, `number_conflict`;
- odrzucanie cropów niskiej jakości;
- brak recognizera jako `number_unreadable`, nigdy `number_absent`.

Status:

```text
IMPLEMENTED
```

## N2 — tracklet i subject consensus

Zaimplementowano:

- deterministic consensus per tracklet i candidate subject;
- minimum independent reads;
- visibility episodes;
- frame separation;
- confidence thresholds;
- same-team unique roster lookup;
- conflict handling;
- goldset precision/recall/false-positive evaluation.

Status:

```text
IMPLEMENTED / CONSERVATIVE
```

## N3 — whole-subject review suggestion

Zaimplementowano:

- strong number consensus jako roster recommendation;
- evidence numeru w review card;
- konflikt z inną rekomendacją jako blocker;
- brak automatycznego potwierdzenia operatora.

Status:

```text
IMPLEMENTED / REVIEW ASSISTANCE
```

## N4 — gated assignment plan

Zaimplementowano:

- benchmark gate;
- lineage gate;
- canonical structural blockers;
- strictly eligible shadow candidates;
- brak zapisu candidate i production identity;
- `automatic_assignments = 0`.

Status:

```text
IMPLEMENTED / SHADOW-ONLY
```

## N5 — strict lineage propagation

Zaimplementowano:

- number-confirmed seed tracklet;
- propagację tylko po istniejących accepted lineage edges;
- brak nowych krawędzi z podobieństwa numeru;
- brak tracklet merge;
- brak cross-subject propagation;
- osobną proweniencję number/operator seeds;
- pełny edge/path audit;
- stale lineage blocking;
- contradictory number blocking.

Status:

```text
IMPLEMENTED / SHADOW-ONLY
```

## N5.5 — aktualny recognizer v0.2

Obecny recognizer:

```text
anchor crop
→ usunięcie selection padding
→ constrained upper-person ROI
→ jasny komponent koszulki
→ ciemny digit mask
→ roster-constrained OpenCV template score
→ temporal episode voting
```

Posiada:

- single-frame confirmation disabled;
- temporal episode consensus;
- panel diagnostics;
- panel precision/recall;
- readability precision/recall;
- false-negative metrics;
- plain-shirt false-positive metrics;
- brak automatycznych assignments.

Ograniczenia:

- template jest generowany `cv2.FONT_HERSHEY_SIMPLEX`;
- aktualny panel detector jest zoptymalizowany głównie pod jasną koszulkę i ciemny nadruk;
- realne fonty, perspektywa, fałdy i mały rozmiar cyfr nie są dobrze modelowane;
- recognizer nie jest jeszcze wytrenowany na dostępnym datasecie;
- wynik realnego materiału pokazał false negative dla numeru widocznego człowiekowi.

Status:

```text
IMPLEMENTED AS DIAGNOSTIC BASELINE
NOT THE TARGET RECOGNIZER
```

## N5.8 — held-out validation infrastructure

Zaimplementowano:

- canonical held-out case contract;
- minimum distinct source matches;
- clips jednego meczu nie liczą się jako osobne matches;
- production identity before/after SHA-256 comparison;
- required recognizer/assignment/propagation/targeted artifacts;
- candidate integration zablokowane bez pozytywnego held-out suite.

Status:

```text
INFRASTRUCTURE IMPLEMENTED
REAL MULTI-MATCH VALIDATION PENDING
```

---

# 3. Najnowszy real-video benchmark: match 07d227bd

Źródło:

```text
analysis job: analysis-20260722T101544Z-3846235e
match:        07d227bd
run:          20260722T102708Z-yolo-ultralytics-chunked-3a1e7bac
duration:     215.849 s
device:       Apple MPS
scope:        within-match shadow validation
```

## Selection correction

Pierwszy selector traktował dowolne trzy cropy jako potencjalny seed. Było to niezgodne z downstream consensus, który wymaga independent visibility episodes.

Po poprawce:

```text
potential multi-tracklet Team A subjects:       15
consensus-eligible subjects:                     4
rejected for insufficient independent evidence: 11
selected seed crops:                            15
hidden target tracklets:                         4
```

Ta poprawka jest właściwa. Selector i consensus muszą używać tego samego kontraktu temporal independence.

## Recognition result

```text
recognizer evaluated crops:          15
reliable evidence rows:              13
rejected evidence rows:               2
automatically confirmed reads:        0
strong subject consensuses:            0
number-propagated tracklets:           0
```

Manual spot check:

```text
tracklet: 100304:1
frames:   3509, 3510, 3512
number:   10
result:   recognizer marked all unreadable
```

Klatki 3509/3510/3512 poprawnie tworzą jeden visibility episode. Nie mogą zostać policzone jako trzy independent identity reads.

Wniosek:

```text
safety:        passed
recognizer:    false-negative fixture found
recall:        too low for activation
propagation:   correctly remained zero
production:    unchanged
```

Ten benchmark nie jest porażką. Dostarcza pierwszy realny fixture, na którym target recognizer musi umieć odczytać numer `10` bez sztucznego obniżania progów.

---

# 4. Dataset numerów — nowy punkt startowy

Dataset już istnieje. Następny etap nie powinien więc polegać na dalszym ręcznym tuningu font templates. Najpierw należy zinwentaryzować dataset i zbudować reprodukowalny training/evaluation contract.

## 4.1. Dataset manifest

Dodać wersjonowany manifest zawierający:

```text
dataset_id
dataset_version
source matches
source videos
source frame ranges
annotation format
numbered samples
no-number samples
unreadable samples
front/back/side distribution
number frequency distribution
one/two/three-digit distribution
team/kit profile distribution
resolution distribution
train/validation/test split
held-out source-match keys
content digest
```

Nie trzeba commitować pełnego datasetu do repo. Commitować manifest, schema, split IDs i metryki.

## 4.2. Split bez leakage

Nie dzielić losowo sąsiednich klatek.

Wymagane:

```text
visibility episode nie może wystąpić w więcej niż jednym splicie
tracklet nie może wystąpić w więcej niż jednym splicie
najlepiej cały source match należy do jednego splitu
held-out test musi zawierać niezależny source match
```

Sąsiednie klatki tego samego numeru są prawie duplikatami. Random frame split da sztucznie zawyżony wynik.

## 4.3. Minimalne klasy semantyczne

Dataset/evaluator musi rozróżniać:

```text
number_readable
number_partially_readable
number_absent
number_unreadable
not_a_jersey_panel
```

Dla `number_readable` przechowywać:

```text
number string
visible digits
front/back/side
occlusion
blur
perspective
pixel height of digit/number panel
team/kit profile
source match / tracklet / visibility episode
```

## 4.4. Negatywne próbki są obowiązkowe

Najważniejsze negatives:

```text
biała koszulka bez numeru
logo lub sponsor wyglądający jak cyfra
fałda koszulki
ręka lub noga przecinająca panel
ciemny element tła wewnątrz cropa
front koszulki bez numeru
inny zawodnik częściowo w cropie
nieczytelny, bardzo mały numer
```

Dataset nie może składać się głównie z dobrych, centralnych cropów numerów.

---

# 5. Target recognizer V1

## 5.1. Nie traktować template matcher jako finalnego modelu

Aktualny template matcher pozostaje:

```text
diagnostic baseline
regression comparison
optional fallback evidence
```

Nie powinien być głównym recognizerem po dostępności datasetu.

## 5.2. Wybór architektury zależy od anotacji datasetu

### Gdy dataset ma bboxy pojedynczych cyfr

Preferowany wariant:

```text
number-panel detector
→ digit detector 0-9
→ sortowanie cyfr left-to-right
→ sequence assembly
→ calibrated confidence
```

Zalety:

- wspiera numery niewidziane jako pełna klasa;
- naturalnie obsługuje 1-3 cyfry;
- daje explainable per-digit confidence;
- pozwala wykryć częściowy numer.

### Gdy dataset ma tylko label całego numeru

Preferowany wariant:

```text
number-panel crop
→ small sequence recognizer / CRNN-CTC
→ digit string
→ calibrated confidence
```

Nie budować osobnej klasy dla każdego numeru rosteru jako jedynego modelu. Taki model nie generalizuje do nowych numerów i może nauczyć się wyglądu konkretnego zawodnika zamiast cyfr.

### Roster constraint

Roster powinien działać po recognition:

```text
recognized digit string
→ same-team trusted roster lookup
```

Nie używać whole-person appearance ani roster player identity jako skrótu do przewidywania numeru.

## 5.3. Team-scoped candidates

Obecny recognizer tworzy listę trusted numerów z całego rosteru. Należy zmienić kontrakt:

```text
trusted Team A subject
→ tylko Team A roster numbers

trusted Team B subject
→ tylko Team B roster numbers

unknown team
→ brak roster-confirmed identity anchor
```

Numer innej drużyny nie może obniżać score margin ani stać się candidate’em dla subjectu.

## 5.4. Kit profiles

Jawnie wspierać profile:

```text
bright_jersey_dark_number
dark_jersey_bright_number
custom_team_profile
unknown_profile
```

Aktualny `bright_jersey_dark_number` może pozostać preprocessor baseline. Inne profile wymagają osobnej normalizacji albo learned panel detectora.

## 5.5. Front/back/side

Rozróżniać orientację:

```text
back:    najwyższy trust
front:   podwyższony próg / większe ryzyko logo i sponsora
side:    partial evidence
unknown: conservative unreadable
```

Front nie może mieć takiego samego activation gate jak czysty back view bez osobnego benchmarku.

---

# 6. Temporal evidence: dwa różne poziomy consensus

Należy rozdzielić:

## 6.1. Episode-level visual fusion

Sąsiednie klatki mogą poprawić jeden odczyt:

```text
frames 3509, 3510, 3512
→ alignment
→ sharp-frame selection / temporal fusion
→ jeden episode-level number prediction
```

Dozwolone techniki:

```text
wybór najostrzejszej klatki
median/weighted fusion po alignment
multi-frame logits aggregation
best-view selection
```

## 6.2. Identity-level independence

Ten sam episode nadal liczy się jako jeden independent read:

```text
3 adjacent frames
→ 1 visual episode
→ 1 identity vote
```

Strong subject consensus nadal wymaga kilku niezależnych episode’ów rozdzielonych w czasie.

Nie mieszać:

```text
multi-frame evidence used to read one number
```

z:

```text
multiple independent sightings used to assign identity
```

---

# 7. Confidence contract

Aktualny confidence:

```text
best_score * (0.5 + score_margin)
```

nie jest skalibrowanym prawdopodobieństwem i może być niezgodny z downstream thresholdami `0.80` i `0.90`.

Wprowadzić osobne pola:

```json
{
  "raw_shape_score": 0.0,
  "raw_score_margin": 0.0,
  "episode_support": 0,
  "episode_competing_votes": 0,
  "model_confidence": 0.0,
  "calibrated_read_confidence": 0.0,
  "calibration_version": "..."
}
```

Downstream evidence i consensus powinny używać wyłącznie:

```text
calibrated_read_confidence
```

Kalibrację wykonać na validation set, np. temperature scaling, isotonic regression albo jawny threshold table. Wybrana metoda musi być zapisana w artifact metadata.

Nie obniżać thresholdów tylko po to, aby uzyskać pierwszy pozytywny wynik.

---

# 8. Natychmiastowe poprawki kontraktu

Przed kolejnym held-out benchmarkiem naprawić:

## 8.1. Observation source

Recognizer powinien emitować:

```json
{
  "source": "automatic_jersey_recognizer",
  "recognizer_version": "...",
  "model_digest": "...",
  "dataset_digest": "..."
}
```

Evidence nie może oznaczać automatycznej obserwacji jako `not_run`.

## 8.2. False-read double counting

`false_number_on_plain_shirt` jest podzbiorem false positive reads i nie może być ponownie dodawany do ogólnej liczby błędów.

Raportować:

```text
false_confirmed_reads_total
false_confirmed_reads_numbered_player
false_confirmed_reads_plain_shirt
```

`total` ma być liczbą unikalnych błędnych observations/episodes, nie sumą nakładających się kategorii.

## 8.3. Canonical production comparison

Candidate integration nie powinno przyjmować gołego:

```text
production_identity_unchanged: bool
```

Powinno przyjmować canonical held-out case/production artifact comparison i samodzielnie sprawdzać jego digest oraz status.

## 8.4. Real regression fixture

Dodać fixture bazujący na realnym przypadku:

```text
match 07d227bd
tracklet 100304:1
frames 3509, 3510, 3512
expected number 10
one visibility episode
```

Wersjonować mały panel crop lub zanonimizowaną maskę/feature fixture, nie całe wideo.

Test powinien wymagać:

```text
number 10 read at episode level
1 visibility episode
not 3 independent identity reads
no automatic assignment
```

Syntetyczny test wygenerowany tym samym fontem co matcher pozostaje unit testem mechaniki, ale nie jest recognizer quality testem.

---

# 9. Metryki recognizera

Raportować na crop, episode, subject i identity level.

## Crop/panel level

```text
panel precision
panel recall
readability precision
readability recall
number accuracy
digit accuracy
partial-number rate
plain-shirt hallucination rate
unreadable rate
```

## Visibility episode level

```text
episodes reviewed
episodes with readable number
correct episode reads
wrong episode reads
episode precision
episode recall
competing-number episodes
```

## Subject level

```text
subjects with trusted number consensus
correct strong consensuses
false strong consensuses
subject precision
subject recall
number conflicts
```

## Identity/product level

```text
correct roster suggestions
false roster suggestions
subjects resolved by number
tracklets resolved by number
frames added by propagation
review cards avoided
review seconds saved
false assignments caused by number evidence
```

Najważniejszy gate nie brzmi:

```text
raw crop model has no mistakes
```

lecz:

```text
0 false strong subject consensuses
0 false roster suggestions
0 false propagated identity
```

Jednocześnie każdy false confirmed episode read na held-out materiale musi blokować pierwszą candidate activation do czasu analizy i ponownej kalibracji.

---

# 10. Benchmark i activation gates

## 10.1. Dataset offline benchmark

Minimum:

```text
match-level held-out split
front/back/side metrics
numbered/no-number/unreadable metrics
one/two/three-digit metrics
kit-profile metrics
real-video regression fixture 07d227bd
```

## 10.2. N5.8 multi-match shadow benchmark

Minimum:

```text
minimum 2 independent source matches for candidate-review experiment
minimum 1 additional held-out source match before production consideration
minimum 2 positive multi-tracklet propagations
0 false strong subject consensuses
0 false roster suggestions
0 unexpected propagated targets
0 cross-subject propagations
0 automatic assignments
production identity hashes unchanged
```

Kilka clipów tego samego meczu nadal liczy się jako jeden source match.

## 10.3. Candidate-only activation

Pierwsza aktywacja może wyłącznie:

```text
pokazać roster suggestion do operator review
pokazać number evidence i episode crops
oznaczyć safe intra-subject propagated tracklets
obniżyć priorytet ręcznego review po potwierdzeniu operatora
```

Nie może:

```text
zapisać player_identity_assignments.json
publikować statystyk
scalać niezależnych subjectów
tworzyć nowych lineage edges
uruchamiać cross-match auto identity
```

---

# 11. Integracja z Player Identity Stabilization

Jersey-number work nie powinien dalej blokować głównej roadmapy.

## Teraz

Wykonać krótki dataset-driven recognizer closeout:

```text
J1 dataset audit + manifest
J2 leak-safe match-level splits
J3 learned recognizer baseline
J4 episode-level temporal fusion
J5 confidence calibration
J6 real fixture + held-out offline evaluation
J7 shadow rerun on 07d227bd
```

## Równolegle

Kontynuować:

```text
P1.22 Full-Match Operator Benchmark
```

P1.22 powinno zbierać dodatkową telemetry:

```text
number_suggestions_shown
number_suggestions_accepted
number_suggestions_rejected
number_conflicts
subjects_resolved_by_number
tracklets_resolved_by_number
frames_added_by_number_propagation
review_seconds_spent_on_number_evidence
estimated_review_seconds_saved
```

Każdy mecz P1.22 powinien jednocześnie dostarczać:

```text
number visibility episodes
plain-shirt negatives
unreadable negatives
held-out recognizer evaluation
N5.8 case package
```

## Po P1.22

Gdy number anchor zostanie potwierdzony przez operatora:

```text
real player
→ approved number-confirmed crops
→ approved appearance crops
→ roster-confirmed ReID prototype
→ ranking unresolved fragments
```

To odpowiada P2.1 z głównej roadmapy.

Nie uruchamiać automatycznego cross-subject merge. Number-confirmed ReID najpierw służy tylko do rankingu i review assistance.

---

# 12. Rekomendowana kolejność implementacji

```text
1. Audit istniejącego datasetu i annotation schema
2. Zbuduj leak-safe train/validation/held-out splits per source match
3. Napraw team-scoped roster candidates
4. Napraw observation_source i false-read double counting
5. Zdefiniuj calibrated confidence contract
6. Wytrenuj learned recognizer baseline odpowiedni do annotations
7. Dodaj episode-level temporal fusion
8. Dodaj real regression fixture number 10
9. Uruchom offline held-out benchmark
10. Uruchom shadow benchmark ponownie na 07d227bd
11. Rozpocznij/ kontynuuj P1.22 na kolejnych meczach
12. Wypełnij N5.8 multi-match suite
13. Dopiero wtedy rozważ N5.9 candidate review suggestions
14. Po P1.22 rozważ P2.1 roster-confirmed ReID prototypes
```

---

# 13. Acceptance criteria

## Zaimplementowane

- [x] optional unique jersey number per team;
- [x] duplicate same-team number disables trust;
- [x] `number_absent` differs from `number_unreadable`;
- [x] operator audit contract;
- [x] independent visibility episode contract;
- [x] tracklet/subject consensus;
- [x] same-team unique roster lookup downstream;
- [x] canonical structural blockers;
- [x] full lineage validation;
- [x] separate number/operator seed provenance;
- [x] safe intra-subject propagation;
- [x] no cross-subject propagation;
- [x] no automatic assignments;
- [x] N5.8 canonical case infrastructure;
- [x] dataset numerów jest dostępny;
- [x] real-video false-negative fixture został zidentyfikowany.

## Wymagane przed candidate review activation

- [ ] dataset manifest i version digest;
- [ ] match-level leak-safe splits;
- [ ] learned recognizer trained and versioned;
- [ ] team-scoped roster candidate list;
- [ ] calibrated confidence used by evidence/consensus;
- [ ] automatic observation source/version metadata;
- [ ] false-read metrics do not double count;
- [ ] real `10` regression fixture passes at episode level;
- [ ] plain-shirt negative benchmark passes;
- [ ] front/back/side metrics reported;
- [ ] minimum 2 independent source matches in N5.8;
- [ ] minimum 2 positive safe multi-tracklet propagations;
- [ ] 0 false strong subject consensuses;
- [ ] 0 false roster suggestions;
- [ ] 0 unexpected propagations;
- [ ] production identity unchanged by digest;
- [ ] candidate integration consumes canonical held-out contract, not a manual boolean.

## Wymagane przed production use

- [ ] minimum 3 evaluated source matches including held-out;
- [ ] P1.22 operator benchmark completed;
- [ ] candidate-only number assistance measurably reduces review;
- [ ] 0 known false assignments after operator review;
- [ ] P1.23 candidate stats validation passes;
- [ ] controlled transaction/backup/rollback from P1.24;
- [ ] downstream freshness rebuilt after apply;
- [ ] no automatic cross-subject or cross-match identity promotion.

---

# 14. Anti-goals

Nie należy:

- kontynuować ręcznego strojenia OpenCV font templates jako głównej strategii po dostępności datasetu;
- dzielić datasetu losowo per frame;
- liczyć sąsiednich klatek jako niezależnych identity reads;
- obniżać progów tylko po to, aby uzyskać pierwszy positive;
- używać whole-person appearance jako etykiety numeru;
- ograniczać learned model do osobnej klasy każdego roster number bez generalizacji cyfr;
- traktować braku OCR jako `number_absent`;
- identyfikować gracza bez numeru na podstawie samego braku numeru;
- tworzyć krawędzi lub scalać subjectów z samego number match;
- aktywować N5.9 przed held-out multi-match validation;
- blokować P1.22 do czasu idealnego jersey OCR;
- przechodzić od number anchor bezpośrednio do production Player ID;
- traktować template synthetic tests jako dowodu real-video quality.

---

# 15. Wniosek końcowy

Aktualny feature jest domknięty jako:

```text
bezpieczna architektura shadow
manualny high-confidence number anchor
conservative consensus i propagation
held-out validation infrastructure
```

Nie jest domknięty jako:

```text
automatyczne odczytywanie numerów z realnego wideo
skalibrowany learned recognizer
multi-match validated candidate suggestion source
```

Dataset usuwa najważniejszą przeszkodę do kolejnego etapu. Następna iteracja powinna być dataset-driven, nie template-driven.

Prawidłowy rozwój:

```text
learned jersey recognizer
+ conservative episode/subject consensus
+ operator-confirmed number anchors
+ roster-confirmed ReID ranking
+ P1.22 full-match validation
```

Jersey number może znacząco zwiększyć liczbę trackletów przypisanych do faktycznego gracza nawet przy umiarkowanym recall. Warunkiem pozostaje bardzo wysoka precision i jawne `unreadable` zamiast agresywnego zgadywania.