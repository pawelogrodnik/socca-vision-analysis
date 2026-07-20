# Player Identity Stabilization Roadmap

## 0. Cel dokumentu

Celem tego dokumentu jest zaprojektowanie takiego flow identyfikacji zawodników, aby po każdym meczu operator nie musiał ręcznie przypisywać setek pojedynczych cropów do zawodników.

Docelowy workflow powinien wyglądać następująco:

```text
14 zawodników przypisanych raz do stabilnych subjectów
+ kilka lub kilkanaście problematycznych zdarzeń do potwierdzenia
+ kilka orphan trackletów do ręcznego przypisania
```

Zamiast obecnego modelu mentalnego:

```text
crop → zgadnij zawodnika
```

należy przejść do:

```text
raw detections
→ krótkie raw tracklets
→ automatyczne stitching i offline identity resolution
→ około 14 stable subjects
→ roster assignment raz na stable subject
→ manual review tylko switch events i unresolved fragments
```

Najważniejszy cel produktowy:

```text
manual review time < 15 minut na mecz
```

przy jednoczesnym zachowaniu konserwatywnego podejścia do niepewnych danych.

## 0.1. Status realizacji — 2026-07-16

Legenda:

```text
DONE    — wdrożone i zweryfikowane benchmarkiem
SHADOW  — działa pasywnie i nie zmienia produkcyjnego identity
PARTIAL — wdrożona jest diagnostyka lub kontrakt, ale nie produkcyjna decyzja
TODO    — jeszcze niewdrożone
```

### P0 — shadow diagnostics: DONE

Wdrożono pasywną diagnostykę uruchamianą po `conservative_identity_v2`:

- klasyfikację trackletów: `trusted`, `recoverable`, `ambiguous`, `duplicate`, `noise`;
- reliability trackletów: occlusion, footpoint, appearance i inside-pitch ratios;
- grupowanie overlapów w explicit occlusion events;
- raport fragmentacji, RAW/ambiguous time, suspected switches i duplicate conflicts;
- bezpieczny tryb best-effort: błąd diagnostyki nie przerywa analizy;
- deterministyczne artefakty i test braku mutacji wejść.

Nowe artefakty:

```text
identity_tracklet_quality.json
identity_occlusion_events.json
identity_fragmentation_report.json
```

Implementacja:

```text
backend/app/services/identity_diagnostics.py
backend/tests/test_identity_diagnostics.py
```

### P0.5 — shadow stitching candidates: DONE / SHADOW

Wdrożono pasywny scorer możliwych kontynuacji trackletów:

- kierunkowe krawędzie `source tracklet -> target tracklet`;
- twarde blokady dla konfliktu czasu, drużyny, roli, niemożliwej prędkości,
  zbyt dużej odległości i trackletów głównie poza boiskiem;
- explainable cost components dla czasu, pozycji, ruchu, appearance, bbox profile
  i jakości trackletu;
- bonus za ciągłość raw tracker ID i wspólny occlusion event;
- konserwatywne rekomendacje, alternatywy i margin confidence;
- ewaluację względem aktualnego stable subjectu wyłącznie jako diagnostykę.

Nowy artefakt:

```text
identity_stitching_candidates.json
```

Implementacja:

```text
backend/app/services/identity_stitching_shadow.py
backend/tests/test_identity_stitching_shadow.py
```

Scorer nie zmienia obecnych slotów, stintów, statusów `detected/ambiguous/RAW`,
statystyk, heatmap ani crop review. Nie jest jeszcze produkcyjnym offline resolverem.

### P0.6 — visual stitching audit: DONE

Dodano deterministyczny generator wizualnego audytu rekomendowanych krawędzi:

- karta zawiera source endpoint, klatkę transition i target endpoint;
- source i target mają osobne powiększone cropy z ponownie narysowanym bboxem
  i strzałką wskazującą dokładnie ocenianą osobę;
- karta ma rozdzielczość `2400x1080`, a kliknięcie obrazu otwiera pełnoekranowy lightbox;
- karta pokazuje stable subjects, quality classes, team, koszt, confidence, gap,
  odległość, wymaganą prędkość, appearance, velocity residual i evidence;
- statyczna galeria pozwala oznaczyć `Same person`, `Different people` lub `Uncertain`;
- reviewed decisions można wyeksportować jako JSON gotowy do użycia jako goldset;
- brak endpointu nie uruchamia fallbacku: przypadek jest jawnie zapisywany jako `skipped`.

Implementacja:

```text
backend/app/services/identity_stitching_audit.py
backend/scripts/generate_identity_stitching_audit.py
backend/tests/test_identity_stitching_audit.py
```

Wygenerowany audyt:

```text
backend/storage/benchmarks/player_identity/
  p05-stitching-20260716-final-v3/visual-stitching-audit-v4/
```

Wynik generacji:

```text
easy90: 12 kart, 0 skipped
hard3m: 20 kart, 0 skipped
łącznie: 32 karty, 8 contact sheets, 2 interaktywne galerie HTML
```

Obserwacje techniczne:

```text
27/32 rekomendacje mają same_raw_tracker evidence
0/32 rekomendacje mają shared occlusion event evidence
easy90: 7 same_subject, 4 unresolved, 1 different_subjects
hard3m: 13 same_subject, 7 unresolved, 0 different_subjects
```

Jedyny przypadek `different_subjects` (`easy90`, karta 11,
`100044:2 -> 100044:3`) wygląda we wstępnej inspekcji jak ciągłość tego samego
niebieskiego zawodnika. Jest to wartościowy kandydat na potwierdzony false split, ale
formalny status pozostaje `pending`, dopóki audyt nie zostanie zapisany przez operatora.

Wniosek: P0.5 jest konserwatywny i dobrze znajduje proste splity raw trackera, ale nie
rozwiązuje jeszcze najważniejszej klasy problemu — zmian identity po realnym overlapie.

### P0.7 — versioned goldset i precision gate: DONE

Dodano kanoniczny kontrakt goldsetu oraz evaluator shadow recommendations:

- goldset używa złożonego klucza `benchmark_id + candidate_key`, a nie numeru karty;
- `confirmed_same` i `confirmed_different` są etykietami benchmarkowymi;
- `uncertain` i `pending` pozostają jawnie poza metrykami;
- sprzeczne duplikaty review zatrzymują budowę goldsetu;
- digest wersji jest deterministyczny i ignoruje techniczne timestampy review;
- CLI odmawia nadpisania istniejącej wersji goldsetu lub raportu;
- evaluator raportuje `TP`, `FP`, `FN`, `TN`, precision, recall i konkretne błędne krawędzie;
- konserwatywny gate domyślnie wymaga precision `>= 0.95`, minimum 10 etykiet
  i 0 false positives;
- brak predykcji albo za mało etykiet daje `not_ready`, nigdy fałszywe `passed`.

Implementacja:

```text
backend/app/services/identity_stitching_goldset.py
backend/scripts/build_identity_stitching_goldset.py
backend/tests/test_identity_stitching_goldset.py
```

Wygenerowano również niezmienny draft wejściowy:

```text
backend/storage/benchmarks/player_identity/goldsets/
  player-identity-stitching-0.1.0-draft.json
  player-identity-stitching-0.1.0-draft-evaluation.json
```

Po ręcznym audycie zbudowano finalny goldset `v1`:

```text
backend/storage/benchmarks/player_identity/goldsets/
  player-identity-stitching-1.0.0.json
  player-identity-stitching-1.0.0-evaluation.json
```

Wynik gate'a:

```text
32/32 labeled
32 confirmed_same
precision=1.0, recall=1.0
status=passed
```

Ograniczenie: goldset nie zawiera jeszcze żadnego `confirmed_different`, więc potwierdza
wysoką jakość prostych pozytywnych połączeń, ale nie mierzy false-positive rate na
negatywnych parach. Przed produkcyjnym włączeniem stitchingu trzeba dodać takie przypadki.

### P0.8 — joint outgoing assignment po occlusion: DONE / SHADOW

Dodano wspólną ocenę dwóch możliwych przypisań dla zdarzeń, w których dwóch zawodników
tej samej drużyny przechodzi przez overlap:

- scorer buduje lokalne endpointy tuż przed i tuż po occlusion;
- porównuje obie permutacje 2x2 jako całość, zamiast wybierać każdą krawędź osobno;
- raportuje `keep_current`, `suspected_swap`, `unresolved_current`,
  `identity_contradiction`, `ambiguous` albo `blocked`;
- wynik jest wyłącznie diagnostyczny i nie zmienia produkcyjnych slotów, stintów,
  team labels, statystyk ani heatmap;
- sąsiednie eventy opisujące tę samą czwórkę trackletów są deduplikowane;
- awaria etapu nie przerywa analizy i zachowuje wcześniejsze artefakty P0/P0.5.

Nowy artefakt:

```text
identity_occlusion_assignments.json
```

Implementacja:

```text
backend/app/services/identity_occlusion_assignment_shadow.py
backend/tests/test_identity_occlusion_assignment_shadow.py
```

Zaakceptowany frozen benchmark:

```text
backend/storage/benchmarks/player_identity/
  p08-joint-occlusion-20260716-v2/identity_benchmark_report.json
```

Wynik:

```text
2/2 benchmarki passed
production identity/stats/heatmap artifacts unchanged: true
easy90: 10 joint cases, 6 recommendations, 3 keep_current, 0 suspected_swap
hard3m: 36 joint cases, 17 recommendations, 6 keep_current,
         9 unresolved_current, 2 identity_contradiction, 6 ambiguous
```

Pierwszy prototyp porównujący pełne tracklety był nadmiernie blokowany przez naturalny
overlap czasowy. Finalna wersja ocenia przycięte endpointy wokół eventu, dzięki czemu
porównuje właściwy moment potencjalnej zamiany.

### P0.9 — visual joint assignment audit: DONE

Dodano deterministyczny audyt wizualny decyzji 2x2 po okluzji:

- każda karta pokazuje duży kontekst `before / event / after`;
- cztery osobne cropy `S1`, `S2`, `T1`, `T2` wskazują dokładnie oceniane osoby;
- cienki bbox i strzałka nie zasłaniają sylwetki zawodnika;
- operator wybiera `Assignment A`, `Assignment B`, jedną z czterech częściowych
  kontynuacji `Only Sx -> Ty`, `Neither` albo `Uncertain`;
- częściowa kontynuacja zapisuje jedną pozytywną i trzy negatywne krawędzie, dzięki
  czemu przypadek z jednym znikającym i jednym kontynuowanym zawodnikiem nie jest
  błędnie oznaczany jako pełne `Neither`;
- kontrolna próbka `keep_current` jest prezentowana obok trudnych przypadków
  `ambiguous` i `identity_contradiction`;
- galeria eksportuje reviewed manifest bez modyfikowania produkcyjnego identity.

Implementacja:

```text
backend/app/services/identity_occlusion_assignment_audit.py
backend/scripts/generate_identity_occlusion_assignment_audit.py
backend/tests/test_identity_occlusion_assignment_audit.py
```

Wygenerowany audyt:

```text
backend/storage/benchmarks/player_identity/
  p08-joint-occlusion-20260716-v2/visual-joint-occlusion-audit-v3/
```

Zakres audytu:

```text
easy90: 3 kontrolne przypadki keep_current
hard3m: 8 trudnych przypadków + 3 kontrolne keep_current
łącznie: 14 kart, 0 skipped
```

### P0.10 — joint assignment goldset i regression gate: DONE

Dodano wersjonowany kontrakt goldsetu i evaluator decyzji po okluzji:

- jednostką oceny jest `benchmark_id + case_key`;
- etykiety to `assignment_a`, `assignment_b`, `partial`, `neither`, `uncertain`
  i `pending`;
- wybór A lub B generuje dwie pozytywne i dwie negatywne etykiety krawędzi;
- wybór `neither` generuje cztery negatywne etykiety krawędzi;
- evaluator raportuje poprawne, błędne i wstrzymane decyzje oraz edge-level
  `TP`, `FP`, `FN`, `TN`;
- gate wymaga minimalnej liczby etykiet, kompletności predykcji, minimalnej accuracy
  oraz limitu błędnych wspólnych przypisań;
- CLI nie nadpisuje istniejących wersji goldsetu ani raportów.

Implementacja:

```text
backend/app/services/identity_occlusion_assignment_goldset.py
backend/scripts/build_identity_occlusion_assignment_goldset.py
backend/tests/test_identity_occlusion_assignment_goldset.py
```

Niezmienny goldset z ręcznego review:

```text
backend/storage/benchmarks/player_identity/goldsets/
  player-identity-joint-occlusion-1.0.0.json
```

Goldset obejmuje:

```text
14 cases
14 labeled
1 assignment_a
5 assignment_b
8 partial
20 positive edge labels
36 negative edge labels
```

Manifesty `easy90` i `hard3m` zostały w całości oznaczone. Wynik bazowego P0.8 przed
dostrojeniem wynosił `5/14`, z trzema błędnymi pełnymi przypisaniami. Review pokazało,
że głównym brakującym stanem nie jest kolejna permutacja 2x2, lecz częściowa kontynuacja,
gdy tylko jeden tracklet po okluzji reprezentuje prawdziwego zawodnika.

### P0.11 — partial-aware joint assignment: DONE / SHADOW

Scorer P0.8 został rozszerzony bez wpływu na produkcyjne identity:

- zapisuje reliability endpointów źródłowych i docelowych;
- rozpoznaje małe, niskoconfidence fragmenty bboxa przy wyjściu z okluzji;
- może zwrócić `partial_continuation` z dokładnie jedną rekomendowaną parą;
- nie wymusza pełnego przypisania, jeśli druga krawędź opiera się na niewiarygodnym
  endpointcie;
- przy uciętym endpointcie długiego trackletu zachowuje pełne przypisanie, jeśli obie
  krawędzie nadal mają mocne i spójne evidence;
- evaluator porównuje dokładną rekomendowaną parę częściową, a nie tylko etykietę
  `partial`.

Implementacja:

```text
backend/app/services/identity_occlusion_assignment_shadow.py
backend/app/services/identity_occlusion_assignment_goldset.py
backend/tests/test_identity_occlusion_assignment_shadow.py
backend/tests/test_identity_occlusion_assignment_goldset.py
```

Frozen wynik po dostrojeniu:

```text
backend/storage/benchmarks/player_identity/
  p08-joint-occlusion-20260717-partial-v1/
```

```text
status=passed
13/14 correct
0 wrong
1 abstained
accuracy=0.928571
edge TP=18 FP=0 FN=2 TN=36
```

Jedyny abstention dotyczy pełnego `assignment_b` w hard3m. Jest zachowany celowo:
shadow scorer nie ma jeszcze dostatecznego evidence, aby podnieść recall bez ryzyka
ponownego wprowadzenia false-positive assignment. Priorytetem tego etapu jest
`0 wrong`, nie wymuszanie decyzji dla każdej okluzji.

### Benchmarki frozen YOLO: DONE

Dodano dwa benchmarki uruchamiające wyłącznie post-processing identity:

- `easy90`: 90-sekundowa próbka z A02, A03 i A05 jako ręcznie zweryfikowanymi
  stabilnymi subjectami;
- `hard3m`: trudny zakres 04:30–07:30 pierwszej połowy z zachowaniem oryginalnych
  numerów klatek i camera motion.

Ostatni zaakceptowany run:

```text
backend/storage/benchmarks/player_identity/
  p05-stitching-20260716-final-v3/identity_benchmark_report.json
```

Wynik:

```text
2/2 benchmarki passed
identity/stats/heatmap artifacts unchanged: true
easy90 verified A02/A03/A05 conflicting recommendations: 0
diagnostics overhead: 0.04% easy, 0.02% hard
shadow recommendations: 12 easy, 20 hard
```

Opis uruchamiania i gate'ów znajduje się w:

```text
docs/PLAYER_IDENTITY_P0_BENCHMARK.md
```

### P1.1 — OFFLINE RESOLVER SHADOW: DONE

Gate P0 jest zaliczony. Zaimplementowano pierwszy produkcyjnie pasywny etap P1, który
buduje alternatywny timeline identity wyłącznie z rekomendacji przechodzących
istniejące goldset gates:

1. stosuje zaakceptowane krawędzie prostego stitchingu i joint assignment do kopii
   grafu trackletów;
2. zachowuje abstentions jako jawne przerwy, bez zgadywania tożsamości;
3. generuje równoległe stable subjects i timeline, bez podmiany produkcyjnych plików;
4. porównuje coverage, konflikty, liczbę subjectów i przewidywany nakład manualnego review;
5. odrzuca atomowo całe joint assignment, gdy choć jedna para łamie constraints;
6. blokuje cykle, temporal overlap, różne drużyny, podwójnego następcę/poprzednika
   oraz równoległe tracklety w jednym shadow subject.

Nowe artefakty:

```text
identity_offline_shadow.json
identity_offline_shadow_report.json
```

Implementacja:

```text
backend/app/services/identity_offline_resolver_shadow.py
backend/tests/test_identity_offline_resolver_shadow.py
```

Benchmark frozen YOLO:

```text
backend/storage/benchmarks/player_identity/p1-offline-shadow-20260717-v1/
```

Wynik:

```text
2/2 benchmarki passed
117 testów identity/stabilization passed
produkcyjne identity/stats/heatmap artifacts unchanged: true
easy90 verified A02/A03/A05 cross-subject links: 0
offline safety gates: true
isolated overhead: 0.06% easy, 0.04% hard
accepted edges: 12/124 eligible tracklets easy, 22/431 hard
```

P1.1 jest celowo konserwatywnym szkieletem grafu. Nie jest jeszcze kandydatem do
produkcji: budowanie subjectów wyłącznie z zaakceptowanych nowych krawędzi daje
112 subjectów dla easy90 i 409 dla hard3m. To potwierdza bezpieczeństwo, ale jeszcze
nie poprawia pokrycia.

### P1.2 — SAFE BASELINE CONTINUITY: DONE / LOW YIELD

Dodano konserwatywną klasę krawędzi bazowych opartą na istniejącej ciągłości
produkcyjnego subjectu:

1. dodaje do grafu bezpieczne istniejące ciągłości produkcyjnego subjectu jako osobną,
   niżej uprzywilejowaną klasę krawędzi;
2. odrzuca takie krawędzie przy suspected switch, overlap conflict, różnej drużynie,
   niepewnym footpoincie lub konflikcie z rekomendacją P0;
3. pozwala zaakceptowanym joint/stitching edges zastąpić błędną lokalną ciągłość;
4. zapisuje audyt wszystkich pominiętych par i powodów odrzucenia.

Benchmark:

```text
backend/storage/benchmarks/player_identity/p12-safe-baseline-20260717-v1/
```

Wynik:

```text
2/2 benchmarki passed
identity outputs unchanged: true
easy90 verified A02/A03/A05 cross-subject links: 0
safe baseline edges: 2 easy, 1 hard
shadow subjects: 110 easy, 408 hard
```

Ten etap wykazał, że produkcyjne sloty nie są wystarczająco czystym źródłem baseline:
większość sąsiednich par odpada przez suspected switch albo temporal overlap. Celowe
poluzowanie tych zabezpieczeń przywróciłoby ryzyko trwałych ID swapów, dlatego P1.2
pozostaje w kodzie jako bezpieczny fallback, ale nie rozwiązuje fragmentacji.

### P1.3 — GLOBAL TEMPORAL PATH SELECTION: DONE / SHADOW / MANUALLY VALIDATED

Zaimplementowano deterministyczny globalny wybór ścieżek bez podmiany produkcyjnego
identity:

1. solver ocenia pełną pulę scored stitching candidates, a nie tylko lokalne
   rekomendacje;
2. rozwiązuje minimum-cost bipartite path cover osobno per team i temporal window,
   łącząc ponownie partycje współdzielące endpoint;
3. traktuje zaakceptowane joint assignments jako forced atomic constraints i audytuje
   sprzeczne constrainty;
4. posiada jawny unmatched cost, minimalny link value i blocking evidence guards,
   dzięki czemu słaby kandydat pozostaje orphanem;
5. po wyborze globalnym ponownie sprawdza constraints na narastającym grafie;
6. raportuje pulę kandydatów, partycje, zaakceptowane cross-production links i
   rzeczywiste konflikty równoległych detekcji bez nazywania merge'u automatycznie
   identity switchem.

Benchmark:

```text
backend/storage/benchmarks/player_identity/p13-global-path-20260717-v2/
```

Wynik:

```text
2/2 benchmarki passed
identity outputs unchanged: true
easy90 verified A02/A03/A05 cross-subject links: 0
offline safety gates: true
candidate pool: 138 easy, 750 hard
accepted edges: 15 easy, 31 hard
cross-production edges requiring review: 1 easy, 1 hard
parallel detected conflicts: 0 easy, 0 hard
shadow subjects: 109 easy, 400 hard
P1.2 baseline shadow subjects: 110 easy, 408 hard
estimated manual review subjects: 53 easy, 194 hard
baseline manual review items: 148 easy, 636 hard
isolated overhead: 0.09% easy, 0.05% hard
```

Manualna walidacja cross-production links:

```text
audit: visual-cross-production-audit-v1
reviewed cases: 2/2
easy90 stitching: 100044:2 -> 100044:3 = ta sama osoba
hard3m joint occlusion: assignment_b = poprawne mapowanie
stitching precision/recall: 1.0 / 1.0
joint assignment accuracy: 1.0
wrong assignments: 0
```

Artefakty walidacji:

```text
backend/storage/benchmarks/player_identity/p13-global-path-20260717-v2/
  visual-cross-production-audit-v1/validation/
```

P1.3 pozostaje shadow-only. Same krawędzie nie są używane do sztucznego estymowania
coverage, RAW/ambiguous time ani liczby identity switchy. Te porównania są teraz
możliwe na poziomie eventów i stanów dzięki P1.4, nadal bez podmiany produkcyjnego
resolvera.

### P1.4 — SHADOW RESOLVED TIMELINE: DONE / SHADOW / CALIBRATED

Dodano osobny artefakt `identity_offline_shadow_timeline.json`, rozwijający graf P1.3
do audytowalnej osi czasu subjectów:

1. każda rzeczywista obserwacja trackletu ma stan `detected`;
2. krótka luka z wiarygodnymi endpointami może mieć stan `predicted`;
3. krótka luka wsparta zaakceptowanym occlusion eventem ma stan `occluded`;
4. luka bez wystarczającego evidence pozostaje `missing`;
5. bezpośrednie i nakładające się przejścia trackletów są zapisywane jako osobne
   `direct_transition` lub `overlap_transition`, nawet gdy nie tworzą luki w klatkach;
6. tylko wiarygodny `detected` wewnątrz boiska jest oznaczany jako potencjalnie
   kwalifikujący się do dystansu i heatmapy;
7. `predicted`, `occluded` i `missing` nigdy nie są traktowane jako observed distance;
8. timeline oraz raport comparison pozostają wyłącznie warstwą shadow.

Benchmark:

```text
backend/storage/benchmarks/player_identity/p14-shadow-timeline-20260717-v2/
```

Wynik:

```text
2/2 benchmarki passed
identity outputs unchanged: true
offline safety gates: true

easy90:
  transition events: 15
  detected / predicted / missing: 1252.085 s / 0.300 s / 29.796 s
  trusted detected ratio: 94.12%
  cross-production direct transition: 100044:2 -> 100044:3
  isolated diagnostics overhead: 0.23%

hard3m:
  transition events: 31
  detected / predicted / occluded / missing:
    2566.867 s / 2.536 s / 1.168 s / 111.545 s
  trusted detected ratio: 88.92%
  isolated diagnostics overhead: 0.14%
```

P1.4 nie zmienia produkcyjnych `global_identity.json`, stable slotów, stintów,
team labels, statystyk ani crop review. Metryki coverage dotyczą sumy osi czasu
shadow subjectów i nie są jeszcze rosterowym czasem gry 7v7.

Audyt wizualny event-level został wygenerowany i oczekuje na ręczną walidację:

```text
backend/storage/benchmarks/player_identity/p14-shadow-timeline-20260717-v2/
  visual-shadow-timeline-audit-v1/

easy90: 14 przypadków
hard3m: 30 przypadków
razem: 44 przypadki, 0 pominiętych
```

Zestaw obejmuje wszystkie przejścia `predicted`, `occluded` i wybrane `missing`,
cross-production links oraz ograniczoną próbkę bezpośrednich przejść kontrolnych.
Każdy przypadek wymaga dwóch niezależnych decyzji: ciągłość realnej osoby oraz
poprawność stanu timeline. Benchmark nie zawiera obecnie przykładu
`overlap_transition`; pozostaje on otwartym elementem goldsetu.

Prowizoryczny audyt wizualny Codexa został ukończony dla wszystkich 44 kart i
zapisany oddzielnie od ludzkiego goldsetu:

```text
visual-shadow-timeline-audit-v1/
  codex_visual_audit_decisions.json
  codex_visual_audit_summary.json
  easy90/identity_shadow_timeline_audit_reviewed_easy90_codex.json
  hard3m/identity_shadow_timeline_audit_reviewed_hard3m_codex.json
```

Wstępny wynik:

```text
easy90:
  13 same person, 1 uncertain
  9 correct, 2 should be predicted, 2 should be occluded, 1 uncertain

hard3m:
  28 same person, 1 different people, 1 uncertain
  15 correct, 4 should be predicted, 9 should be occluded,
  1 invalid identity link, 1 uncertain
```

Najważniejszy sygnał z audytu to niedostateczne rozpoznawanie krótkich zasłonięć:
część luk oznaczonych jako `predicted` lub `missing` powinna być `occluded`.
W hard3m wykryto również jeden jawny link blue -> white, który nie może zostać
zaakceptowany jako ciągłość osoby. Ten wynik jest pomocniczą oceną modelową, a nie
ludzkim ground truth; dwa przypadki pozostają celowo nierozstrzygnięte.

#### P1.4.1 — kalibracja stanów luk i event-level evaluator: DONE / SHADOW

Dodano deterministyczny evaluator oparty na reviewed audit manifests oraz
konserwatywną kalibrację stanów timeline. Zmiana wykorzystuje P0
`identity_occlusion_events.json` również dla luk wewnątrz jednego raw trackletu,
rozróżnia krótką predykcję od zasłonięcia i jawnie abstainuje przy ryzyku zmiany
drużyny lub niewiarygodnym appearance. `predicted` i `occluded` nadal nie są
kwalifikowane do dystansu ani heatmapy.

Nowe moduły:

```text
backend/app/services/identity_shadow_timeline_goldset.py
backend/scripts/evaluate_identity_shadow_timeline_goldset.py
```

Wynik na prowizorycznym audycie Codexa, po wyłączeniu dwóch nierozstrzygniętych
kart z oceny stanu:

```text
baseline:
  identity labeled: 42
  identity false positives: 1
  identity abstentions: 0
  state correct: 24/41 (58.54%)

candidate 0.2.0:
  identity labeled: 42
  identity false positives: 0
  identity abstentions: 1
  state correct: 36/41 (87.80%)
```

Pozostałe pięć różnic stanu dotyczy zasłonięć widocznych dla człowieka, których
obecny P0 occlusion artifact nie opisuje wystarczająco mocno. Nie dodano wyjątków
pod konkretne klatki. Zamiast ryzykować pewny błędny merge, candidate pozostawia
przypadki z cross-team occlusion, słabym appearance i niskim team confidence jako
`identity_continuity_status=uncertain` oraz `requires_review=true`.

Frozen benchmark:

```text
backend/storage/benchmarks/player_identity/
  p14-shadow-timeline-calibration-20260717-v2-full-benchmark/
```

Wynik benchmarku:

```text
2/2 cases passed
production identity/stat/heatmap artifacts unchanged: true

easy90:
  detected / predicted / occluded: 1252.085 s / 17.217 s / 12.880 s
  isolated diagnostics overhead: 0.25%

hard3m:
  detected / predicted / occluded / missing:
    2566.867 s / 65.499 s / 45.846 s / 3.904 s
  isolated diagnostics overhead: 0.12%
```

Evaluator jest teraz bramką regresyjną: blokuje identity false positive, wymaga
co najmniej 75% poprawnych stanów timeline i raportuje abstention osobno od błędu.
Prowizoryczny audyt Codexa nie zastępuje ludzkiego goldsetu przed włączeniem zmian
do produkcyjnego resolvera.

#### P1.4.2 — lokalne evidence zasłonięcia w shadow timeline: DONE / SHADOW

Po ręcznej trudności w ocenie pięciu pozostałych różnic stanu przygotowano
krótką galerię delta i wykonano dodatkowy audyt wizualny Codexa. Pierwsza karta
potwierdziła poprawne abstain przy błędnym blue -> white linku, a pozostałe pięć
kart pokazało krótkie lokalne zasłonięcia/kontakty, których P0 artifact nie
zawsze opisuje wystarczająco jawnie.

Dodano shadow-only helper:

```text
backend/app/services/identity_local_occlusion.py
```

Helper analizuje image-space bboxes w krótkiej luce, szuka cross-team contact
przy endpointach i zapisuje local occlusion evidence w event-level timeline.
Po pierwszej próbie progi zostały zawężone, bo zbyt szeroki overlap podbijał
`predicted` do `occluded` w kilku kontrolnych przypadkach. Finalna wersja
pozostaje konserwatywna: sygnał jest dostępny diagnostycznie, ale nie zwiększa
liczby automatycznie akceptowanych identity links.

Audyt i benchmark:

```text
backend/storage/benchmarks/player_identity/
  p14-shadow-timeline-local-occlusion-20260717-v6/
    CODEX_VISUAL_AUDIT.md
```

Wynik goldsetu pozostaje zgodny z P1.4.1:

```text
identity labeled: 42
identity correct: 41
identity abstained: 1
identity false positives: 0
state correct: 36/41 (87.80%)
```

Frozen benchmark:

```text
2/2 cases passed
hard benchmark has more recoverable and occlusion events: true
easy90 diagnostics overhead: 0.42%
hard3m diagnostics overhead: 0.19%
```

Skrypt benchmarkowy rozdziela teraz dwa sygnały: pełny candidate reprocess daje
kompletne metryki diagnostyczne, a `independent_reprocess_core_equal` pozostaje
informacją o deterministyczności niezależnych przebiegów, nie blokadą P1.4.
No-impact gate dla shadow diagnostics pilnuje, że warstwa dopisuje wyłącznie
artefakty diagnostyczne i nie podmienia produkcyjnego resolvera.

### P1.5 — SHADOW CANDIDATE PACKAGE I IDENTITY-ONLY OVERLAY: DONE / SHADOW / VISUAL BASELINE

Dodano pierwszy odseparowany pakiet kandydata stable identity zbudowany na osi czasu
P1.4. Kandydat nadal nie podmienia produkcyjnego resolvera:

1. nadaje shadow subjectom deterministyczne, wyłącznie wizualne ID;
2. zachowuje produkcyjny label przy jednoznacznym mapowaniu 1:1;
3. oznacza fragmenty splitu suffixem, np. `A03~2`;
4. jawnie flaguje merge kilku produkcyjnych subjectów, cross-production transition,
   brak anchoru i niepewną ciągłość;
5. interpoluje bbox wyłącznie dla `predicted` i `occluded` w lekkim overlayu;
6. nigdy nie kwalifikuje interpolowanych pozycji do dystansu ani heatmapy;
7. nie zapisuje ciężkiego overlay JSON przy zwykłej analizie. Jest budowany w pamięci
   dopiero podczas renderu na żądanie;
8. renderer identity-only nie rysuje pitchu, piłki ani statystyk, dzięki czemu audyt
   dotyczy wyłącznie zachowania ID.

Nowe artefakty shadow:

```text
identity_candidate_shadow.json
identity_candidate_shadow_report.json
```

Implementacja:

```text
backend/app/services/identity_candidate_shadow.py
backend/app/services/identity_candidate_overlay.py
backend/scripts/render_identity_candidate_overlay.py
backend/tests/test_identity_candidate_shadow.py
```

Benchmark frozen YOLO:

```text
backend/storage/benchmarks/player_identity/p15-candidate-20260719-v2/
```

Wynik:

```text
2/2 benchmarki passed
337 backend tests passed
produkcyjne identity/stats/heatmap artifacts unchanged: true
isolated diagnostics overhead: 0.55% easy, 0.24% hard

easy90:
  candidate subjects: 109
  anchored / unanchored: 77 / 32
  split fragments / merged subjects: 74 / 3
  subjects requiring review: 107
  max active A / B: 10 / 8
  frames over seven candidates: 592

hard3m:
  candidate subjects: 400
  anchored / unanchored: 279 / 121
  split fragments / merged subjects: 275 / 11
  subjects requiring review: 396
  max active A / B: 12 / 10
  frames over seven candidates: 2823
```

Wygenerowane identity-only overlaye:

```text
backend/storage/benchmarks/player_identity/p15-candidate-20260719-v2/
  easy90/identity_candidate_overlay.mp4
  hard3m/identity_candidate_overlay.mp4
```

P1.5 jest technicznie gotowe jako wizualny baseline, ale nie jest gotowe do promocji.
Duża liczba split fragments, unanchored subjects i klatek z więcej niż siedmioma
kandydatami na drużynę potwierdza, że kolejnym krokiem musi być redukcja fragmentacji
i filtrowanie aktywnego składu, nie podmiana produkcyjnego `global_identity.json`.
Persistent gallery i role priors z P2 nie zostały rozpoczęte: wymagają zatwierdzonych
próbek rosteru i osobnych gate'ów chroniących przed utrwaleniem błędnej tożsamości.

### P1.6 — SHADOW ACTIVE ROSTER SELECTOR: DONE / SHADOW / NOT PROMOTED

Dodano osobną warstwę ograniczającą wizualny candidate roster bez modyfikowania
subjectów i bez wpływu na statystyki:

1. ścisłe duplikaty tej samej obserwacji są usuwane przed limitem składu;
2. przy duplikacie preferowany jest istniejący anchor, reliable footpoint i detekcja;
3. jeśli po deduplikacji drużyna nadal ma więcej niż siedmiu kandydatów, wybór używa
   statusu obserwacji, inside-pitch reliability, confidence i słabego continuity bonusu;
4. Team U nie jest promowany do aktywnego rosteru;
5. odrzucone pozycje nie są kasowane. Pozostają w raporcie z powodem
   `duplicate_same_observation`, `team_active_cap_lower_rank` albo
   `unknown_team_not_roster`;
6. selector pozostaje shadow-only i nie zasila czasu gry, dystansu ani heatmap.

Nowe artefakty:

```text
identity_active_roster_shadow.json
identity_active_roster_shadow_report.json
```

Implementacja:

```text
backend/app/services/identity_active_roster_shadow.py
backend/tests/test_identity_active_roster_shadow.py
```

Frozen benchmark:

```text
backend/storage/benchmarks/player_identity/p16-active-roster-20260719-v3/
```

Wynik:

```text
2/2 benchmarki passed
342 backend tests passed
produkcyjne identity/stats/heatmap artifacts unchanged: true

easy90:
  max active A/B: 10/8 -> 7/7
  frames over cap: 577 -> 0
  strict duplicate suppressions: 3
  lower-rank overflow suppressions: 607
  reliable A/B detections retained: 99.80%
  isolated diagnostics overhead: 0.80%

hard3m:
  max active A/B: 12/10 -> 7/7
  frames over cap: 2742 -> 0
  strict duplicate suppressions: 3
  lower-rank overflow suppressions: 4170
  reliable A/B detections retained: 99.65%
  isolated diagnostics overhead: 0.42%
```

Identity-only active-roster overlaye:

```text
backend/storage/benchmarks/player_identity/p16-active-roster-20260719-v3/
  easy90/identity_active_roster_overlay.mp4
  hard3m/identity_active_roster_overlay.mp4
```

P1.6 nie jest promotion gate. Wizualny audyt hard3m nadal pokazuje wcześniejsze błędy
team assignment, np. niebieskiego zawodnika w kandydacie Team A. Sam limit siedmiu
nie może naprawić takiego błędu i nie może być użyty do ukrycia problemu. Następny
etap powinien ograniczyć split fragments i naprawić team continuity przed próbą
produkcyjnego stable subject rebuild.

### P1.7 — TEAM-SAFE VISUAL ANCHORS: DONE / SHADOW / NOT PROMOTED

Naprawiono diagnostyczne etykiety kandydatów, które mogły dziedziczyć produkcyjny
anchor z przeciwnej drużyny. Taki przypadek nie oznaczał, że shadow candidate został
przypisany do złej drużyny: kolor bboxa i `team_label` były prawidłowe, ale nazwa
wizualna, np. `A06~7` na niebieskim zawodniku, pochodziła z błędnego produkcyjnego
subjectu i utrudniała audyt.

Nowe zasady:

1. label produkcyjnego slotu może zostać odziedziczony tylko wtedy, gdy prefiks
   `A/B` zgadza się z drużyną shadow candidate;
2. konflikt jest zapisywany jako `production_anchor_team_mismatch`;
3. kandydat z konfliktem otrzymuje neutralną etykietę tej drużyny, np. `B-new01`;
4. taki kandydat nie jest traktowany jako anchored podczas selekcji aktywnego rosteru;
5. zmiana pozostaje wyłącznie wizualna i diagnostyczna. Nie przepina produkcyjnego
   identity ani team assignment.

Frozen benchmark:

```text
backend/storage/benchmarks/player_identity/p17-team-safe-labels-20260719-v1/
```

Wynik:

```text
2/2 benchmarki passed
produkcyjne identity/stats/heatmap artifacts unchanged: true

easy90:
  conflicting production anchors reported: 4
  cross-team visual labels after guard: 0
  reliable A/B detections retained: 99.80%
  max active A/B: 7/7

hard3m:
  conflicting production anchors reported: 11
  cross-team visual labels after guard: 0
  reliable A/B detections retained: 99.65%
  max active A/B: 7/7
```

Overlay kontrolny:

```text
backend/storage/benchmarks/player_identity/p17-team-safe-labels-20260719-v1/
  hard3m/identity_active_roster_overlay.mp4
```

Kontrola wizualna na źródłowej klatce `8146` potwierdziła, że wcześniejszy niebieski
`A06~7` jest teraz pokazany jako `B-new01`. Pozostają liczne krótkie fragmenty tego
samego visual labelu. Nie wolno ich scalać wyłącznie po wspólnym produkcyjnym anchorze,
ponieważ anchor może zawierać overlap, cross-team switch albo wcześniejszy ID swap.
Następny etap powinien najpierw wygenerować audytowalne propozycje konsolidacji
fragmentów, bez automatycznej promocji.

### P1.8 — SHADOW FRAGMENT CONSOLIDATION PROPOSALS: DONE / AUDITED / NOT PROMOTED

Dodano konserwatywny generator propozycji łączenia sąsiednich candidate fragments.
Warstwa nie przebudowuje subjectów. Jej celem jest zmniejszenie ręcznej przestrzeni
decyzji i jawne odrzucenie połączeń, które są niebezpieczne.

Propozycja może powstać wyłącznie, gdy:

1. oba fragmenty należą do tej samej drużyny;
2. mają dokładnie jeden wspólny, team-safe production anchor;
3. są sąsiednimi fragmentami tego anchora w czasie;
4. nie nakładają się równolegle poza małą granicą segmentacji;
5. przerwa nie przekracza trzech sekund.

Każda propozycja zapisuje gap, overlap, endpoint distance, wymaganą prędkość,
active-roster ratio, confidence i reason codes. Nawet `recommended_review` wymaga
kontroli wizualnej. Brak ReID oznacza, że wspólny produkcyjny anchor pozostaje tylko
słabym evidence i nigdy nie może samodzielnie zatwierdzić merge'a.

Nowe artefakty:

```text
identity_fragment_consolidation_shadow.json
identity_fragment_consolidation_shadow_report.json
```

Implementacja i testy:

```text
backend/app/services/identity_fragment_consolidation_shadow.py
backend/tests/test_identity_fragment_consolidation_shadow.py
```

Frozen benchmark:

```text
backend/storage/benchmarks/player_identity/p18-fragment-consolidation-20260719-v1/
```

Wynik:

```text
2/2 benchmarki passed
346 backend tests passed
produkcyjne identity/stats/heatmap artifacts unchanged: true

easy90:
  eligible fragments / anchor groups: 71 / 17
  proposals: 26 (17 recommended, 9 needs review)
  rejected pairs: 28
  rejected parallel overlaps: 19
  rejected long gaps: 9
  isolated diagnostics overhead: 0.81%

hard3m:
  eligible fragments / anchor groups: 258 / 25
  proposals: 106 (84 recommended, 22 needs review)
  rejected pairs: 127
  rejected parallel overlaps: 85
  rejected long gaps: 42
  isolated diagnostics overhead: 0.37%
```

P1.8 nie jest jeszcze gotowe do promocji. Przygotowano interaktywny audyt wizualny
wszystkich wygenerowanych propozycji:

```text
backend/storage/benchmarks/player_identity/p18-fragment-consolidation-20260719-v1/
  visual-fragment-consolidation-audit-v2/
```

Audyt zawiera 26 kart `easy90` i 106 kart `hard3m`. Każda karta pokazuje końcową
obserwację fragmentu źródłowego, kontekst przejścia, początkową obserwację fragmentu
docelowego oraz powiększone cropy obu osób. Operator wybiera wyłącznie:
`same_person`, `different_people` albo `uncertain`. Eksportowane manifesty zachowują
stabilny `proposal_key` i mogą zostać użyte jako goldset precision P1.8.

Dwa kompletne manifesty zostały zaimportowane do wersjonowanego goldsetu. Wynik
ręcznego audytu:

```text
easy90:
  confirmed same: 23
  confirmed different: 3
  uncertain: 0

hard3m:
  confirmed same: 76
  confirmed different: 10
  uncertain: 20

łącznie:
  132 propozycje
  99 same / 13 different / 20 uncertain / 0 pending
```

Audyt wykazał, że niski koszt lub `recommended_review` nie wystarcza do
automatycznego merge'a. Szczególnie ryzykowne są propozycje `gap=0`, które często
łączą prawidłową sylwetkę z partial bboxem nogi, buta, pustego pola albo piłki.
Wynik audytu został zapisany jako goldset P1.9. Produkcyjne i candidate identity
pozostają bez zmian.

### P1.9 — FRAGMENT CONSOLIDATION GOLDSET I STRICT PROMOTION GATE: DONE / SHADOW

Dodano deterministyczny, trzywartościowy goldset oraz evaluator rozróżniający:

- `confirmed_same`;
- `confirmed_different`;
- `uncertain`, które nie jest zgadywane ani traktowane jak negatywna etykieta.

Goldset:

```text
backend/tests/fixtures/player_identity/
  identity_fragment_consolidation_goldset_v1.json
```

Implementacja:

```text
backend/app/services/identity_fragment_consolidation_goldset.py
backend/scripts/build_identity_fragment_consolidation_goldset.py
backend/tests/test_identity_fragment_consolidation_goldset.py
```

Polityka `strict_v1` oznacza link jako `auto_accept_shadow` tylko przy jednoczesnym
spełnieniu wszystkich warunków:

1. istnieje rzeczywista dodatnia luka czasowa; `gap=0` zawsze wymaga review;
2. luka nie przekracza `0.7 s`;
3. confidence wynosi co najmniej `0.8`;
4. endpoint distance nie przekracza `1.5 m`;
5. wymagana prędkość nie przekracza `4.0 m/s`;
6. oba fragmenty mają active ratio co najmniej `0.9`;
7. drużyna i team-safe anchor są zgodne;
8. propozycja nie ma overlapu ani reason codes.

Frozen evaluation:

```text
backend/storage/benchmarks/player_identity/p18-fragment-consolidation-20260719-v1/
  identity_fragment_consolidation_goldset_v1_evaluation.json
```

Wynik:

```text
status: passed
labeled: 112
unlabeled uncertain: 20
auto_accept_shadow: 5
  easy90: 1
  hard3m: 4
precision: 100%
false merges: 0
uncertain auto-accepts: 0
recall: 5.05%
```

Gate jest celowo bardzo wąski. Przejście benchmarku nie uruchamia jeszcze merge'a:
`auto_accept_shadow` pozostaje advisory, nie przebudowuje candidate subjectów i nie
dotyka produkcyjnego identity ani statystyk. Przed rozszerzeniem coverage trzeba
dodać niezależną ocenę jakości endpoint bboxa, aby odróżnić pełną sylwetkę od
partial fragmentu, piłki i pustego bboxa bez uczenia progów pod te dwa materiały.

Sprawdzono proste cechy endpointów: aspect ratio, powierzchnię bboxa, wzajemny ratio
powierzchni oraz detector confidence. Ich rozkłady dla `same`, `different` i
`uncertain` silnie się pokrywają. Nie dodano progów dopasowanych do easy90/hard3m,
ponieważ dawałyby pozorny progres i ryzyko regresji na nowym meczu. Następne
rozszerzenie coverage wymaga observation-level evidence, np. spójności maski osoby,
widoczności torso/footpoint albo reliable appearance crop, a nie kolejnego progu na
samej geometrii bboxa.

### P1.10 — LOCAL ENDPOINT OBSERVATION RELIABILITY: DONE / SHADOW / NOT A REID SIGNAL

Dodano pasywną ocenę lokalnej jakości obserwacji na końcach fragmentów. Scorer
porównuje endpoint z maksymalnie dziesięcioma sąsiednimi obserwacjami tego samego
fragmentu i zapisuje:

- liczbę dostępnych obserwacji kontekstowych;
- ratio powierzchni i proporcji bboxa względem lokalnej mediany;
- lokalną prędkość w układzie boiska;
- istniejące flagi reliability footpointu i appearance;
- jawne `visual_content_verified: false`.

Klasy `locally_consistent`, `review` i `invalid` opisują wyłącznie jakość lokalnej
obserwacji. Nie są dowodem `same person` i nigdy nie autoryzują merge'a. Każda para
endpointów ma `safe_for_automatic_identity_merge: false`.

Implementacja:

```text
backend/app/services/identity_fragment_endpoint_reliability.py
backend/scripts/evaluate_identity_fragment_endpoint_reliability.py
backend/tests/test_identity_fragment_endpoint_reliability.py
```

`identity_fragment_consolidation_shadow.json` został rozszerzony o:

```text
source_endpoint_reliability
target_endpoint_reliability
endpoint_reliability
summary.endpoint_quality_counts
gates.endpoint_quality_is_advisory_only
```

Wersja kontraktu i algorytmu consolidation shadow została podniesiona do `0.2.0`.
Strict gate P1.9 pozostaje bez zmian: nadal ma pięć advisory auto-acceptów i nie
wykonuje produkcyjnych merge'ów.

Frozen benchmark:

```text
backend/storage/benchmarks/player_identity/p110-endpoint-reliability-20260720-v2/
  identity_benchmark_report.json
  identity_fragment_endpoint_reliability_evaluation.json
```

Wynik no-impact:

```text
easy90: passed
hard3m: passed
identity outputs unchanged: true
independent reprocess core equal: true
isolated diagnostics overhead:
  easy90: 0.79%
  hard3m: 0.37%
```

Macierz na 132 ręcznie ocenionych propozycjach:

```text
confirmed same:      locally consistent 34 | review 65 | invalid 0
confirmed different: locally consistent  4 | review  9 | invalid 0
uncertain:           locally consistent  7 | review 13 | invalid 0
```

Wszystkie endpointy użyte przez generator propozycji były strukturalnie poprawne,
więc żaden nie otrzymał klasy `invalid`. Jednocześnie cztery ręcznie potwierdzone
błędne pary przeszły jako `locally_consistent`. Przypadek hard3m `#93`, w którym
oba endpointy przedstawiają piłkę zamiast zawodnika, również jest lokalnie spójny.
To potwierdza, że ciągłość bboxa i ruchu nie potrafi zweryfikować zawartości obrazu.

P1.10 nie zwiększa zatem recall auto-merge. Zamyka natomiast ryzykowną ścieżkę
strojenia kolejnych progów geometrycznych pod dwa benchmarki. Następny krok powinien
dodać niezależne image-content evidence: person/foreground occupancy endpoint cropa,
widoczność torso i footpointu oraz lokalną spójność appearance. Taki sygnał najpierw
pozostaje shadow i wymaga walidacji na większej liczbie meczów.

### P1.11 — ENDPOINT VISUAL-CONTENT EVIDENCE CONTRACT: DONE / SHADOW / AUDITED

Dodano jawny kontrakt zawartości wizualnej endpointu, niezależny od oceny ciągłości
tożsamości. Każdy unikalny endpoint posiada stabilny `endpoint_key` oraz jeden ze
stanów:

- `person` — bbox zawiera rozpoznawalną sylwetkę osoby;
- `partial_person` — bbox zawiera rozpoznawalny fragment osoby;
- `not_person` — bbox przedstawia piłkę, tło albo inny obiekt;
- `unclear` — obraz nie pozwala na bezpieczną ocenę;
- `pending` albo `unavailable` — brak zakończonego evidence.

Status `not_person` blokuje połączenie wyłącznie w warstwie diagnostycznej. Statusy
`person` i `partial_person` potwierdzają tylko zawartość bboxa i nigdy nie są
dowodem, że dwa endpointy przedstawiają tę samą osobę. Cały etap pozostaje advisory:
nie zmienia candidate identity, produkcyjnego resolvera, slotów, stintów ani statystyk.

Implementacja:

```text
backend/app/services/identity_fragment_visual_content.py
backend/app/services/identity_fragment_visual_content_audit.py
backend/scripts/generate_identity_fragment_visual_content_audit.py
backend/scripts/build_identity_fragment_visual_content_evidence.py
backend/tests/test_identity_fragment_visual_content.py
backend/tests/test_identity_fragment_visual_content_audit.py
```

Nowe artefakty:

```text
identity_fragment_visual_content.json
identity_fragment_visual_content_report.json
```

Selektywny audyt obejmuje endpointy z ręcznie potwierdzonych par
`confirmed_different`, par `uncertain` oraz pięciu kandydatów strict shadow
auto-accept. Wygenerowano 76 unikalnych kart:

```text
backend/storage/benchmarks/player_identity/
  p111-endpoint-content-audit-20260720-v2/

easy90: 8 endpointów
hard3m: 68 endpointów
```

Każda karta rozdziela trzy widoki:

1. pełną klatkę z pozycją endpointu;
2. dokładny crop `EXACT BBOX`, który jest przedmiotem klasyfikacji;
3. szerszy `EXPANDED CONTEXT`, używany tylko do interpretacji sceny.

Rozdzielenie jest konieczne, ponieważ padding cropa może pokazać sąsiedniego
zawodnika mimo że właściwy bbox zawiera tylko piłkę, kończynę albo puste tło.
Nie dodano heurystyk HSV, edge density ani ponownej inferencji tym samym modelem
player YOLO: nie są niezależnym evidence i mogłyby nauczyć gate błędów obecnego
detektora. Lokalny zestaw modeli nie zawiera niezależnego person verifiera/ReID.

Selektywny zestaw został następnie oceniony wizualnie. Zapisano reviewed manifesty
z provenance `codex_visual_review` oraz zbudowano pełne evidence dla obu benchmarków:

```text
easy90: 7 person, 1 partial_person
hard3m: 42 person, 24 partial_person, 1 unclear, 1 not_person
```

Po zmapowaniu etykiet na wszystkie 132 propozycje goldsetu otrzymano:

```text
person_content_supported: 36 par
invalid_content:           1 para
unclear:                   1 para
unavailable:              94 pary
```

Przypadek `invalid_content` to hard3m `A07~3 -> A07~5`, w którym jeden endpoint
przedstawia piłkę zamiast zawodnika. Wszystkie pięć kandydatów strict P1.9 ma
potwierdzoną zawartość osoby. Audyt potwierdza więc przydatność content evidence
jako blokady oczywistych błędów detektora, ale nie jako dowodu `same person`.

Reviewed evidence:

```text
backend/storage/benchmarks/player_identity/
  p111-endpoint-content-audit-20260720-v2/reviewed-evidence/
```

### P1.12 — STRICT VISUAL-CONTENT SHADOW GATE: DONE / SHADOW / PASSED

Dodano fail-closed gate komponujący rygorystyczną politykę P1.9 z evidence P1.11:

- `not_person` blokuje kandydaturę;
- `unavailable`, `pending`, `unclear` oraz brak dokumentu powodują abstencję;
- `person` i `partial_person` jedynie przepuszczają kandydaturę do istniejącej
  polityki strict i nigdy samodzielnie nie autoryzują połączenia;
- nieudany strict gate nie może zostać nadpisany pozytywnym content evidence;
- wszystkie decyzje pozostają advisory i nie zmieniają produkcyjnego identity.

Implementacja:

```text
backend/app/services/identity_fragment_visual_content_gate.py
backend/scripts/evaluate_identity_fragment_visual_content_gate.py
backend/tests/test_identity_fragment_visual_content_gate.py
```

Raport benchmarkowy:

```text
backend/storage/benchmarks/player_identity/
  p111-endpoint-content-audit-20260720-v2/
    identity_fragment_visual_content_gate_evaluation.json
```

Wynik na pełnym goldsecie:

```text
132/132 propozycje mają prediction i content-pair contract
strict auto-accepts: 5
gated auto-accepts:  5
true positive:       5
false positive:      0
uncertain accepted:  0
precision:           1.000000
recall:              0.050505
status:              passed
```

P1.12 wzmacnia granicę bezpieczeństwa i zachowuje wszystkie dotychczasowe bezpieczne
kandydatury, ale zgodnie z projektem nie zwiększa recall. Następny wzrost coverage
wymaga niezależnego evidence tożsamości, np. same-match ReID z reliable cropów lub
manualnego potwierdzenia pary. Dalsze strojenie geometrii bboxa nie jest uzasadnione.

### P1.13 — SAME-MATCH REID SHADOW EVIDENCE: DONE / BENCHMARKED / NOT PROMOTED

Dodano pierwszy niezależny sygnał wyglądu dla par fragmentów z tego samego meczu.
Warstwa jest całkowicie advisory: nie scala fragmentów, nie zmienia candidate ani
production identity i nie może nadpisać konfliktu drużyny, czasu lub równoległej
obserwacji.

Implementacja:

```text
backend/app/services/identity_same_match_reid.py
backend/scripts/download_person_reid_model.py
backend/scripts/evaluate_identity_same_match_reid.py
backend/tests/test_identity_same_match_reid.py
backend/requirements-reid.txt
```

Zakres:

- opcjonalny adapter OpenVINO CPU dla lekkiego modelu
  `person-reidentification-retail-0288`;
- crop quality gate: detected, reliable appearance/footpoint, inside play area,
  confidence, minimalny bbox, overlap/containment, blur i brightness;
- maksymalnie osiem czasowo rozłożonych clean cropów per subject;
- prototype jako medoid oraz jawny dispersion/quality;
- wersjonowany persistent embedding cache związany z modelem i preprocessingiem;
- prototype distance, reason codes i pełny kontrakt bezpieczeństwa per proposal;
- evaluator goldsetu z coverage, medianami, AUC, precision@K i diagnostycznym
  zero-false-positive operating point.

Benchmark po cache warm-up:

```text
backend/storage/benchmarks/player_identity/
  p113-same-match-reid-20260720-v7-cache-v2-cached/
```

Wynik łączny:

```text
labeled pairs:             112
available reliable pairs:  101 (coverage 0.9018)
same / different:          89 / 12
same median distance:      0.253463
different median distance: 0.573349
pairwise AUC:              0.839888
precision@5/10/20:         0.80 / 0.90 / 0.95
```

Wynik per benchmark:

```text
easy90:  coverage 0.9231, AUC 1.000000, zero-FP recall 1.0000 (21/21)
hard3m:  coverage 0.8953, AUC 0.799020, zero-FP recall 0.0441 (3/68)
```

Na easy90 sygnał wyraźnie rozdziela osoby. Na hard3m istnieje confirmed-different
collision z dystansem około `0.1078`; wspólny próg z zerem false positives ma przez
to recall tylko około `4.5%`. Nie włączono zatem automatycznego merge threshold.
ReID jest obecnie wartościowy do rankingu/manual review i jako dodatkowy koszt w
następnym shadow solverze, ale nie jako samodzielna decyzja identity.

Cache potwierdzono drugim bitowo deterministycznym przebiegiem: `941/941` hitów
easy90 i `3700/3700` hitów hard3m, bez ponownego wywołania modelu. Quality gate
raportuje faktyczne użycie filtrów cropów, niezależnie od tego, czy subject zebrał
minimalną liczbę embeddingów do reliable prototype.

### Następny krok po P1.13

Przed podmianą produkcyjnego resolvera należy wykonać mały ludzki audyt delta:

- jeden przypadek identity, dla którego candidate abstainuje zamiast wykonać błędny
  cross-team merge;
- pięć pozostałych różnic `predicted` kontra `occluded` w hard3m;
- co najmniej kilka przypadków `overlap_transition`, których obecny audyt nie zawiera.

Nie trzeba ponownie oceniać wszystkich 44 kart. Po zatwierdzeniu delta goldsetu
następny candidate może używać timeline do porównania RAW/ambiguous i coverage, ale
każda zmiana produkcyjnego identity nadal wymaga osobnego benchmarku oraz ręcznej
walidacji nowych cross-production links.

Nadal otwarte są między innymi:

- produkcyjny offline graph optimizer i stable subject rebuild;
- joint assignment outgoing trackletów po overlapie;
- jawny stan `occluded` w produkcyjnym resolved timeline;
- event-level UI review i orphan review;
- anchor assignment per stable subject;
- użycie same-match ReID w assignment cost wyłącznie w kolejnym shadow candidate;
- poprawa hard3m ReID przez lepsze domain crops/model, bez obniżania strict gate;
- wpływ quality/reliability na statystyki i crop review.

---

# 1. Baseline repozytorium

Dokument został przygotowany względem aktualnego `HEAD` w momencie analizy:

```text
f569e6ebded5bb2da1cf360b19f3b903260b24e7
```

Przed rozpoczęciem implementacji agent musi:

1. pobrać aktualny `HEAD`;
2. sprawdzić, czy opisane funkcje i artefakty nadal istnieją;
3. nie zakładać, że nazwy prywatnych helperów są identyczne;
4. zmapować poniższe wymagania na aktualny kod;
5. nie usuwać istniejącego crop review, dopóki nowe flow nie jest funkcjonalnie gotowe.

Aktualne repo posiada już kilka ważnych elementów:

- raw YOLO tracks;
- tracker Ultralytics z `persist=True`;
- global identity resolver;
- stable players / slots / stints;
- identity review gallery;
- manual splits;
- crop-level identity assignments;
- resolved player timeline i resolved player stats;
- analysis quality report;
- role i goalkeeper metadata;
- zabezpieczenia przed team switchami, duplicate observations i identity conflicts.

To oznacza, że nie należy pisać systemu od zera. Należy zmienić jednostkę pracy i sposób łączenia informacji.

---

# 2. Diagnoza problemu

## 2.1. Duża liczba raw tracków nie oznacza, że YOLO nie widzi zawodników

Jeżeli overlay pokazuje praktycznie wszystkich zawodników przez cały mecz, ale powstają setki tracków, to prawdopodobny problem wygląda tak:

```text
player detection recall: wysoki
tracking continuity: średni lub niski
identity association: niestabilne przy overlapach
```

Przykład:

```text
prawdziwy zawodnik A
→ raw track 14
→ raw track 38
→ raw track 61
→ raw track 94
```

Z punktu widzenia detection zawodnik był widoczny. Z punktu widzenia tracker/identity został podzielony na wiele fragmentów.

## 2.2. Główne źródła fragmentacji

Najbardziej prawdopodobne przyczyny:

- chwilowy brak jednej z dwóch detekcji podczas overlapu;
- po rozdzieleniu tracker przypisuje niewłaściwe ID;
- skrócony visible bbox daje błędny bottom-center jako footpoint;
- zbyt duży skok pozycji po homografii;
- zbyt lokalna i zachłanna decyzja identity;
- proste cechy RGB są za słabe do rozróżnienia zawodników tej samej drużyny;
- nowe raw track ID po krótkiej luce jest traktowane jak nowa osoba;
- krótkie śmieciowe tracklety trafiają do review;
- review działa na pojedynczych cropach zamiast na dużych, spójnych fragmentach czasu.

## 2.3. Problem anotacji i overlapów

Dataset graczy powinien stosować spójną politykę `visible player extent`:

```text
pełny zawodnik widoczny
→ bbox pełnej widocznej sylwetki

częściowo zasłonięty, ale jednoznaczny zawodnik
→ bbox całej widocznej części

widoczna tylko ręka, stopa lub przypadkowy fragment kończyny
→ brak anotacji
```

BBoxy różnych zawodników mogą i powinny się nakładać.

Nie należy przycinać bboxa tylko po to, aby uniknąć overlapu z zawodnikiem z przodu.

Jednocześnie downstream nie może zakładać, że bottom-center każdego częściowego bboxa jest prawdziwą pozycją stóp.

---

# 3. Docelowy model identity

## 3.1. Warstwy

```text
YOLO detection
    ↓
raw tracker ID
    ↓
raw tracklet
    ↓
tracklet quality classification
    ↓
offline tracklet stitching
    ↓
stable subject / slot
    ↓
stint
    ↓
roster player_id
    ↓
resolved player timeline
    ↓
player stats / heatmaps / passes
```

Każda warstwa ma inną odpowiedzialność.

### YOLO

Ma wykrywać wystarczająco widoczne osoby.

### Raw tracker

Ma utrzymać krótkoterminową ciągłość, ale jego ID nie jest finalną tożsamością.

### Tracklet stitching

Ma łączyć wiele raw trackletów należących do tej samej prawdziwej osoby.

### Stable subject

Ma reprezentować jedną osobę na boisku niezależnie od zmian raw ID.

### Roster assignment

Ma być wykonany raz na duży stable subject albo duży stint, nie na każdy crop.

### Manual review

Ma rozstrzygać tylko niepewne miejsca, a nie rekonstruować cały mecz ręcznie.

---

# 4. P0 — diagnostyka zanim zmienimy model

**Status: DONE w trybie `shadow_read_only`.** Implementacja generuje raport bez wpływu
na produkcyjny resolver. Szczegóły i benchmarki opisano w sekcji 0.1.

Przed dużym refactorem należy mierzyć problem.

## 4.1. Nowy raport fragmentacji

Dodać artefakt:

```text
identity_fragmentation_report.json
```

Przykładowy kontrakt:

```json
{
  "schema_version": "0.1.0",
  "generated_at": "...",
  "summary": {
    "raw_tracklets": 427,
    "trusted_tracklets": 112,
    "recoverable_tracklets": 94,
    "ambiguous_tracklets": 38,
    "duplicate_tracklets": 71,
    "noise_tracklets": 112,
    "stable_subjects": 14,
    "median_tracklet_duration_sec": 0.8,
    "short_tracklet_count": 184,
    "suspected_switch_events": 17,
    "overlap_triggered_switches": 12,
    "unresolved_time_sec": 43.5
  },
  "subjects": [
    {
      "subject_id": "A03",
      "raw_tracklets": 23,
      "stints": 4,
      "resolved_time_sec": 1350.2,
      "unresolved_time_sec": 5.7,
      "suspected_switches": 2
    }
  ]
}
```

## 4.2. Minimalne metryki

Mierzyć:

```text
raw_tracklets_per_stable_subject
median_tracklet_duration_sec
p10/p50/p90 tracklet duration
short_tracklet_count
orphan_tracklet_count
suspected_switch_events
switches_after_overlap
duplicate identity conflicts
unresolved seconds
manual assignments count
manual review duration
```

## 4.3. Dlaczego jest to P0

Bez tych metryk nie wiadomo, czy lepszy wynik pochodzi z:

- lepszego YOLO;
- lepszego trackera;
- agresywniejszego stitching;
- większej liczby błędnych automatycznych merge’ów;
- ręcznego review.

Optymalizowanie tylko `tracks_count` jest niedopuszczalne.

---

# 5. P0 — tracklet quality classification

**Status: DONE w trybie `shadow_read_only`.** Klasy i reason codes są zapisywane w
`identity_tracklet_quality.json`; nie filtrują jeszcze produkcyjnego crop review.

Każdy raw tracklet powinien otrzymać klasę jakości:

```text
trusted
recoverable
ambiguous
duplicate
noise
```

## 5.1. Trusted

Przykładowe warunki:

- odpowiednia długość;
- wysoki średni detection confidence;
- poprawna geometria ruchu;
- stabilny team assignment;
- brak dużych overlapów;
- sensowny bbox size;
- niski footpoint jitter;
- brak jednoczesnego konfliktu z innym trackletem tego samego subjectu.

## 5.2. Recoverable

Tracklet nie jest idealny, ale można go połączyć przez:

- krótki time gap;
- wykonalną prędkość;
- zgodny team;
- zgodne ReID;
- spójność przed i po overlapie.

## 5.3. Ambiguous

Możliwe są co najmniej dwa podobnie dobre przypisania.

Taki tracklet może trafić do review albo switch eventu.

## 5.4. Duplicate

Jednoczesna obserwacja tej samej osoby, często wynik:

- podwójnej detekcji;
- małego bboxa fragmentu ciała;
- silnego containment;
- błędnego splitu jednej sylwetki.

## 5.5. Noise

Przykłady:

- 1–3 klatki;
- bardzo niski confidence;
- nielogiczna pozycja;
- niemożliwa prędkość;
- bbox ekstremalnie mały;
- osoba poza aktywnym boiskiem;
- pojedyncza kończyna;
- niestabilny team.

`noise` i jednoznaczne `duplicate` nie powinny trafiać do manualnego crop review.

---

# 6. P0 — footpoint reliability i occlusion awareness

**Status: PARTIAL / SHADOW.** Zagregowane reliability ratios i niewiarygodne zakresy
klatek są raportowane per tracklet. Produkcyjna pozycja, status obserwacji i naliczanie
dystansu nie zostały przez P0 zmienione.

## 6.1. Problem

Aktualna pozycja gracza jest zwykle liczona jako:

```text
bottom-center bboxa
```

Dla pełnej sylwetki jest to rozsądne przybliżenie stóp.

Dla bboxa obejmującego tylko górną połowę ciała:

```text
bottom-center bboxa = okolice pasa
```

Po homografii może to utworzyć duży sztuczny skok pozycji.

## 6.2. Nowe pola obserwacji

Każda detekcja powinna otrzymać:

```json
{
  "detection_confidence": 0.88,
  "occlusion_score": 0.56,
  "footpoint_reliable": false,
  "appearance_reliable": false,
  "position_source": "motion_prediction"
}
```

Dopuszczalne `position_source`:

```text
detected_footpoint
motion_prediction
short_gap_interpolation
offline_smoothed
unknown
```

## 6.3. Heurystyka occlusion score

Może uwzględniać:

- bbox IoU z innymi zawodnikami;
- containment;
- stosunek bbox height do mediany tego subjectu;
- nagłe skrócenie bboxa;
- nagły skok bottom-center przy stabilnym centroidzie;
- ile bboxa jest przykryte przez osoby bliżej kamery;
- liczbę osób w lokalnym klastrze;
- spadek confidence;
- zmianę aspect ratio.

## 6.4. Zasady pozycji podczas overlapu

Jeżeli `footpoint_reliable=false`:

1. nie używać bottom-center jako twardej pozycji;
2. użyć predykcji z wcześniejszego ruchu;
3. zachować identity jako `occluded`;
4. nie naliczać tej pozycji jako observed distance;
5. po rozdzieleniu zastosować offline smoothing pomiędzy ostatnią i następną wiarygodną pozycją;
6. przechowywać confidence i source w resolved timeline.

## 6.5. Rozdzielenie obecności od obserwacji

```text
subject exists
!=
subject has reliable detected position
```

Przykład:

```json
{
  "subject_id": "A03",
  "status": "occluded",
  "bbox_xyxy": null,
  "pitch_m": [12.4, 31.8],
  "position_source": "motion_prediction",
  "position_confidence": 0.42,
  "eligible_for_distance": false
}
```

---

# 7. P0 — explicit occlusion events

**Status: PARTIAL / SHADOW.** Explicit events i incoming/outgoing candidates są
generowane diagnostycznie. Joint assignment, stan `occluded` i UI review eventu
pozostają niewdrożone.

## 7.1. Początek zdarzenia

Gdy co najmniej dwa bboxy:

- należą do tej samej lub dowolnej drużyny;
- zbliżają się do siebie;
- przekraczają IoU/containment threshold;
- jeden nagle znika albo oba tracą stabilne footpointy;

utworzyć:

```text
occlusion_event
```

Przykład:

```json
{
  "event_id": "occlusion-0042",
  "start_frame": 12041,
  "end_frame": 12078,
  "incoming_subject_ids": ["A03", "A07"],
  "incoming_tracklets": ["tracklet-014", "tracklet-087"],
  "outgoing_tracklets": ["tracklet-102", "tracklet-103"],
  "status": "needs_resolution"
}
```

## 7.2. Zachowanie podczas overlapu

- nie zmieniać identity na podstawie jednej słabej obserwacji;
- utrzymywać oba subjecty jako istniejące;
- nie rysować fake detected bboxów jako pewnych detekcji;
- przechowywać predicted state;
- ograniczyć możliwość utworzenia nowego stable subjectu;
- nie aktualizować appearance prototype słabymi cropami;
- nie aktualizować footpoint history niewiarygodnym bottom-center.

## 7.3. Joint assignment po wyjściu z overlapu

Nie przypisywać outgoing trackletów niezależnie.

Dla dwóch subjectów porównać wspólnie:

```text
wariant 1:
outgoing-left  → A03
outgoing-right → A07

wariant 2:
outgoing-left  → A07
outgoing-right → A03
```

Wybrać assignment o najmniejszym łącznym koszcie.

Dla większej grupy użyć Hungarian assignment albo innego globalnego minimum dla danego eventu.

## 7.4. Review eventu

Operator powinien zobaczyć:

```text
3 sekundy przed overlapem
moment overlapu
3 sekundy po overlapie
proponowane mapowanie outgoing → incoming
confidence i powody
```

Akcje:

```text
keep identities
swap identities
choose custom mapping
mark unresolved
mark false event
```

---

# 8. P1 — anchor-based roster assignment

## 8.1. Zmiana jednostki pracy

Nie przypisywać roster player do pojedynczego cropa.

Przypisywać:

```text
stable subject
lub
long stable stint
```

## 8.2. Automatyczny wybór anchor cropów

Dla każdego stable subjectu wybrać 3–5 najlepszych cropów:

- wysokie detection confidence;
- niski occlusion score;
- `appearance_reliable=true`;
- duży bbox;
- brak overlapu;
- cropy z różnych momentów;
- preferowane spokojne restarty albo momenty bez tłoku;
- brak motion blur;
- brak boundary/bench;
- team assignment locked.

## 8.3. UI

Jedna karta:

```text
Stable Subject A03
[best crop 1] [best crop 2] [best crop 3]
Total time: 21:34
Raw tracklets: 18
Identity confidence: 0.91

Assign player: [select]
```

Jedno przypisanie:

```text
A03 → Paweł
```

powinno automatycznie przypisać roster ID do wszystkich jednoznacznie połączonych trackletów i stintów.

## 8.4. Dziedziczenie

Tracklet/stint dziedziczy player ID tylko, jeżeli:

- jest częścią tego stable subjectu;
- edge confidence przekracza wymagany próg;
- nie ma unresolved switch eventu przecinającego fragment;
- nie ma konfliktu czasowego z innym fragmentem tego player ID.

---

# 9. P1 — same-match ReID embeddings

## 9.1. Cel

Proste RGB wystarcza do wspomagania team assignment, ale zwykle nie rozróżnia dwóch zawodników w identycznych strojach.

Należy dodać lekki person ReID embedding jako dodatkowy sygnał.

Pierwszym celem nie jest rozpoznawanie zawodnika pomiędzy różnymi meczami.

Pierwszym celem jest:

```text
tracklet A i tracklet B z tego samego meczu
→ czy prawdopodobnie przedstawiają tę samą osobę?
```

## 9.2. Wybór cropów do embeddingu

Embedding generować tylko, gdy:

```text
appearance_reliable=true
bbox odpowiednio duży
occlusion_score poniżej progu
brak silnego overlapu
confidence powyżej progu
crop nie jest mocno rozmyty
```

Nie generować albo nie używać embeddingu jako wiarygodnego dla:

- fragmentu ręki;
- krótkiego cropa górnej części ciała w tłoku;
- cropa zawierającego dużą część innego zawodnika;
- bboxa o bardzo małej powierzchni;
- motion blur;
- ciemnego, przepalonego lub silnie skompresowanego cropa.

## 9.3. Prototype subjectu

Dla stable subjectu:

```python
subject_prototype = robust_median(clean_embeddings)
```

albo medoid/trimmed mean.

Nie używać zwykłej średniej wszystkich cropów, ponieważ jeden ID switch może zatruć prototype.

## 9.4. Appearance confidence

Zapisywać:

```json
{
  "embedding_model": "...",
  "embedding_version": "...",
  "embedding_quality": 0.82,
  "prototype_distance": 0.17,
  "appearance_reliable": true
}
```

## 9.5. ReID jako sygnał pomocniczy

ReID nie może samodzielnie przebić:

- niemożliwej prędkości;
- konfliktu czasowego;
- pewnej różnicy drużyn;
- dwóch jednoczesnych obserwacji;
- roli dwóch różnych bramkarzy.

---

# 10. P1 — offline tracklet graph

**Status: P1.4 DONE / SHADOW / CALIBRATED.** Zaimplementowano deterministyczną ekstrakcję i scoring
krawędzi, twarde constraints, wizualny audyt rekomendacji, pierwszy offline stable
subject rebuild, konserwatywny safe baseline continuity oraz global temporal path
selection z abstention. Dwa nowe cross-production links potwierdzono ręcznie 2/2,
a graf rozwinięto do event-level shadow timeline ze stanami `detected`, `predicted`,
`occluded` i `missing`. Produkcyjny resolver nadal nie jest podmieniany.

To jest najważniejszy długoterminowy element stabilizacji identity.

## 10.1. Graf

```text
node = raw/recoverable tracklet
edge A→B = możliwość, że B jest kontynuacją A
```

## 10.2. Edge features

Każda krawędź powinna uwzględniać:

```text
time gap
predicted position distance
required speed
velocity direction consistency
team compatibility
role compatibility
goalkeeper goal-end compatibility
same-match ReID distance
bbox size/profile similarity
footpoint reliability
occlusion event context
boundary/bench context
raw tracker continuity
tracklet quality
```

## 10.3. Twarde ograniczenia

Krawędź jest niedozwolona, jeżeli:

- tracklety nakładają się w czasie w różnych miejscach;
- wymagają niemożliwej prędkości;
- mają pewne, różne drużyny;
- reprezentują pewnych różnych bramkarzy;
- były jednocześnie przypisane do dwóch aktywnych subjectów;
- połączenie tworzy konflikt roster player ID;
- tracklet jest oznaczony `noise` lub jednoznaczny `duplicate`.

## 10.4. Globalne rozwiązanie

Nie optymalizować każdej krawędzi niezależnie.

Rozwiązać cały mecz globalnie albo w dużych oknach z overlapem.

Możliwe warianty:

```text
min-cost flow
minimum path cover
Hungarian assignment per temporal layer
integer programming dla małych problemów
graph clustering z constraints
```

Agent ma wybrać rozwiązanie proporcjonalne do skali i budżetu projektu.

Preferowane jest rozwiązanie proste, deterministyczne i łatwe do debugowania przed bardziej złożonym ML.

## 10.5. Maksymalna liczba aktywnych subjectów

Resolver powinien korzystać z wiedzy o expected player count, ale nie wymuszać błędnego merge’u tylko po to, aby osiągnąć dokładnie 14.

Dopuszczalne:

```text
14 stable subjects
+ unresolved/orphan tracklets
```

Niedopuszczalne:

```text
agresywny merge obcych zawodników tylko dlatego, że limit to 14
```

## 10.6. Offline future evidence

Resolver ma wykorzystywać dane po zdarzeniu:

- zachowanie po wyjściu z overlapu;
- późniejszą pozycję;
- powrót do typowej strefy;
- późniejsze czyste cropy;
- spójność całej trajektorii.

To jest przewaga nad online trackerem, który musi decydować natychmiast.

---

# 11. P1 — tracking frequency i frame stride

## 11.1. Rozdzielenie częstotliwości

```text
player detection/tracking FPS
!=
analytics sampling FPS
```

Możliwy kompromis:

```text
tracking: 10–15 FPS
analytics output: 5–10 FPS
```

## 11.2. Benchmark

Na tych samych trudnych fragmentach przetestować:

```text
frame_stride = 1
frame_stride = 2
frame_stride = 3
```

Porównać:

```text
raw tracklets
median tracklet duration
switches after overlap
missed player frames
processing time
GPU memory
```

Nie zakładać z góry, że pełne 30 FPS jest konieczne.

Nie używać niższego stride tylko dlatego, że daje więcej raw tracków — celem jest mniejsza fragmentacja i mniej switchy.

---

# 12. P1 — zachowanie stanu `occluded`

## 12.1. Nowy stan

Dodać jawny status:

```text
occluded
```

obok:

```text
detected
predicted
ambiguous
missing
inactive
```

## 12.2. Semantyka

`occluded` oznacza:

- subject nadal istnieje;
- nie ma wiarygodnej detekcji;
- identity nie powinna zostać zwolniona natychmiast;
- pozycja może być predykowana;
- observation nie jest eligible do wszystkich statystyk.

## 12.3. Czas utrzymania

Przetestować zakres około:

```text
0.5–1.5 s
```

zależnie od FPS, prędkości i tłoku.

Nie trzeba rysować ghost bboxa w finalnym overlayu.

Można pokazać subtelny marker predicted/occluded w debug overlay.

## 12.4. Reacquisition

Nowy tracklet po overlapie lub krótkiej luce powinien najpierw próbować wrócić do aktywnego `occluded` subjectu, zanim utworzy nowy stable subject.

---

# 13. P1 — switch event review zamiast crop review

## 13.1. Nowa jednostka review

```text
identity switch event
```

zamiast:

```text
pojedynczy crop
```

## 13.2. Event types

```text
possible_swap
possible_merge
possible_split
orphan_reacquisition
duplicate_subject_conflict
team_switch_conflict
roster_overlap_conflict
```

## 13.3. Review card

Każda karta zawiera:

- timestamp;
- clip przed/po;
- incoming subject IDs;
- outgoing tracklets;
- propozycję systemu;
- alternatywne mapowanie;
- motion cost;
- appearance similarity;
- team/role evidence;
- confidence;
- reason codes.

## 13.4. Akcje

```text
accept proposed mapping
swap
manual map
split subject
merge subject
mark unresolved
mark false positive
```

## 13.5. Efekt decyzji

Jedna decyzja powinna aktualizować cały fragment/stint, nie pojedynczy crop.

Po review należy przebudować:

```text
stable subject mapping
player identity assignments
resolved timeline
resolved player stats
heatmaps
player-level passes/events, jeśli zależą od identity
quality/readiness
```

---

# 14. P1 — orphan tracklet review

Po automatycznym stitching operator widzi tylko orphan tracklets spełniające próg istotności.

## 14.1. Nie pokazuj

- 1–3 klatkowych śmieci;
- duplicate fragmentów;
- niskiej jakości cropów;
- trackletów poza boiskiem;
- fragmentów już objętych occlusion eventem;
- trackletów krótszych niż ustalony próg bez wpływu na statystyki.

## 14.2. Pokazuj

- dłuższe unresolved fragmenty;
- fragmenty wpływające na czas gry;
- tracklety z wysokim confidence;
- fragmenty mogące należeć do dwóch subjectów;
- fragmenty zawierające posiadanie piłki lub ważne eventy;
- fragmenty powodujące konflikt dwóch player IDs.

## 14.3. Jedna decyzja

Operator przypisuje cały tracklet/stint:

```text
orphan tracklet-082 → Stable Subject A03
```

Nie przypisuje każdego cropa osobno.

---

# 15. P2 — persistent player gallery pomiędzy meczami

## 15.1. Cel

Po zatwierdzeniu roster assignment i czystych cropów utworzyć profil zawodnika:

```json
{
  "player_id": "player-007",
  "team_id": "team-a",
  "approved_embeddings": [],
  "embedding_prototype": [],
  "height_profile": {},
  "aspect_profile": {},
  "role_history": [],
  "updated_at": "..."
}
```

## 15.2. Bezpieczna aktualizacja

Do gallery trafiają tylko cropy:

- ręcznie zatwierdzone;
- bez overlapu;
- `appearance_reliable=true`;
- wysokiej jakości;
- zgodne z istniejącym prototype;
- pochodzące z fragmentu bez unresolved switcha.

Nie aktualizować galerii wszystkimi automatycznie przypisanymi cropami.

## 15.3. Następny mecz

System może proponować:

```text
Stable Subject A04
→ prawdopodobnie Paweł
confidence: 0.81
```

Operator nadal zatwierdza initial anchors.

Cross-match ReID jest pomocą, nie źródłem prawdy.

---

# 16. P2 — position/role priors

## 16.1. Słabe priory dla field players

Można używać jako małego kosztu:

- typowa strona boiska;
- przeciętna wysokość pozycji;
- rola ofensywna/defensywna;
- czas przebywania blisko linii;
- częste zmiany.

Nie może to być twarda reguła, ponieważ zawodnicy zamieniają pozycje.

## 16.2. Mocniejszy prior dla bramkarzy

Dla bramkarzy można zastosować silniejsze constraints:

- role goalkeeper;
- goal end;
- ograniczona strefa;
- osobny strój;
- zmiana stron po połowie;
- niewielka liczba kandydatów.

## 16.3. Nie używać roli jako tożsamości

```text
zawodnik jest na lewym skrzydle
```

nie oznacza:

```text
to na pewno player X
```

---

# 17. Player YOLO — co poprawia, a czego nie

## 17.1. Co lepszy model może poprawić

- mniej missed detections;
- dwa bboxy utrzymane dłużej podczas overlapu;
- stabilniejszy bbox;
- stabilniejszy footpoint dla pełnych sylwetek;
- mniej false positives;
- wyższe confidence dla dalekich graczy;
- mniej merged-person frames.

## 17.2. Czego nie rozwiąże

- który z dwóch identycznie ubranych zawodników wyszedł z overlapu;
- globalnej ciągłości przez dłuższe zasłonięcie;
- roster player ID;
- błędnego online association przy dwóch równie dobrych wariantach;
- potrzeby freshness/rebuild resolved stats;
- niepewności między podobnymi członkami tej samej drużyny.

## 17.3. Dataset targeted improvement

Nie trenować nowego player modelu tylko na losowych łatwych klatkach.

Zbierać:

```text
penalty area crowds
same-team overlaps
crossing trajectories
partial occlusions
far-side players
motion blur
players near pitch boundary
players behind another player
```

Tagi organizacyjne:

```text
overlap
severe_occlusion
partial_occlusion
crowded
far_view
merged_detection_failure
```

## 17.4. Annotation policy

```text
widoczny i jednoznaczny zawodnik
→ oznacz visible extent

bboxy zachodzą na siebie
→ to jest poprawne

widoczna tylko kończyna
→ nie oznaczaj

nie zmniejszaj bboxa tylko po to, aby uniknąć overlapu
```

---

# 18. Docelowy operator workflow

## Krok 1 — Team and roster setup

Operator definiuje:

- drużyny;
- zawodników;
- role bramkarzy;
- ewentualne zmiany.

## Krok 2 — Automatic stable subject build

System tworzy stable subjects i pokazuje quality summary.

## Krok 3 — Initial anchor assignment

Około 14 kart:

```text
Stable Subject → roster player
```

## Krok 4 — Switch event review

Kilka/kilkanaście eventów:

```text
keep
swap
custom
unknown
```

## Krok 5 — Orphan review

Tylko istotne unresolved tracklets.

## Krok 6 — Final identity quality check

```text
automatically assigned time
manually confirmed time
unresolved time
suspected switches
roster overlap conflicts
subjects without roster assignment
```

## Krok 7 — Publish readiness

Player-level stats są publikowane tylko, jeśli spełniają readiness gate.

---

# 19. Quality gates

## 19.1. Team-level analyses

Mogą działać nawet przy pewnych switchach wewnątrz jednej drużyny, jeśli:

- pozycje wszystkich graczy są dostępne;
- team labels są poprawne;
- nie ma duplikatów;
- nie ma długich missing ranges.

## 19.2. Player-level analyses

Przed publikacją wymagają:

```text
roster assignment confirmed
brak długich unresolved switchy
brak dwóch jednoczesnych fragmentów tego samego player ID
resolved coverage powyżej progu
estimated/predicted position share poniżej progu
```

## 19.3. Proponowany status

```text
ready
ready_with_review
experimental
not_available
```

Przykład:

```json
{
  "player_identity": {
    "status": "ready_with_review",
    "resolved_time_ratio": 0.966,
    "manual_review_complete": true,
    "unresolved_time_sec": 27.4,
    "suspected_switches_open": 0,
    "duplicate_roster_conflicts": 0
  }
}
```

---

# 20. KPI projektu

Główne KPI:

```text
ID switches per 10 minutes
tracklets per real player
raw tracklets per stable subject
% playing time automatically resolved
% playing time manually confirmed
% playing time unresolved
manual decisions per match
manual review time
orphan tracklets shown to operator
duplicate player conflicts
longest unresolved switch duration
```

## 20.1. Cel pierwszego etapu

```text
14 initial roster assignments
5–20 switch/orphan decisions
<15 minut review
>95% czasu gry przypisane
0 znanych długich switchy po review
0 duplicate roster conflicts
```

## 20.2. Nieakceptowalne KPI

Nie uznawać za sukces wyłącznie:

```text
mniejszej liczby stable subjects
mniejszej liczby raw tracks
większego resolved coverage
```

jeśli osiągnięto je przez błędne, agresywne merge’e.

---

# 21. Kolejność implementacji według ROI

## Milestone 1 — observability i review reduction

1. `identity_fragmentation_report.json`;
2. tracklet quality classification;
3. occlusion score;
4. footpoint reliability;
5. ukrycie noise/duplicates przed crop review;
6. switch event candidates;
7. initial anchor assignment per stable subject;
8. review całych trackletów zamiast pojedynczych cropów.

### Definition of Done

Operator nie musi oglądać większości śmieciowych cropów, a system pokazuje mierzalną liczbę switch/orphan decisions.

---

## Milestone 2 — occlusion-aware identity

1. explicit occlusion events;
2. state `occluded`;
3. predicted position bez fake detected bbox;
4. footpoint reliability gating;
5. joint outgoing assignment;
6. review clipów overlapów;
7. integration tests na ręcznie opisanych overlapach.

### Definition of Done

Krótkie zasłonięcie dwóch graczy nie tworzy automatycznie nowych stable subjects ani pochopnego switcha.

---

## Milestone 3 — same-match ReID

1. [x] model adapter;
2. [x] crop quality gate;
3. [x] embeddings cache;
4. [x] robust subject prototypes;
5. appearance distance w assignment cost;
6. [x] diagnostyka i explainability;
7. [x] test set tych samych i różnych zawodników.

### Definition of Done

ReID poprawia stitching na trudnych fragmentach bez zwiększenia false merges na konflikcie czasowym/teamowym.

---

## Milestone 4 — offline graph stitching

1. canonical tracklet nodes;
2. edge feature extraction;
3. hard constraints;
4. global optimizer;
5. confidence margins;
6. orphan detection;
7. stable subject rebuild;
8. resolved timeline integration;
9. comparison z dotychczasowym resolverem.

### Definition of Done

Automatyczne rozwiązanie całego meczu zmniejsza liczbę manualnych decyzji i switchy na goldsecie.

---

## Milestone 5 — persistent roster assistance

1. zatwierdzona player gallery;
2. cross-match suggestions;
3. safe gallery updates;
4. operator confirmation;
5. drift/contamination detection.

### Definition of Done

W kolejnym meczu system proponuje roster assignments, ale nie zatruwa gallery błędnymi automatycznymi cropami.

---

# 22. Goldset i testy

## 22.1. Identity goldset

Przygotować ręcznie opisane fragmenty:

```text
normal movement
two-player same-team overlap
opponent overlap
three-player crowd
merged YOLO bbox
one player fully hidden
partial bbox with unreliable footpoint
players crossing trajectories
player leaves and returns
substitution/boundary sequence
goalkeeper near field player
```

## 22.2. Format

Przykład:

```json
{
  "clip_id": "overlap-001",
  "start_frame": 12000,
  "end_frame": 12120,
  "ground_truth": {
    "incoming": ["player-03", "player-07"],
    "outgoing": ["player-03", "player-07"],
    "expected_mapping": {
      "tracklet-102": "player-07",
      "tracklet-103": "player-03"
    }
  }
}
```

## 22.3. Testy jednostkowe

- overlap detection;
- occlusion score;
- footpoint reliability;
- impossible-speed edge rejection;
- temporal overlap conflict;
- team conflict;
- goalkeeper conflict;
- duplicate classification;
- noise suppression;
- joint assignment keep;
- joint assignment swap;
- ambiguous assignment;
- robust prototype resistant to one outlier;
- no prototype update from occluded crop.

## 22.4. Testy integracyjne

- raw tracks → stable subjects;
- subject assignment → resolved timeline;
- switch review → stats rebuild;
- orphan assignment → coverage increase;
- duplicate conflict prevention;
- stale resolved stats detection;
- review decision persists after rebuild;
- deterministic result for same inputs.

## 22.5. Benchmark

Porównać current vs new:

```text
ID switches
false merges
false splits
resolved coverage
manual decisions
review time
processing time
```

---

# 23. Explainability

Każde automatyczne połączenie powinno mieć powody:

```json
{
  "source_tracklet_id": "tracklet-014",
  "target_tracklet_id": "tracklet-102",
  "decision": "linked",
  "confidence": 0.87,
  "cost": 12.4,
  "components": {
    "motion": 2.1,
    "time_gap": 1.2,
    "appearance": 3.4,
    "team": 0.0,
    "role": 0.0,
    "occlusion_context": 1.1,
    "boundary": 0.0
  },
  "reasons": [
    "same_team",
    "feasible_speed",
    "same_occlusion_event",
    "appearance_match"
  ]
}
```

Operator i developer muszą móc zrozumieć, dlaczego dwa fragmenty zostały połączone.

---

# 24. Data contracts

## 24.1. Stable identifiers

Nie używać wyłącznie indeksów zależnych od kolejności.

Tracklet/event keys powinny być stabilne względem nieistotnego rebuilda.

## 24.2. Lineage

Resolved identity artifacts powinny zapisywać:

```text
source raw tracks hash
team config hash
identity review hash
split decisions hash
ReID model version
graph algorithm version
parameters
```

## 24.3. Freshness

Zmiana:

- team config;
- manual split;
- switch review;
- anchor assignment;
- ReID model/version;
- graph parameters;

musi oznaczyć downstream identity/stats artifacts jako stale albo przebudować je automatycznie.

---

# 25. Anti-goals

Na tym etapie nie należy:

- budować face recognition;
- polegać na OCR numerów jako głównym sygnale;
- wymuszać dokładnie 14 subjects kosztem false merge;
- traktować każdego raw track ID jako zawodnika;
- przypisywać pojedynczych kończyn jako `player`;
- używać cropów z overlapów do persistent gallery;
- automatycznie zatwierdzać cross-match identity bez operatora;
- publikować player stats z unresolved długimi switchami;
- trenować nowego dużego player YOLO bez benchmarku merged/missed detections;
- ukrywać niepewności przez agresywną interpolację;
- używać predicted positions jako observed distance;
- optymalizować tylko pod jeden mecz.

---

# 26. Rekomendowany pierwszy task dla agenta

Tytuł:

```text
Reduce player identity review with tracklet quality, occlusion diagnostics and subject anchors
```

Zakres pierwszego taska:

```text
identity_fragmentation_report.json
tracklet quality classification
occlusion_score
footpoint_reliable
appearance_reliable
noise/duplicate filtering from manual review
best anchor crops per stable subject
one roster assignment per stable subject
suspected switch event list
basic review UI contract
unit and integration tests
```

Nie implementować jeszcze pełnego ReID ani globalnego graph optimizer w tym samym pierwszym tasku.

Najpierw należy zredukować review i zebrać metryki, które pokażą, gdzie faktycznie tracona jest tożsamość.

---

# 27. Acceptance Criteria całej roadmapy

- [x] system mierzy track fragmentation (`identity_fragmentation_report.json`);
- [x] raw track count jest raportowany oddzielnie od stable subjects;
- [x] tracklety mają klasy jakości w warstwie shadow;
- [ ] noise i duplicate tracklety nie zaśmiecają review;
- [ ] obserwacje posiadają `occlusion_score`;
- [ ] obserwacje posiadają `footpoint_reliable`;
- [ ] niewiarygodny partial bbox nie aktualizuje pozycji jako pewny footpoint;
- [x] istnieje jawny stan `occluded` w shadow timeline; wdrożenie produkcyjne pozostaje otwarte;
- [x] overlap tworzy explicit shadow event z evidence;
- [ ] overlap event ma operator review UI;
- [ ] outgoing tracklety są rozwiązywane wspólnie;
- [ ] roster assignment działa per stable subject;
- [ ] system automatycznie wybiera najlepsze anchor cropy;
- [ ] manual decision aktualizuje cały fragment, nie jeden crop;
- [ ] orphan review pokazuje tylko istotne tracklety;
- [x] same-match ReID używa tylko reliable cropów;
- [x] ReID nie przebija twardych constraintów w warstwie shadow evidence;
- [x] shadow stitching candidates mają explainable edge costs i hard constraints;
- [x] rekomendowane krawędzie mają karty source/transition/target i eksport review JSON;
- [x] shadow offline graph ma globalny optimizer i buduje odseparowany candidate stable subjects;
- [x] shadow active roster usuwa ścisłe duplikaty i ogranicza wizualny skład do 7 na drużynę;
- [x] shadow visual labels nie dziedziczą produkcyjnego anchora z przeciwnej drużyny;
- [x] shadow fragment consolidation odrzuca równoległe konflikty i generuje audytowalne propozycje;
- [ ] produkcyjny offline graph przebudowuje stable subjects po promotion gate;
- [ ] persistent gallery przyjmuje tylko zatwierdzone próbki;
- [x] shadow resolved timeline odróżnia detected, predicted, occluded i missing;
- [x] predicted/occluded positions w shadow timeline nie są liczone jako observed distance;
- [ ] player-level readiness blokuje nierzetelne statystyki;
- [x] istnieją frozen benchmarki easy90 i hard3m z no-impact gates;
- [ ] istnieje pełny ręcznie opisany identity goldset z expected mappings;
- [ ] mierzone są false merges i false splits;
- [ ] manual review time jest mierzone;
- [ ] docelowo operator wykonuje około 14 anchor assignments i kilka/kilkanaście decyzji wyjątków;
- [ ] docelowy review time wynosi mniej niż 15 minut na mecz.

---

# 28. Finalny raport agenta

Po każdym milestone agent ma podać:

## Zmienione pliki

Lista plików.

## Zmieniony flow

```text
raw tracks
→ tracklet classification
→ stable subjects
→ review
→ resolved timeline
```

## Nowe artefakty

Nazwy, schema versions i source of truth.

## Metryki before/after

```text
raw tracklets
switches
false merges
false splits
resolved coverage
manual decisions
review time
```

## Testy

```text
backend unit tests
integration tests
client typecheck/build
identity goldset evaluation
```

## Znane ograniczenia

W szczególności:

- identical kits ograniczają ReID;
- severe full occlusion może pozostać nierozwiązywalne;
- cross-match identity wymaga operator confirmation;
- player YOLO improvements pozostają osobnym, mierzalnym eksperymentem;
- manual review nadal jest źródłem prawdy dla najbardziej niejednoznacznych sytuacji.
