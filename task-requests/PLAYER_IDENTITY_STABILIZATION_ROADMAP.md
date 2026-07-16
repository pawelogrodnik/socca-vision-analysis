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

1. model adapter;
2. crop quality gate;
3. embeddings cache;
4. robust subject prototypes;
5. appearance distance w assignment cost;
6. diagnostyka i explainability;
7. test set tych samych i różnych zawodników.

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

- [ ] system mierzy track fragmentation;
- [ ] raw track count nie jest traktowany jako liczba zawodników;
- [ ] tracklety mają klasy jakości;
- [ ] noise i duplicate tracklety nie zaśmiecają review;
- [ ] obserwacje posiadają `occlusion_score`;
- [ ] obserwacje posiadają `footpoint_reliable`;
- [ ] niewiarygodny partial bbox nie aktualizuje pozycji jako pewny footpoint;
- [ ] istnieje jawny stan `occluded`;
- [ ] overlap tworzy reviewable event;
- [ ] outgoing tracklety są rozwiązywane wspólnie;
- [ ] roster assignment działa per stable subject;
- [ ] system automatycznie wybiera najlepsze anchor cropy;
- [ ] manual decision aktualizuje cały fragment, nie jeden crop;
- [ ] orphan review pokazuje tylko istotne tracklety;
- [ ] same-match ReID używa tylko reliable cropów;
- [ ] ReID nie przebija twardych constraintów;
- [ ] offline graph ma explainable edge costs;
- [ ] persistent gallery przyjmuje tylko zatwierdzone próbki;
- [ ] resolved timeline odróżnia detected, predicted i occluded;
- [ ] predicted positions nie są liczone jako observed distance;
- [ ] player-level readiness blokuje nierzetelne statystyki;
- [ ] istnieje identity goldset;
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
