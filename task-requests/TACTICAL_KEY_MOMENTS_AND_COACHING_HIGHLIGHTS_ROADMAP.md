# Tactical Key Moments & Coaching Highlights Roadmap

## Status

```text
SEPARATE ROADMAP / SHADOW-FIRST
TEAM-LEVEL BEFORE PLAYER-LEVEL
CANDIDATE + REVIEW BEFORE AUTOMATIC COACHING CLAIMS
```

Baseline przeanalizowany przy tworzeniu dokumentu:

```text
01923e57662907d1f26671f2f3a542cb28b59ff1
```

Dokument uzupełnia:

```text
task-requests/ANALYTICS_FEATURE_READINESS_ROADMAP.md
task-requests/CURRENT_PIPELINE_AND_MOMENTUM_POST_IMPLEMENTATION_REVIEW.md
task-requests/PLAYER_IDENTITY_STABILIZATION_ROADMAP.md
```

---

# 0. Cel produktu

Celem jest automatyczne znajdowanie fragmentów meczu przydatnych dla zawodników i trenera, a nie tylko efektownych goli.

System powinien umieć proponować między innymi:

```text
wysoki lub środkowy przechwyt prowadzący do kontry
szybkie wyjście spod pressingu
rozegranie od bramkarza do final third
szybką kombinację 3-4 podań
szybką zmianę strony
progresję zakończoną wejściem w niebezpieczną strefę
groźną stratę pod własną bramką
counterpress i szybki odzysk
zatrzymanie groźnej akcji przeciwnika
okres długiej presji pod bramką
akcję zakończoną golem, gdy goal event jest dostępny
```

Docelowy flow:

```text
canonical ball/team/possession data
→ possession sequences
→ atomic event candidates
→ compound tactical-pattern candidates
→ confidence + evidence + readiness
→ operator review
→ ranking i diversity selection
→ klipy dla drużyny/trenera
```

Najważniejsza zasada:

> System ma wykrywać obserwowalne wzorce gry, a nie zgadywać intencję zawodników ani tworzyć pewne narracje z niepewnych danych.

---

# 1. Czy potrzebujemy perfekcyjnego przypisania bramkarza i zawodników?

Nie.

## 1.1. Team-level moments

Większość pierwszych feature nie wymaga rzeczywistego `player_id`:

```text
Team A przejęła piłkę przy połowie
Team A w 6 sekund przeniosła piłkę do final third
Team B straciła piłkę w defensive third
Team A wykonała szybką sekwencję podań
```

Wymagane są przede wszystkim:

```text
poprawna geometria i kierunek ataku
stabilna pozycja piłki
wiarygodny team assignment
wiarygodne team possession
poprawne timestamps
```

Zamiana dwóch stable IDs wewnątrz tej samej drużyny nie musi zepsuć takich analiz, jeżeli obie pozycje nadal należą do właściwej drużyny.

## 1.2. Player-level enrichment

Rzeczywisty `player_id` jest potrzebny dopiero do komunikatów:

```text
Paweł rozpoczął kontrę
Piotrek odzyskał piłkę
Kamil wykonał trzy progresywne podania
```

Bez G3 system może nadal pokazać poprawny team-level moment bez nazwiska.

## 1.3. Goalkeeper-specific moments

Moment nazwany:

```text
rozegranie od bramkarza
```

wymaga zaufanej roli GK na początku sekwencji, ale nie perfekcyjnego śledzenia bramkarza przez cały mecz.

Dozwolone źródła trusted GK:

```text
explicit roster role
operator-confirmed goalkeeper role
trusted match configuration
```

Nie wystarczy:

```text
kolorystyczny outlier
zawodnik stojący najbliżej bramki
pojedynczy visual guess
```

Gdy rola GK nie jest pewna, ten sam wzorzec można bezpiecznie nazwać:

```text
deep build-up from defensive third
```

zamiast:

```text
goalkeeper build-up
```

## 1.4. Wymagana filozofia jakości

Nie potrzebujemy absolutnego zera błędów w całym meczu.

Potrzebujemy:

```text
wysokiego confidence w konkretnym candidate moment
jawnego unresolved/unknown
braku pewnej etykiety przy niepewnym GK/team
szybkiego operator review
```

Lepszy wynik:

```text
8 dobrych momentów
3 niepewne pominięte
0 fałszywych narracji
```

niż:

```text
20 automatycznych momentów
część z błędną drużyną lub zmyśloną intencją
```

---

# 2. Aktualny fundament repo

Repo posiada już znaczną część danych potrzebnych do candidate layer:

```text
pitch calibration i pitch_m
camera-motion compensation
attack direction / match phases
stable players i team labels
ball tracks detected/interpolated/unknown
possession controlled/contested/free/unknown
possession transitions
contact/event candidates
pass candidates i outcome
restart candidates
attacking momentum experimental
video timestamps i match package
```

Najważniejsze ograniczenia:

```text
possession nadal wymaga goldset validation
pass candidates nadal są candidate layer
shot/save/goal nie są stabilnym fundamentem
attacking momentum nie jest xG ani oficjalną statystyką
manual review może powodować stale downstream artifacts, dopóki rebuild flow nie jest naprawiony
```

Ten feature nie może omijać freshness i readiness z istniejących roadmap.

---

# 3. Model danych: od zdarzenia do ciekawej akcji

## 3.1. Atomic events

Podstawowe obserwowalne zdarzenia:

```text
possession_start
possession_end
possession_change
controlled_touch
pass_attempt
completed_pass
failed_pass
ball_progression
zone_entry
zone_exit
restart
goalkeeper_release_candidate
shot_candidate
save_candidate
goal_candidate
ball_out
```

## 3.2. Possession sequence

Sekwencja possession jest jednostką nadrzędną:

```json
{
  "sequence_id": "...",
  "team_label": "A",
  "start_time_sec": 120.4,
  "end_time_sec": 131.1,
  "start_position_m": [12.0, 4.0],
  "end_position_m": [39.0, 19.0],
  "start_zone": "defensive_third",
  "end_zone": "final_third",
  "duration_sec": 10.7,
  "pass_attempts": 4,
  "completed_passes": 4,
  "forward_progress_m": 27.0,
  "peak_ball_speed_mps": null,
  "possession_confidence": 0.93,
  "data_coverage": 0.88
}
```

Nie przerywać sekwencji automatycznie przez pojedynczy krótki possession flicker. Korzystać z canonical possession transition/debounce.

## 3.3. Compound tactical moment

Ciekawa akcja jest zwykle wzorcem składającym się z kilku atomic events:

```json
{
  "moment_id": "...",
  "type": "midfield_regain_to_counterattack",
  "team_label": "A",
  "start_time_sec": 430.2,
  "end_time_sec": 441.8,
  "peak_time_sec": 438.5,
  "confidence": 0.87,
  "interestingness_score": 0.81,
  "readiness": "ready_with_review",
  "evidence": {},
  "warnings": [],
  "clip": {
    "pre_roll_sec": 5.0,
    "post_roll_sec": 4.0
  }
}
```

---

# 4. Typy momentów MVP

# 4.1. Midfield/high regain → counterattack

Przykład produktowy:

```text
Dynamiczny przechwyt przy połowie i szybki kontratak
```

Obserwowalna definicja:

```text
possession zmienia się na Team A
+ miejsce odzysku znajduje się w middle third lub attacking half
+ Team A przesuwa piłkę znacząco w kierunku bramki
+ progresja następuje szybko po odzysku
+ sekwencja dociera do final third / danger zone albo tworzy wysoki momentum peak
```

Początkowe candidate features:

```text
regain_x/y
regain_zone
seconds_to_final_third
forward_progress_m
progression_rate_mps
completed_passes_after_regain
peak_momentum_after_regain
sequence_end_reason
```

Przykładowy heuristic candidate:

```text
possession_change confidence >= threshold
+ regain w middle third lub wyżej
+ >= 12-15 m progresji w <= 6-8 s
+ wejście do final third lub wysoki danger/momentum delta
```

Nie wymaga:

```text
real player_id
shot detection
perfect pass detection
trusted goalkeeper
```

Pass candidates mogą wzbogacać opis, ale ruch piłki i possession wystarczą do V1.

Bez trusted player identity opis brzmi:

```text
Team A: przechwyt i szybka kontra
```

Nie:

```text
Paweł wykonał przechwyt
```

# 4.2. Counterpress regain

Definicja:

```text
Team A traci controlled possession
→ Team B przejmuje piłkę
→ Team A odzyskuje ją w krótkim czasie
→ odzysk następuje blisko miejsca straty
```

Candidate evidence:

```text
loss_time
regain_time
seconds_to_regain
loss_position_m
regain_position_m
spatial_distance_m
possession confidence przed/po
```

Początkowo:

```text
regain <= 5-8 s od straty
+ regain <= 8-12 m od miejsca straty
```

Progi muszą zostać skalibrowane do małego boiska i nie mogą być kopiowane bezpośrednio z piłki 11-osobowej.

# 4.3. Goalkeeper/deep build-up → final third

Przykład:

```text
Szybkie rozegranie od bramkarza pod pole karne przeciwnika
```

Warstwa bazowa:

```text
possession sequence starts in own defensive zone
+ first trusted controller is goalkeeper albo sequence starts very close to own goal
+ team zachowuje possession
+ piłka dociera do middle/final third
+ sekwencja spełnia warunek czasu lub liczby podań
```

Dwa statusy etykiety:

```text
goalkeeper_build_up
→ trusted GK role confirmed

deep_build_up
→ start w defensive zone, ale GK role niepewna
```

Candidate features:

```text
trusted_gk_at_start
start_distance_to_own_goal_m
seconds_to_halfway
seconds_to_final_third
completed_passes
progressive_passes
forward_progress_m
possession_retained
sequence_duration
```

Przykładowy pattern:

```text
trusted GK controlled/released ball
+ 3-5 completed passes
+ final-third entry w <= 10-15 s
+ brak utraty possession po drodze
```

Nie nazywać sekwencji `goalkeeper_build_up`, jeżeli goalkeeper attribution jest low-confidence.

# 4.4. Rapid combination / possible one-touch sequence

Przykład:

```text
3-4 szybkie podania z pierwszej piłki
```

Automatyczne udowodnienie intencji `one-touch` jest trudne. Dlatego model ma rozróżniać:

```text
rapid_combination
possible_one_touch_combination
confirmed_one_touch_combination
```

## Rapid combination

Może być wykrywana na podstawie:

```text
minimum 3 completed pass candidates
krótkie interwały między release/contact
possession pozostaje w tej samej drużynie
łączna progresja lub zmiana strefy
brak długiego ball hold pomiędzy podaniami
```

## Possible one-touch

Dodatkowe evidence:

```text
krótki czas od receive do release
brak dłuższego controlled carry
stabilny receiver/passer candidate
wystarczające detected ball coverage przy kontakcie
```

## Confirmed one-touch

Powinno wymagać operator review albo bardzo mocnego contact/touch modelu.

W MVP używać w UI nazwy:

```text
Szybka kombinacja podań
```

Nie:

```text
Cztery podania z pierwszej piłki
```

chyba że evidence spełnia wysoki gate.

# 4.5. Fast progression / press escape

Definicja:

```text
possession start w own defensive third
+ przeciwnicy znajdują się blisko piłki lub team jest pod presją
+ piłka szybko opuszcza defensive pressure zone
+ possession zostaje utrzymane
```

Wersja bez stabilnego pressure modelu:

```text
fast progression from defensive third
```

Wersja z pressure evidence:

```text
successful press escape
```

Nie nazywać `press escape`, jeżeli system nie ma wystarczającego evidence obecności presji.

# 4.6. Dangerous loss

Definicja:

```text
Team A traci controlled possession
+ strata jest w own defensive third albo centralnej danger zone
+ Team B szybko zwiększa attacking momentum / przesuwa piłkę w stronę bramki
```

Opis:

```text
Groźna strata pod własną bramką
```

Player attribution dopiero po G3.

# 4.7. Danger neutralized / defensive intervention candidate

Bez shot detection system nie powinien automatycznie twierdzić:

```text
świetna obrona bramkarza
```

Może wykryć:

```text
wysokie zagrożenie przeciwnika
→ nagły koniec progresji
→ possession change / ball clear / wyjście ze strefy
→ duży spadek attacking momentum lub possession danger
```

Bezpieczna etykieta:

```text
Groźna akcja zatrzymana
```

Dopiero dodatkowe evidence może rozróżnić:

```text
defender interception
goalkeeper intervention
blocked attempt
opponent mistake
ball out
```

# 4.8. Sustained pressure

Definicja:

```text
przeciwnik utrzymuje wysoką wartość momentum/danger przez określony czas
+ kilka wejść, odzysków lub podań w attacking third
+ piłka nie wraca trwale do neutralnej strefy
```

Może służyć do wskazywania:

```text
okresów, gdy drużyna była zamknięta pod bramką
okresów wysokiej presji własnej drużyny
```

---

# 5. Possession Danger zamiast xG

Bez stabilnego shot detectora nie liczyć klasycznego xG.

Dla candidate moments można używać eksperymentalnej wartości:

```text
possession_danger
```

V0 może zależeć od:

```text
pozycji piłki względem atakowanej bramki
odległości do bramki
kąta widzenia bramki
kontrolującej drużyny
kierunku i szybkości progresji
wejścia do final third / central danger zone
```

V1 może dodać:

```text
pozycję bramkarza, gdy trusted
liczbę obrońców w pobliżu piłki
liczbę obrońców pomiędzy piłką a bramką
support wokół ball carrier
```

W UI:

```text
Threat / Danger (experimental)
```

Nie:

```text
xG
prawdopodobieństwo gola
```

Dopóki nie ma strzału i modelu kalibrowanego na outcome.

---

# 6. Confidence i etykiety

Każdy moment musi posiadać osobne confidence components:

```json
{
  "confidence": {
    "overall": 0.84,
    "ball": 0.91,
    "team": 0.96,
    "possession": 0.89,
    "passes": 0.73,
    "goalkeeper": null,
    "geometry": 0.98,
    "pattern": 0.85
  }
}
```

## 6.1. Degradacja etykiety

Przykład:

```text
trusted GK + sequence pattern
→ goalkeeper_build_up

unknown GK + ten sam pattern
→ deep_build_up

unknown team possession
→ moment blocked / not available
```

```text
high-confidence short receive-release
→ possible_one_touch_combination

same pass timings bez touch confidence
→ rapid_combination
```

## 6.2. Readiness

Statusy:

```text
ready
ready_with_review
experimental
not_available
```

Niepewny moment może pojawić się lokalnie do review, ale nie może być publikowany jako pewny coaching insight.

---

# 7. Interestingness score

`interestingness_score` służy do rankingu, nie jest statystyką piłkarską.

Przykładowe komponenty:

```text
zone_value_delta
possession_danger_delta
forward_progress_m
progression_rate_mps
sequence_speed
completed_pass_count
progressive_pass_count
regain_height
seconds_from_regain_to_entry
momentum_peak
rarity_bonus
outcome bonus, gdy goal/shot istnieje
confidence multiplier
```

Przykładowa semantyka:

```text
wysoki score
→ kandydat do top highlights

średni score
→ widoczny w pełnej timeline

niski score
→ zachowany diagnostycznie, nie generuje klipu
```

Nie mieszać confidence z interestingness:

```text
bardzo ciekawy, ale niepewny moment
→ high interestingness, low confidence, requires review

pewny, ale rutynowy moment
→ high confidence, low interestingness
```

---

# 8. Diversity selection

Top moments nie mogą zawierać dziesięciu prawie identycznych wejść do final third.

Selection policy ma uwzględniać:

```text
maksymalną liczbę momentów jednego typu
minimalny odstęp czasowy
równowagę Team A / Team B
różne fazy meczu
różne typy: transition, build-up, regain, defensive, pressure
```

Przykład top-8 dla trenera:

```text
2 transition moments
2 build-up/progression moments
1 dangerous loss
1 defensive intervention
1 sustained pressure
1 rapid combination
```

Konfiguracja powinna pozwolić trenerowi filtrować wyłącznie własną drużynę.

---

# 9. Artefakty

Proponowane:

```text
possession_sequences.json
tactical_moment_candidates.json
tactical_moment_quality_report.json
tactical_moment_review_decisions.json
tactical_moment_selection.json
tactical_moment_clips_manifest.json
```

## Candidate row

```json
{
  "moment_id": "...",
  "type": "goalkeeper_build_up",
  "team_label": "A",
  "start_time_sec": 100.0,
  "peak_time_sec": 108.4,
  "end_time_sec": 114.0,
  "interestingness_score": 0.86,
  "confidence": {},
  "readiness": "ready_with_review",
  "evidence": {
    "sequence_id": "...",
    "pass_candidate_ids": [],
    "possession_transition_ids": [],
    "source_frame_ranges": []
  },
  "warnings": [],
  "label": {
    "safe": "Szybkie rozegranie z własnej strefy obronnej",
    "trusted_enrichment": "Rozegranie od bramkarza do final third"
  }
}
```

Każdy artifact musi zawierać source digests i algorithm parameters.

---

# 10. Operator review

System nie powinien wymagać ręcznego oglądania całego meczu.

Review unit:

```text
jeden candidate moment
+ klip 5-8 s przed początkiem
+ klip 3-5 s po końcu
+ mini-pitch trajectory
+ event evidence
```

Akcje:

```text
accept
reject
change_type
change_team
adjust_start
adjust_end
mark_goalkeeper_confirmed
mark_player, gdy G3 dostępne
comment
```

UI powinno pokazywać, dlaczego moment został wybrany:

```text
odzysk na 24. metrze
18.4 m progresji w 5.6 s
wejście do final third
3 completed passes
momentum +0.42
```

Nie wystarczy czarny-box label `interesting action`.

---

# 11. Clip generation

Infrastruktura klipów może powstać niezależnie od pełnej automatyzacji momentów.

Zasady:

```text
clip start = moment start - pre_roll
clip end = moment end + post_roll
clamp do granic źródłowego wideo
zachowanie dokładnych timestamps
brak ponownej kompresji, jeżeli możliwy jest stream copy
fallback encode dla niekompatybilnych keyframes
manifest z source video digest
```

Wersje klipu:

```text
clean video
analysis overlay
coach overlay z tytułem i mini-pitch
```

Nie publikować automatycznie klipu z low-confidence team attribution.

---

# 12. Milestones

## H0 — Contract and Readiness

Cel:

```text
schema momentów
readiness per typ
source digests
brak klipów i brak automatycznych claimów
```

Dodać dependency matrix dla każdego typu momentu.

## H1 — Possession Sequences V1

Input:

```text
G0 + G4 + G5
```

Output:

```text
possession_sequences.json
```

Nie wymaga pass candidates ani Player ID.

## H2 — Transition Moments V1

Typy:

```text
midfield_regain_to_counterattack
high_regain
counterpress_regain
dangerous_loss
fast_progression
```

Input:

```text
G0 + G4 + G5 + direction
```

Pass data jako optional enrichment.

## H3 — Build-up and Rapid Combination V1

Typy:

```text
deep_build_up
goalkeeper_build_up
rapid_combination
final_third_progression_sequence
```

Input:

```text
G0 + G4 + G5 + G6 candidate layer
trusted GK role tylko dla goalkeeper label
```

## H4 — Danger and Defensive Moments V1

Typy:

```text
danger_spike
danger_neutralized
sustained_pressure
press_escape_candidate
```

Input:

```text
attacking momentum after freshness stabilization
possession_danger experimental
G0 + G1 + G4 + G5
```

## H5 — Review UI and Clip Export

Input:

```text
VIDEO + candidate timestamps
```

Output:

```text
review decisions
accepted moment selection
clip manifest
local clips
```

## H6 — Coach Benchmark

Minimum:

```text
3 pełne mecze
1 held-out match
minimum 50 ręcznie ocenionych candidates
```

Mierzyć:

```text
candidate precision per type
candidate recall na manual goldsecie
team attribution accuracy
GK-label precision
median review seconds per candidate
accepted moments per match
false coaching narratives
clip usefulness rating 1-5
```

## H7 — Candidate Package Integration

Dopiero po benchmarku:

```text
accepted moments w candidate package
brak automatycznej publikacji low-confidence labels
feature readiness per moment type
```

---

# 13. Kolejność implementacji

Rekomendowana:

```text
najpierw naprawa downstream freshness z momentum review
H0 Contract and Readiness
H1 Possession Sequences V1
H2 Transition Moments V1
H5 minimal Review UI + Clip Export
manual A/B na pełnym meczu
H3 Build-up and Rapid Combination V1
H4 Danger and Defensive Moments V1
H6 Coach Benchmark
H7 Candidate Package Integration
```

Najwcześniejszy widoczny efekt produktowy powinien powstać po H2 + minimalnym H5:

```text
lista kandydatów przechwyt → kontra
groźne straty
szybkie progresje
kliknięcie → klip wideo
```

Nie trzeba czekać na perfekcyjne Player ID ani shot detector.

---

# 14. Quality gates per moment type

| Moment | Minimalne gates | Player ID | Trusted GK | Pass detector | Shot detector |
|---|---|---:|---:|---:|---:|
| Midfield regain → counter | G0, G4, G5, direction, VIDEO | nie | nie | opcjonalny | nie |
| High regain | G0, G4, G5 | nie | nie | nie | nie |
| Counterpress regain | G0, G4, G5 | nie | nie | nie | nie |
| Dangerous loss | G0, G4, G5, direction | nie | nie | nie | nie |
| Fast progression | G0, G4, G5, direction | nie | nie | opcjonalny | nie |
| Deep build-up | G0, G4, G5, direction | nie | nie | opcjonalny | nie |
| Goalkeeper build-up | G0, G4, G5, direction | nie | tak na starcie | opcjonalny/V2 | nie |
| Rapid combination | G0, G4, G5, G6 | nie dla team view | nie | tak | nie |
| Possible one-touch | G0, G4, G5, G6 + touch evidence | nie dla team view | nie | tak | nie |
| Press escape | G0, G1, G4, G5 + pressure evidence | nie | nie | opcjonalny | nie |
| Danger neutralized | G0, G4, G5 + danger/momentum | nie | nie | nie | nie |
| Goal from low xG | G7 + calibrated xG | opcjonalny | nie | nie | tak |
| Great goalkeeper save | G7 + save/xGOT + trusted GK | opcjonalny | tak | nie | tak |

---

# 15. Początkowe benchmark targets

Są to targety produktowe do kalibracji, nie naukowe normy.

## Candidate layer

```text
recall >= 85% dla wybranych manual goldset moments
precision >= 70-80%, jeżeli review jest szybkie
team attribution >= 95%
GK-specific label precision = 100% w zaakceptowanej próbce
```

## Po review

```text
accepted moment precision >= 95%
0 momentów przypisanych do złej drużyny
0 goalkeeper labels bez trusted GK evidence
0 one-touch claims bez odpowiedniego evidence
median review <= 10-15 s per candidate
minimum 5 użytecznych momentów dla własnej drużyny na typowy mecz
```

## Automatic publication

Nie wcześniej niż:

```text
precision >= 90% per automatycznie publikowany typ
team attribution >= 98%
0 false high-severity coaching narratives na held-out benchmarku
```

---

# 16. Acceptance criteria

## Fundament

- [ ] momenty używają canonical pitch coordinates;
- [ ] kierunek ataku pochodzi z match phase config;
- [ ] krótkie possession flickers nie rozcinają sekwencji;
- [ ] każdy moment ma source digests;
- [ ] każdy moment ma readiness i confidence components;
- [ ] brak required artifact daje `not_available`, nie zgadywanie;
- [ ] review downstream invalidation jest poprawne.

## Team-level

- [ ] team-level moments nie wymagają real Player ID;
- [ ] unknown team blokuje pewny label;
- [ ] same-team identity swap nie zmienia team-level eventu;
- [ ] moment nie jest przypisywany złej drużynie w accepted benchmarku.

## Goalkeeper

- [ ] `goalkeeper_build_up` wymaga trusted GK role;
- [ ] bez trusted GK system używa `deep_build_up`;
- [ ] visual/color/position guess nie tworzy trusted GK;
- [ ] GK confidence jest raportowane osobno.

## Rapid combinations

- [ ] rapid combination wymaga wielu completed pass candidates;
- [ ] possible one-touch wymaga receive-release/touch evidence;
- [ ] niepewny wzorzec nie jest nazywany confirmed one-touch;
- [ ] missed/failed pass nie jest liczony jako completed combination.

## Tactical moments

- [ ] regain/counter candidate ma jawne recovery i progression evidence;
- [ ] dangerous loss ma poprawny direction i own-goal context;
- [ ] danger neutralized nie jest automatycznie nazywane save/interception;
- [ ] momenty mogą zostać zaakceptowane, odrzucone i zmienione przez operatora;
- [ ] ranking rozdziela confidence od interestingness;
- [ ] selection zapewnia różnorodność typów.

## Clips

- [ ] timestamp klipu zgadza się z eventem;
- [ ] pre-roll pokazuje początek kontekstu;
- [ ] post-roll pokazuje outcome;
- [ ] clip manifest zapisuje source video digest;
- [ ] low-confidence team moments nie są automatycznie publikowane.

---

# 17. Anti-goals

Nie należy:

- czekać na perfekcyjne roster identity przed team-level MVP;
- nazywać deep build-up rozegraniem bramkarza bez trusted GK;
- nazywać szybkiej sekwencji `one-touch` wyłącznie po krótkich interwałach;
- traktować attacking momentum jako xG;
- nazywać spadku danger świetną obroną bramkarza bez save evidence;
- wymuszać shot/pass classification dla niejednoznacznych zagrań;
- generować wyłącznie goli i pomijać ważne fazy treningowe;
- wybierać top moments wyłącznie według maksymalnej pozycji piłki;
- publikować narracji z unknown team lub stale artifacts;
- wymagać 100% ball/possession coverage zamiast jawnie oznaczać luki;
- budować zaawansowanego LLM opisującego wideo przed stabilnym event candidate layer.

---

# 18. Wniosek

Taki system jest osiągalny, ale powinien powstawać jako warstwa wzorców taktycznych nad possession sequences, a nie jako jeden klasyfikator `interesting/not interesting`.

Największą wartość treningową można uzyskać jeszcze przed shot/xG i przed perfekcyjnym Player ID:

```text
przechwyt → kontra
groźna strata
szybka progresja
deep build-up
counterpress regain
okres presji
```

Trusted goalkeeper jest potrzebny tylko do szczegółowej etykiety `goalkeeper_build_up`. Bez niego system nadal może poprawnie wykryć team-level deep build-up.

Najlepszy pierwszy checkpoint produktowy:

```text
pełny mecz
→ 15-30 tactical candidates
→ szybki operator review
→ 5-10 zaakceptowanych klipów dla trenera
```

Dopiero po zmierzeniu precision, recall, czasu review i realnej użyteczności należy dodawać automatyczne opisy, Player ID enrichment, shot/xG i zaawansowane modele wartości akcji.