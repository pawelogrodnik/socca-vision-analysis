# Jersey Number Identity Anchors

## Status

```text
UZUPEŁNIENIE task-requests/PLAYER_IDENTITY_STABILIZATION_ROADMAP.md
SHADOW-FIRST / HIGH-CONFIDENCE IDENTITY EVIDENCE
N0-N5 ZAIMPLEMENTOWANE W SHADOW
NIEGOTOWE DO AUTOMATYCZNYCH CANDIDATE ANI PRODUCTION ASSIGNMENTS
```

Przeanalizowany baseline:

```text
d1ef613e1916a503f22607a035288edc432b95f9
```

Dokument opisuje rzeczywisty stan implementacji N0-N5 wraz z targeted benchmarkiem hard3m dodanym po pierwszym teście N5 na easy90.

Kierunek implementacji jest poprawny, ale feature nie jest jeszcze gotowym automatycznym recognizerem numerów ani źródłem candidate assignments. N0-N5 udowadniają obecnie, że ręcznie zweryfikowane evidence numeru można bezpiecznie przekształcić w roster suggestion i propagować po istniejącym, ścisłym lineage bez zmiany candidate ani produkcyjnego identity.

Feature nadal nie udowadnia, że system samodzielnie odczytuje numery z wideo z wystarczającą precyzją.

---

# 1. Założenia domenowe

- niepusty numer jest unikalny w obrębie jednej drużyny;
- ten sam numer może wystąpić w Team A i Team B;
- nie każdy zawodnik ma numer;
- zawodnik z numerem używa go przez cały mecz;
- kilku zawodników może grać w białych koszulkach bez numeru;
- `Team A + 92` może jednoznacznie wskazywać Pawła;
- `Team A + 15` może jednoznacznie wskazywać Piotrka.

Zaufany, unikalny numer jest silniejszym identity evidence niż ogólny appearance/ReID, ale nigdy nie omija ograniczeń drużyny, czasu, przestrzeni, struktury i lineage.

---

# 2. Aktualny stan N0-N5

## N0 — roster number registry

Zaimplementowano:

- opcjonalny numer dla zawodnika;
- jawnie potwierdzony brak numeru;
- normalizację numerów jedno-, dwu- i trzycyfrowych;
- unikalność numeru wewnątrz drużyny;
- wyłączenie zaufania przy duplikacie;
- możliwość tego samego numeru w różnych drużynach;
- brak mutacji rosteru przez wykryte evidence.

Status:

```text
ZAIMPLEMENTOWANE / SHADOW-ONLY / POPRAWNY KIERUNEK
```

## N1 — crop evidence i operator audit

Zaimplementowano:

- filtrowanie reliable anchor crops;
- torso ROI;
- galerię do ręcznego audytu;
- cztery stany evidence;
- odrzucanie cropów niskiej jakości;
- brak wyniku recognizera jako `number_unreadable`, nigdy `number_absent`.

Stany:

```text
number_confirmed
number_absent
number_unreadable
number_conflict
```

Najważniejsze ograniczenie:

```text
skalibrowany automatyczny recognizer/OCR numerów nie jest zaimplementowany
```

Obecne pozytywne evidence pochodzi z audytu operatora. N1 waliduje więc kontrakt danych i workflow ręcznego review, a nie automatyczne odczytywanie numerów.

Status:

```text
INFRASTRUKTURA ZAIMPLEMENTOWANA
AUTOMATYCZNY RECOGNIZER PENDING
```

## N2 — tracklet i subject consensus

Zaimplementowano:

- deterministyczny consensus per tracklet;
- deterministyczny consensus per candidate subject;
- domyślnie minimum trzy niezależne odczyty;
- minimalny odstęp klatek;
- próg confidence;
- lookup tylko do unikalnego numeru tej samej drużyny;
- konflikt przy mocnych sprzecznych odczytach;
- evaluator goldsetu z precision, recall, false positives i identity false assignments.

Wynik easy90:

```text
numbered goldset subjects:    17
strong consensus subjects:     4
poprawne strong consensus:      4
false positives:                0
identity false assignments:     0
precision:                    1.0
recall:                  0.235294
```

Interpretacja:

```text
precision: obiecująca
coverage: niskie
próbka: zbyt mała do aktywacji
```

## N3 — whole-subject review suggestion

Zaimplementowano:

- strong number consensus może ustawić rekomendowanego zawodnika;
- evidence numeru jest widoczne na karcie whole-subject review;
- słabe odczyty nie tworzą sugestii;
- rozbieżność z inną rekomendacją daje `jersey_number_roster_conflict`;
- konflikt blokuje one-click confirmation;
- operator review pozostaje wymagane.

Status:

```text
ZAIMPLEMENTOWANE / SHADOW REVIEW ASSISTANCE
```

## N4 — gated assignment plan

Zaimplementowano:

- jawne żądanie aktywacji;
- benchmark gate;
- lineage gate względem review i report artifacts;
- structural blockers;
- strictly eligible shadow candidates;
- brak zapisu candidate i production identity;
- `automatic_assignments = 0` nawet dla eligible row.

Status:

```text
SHADOW PLAN ZAIMPLEMENTOWANY
GATE CONTRACT WYMAGA WZMOCNIENIA PRZED CANDIDATE USE
```

## N5 — strict propagation po istniejącym lineage

Zaimplementowano:

- number-confirmed seed tracklet;
- propagację wyłącznie przez istniejące jawne transition edges;
- zgodność candidate/timeline subject membership;
- audyt ścieżki i liczby hopów;
- blokadę sprzecznego numeru;
- brak tworzenia krawędzi na podstawie podobieństwa numeru;
- brak merge trackletów;
- brak cross-subject propagation;
- brak automatycznych assignments.

Blokowane przypadki:

```text
uncertain_transition
cross_production_transition
temporal overlap
weak ReID-only edge
candidate/timeline tracklet mismatch
team mismatch
structural subject conflict
contradictory number evidence
```

Status:

```text
ZAIMPLEMENTOWANE W SHADOW
ZYSK COVERAGE WEWNĄTRZ SUBJECTU POTWIERDZONY NA JEDNYM TARGEcie
NIEGOTOWE DO AKTYWACJI
```

---

# 3. Wyniki benchmarków

## 3.1. Easy90 N0-N4

Lokalne artefakty:

```text
backend/storage/benchmarks/player_identity/n0-n4-jersey-number-easy90-20260721-v2
backend/storage/benchmarks/player_identity/n0-n4-jersey-number-easy90-20260722-goldset-evaluated
```

Raportowany wynik:

```text
evidence rows:             437
reliable rows:             331
rejected rows:             106
reliable Team A crops:     133
numbered goldset subjects:  17
strong consensus:            4
correct:                     4
precision:                 1.0
recall:               0.235294
```

Wynik potwierdza konserwatywny consensus na małej, ręcznie zweryfikowanej próbce. Nie waliduje automatycznego recognizera.

## 3.2. Easy90 N5

```text
backend/storage/benchmarks/player_identity/n5-jersey-number-propagation-easy90-20260722-v1
```

```text
seed subjects:              3
seed tracklets:             3
propagated tracklets:       0
cross-subject propagation:  0
automatic assignments:      0
```

Każdy eligible subject w easy90 zawierał tylko jeden tracklet. Test potwierdził brak fałszywego rozszerzenia identity, ale nie mógł zmierzyć zysku propagacji.

## 3.3. Targeted hard3m N5

Selekcja:

```text
backend/storage/benchmarks/player_identity/n5-jersey-number-hard3m-targeted-20260722-v1
```

Wynik po review:

```text
backend/storage/benchmarks/player_identity/n5-jersey-number-hard3m-targeted-reviewed-20260722-v1
```

Raportowana selekcja:

```text
multi-tracklet Team A subjects: 7
seed tracklets:                 7
selected crops:                25
final reliable audit crops:    22
hidden target tracklets:        8
confirmed number reads:         5
number_absent reads:            5
number_unreadable reads:       12
```

Raportowana ewaluacja:

```text
strong consensus subjects:              1
eligible hidden target tracklets:        1
matched eligible hidden targets:         1
eligible_target_recall:                1.0
unexpected propagated tracklets:         0
cross-subject propagations:              0
automatic assignments:                   0
safety passed:                         true
```

To pierwszy realny dowód, że N5 może zwiększyć coverage wewnątrz multi-tracklet candidate subjectu przy zachowaniu obecnych zasad bezpieczeństwa.

Próbka obejmuje jednak tylko jeden pozytywny eligible target. Nie wystarcza do candidate activation.

---

# 4. Decyzja architektoniczna dla N5

N5 oznacza:

```text
trusted number seed
→ strict accepted existing lineage edge
→ kolejny tracklet wewnątrz tego samego candidate subjectu
```

N5 nie jest cross-subject identity resolverem.

Obecne zachowanie jest celowe:

```text
cross-subject edge
→ blocked
```

Chroni to przed rozlaniem jednego błędnego OCR lub jednej słabej krawędzi grafu na niezależne subjecty.

Ewentualny future cross-subject number-assisted resolver musi być osobnym milestone’em z osobnym goldsetem, ograniczeniami i gate’em. Powinien zacząć jako ranking/review assistance, nie automatyczny merge.

---

# 5. Semantyka evidence

## `number_confirmed`

Kilka niezależnych, dobrych obserwacji wskazuje jeden zaufany numer istniejący unikalnie w rosterze tej samej drużyny.

## `number_absent`

Na czystej powierzchni koszulki widać rzeczywisty brak numeru.

`number_absent` nie identyfikuje konkretnego zawodnika, gdy więcej niż jedna osoba gra bez numeru.

## `number_unreadable`

Rozmycie, skala, orientacja ciała, jakość cropa, zasłonięcie albo brak wyniku recognizera uniemożliwiają odczyt.

```text
no OCR result != number_absent
```

## `number_conflict`

Przykłady:

```text
ten sam tracklet zawiera trusted 92 i trusted 15
subject Pawła 92 zawiera trusted 15
numer wskazuje Team A, ale identity evidence mówi Team B
numer jest zduplikowany w rosterze tej samej drużyny
```

Mocny konflikt numeru jest structural evidence i blokuje candidate/automatic assignment do czasu review lub remediation.

---

# 6. Kontrakt consensus

Domyślny strong consensus wymaga:

```text
known trusted team
+ unique trusted roster number
+ minimum 3 niezależne high-confidence reads
+ odczyty rozdzielone w czasie
+ reliable crop quality
+ brak mocnego konkurencyjnego numeru
+ brak structural identity conflict
```

Pojedynczy czysty crop może być evidence pomocniczym, ale nie tworzy strong consensus.

Dwa mocne sprzeczne numery muszą dawać `number_conflict`.

---

# 7. Wymagane poprawki przed candidate activation

Obecna implementacja nie jest fundamentalnie błędna. Poniższe punkty są potrzebne, aby kontrakt aktywacji był poprawny i mierzalny.

## N5.1 — wspólne structural blockers

Utworzyć jeden kanoniczny zestaw używany przez:

```text
P1.20A promotion safety
whole-subject review
N4 assignment plan
N5 propagation
candidate apply
```

Aktualne listy N4 i N5 nie są identyczne. Subject nie może być `strictly_eligible` w N4 i dopiero w N5 stać się zablokowany przez flagę nieobsługiwaną wcześniej.

Minimum:

```text
cross_production_transition
merges_production_subjects
parallel_distant_observation
parallel_roster_candidate_conflict
roster_identity_conflict
structural_identity_conflict
team_switch
temporal_overlap_conflict
uncertain_transition
jersey_number_roster_conflict
cross_team_evidence
```

## N5.2 — wzmocnienie N4 benchmark gate

N4 nie może przechodzić wyłącznie dlatego, że:

```text
identity_false_assignments == 0
```

Gate musi wymagać:

```text
identity_false_assignments == 0
false_positive == 0
precision == 1.0
minimalna liczba reviewed numbered subjects
minimalna liczba reviewed no-number subjects
minimalna liczba reviewed unreadable/negative subjects
minimum jeden held-out match
```

Minimalne liczby należy ustalić przed aktywacją i zapisać w raporcie, a nie dopasowywać po zobaczeniu wyniku.

## N5.3 — pełna walidacja lineage

N5 zapisuje digests bezpośrednich wejść, ale przed candidate use musi również sprawdzać ich wzajemną zgodność.

Wymagane:

```text
assignment consensus digest == aktualny consensus
assignment review digest == aktualny review artifact
consensus evidence digest == aktualne evidence
consensus roster digest == aktualny roster
candidate digest == oczekiwany candidate artifact
timeline digest == oczekiwany timeline artifact
algorithm/version/parameter digests są obecne
```

Stary assignment plan z nowym timeline lub evidence musi dawać:

```text
status = blocked
reason = stale_jersey_number_lineage
```

## N5.4 — osobna proweniencja seedów

Nie łączyć evidence numeru i potwierdzenia operatora w jeden nierozróżnialny seed set.

Raportować osobno:

```text
number_seed_tracklet_ids
operator_confirmed_tracklet_ids
number_propagated_tracklet_ids
operator_inherited_tracklet_ids
```

Whole-subject operator confirmation może potwierdzać membership, ale nie może zawyżać raportowanego zysku N5.

## N5.5 — automatyczny recognizer

Po ustabilizowaniu kontraktu galerii zaimplementować i ocenić recognizer:

```text
reliable torso/back crop
→ number-region proposal lub constrained torso ROI
→ digit/number recognizer
→ calibrated confidence
→ per-crop evidence
```

Nie używać unconstrained OCR na całej klatce ani pełnym player cropie.

Mierzyć osobno:

```text
readability classification precision
number/digit accuracy
numbered-player false positive rate
no-number-player false positive rate
subject consensus precision
subject consensus coverage
identity false assignments
```

Najważniejszy negatywny przypadek:

```text
biała koszulka bez numeru
→ recognizer halucynuje 92
→ błędne przypisanie Pawła
```

## N5.6 — wersjonowane lekkie benchmark artifacts

`backend/storage/**` jest ignorowane przez Git. Lokalne podsumowania benchmarków nie są więc niezależnie dostępne z repo.

Duże cropy i wideo pozostają lokalne, ale lekkie raporty należy commitować, np.:

```text
backend/benchmarks/player_identity/jersey-number/easy90-v1/
backend/benchmarks/player_identity/jersey-number/hard3m-targeted-v1/
```

Minimalny zestaw:

```text
benchmark_manifest.json
goldset_summary.json
consensus_report.json
assignment_gate_report.json
propagation_report.json
targeted_evaluation.json
source commit i input digests
```

## N5.7 — CI

Dodać lekkie testy CI dla:

```text
roster uniqueness
no-read vs no-number semantics
consensus conflicts
N4 benchmark gate
stale lineage blocking
N5 safe-edge propagation
N5 unsafe-edge blocking
targeted hidden-tracklet evaluation
determinism i input immutability
```

Ciężka ewaluacja modelu/cropów może pozostać manualna, ale frozen JSON contract tests powinny działać w CI.

---

# 8. Kolejność dalszej implementacji

```text
N5.1  canonical blockers
N5.2  benchmark gate hardening + negative goldset
N5.3  full lineage validation
N5.4  seed provenance metrics
N5.5  automatic recognizer calibration
N5.6  tracked lightweight benchmark reports
N5.7  CI contract coverage
N5.8  held-out multi-match shadow benchmark
N5.9  controlled candidate-only integration
```

Nie czekać z recognizerem i propagacją do production apply. Testować je w shadow w ramach pełnomaczowych benchmarków P1.22.

---

# 9. Candidate-only activation gate

Jersey-number evidence może wpływać na candidate identity dopiero po spełnieniu wszystkich warunków:

```text
trusted same-team unique roster number
multi-frame strong consensus
0 identity false assignments
0 false positives
negative no-number sample included
held-out match included
fresh full lineage
0 structural blockers
0 contradictory trusted number
0 cross-team evidence
0 temporal overlap conflict
0 parallel distant observation
0 unexpected propagated target
production artifacts unchanged
```

Pierwsza aktywacja pozostaje candidate-only.

Może:

```text
dodać candidate roster suggestion
oznaczyć candidate tracklet jako number-propagated
obniżyć priorytet manual review
```

Nie może:

```text
zapisać production assignments
publikować player stats
scalać niezależnych subjectów
tworzyć nowej krawędzi lineage z podobieństwa numeru
```

---

# 10. Metryki

## Recognition

```text
reliable crops evaluated
readable-number precision
number accuracy
digit accuracy
false number on plain shirt
number_absent precision
number_unreadable rate
```

## Consensus

```text
numbered goldset subjects
strong consensus subjects
subject precision
subject recall
false positives
identity false assignments
number conflicts
```

## Propagation

```text
number seed tracklets
operator seed tracklets
eligible hidden targets
matched hidden targets
unexpected propagated tracklets
eligible target recall
coverage frames added
subjects with real propagation benefit
cross-subject propagations
automatic assignments
```

## Product

```text
additional subjects correctly suggested
additional tracklets correctly resolved
manual review cards avoided
manual review time saved
false roster assignments caused by number evidence
```

Wysoka precision jest ważniejsza niż coverage.

---

# 11. Acceptance criteria

## Zaimplementowane w shadow

- [x] roster wspiera opcjonalny, unikalny numer per team;
- [x] duplikaty numeru wyłączają trust;
- [x] `number_absent` różni się od `number_unreadable`;
- [x] brak recognizera oznacza unreadable;
- [x] tylko reliable torso crops trafiają do audytu;
- [x] consensus wymaga wielu niezależnych odczytów;
- [x] mocne konflikty blokują consensus;
- [x] numer mapuje tylko do unikalnego zawodnika tej samej drużyny;
- [x] gracz bez numeru nie jest identyfikowany przez brak OCR;
- [x] strong consensus wspiera whole-subject review;
- [x] N4 nie zapisuje assignments;
- [x] N5 używa tylko istniejących jawnych lineage edges;
- [x] N5 nie tworzy cross-subject edges;
- [x] N5 blokuje uncertain, overlap, cross-production i weak ReID-only;
- [x] targeted hard3m pokazał jedną poprawną hidden-tracklet propagation;
- [x] w raportowanej próbce nie było unexpected propagation;
- [x] produkcyjne identity, stats i heatmaps pozostają niezmienione.

## Wymagane przed candidate activation

- [ ] wspólny canonical structural blocker set;
- [ ] N4 gate uwzględnia false positives i no-number negatives;
- [ ] N5 waliduje pełne lineage;
- [ ] number seeds i operator seeds są mierzone osobno;
- [ ] recognizer jest skalibrowany na front/back torso crops;
- [ ] mierzony jest no-number hallucination rate;
- [ ] lekkie raporty benchmarków są wersjonowane w Git;
- [ ] frozen contract tests działają w CI;
- [ ] held-out match przechodzi bez identity false assignments;
- [ ] oceniono więcej niż jeden pozytywny multi-tracklet propagation;
- [ ] candidate integration jest odwracalne i production-safe.

## Wymagane przed production use

- [ ] candidate-only integration realnie redukuje review;
- [ ] accepted multi-match benchmark ma 0 false automatic roster assignments;
- [ ] nie występuje unexpected propagation;
- [ ] production promotion używa transaction/backup/rollback z głównej roadmapy;
- [ ] readiness rozróżnia identity, stats i niedostępne optional inputs.

---

# 12. Anti-goals

Nie należy:

- traktować jednego cropa jako automatyczne identity;
- traktować braku OCR jako koszulki bez numeru;
- identyfikować no-number playera wyłącznie przez brak numeru;
- tworzyć lub scalać trackletów na podstawie podobnego numeru;
- propagować przez uncertain lub overlap transitions;
- otwierać cross-subject propagation wewnątrz N5;
- stroić progów tylko pod easy90 i reviewed hard3m;
- traktować `1/1` jako production validation;
- aktywować N4 wyłącznie na podstawie jednego małego goldsetu;
- publikować candidate identity lub stats;
- commitować raw video i duże galerie cropów tylko po to, aby wersjonować benchmark.

---

# 13. Wniosek końcowy

Aktualne N0-N5 to poprawny, konserwatywny prototyp shadow.

Targeted hard3m zamyka największą lukę pierwszego benchmarku N5: pokazuje, że trusted number seed może odzyskać hidden tracklet wewnątrz multi-tracklet subjectu.

Dowód jest nadal minimalny:

```text
1 eligible positive target
1 matched target
0 unexpected targets
```

Prawidłowy status projektu:

```text
architektura: poprawna
shadow safety: obiecujące
intra-subject coverage benefit: potwierdzony na minimalnej próbce
automatic recognition: niezaimplementowane
candidate activation: zablokowana do hardeningu i held-out benchmarku
production activation: niedozwolona
```

Następny rozwój powinien skupić się na poprawności gate’ów, automatycznym recognizerze i multi-match evidence, a nie na kolejnym ogólnym ReID score ani cross-subject propagation.