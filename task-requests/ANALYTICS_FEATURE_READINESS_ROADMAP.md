# Analytics Feature Readiness Roadmap

## 0. Cel dokumentu

Ten dokument definiuje **warunki jakościowe, zależności i kolejność implementacji przyszłych analiz piłkarskich** dla systemu `socca-vision-analysis`.

Nie jest to polecenie zaimplementowania wszystkich opisanych feature jednocześnie.

Dokument ma być używany przez agenta jako:

- mapa zależności między warstwami analizy;
- kontrakt określający, kiedy dany feature może zostać wdrożony;
- ochrona przed budowaniem zaawansowanych statystyk na niestabilnych danych bazowych;
- roadmapa rozwoju produktu przy ograniczonym budżecie;
- baza do tworzenia mniejszych, osobnych tasków implementacyjnych.

Najważniejsza zasada:

> Nie każdy feature wymaga perfekcyjnego Player ID, possession i podań. Warunki muszą być oceniane osobno dla każdej analizy.

---

## 1. Baseline repozytorium

Dokument został przygotowany na podstawie stanu repozytorium z commita:

```text
3c22296e02d51b3bba23bc1f63c702d5c5b44ff0
```

Przed realizacją dowolnego taska agent ma:

1. pobrać aktualny `HEAD`;
2. sprawdzić, czy opisane artefakty i funkcje nadal istnieją;
3. zaktualizować założenia, jeżeli pipeline się zmienił;
4. nie traktować progów z tego dokumentu jako potwierdzonych norm naukowych.

Progi jakości są **początkowymi targetami produktowymi**. Muszą zostać zweryfikowane na ręcznie oznaczonych fragmentach kilku meczów.

---

## 2. Aktualny stan fundamentów

Aktualny pipeline posiada już ważne elementy potrzebne do dalszego rozwoju:

- kalibrację boiska i pozycje w metrach;
- camera-motion compensation;
- raw tracks, tracklets, global identity, stable players i stints;
- zabezpieczenia przed częścią identity switchy i duplikatów;
- identity review gallery;
- ręczne dzielenie fragmentów identity review;
- crop review oraz przypisywanie fragmentów do roster players;
- resolved player stats i osobny resolved stats quality report;
- ball candidates, ball tracks oraz detected/interpolated/unknown coverage;
- refinement ball tracks względem stable players;
- possession candidates z `controlled`, `contested`, `free`, `unknown`;
- fly-through suppression;
- contact candidates i event candidates;
- pass candidates z outcome, evidence, confidence i review;
- pass goldset evaluator;
- match package i public report.

Jednocześnie należy przyjąć następujące ograniczenia:

- possession jest nadal warstwą eksperymentalną;
- pass candidates są candidate layer, a nie automatycznie wiarygodną finalną statystyką;
- indywidualne identity może wymagać crop review;
- overlapping players, szczególnie w polu karnym, nadal mogą powodować miganie, missing slots lub switch;
- nie istnieje jeszcze stabilna warstwa shot/save/goal/block;
- attacking momentum jest opisane w osobnym planie i nie należy zakładać, że zostało już wdrożone;
- najbardziej zaawansowane analizy kontrfaktyczne wymagają danych, których obecnie jeszcze nie ma.

---

# 3. Model jakości — quality gates

Każdy feature ma zależeć od jawnych quality gates.

Legenda:

| Gate | Warstwa |
|---|---|
| `G0` | Geometria boiska, camera motion i kierunek ataku |
| `G1` | Kompletność pozycji graczy i team assignment |
| `G2` | Stabilny stable subject / slot / stint |
| `G3` | Rzeczywisty roster `player_id` po review |
| `G4` | Tracking piłki |
| `G5` | Possession i zmiany possession |
| `G6` | Pass candidates i finalne statystyki podań |
| `G7` | Shot candidates i outcome strzału |
| `G8` | Feature readiness, quality gating i jawna prezentacja confidence |
| `VIDEO` | Poprawna synchronizacja timestampu z odtwarzaczem |
| `MULTI` | Spójne schematy i definicje między wieloma meczami |

---

## 3.1. G0 — geometria boiska i camera motion

### MUST HAVE

- poprawna kalibracja boiska o rzeczywistych wymiarach;
- stabilna homografia;
- pozycje `pitch_m` zgodne z ruchem na nagraniu;
- camera motion nie może przesuwać całych drużyn po mini-boisku;
- poprawny kierunek ataku dla każdej drużyny i każdej połowy;
- poprawna klasyfikacja `inside_play`, `boundary_transient`, `outside_play`;
- ławka, osoby poza boiskiem i zawodnicy przy linii nie mogą być automatycznie clampowani jako pełnoprawne obserwacje in-play.

### Początkowy próg minimalny

- brak systematycznego błędu pozycji większego niż około `1.0–1.5 m`;
- brak skoków całej drużyny wynikających z ruchu kamery;
- potwierdzony kierunek ataku w 100% okresów meczu używanych przez analitykę;
- poprawne granice boiska dla wszystkich analizowanych fragmentów.

### Target docelowy

- mediana błędu pozycji względem ręcznych punktów kontrolnych `< 0.6 m`;
- p95 błędu `< 1.2 m`;
- brak nieciągłości kierunku i błędnej zmiany stron.

### Blokowane feature przy niespełnieniu

- dystanse i prędkości;
- strefy;
- field tilt;
- progresja;
- width/depth;
- final-third entries;
- xG;
- pitch control.

---

## 3.2. G1 — kompletność pozycji graczy i team assignment

Ten gate nie wymaga jeszcze wiedzy, że `A03` to konkretny zawodnik. Wymaga wiedzy, że na boisku znajduje się poprawna liczba osób należących do właściwych drużyn.

### MUST HAVE

- wystarczająca liczba widocznych i zaufanych pozycji graczy;
- brak długich zniknięć podczas overlapów;
- brak trwałych podwójnych bboxów reprezentujących tę samą osobę;
- poprawne team labels;
- niski udział `unknown team`;
- brak trwałych team-over-cap;
- osoby poza boiskiem nie mogą wejść do aktywnego shape drużyny;
- goalkeeper nie może być przypadkowo przerzucany między drużynami.

### Początkowy próg minimalny do analiz drużynowych

- średnio co najmniej `12/14` zaufanych pozycji;
- przynajmniej `11` zawodników w `90%` klatek in-play;
- `unknown team < 3%` obserwacji używanych przez statystyki;
- duplikaty i team-over-cap w `< 1–2%` klatek;
- brak nieuzasadnionych zniknięć trwających dłużej niż `2–3 s`.

### Target docelowy

- średnio `13+/14` pozycji;
- minimum `12` zawodników w `95%` klatek;
- `unknown team < 1%`;
- duplikaty `< 0.5%` klatek;
- overlap w polu karnym nie powoduje utraty obu graczy.

### Uwaga produktowa

Analizy drużynowe mogą działać, nawet gdy dwóch zawodników tej samej drużyny zamieni stable ID, pod warunkiem że obie pozycje nadal istnieją i pozostają przypisane do właściwej drużyny.

---

## 3.3. G2 — stabilny stable subject / slot / stint

### MUST HAVE

- raw tracker ID nie jest traktowany jako tożsamość zawodnika;
- tracklets są poprawnie dzielone przy lukach i nierealnych skokach;
- stable slot nie przeskakuje łatwo pomiędzy nakładającymi się zawodnikami;
- wykryte identity switch tworzy granicę stint/segment, zamiast zanieczyścić cały mecz;
- ambiguous i missing ranges są raportowane;
- podejrzane fragmenty można szybko otworzyć w review;
- system nie łączy bezkrytycznie dwóch różnych osób tylko dlatego, że tracker ponownie użył ID.

### Początkowy próg minimalny do player analytics po review

- ambiguous frame rate `< 5%`;
- missing frame rate dla aktywnych slotów `< 10%`;
- brak niepoprawionego identity switch trwającego dłużej niż około `1 s`;
- brak dwóch aktywnych subjectów reprezentujących tę samą osobę przez dłuższy okres;
- każda podejrzana zmiana ma timestamp możliwy do sprawdzenia.

### Target docelowy automatyczny

- ambiguous frame rate `< 2%`;
- missing frame rate `< 5%`;
- maksymalnie kilka fragmentów wymagających ręcznej korekty na mecz;
- review identity mieści się w realistycznym procesie operatorskim.

### Najważniejszy cel

Nie jest konieczne osiągnięcie absolutnego `0 identity switches`.

Wymagany jest proces:

```text
wykryj podejrzany fragment
→ pokaż crop/video
→ pozwól rozdzielić segment
→ przypisz poprawnego zawodnika
→ przelicz downstream stats
```

---

## 3.4. G3 — rzeczywisty roster Player ID

### MUST HAVE

- stable subject/stint może zostać przypisany do rzeczywistego roster `player_id`;
- crop review może oznaczyć `player`, `unknown`, `wrong_team`, `false_positive`;
- unresolved fragments nie są automatycznie dopisywane do konkretnego zawodnika;
- resolved timeline jest source of truth dla statystyk indywidualnych;
- po zmianie identity assignment resolved stats są odświeżane;
- package nie publikuje stale resolved player stats;
- overlap dwóch identity assignments nie może podwójnie naliczać minut lub dystansu.

### Warunek publikowania statystyk zawodnika

- 100% fragmentów użytych w finalnych statystykach ma zatwierdzone `player_id`;
- odrzucone i nierozstrzygnięte fragmenty pozostają poza finalnymi statystykami;
- resolved stats quality report nie zgłasza blokującego konfliktu;
- ręczny review meczu powinien docelowo trwać nie więcej niż `10–15 min`.

### Uwaga budżetowa

Pełne automatyczne rozpoznawanie zawodników nie jest wymagane dla MVP produktu. Manual crop review jest akceptowalnym etapem produkcyjnym, jeżeli jest szybki i deterministyczny.

---

## 3.5. G4 — tracking piłki

### MUST HAVE

- jawne źródła `detected`, `interpolated`, `unknown`;
- raport detected, interpolated i known coverage;
- brak długiego śledzenia innego obiektu zamiast piłki;
- recovery po luce nie może wybierać przypadkowego low-confidence obiektu;
- pozycja piłki jest w metrach boiska;
- coverage liczone osobno dla czasu in-play;
- najdłuższe unknown gaps są dostępne do review;
- refinement względem graczy nie może usuwać prawidłowej piłki bez śladu diagnostycznego.

### Początkowy próg minimalny

- known ball coverage `>= 80%` czasu in-play;
- detected coverage `>= 55–60%`;
- brak niepoprawnego hijack trwającego ponad około `1 s`;
- system wraca do właściwej piłki po luce;
- długie `unknown` nie są sztucznie uzupełniane.

### Target docelowy

- known coverage `>= 90%`;
- detected coverage `>= 70%`;
- p95 błędu pozycji piłki około `< 1 m`;
- praktycznie brak długich hijacków.

### Nie liczyć coverage względem

- przerw;
- fragmentów przed rozpoczęciem gry;
- długiego czasu po zakończeniu;
- sytuacji jawnie poza grą, jeżeli produkt raportuje statystyki in-play.

---

## 3.6. G5 — possession

### MUST HAVE

- statusy `controlled`, `contested`, `free`, `unknown`;
- poprawna drużyna posiadająca piłkę w klatkach controlled;
- indywidualny ball carrier tylko przy odpowiednim confidence;
- fly-through suppression;
- debounce bardzo krótkich, fałszywych zmian;
- zmiana possession jest osobnym eventem, a nie prostą różnicą sąsiednich klatek;
- possession quality mierzone na goldsecie;
- nie wymuszać 100% czasu jako controlled possession.

### Dlaczego 95% controlled coverage nie jest wymagane

Poprawny system powinien móc powiedzieć:

```text
free
contested
unknown
```

zamiast przypisywać posiadanie na siłę.

### Początkowy próg do analiz drużynowych

- określona drużyna possession dla `>= 75–80%` czasu in-play;
- team possession accuracy `>= 90%` na goldsecie;
- precision zmian possession `>= 90%`;
- krótkie fałszywe switche `< 0.5 s` są wygaszane.

### Target docelowy

- team possession coverage `85–90%`;
- team possession accuracy `>= 95%`;
- transition precision `>= 95%`;
- ball carrier accuracy `85–90%` w controlled frames.

---

## 3.7. G6 — podania

### MUST HAVE candidate layer

- wysoki recall kandydatów;
- jawne `completed_pass`, `failed_pass`, `excluded_non_pass`, `unknown_pass_attempt`;
- poprawny team attribution;
- timestamp początku i końca;
- start/end position;
- release, receiver i trajectory evidence;
- restart deduplication;
- review status;
- goldset precision/recall evaluator;
- final stat eligibility oddzielone od candidate detection.

### Początkowy próg candidate layer

- recall `>= 90%`;
- precision może początkowo wynosić `75–85%`, jeżeli review jest szybkie;
- missed passes i false positives są widoczne w raporcie.

### Automatyczne statystyki bez review

- precision `>= 90%`;
- recall `>= 90%`;
- poprawny team i outcome `>= 95%`.

### Finalne statystyki po review

- zgodność z goldsetem `>= 95%`;
- accepted same-team completed passes są wyraźnie odróżnione od prób i nieudanych podań;
- review zmiania downstream artifacts deterministycznie.

---

## 3.8. G7 — shots

Warstwa nie jest jeszcze gotowym fundamentem.

### MUST HAVE przed xG i oceną decyzji

- shot candidate timestamp;
- punkt oddania strzału;
- drużyna i shooter;
- odróżnienie strzału od mocnego podania i wybicia;
- podstawowe outcomes: `goal`, `saved`, `blocked`, `wide`, `unknown`;
- shot goldset;
- review UI.

### Początkowy próg

- recall `>= 90%`;
- precision `>= 90%`;
- timestamp około `±0.5 s`;
- wynik strzału może początkowo wymagać ręcznego tagu.

---

## 3.9. G8 — feature readiness

Każdy feature ma otrzymywać jeden status:

```text
ready
ready_with_review
experimental
not_available
```

### Proponowany artefakt

```text
feature_readiness.json
```

Przykład:

```json
{
  "schema_version": "0.1.0",
  "features": {
    "turnover_map_team": {
      "status": "ready",
      "requirements": {
        "ball_known_coverage": {
          "value": 0.88,
          "required": 0.80,
          "passed": true
        },
        "team_possession_accuracy": {
          "value": 0.93,
          "required": 0.90,
          "passed": true
        }
      },
      "warnings": []
    },
    "pass_network": {
      "status": "not_available",
      "requirements": {},
      "warnings": [
        "Player identity review is incomplete",
        "Pass precision is below the publication threshold"
      ]
    }
  }
}
```

### Zasady

- brak wymaganego artifact nie może kończyć się zgadywaniem;
- UI musi pokazywać powód niedostępności;
- wartości low-confidence nie mogą wyglądać tak samo jak finalne statystyki;
- readiness powinno zostać dodane do package jako optional;
- starsze package bez readiness nadal muszą się otwierać.

---

# 4. Kluczowe rozróżnienie: team-level vs player-level

## Analizy odporne na zamianę ID wewnątrz drużyny

Mogą zostać wdrożone wcześniej, jeżeli G0 i G1 są dobre:

- team width;
- team depth;
- compactness;
- team centroid;
- block height;
- local overloads;
- field tilt;
- possession zones;
- final-third entries;
- team turnovers;
- high regains;
- team transitions;
- attacking momentum.

Przykład:

```text
A03 ↔ A07
```

nie zmieni team centroid, jeżeli obaj gracze nadal istnieją w poprawnych miejscach i pozostają w Team A.

## Analizy wymagające G2/G3 albo review

- dystans zawodnika;
- maksymalna prędkość zawodnika;
- sprinty;
- player heatmap;
- average player position;
- player pass count;
- pass network;
- progressive carries zawodnika;
- turnovers zawodnika;
- recovery runs;
- player trends.

---

# 5. Feature dependency matrix

Poziom trudności:

```text
S — mały
M — średni
L — duży
XL — badawczy / wymagający datasetu
```

Status aktualny:

```text
NOW — można zacząć na obecnym fundamencie
AFTER_GATE — implementować po osiągnięciu wskazanego gate
LATER — późniejsza roadmapa
RESEARCH — nie traktować jako bliski feature produktowy
```

| # | Feature | MUST HAVE | Nie jest wymagane | Trudność | Status / kolejność |
|---:|---|---|---|---:|---|
| 1 | Quality Dashboard | istniejące quality reports, G8 | wysoka jakość wszystkich danych | S/M | NOW — pierwszy krok |
| 2 | Interaktywna timeline | VIDEO, poprawny timestamp | possession, Player ID | M | NOW |
| 3 | Automatyczne klipy | VIDEO + event timestamps | real Player ID | M | infrastruktura NOW, pełne klipy później |
| 4 | Manual tagging | VIDEO | automatyczne eventy | S/M | NOW |
| 5 | Field Tilt | G0, G4, G5, direction | G2, G3, pass count | S | AFTER G5 |
| 6 | Possession by Zone | G0, G4, G5 | Player ID, passes | S | AFTER G5 |
| 7 | Zone / final-third entries | G0, G4, G5, direction | G3 | S/M | AFTER G5 |
| 8 | Possession Sequences V1 | G4, G5 | pass detection | M | AFTER G5 |
| 8b | Possession Sequences V2 | G4, G5, G6 | shot detector | M | AFTER G6 |
| 9 | Team Turnover Map | G0, G4, G5, transition debounce | pass count, G2, G3 | M | AFTER G5 — wysoki priorytet |
| 9b | Player Turnover Map | G0–G5, G2, G3 | pass network | M/L | AFTER G3 + G5 |
| 10 | High Regains | G0, G4, G5 | G3 w team view | M | AFTER G5 |
| 11 | Width / Depth / Compactness | G0, G1 | ball, possession, G2, G3 | S/M | NOW po stabilizacji G1 |
| 12 | Team Average Shape | G0, G1 | stable individual ID | S | NOW po G1 |
| 12b | Player Average Position | G0, G2, G3 | ball | S/M | AFTER G3 |
| 13 | Block Height | G0, G1, direction | possession, G3 | M | AFTER G1 |
| 14 | Local Overloads | G0, G1 | roster identity | M | AFTER G1 |
| 15 | Support Around Ball Carrier | G0, G1, G4, G5 | G3 dla team view | L | LATER |
| 16 | Pass Map | G0, G4, G5, G6 | G3 dla team view | S/M | AFTER G6 |
| 17 | Pass Network | G2, G3, G6 | shots | M | AFTER G3 + G6 |
| 18 | Progressive Passes | G0, G6, direction | G3 w team view | S | AFTER G6 |
| 18b | Progressive Carries | G0, G2, G4, G5 | pass detector | M | AFTER G2 + G5 |
| 19 | Pass Direction / Length Profile | G0, G6, direction | G3 w team view | S | AFTER G6 |
| 20 | Line-breaking Pass Candidates | G0, G1, G6, direction | shot detector | L | LATER |
| 21 | Transition Dashboard | G0, G4, G5 | pełna liczba podań | M | AFTER G5 |
| 22 | Pressure Events | G0, G1, ciągłe trajektorie | G3 w team view | L | LATER |
| 23 | Pressing Efficiency | G0, G1, G5, pressure events | pass network | L | LATER |
| 24 | Recovery Runs | G0, G2, G3, G5 | passes | M/L | AFTER G3, później |
| 25 | Workload Timeline | G0, G2, G3 | ball, possession | S/M | AFTER G3 |
| 26 | Physical Output Trend | G0, G2, G3, poprawne minuty | ball | S/M | AFTER G3 |
| 27 | Player Movement Profile | G0, G2, G3 | passes | M | AFTER G3 |
| 28 | Set Piece Dashboard | G0, G4, restart detection | G3 w team view | M | AFTER stabilnych restartach |
| 29 | Goalkeeper Distribution | G0, G3, G4, G5, G6, GK role | shots | M/L | AFTER G6 |
| 30 | Attacking Momentum | G0, G4, G5, direction | G3; passes optional enrichment | M | AFTER G5, osobny plan |
| 31 | Automated Key Moments | VIDEO + 2–3 stabilne metryki | wszystkie feature | M | po turnover/entries/momentum |
| 32 | Match Summary | G8 + stabilne gotowe metryki | LLM analizujący video | S/M | po key moments |
| 33 | Team Trends | MULTI + stabilna definicja metryki | G3 | M | po kilku meczach |
| 34 | Player Trends | G3, MULTI | shot detector | M | po kilku reviewed matches |
| 35 | Opponent Scouting | MULTI, G0, G1, team metrics | roster rywala | L | później |
| 36 | Shot Detector | G0, G4, goal geometry, goldset | pitch control | L | późna roadmapa |
| 37 | Basic xG | G7 + dataset strzałów | identity wszystkich graczy | XL | RESEARCH/LATER |
| 38 | Approximate Pitch Control | G0, G1, velocity/direction | roster identity | XL | RESEARCH |
| 39 | Pass Completion Probability | G0, G1, G6, duży goldset | shots | XL | RESEARCH |
| 40 | Decision Review Candidates | G0–G7, pitch control/value model | brak istotnych skrótów | XL | ostatnia faza |

---

# 6. Szczegółowe definicje wybranych feature

## 6.1. Turnover Map V1 — drużynowa

### MUST HAVE

- G0;
- G4;
- G5;
- pozycja piłki w momencie zmiany;
- poprawna sekwencja:

```text
Team A controlled
→ free/contested/short unknown
→ Team B controlled
```

- debounce bardzo krótkich zmian;
- minimalny czas potwierdzenia nowej drużyny;
- quality/confidence eventu.

### Nie wymaga

- poprawnej liczby podań;
- real Player ID;
- stabilnego identity pomiędzy dwoma graczami tej samej drużyny;
- pass network.

### Output MVP

```json
{
  "time_sec": 742.4,
  "from_team_label": "A",
  "to_team_label": "B",
  "position_m": [12.4, 31.8],
  "zone": "own_central_third",
  "confidence": 0.91,
  "source": "confirmed_possession_transition"
}
```

### V2 — zawodnik

Dodatkowo wymaga:

- G2;
- G3;
- stabilnego ball carriera;
- poprawnego pass outcome, jeżeli strata wynika z podania.

---

## 6.2. Width / Depth / Compactness

### MUST HAVE

- G0;
- G1;
- odfiltrowane osoby poza grą;
- poprawne team labels;
- minimalna liczba graczy wymaganych do klatki;
- coverage i confidence per bin czasu.

### Nie wymaga

- ball tracking;
- possession;
- pass detection;
- real Player ID;
- perfekcyjnego stable ID wewnątrz drużyny.

### Warianty

- all in-play;
- in possession;
- out of possession;
- first half vs second half;
- rolling timeline.

Warianty zależne od possession mogą zostać dodane dopiero po G5.

---

## 6.3. Field Tilt

### MUST HAVE

- G0;
- G4;
- G5;
- poprawny kierunek ataku;
- określona definicja wysokiej strefy;
- coverage denominator obejmujący wszystkie in-play samples.

### Nie wymaga

- Player ID;
- pass count;
- player identity review;
- shot detector.

### Minimalna wersja

```text
controlled possession samples na ofensywnej połowie / w wysokiej strefie
```

Nie przedstawiać jako proprietary Opta field tilt, jeżeli definicja jest inna.

---

## 6.4. Pass Network

### MUST HAVE

- G2 i G3;
- G6;
- zatwierdzone roster assignments;
- final eligible passes albo osobny wyraźny candidate mode;
- brak dublowania restartów;
- minuty gry dla normalizacji;
- możliwość filtrowania first/second half.

### Nie wdrażać jako finalnej statystyki, gdy

- identity review jest niekompletne;
- wiele pass candidates nadal ma `needs_review`;
- precision/recall nie są zmierzone;
- resolved stats są stale.

---

## 6.5. Workload Timeline

### MUST HAVE

- G0;
- G2;
- G3;
- poprawne minuty obecności;
- ruch oparty na resolved timeline;
- odrzucanie nierealnych skoków;
- osobne observed vs estimated distance;
- brak podwójnego liczenia overlap identity.

### Nie wymaga

- ball tracking;
- possession;
- passes.

### Prezentacja

- distance/min;
- high-intensity distance per 5 min;
- sprint candidates per interval;
- quality badge dla zawodnika.

Nie nazywać spadku outputu diagnozą zmęczenia.

---

## 6.6. Attacking Momentum

### MUST HAVE MVP

- G0;
- G4;
- G5;
- kierunek ataku;
- confidence i coverage;
- canonical `ball_position_m` po camera/stabilization.

### Optional enrichment

- G6 pass candidates;
- restart candidates.

### Nie wymaga MVP

- real Player ID;
- shot detector;
- xG;
- pitch control.

Szczegóły implementacyjne znajdują się w:

```text
task-requests/ATTACKING_MOMENTUM_IMPLEMENTATION.md
```

---

## 6.7. Automated Key Moments

### MUST HAVE

Nie wymaga wszystkich feature. Wymaga przynajmniej 2–3 stabilnych sygnałów, np.:

- dangerous turnover;
- high regain;
- final-third entry;
- momentum peak;
- progressive possession sequence.

Każdy moment musi posiadać:

```text
start_time_sec
end_time_sec
type
team_label
importance
confidence
reasons
source_artifacts
```

System ma wybierać timestampy do review, a nie wydawać kategoryczne oceny trenerskie.

---

# 7. Goldset roadmap

Nie należy poprawiać algorytmów wyłącznie na podstawie subiektywnego oglądania pojedynczego outputu.

## 7.1. Identity goldset

Ręcznie oznaczyć fragmenty obejmujące:

- overlap w polu karnym;
- crossing trajectories;
- chwilowe zasłonięcie;
- ponowne pojawienie się zawodnika;
- ławkę przy linii;
- goalkeeper blisko field players;
- team color ambiguity.

Mierzyć:

- switch count;
- switch duration;
- missing duration;
- duplicate duration;
- review time.

## 7.2. Ball goldset

Dla wybranych fragmentów zapisać:

- widoczna piłka / brak;
- ręczna pozycja co określony interwał;
- prawidłowy segment po luce;
- hijack false positives;
- in-play/out-of-play.

Mierzyć:

- detected coverage;
- known coverage;
- position error;
- hijack duration;
- longest unknown gaps.

## 7.3. Possession goldset

Oznaczyć:

- team possession;
- ball carrier, gdy kontrolowany;
- free;
- contested;
- unknown;
- moment zmiany possession.

Mierzyć:

- team accuracy;
- carrier accuracy;
- coverage;
- transition precision/recall;
- false short switches.

## 7.4. Pass goldset

Rozszerzać istniejący evaluator o większą liczbę meczów i osobne grupy:

- completed;
- failed;
- interception/turnover;
- restart;
- false contact;
- same-player continuation;
- clearance/non-pass.

## 7.5. Shot goldset

Tworzyć dopiero po dodaniu manual shot tagging.

---

# 8. Kolejność implementacji

## Faza 0 — jakość i operator workflow

### Cel

Najpierw umożliwić mierzenie jakości i szybkie poprawianie danych.

### Kolejność

1. Rozbudować Quality Dashboard.
2. Dodać `feature_readiness.json`.
3. Dodać wspólny widok problematycznych timestampów.
4. Dodać jump-to-video.
5. Dodać manual tags.
6. Utworzyć pierwsze goldsety identity, ball, possession i transitions.
7. Dodać raport czasu wymaganego na review meczu.

### Definition of Done

- operator widzi, dlaczego feature jest niedostępny;
- każdy warning prowadzi do konkretnego timestampu albo zakresu;
- można porównać dwa uruchomienia tego samego meczu;
- testy regresyjne używają goldsetów.

---

## Faza 1 — geometria drużyny

### Priorytet

```text
Nie kto jest kim, lecz czy wszystkie osoby są we właściwych miejscach.
```

### Prace fundamentowe

- overlap regression set;
- duplicate observation diagnostics;
- missing player ranges;
- team-over-cap;
- boundary/bench handling;
- goalkeeper role stability;
- coverage 14 pozycji.

### Feature po osiągnięciu G0 + G1

1. Width / Depth / Compactness.
2. Team Centroid.
3. Team Average Shape.
4. Block Height.
5. Local Overloads V1.

To jest pierwsza grupa nowych analiz, ponieważ nie zależy od possession i pass detection.

---

## Faza 2 — identity review i player-level foundation

### Cel

Nie wymagać perfekcyjnego auto-ID. Wymagać skutecznego review.

### Prace

- automatyczne wskazywanie podejrzanych switchy;
- sprawne split identity segment;
- crop assignments;
- resolved timeline;
- stale detection;
- resolved quality report;
- testy overlap assignments;
- pomiar czasu review.

### Target operatorski

```text
maksymalnie 10–15 minut identity review na mecz
+ brak błędnych danych po review
```

### Feature po G2 + G3

- player heatmaps;
- player average positions;
- workload timeline;
- physical output trend;
- player movement profile.

---

## Faza 3 — ball tracking i possession

### Kolejność prac

1. Coverage in-play, nie względem całego filmu.
2. Lista unknown gaps.
3. Hijack diagnostics.
4. Ball goldset.
5. Team possession goldset.
6. Transition goldset.
7. Tuning fly-through.
8. Tłumienie krótkich false possession switches.
9. Readiness gates dla team possession features.

### Feature po G4 + G5

1. Field Tilt.
2. Possession by Zone.
3. Final-third / zone entries.
4. Team Turnover Map.
5. High Regains.
6. Transition Dashboard V1.
7. Attacking Momentum.
8. Possession Sequences V1.

To jest pierwszy pełny, atrakcyjny raport drużynowy.

---

## Faza 4 — podania

### Najpierw

Priorytetem jest candidate recall i review, nie efektowna tabela pass count.

### Kolejność prac

1. Rozszerzyć goldset do kilku meczów.
2. Osobno mierzyć completed, failed, restart, interception i false contact.
3. Dodać szybki pass review z video.
4. Dodać gate final-stat eligibility.
5. Zapewnić poprawne odświeżanie downstream reports po review.

### Feature po G6

- Pass Map.
- Pass Direction / Length Profile.
- Progressive Passes.
- Possession Sequences V2.
- Pass Network.
- Line-breaking Pass Candidates.
- Set Piece Dashboard.
- Goalkeeper Distribution.

---

## Faza 5 — key moments i raport trenerski

### Warunek

Dostępne są przynajmniej stabilne:

```text
turnover
high regain
zone entry
momentum lub progressive sequence
```

### Feature

1. `key_moments.json`.
2. Timeline markers.
3. Automatic clip ranges.
4. Top 10 moments.
5. Auto-generated match summary z gotowych metryk.

LLM może formatować gotowe fakty, ale nie powinien liczyć statystyk z surowego video.

---

## Faza 6 — pressing i zaawansowana taktyka

Po stabilnym G0/G1/G5:

- Pressure Events.
- Pressing Efficiency.
- Recovery Runs.
- Support Around Ball Carrier.
- Advanced Overloads.
- Transition Compactness.

Nie realizować przed opanowaniem overlapów i missing players.

---

## Faza 7 — shots, xG i decyzje kontrfaktyczne

Kolejność:

1. Manual shot tags.
2. Heurystyczny Shot Detector.
3. Shot goldset.
4. Basic xG.
5. Approximate Pitch Control.
6. Pass Completion Probability.
7. Decision Review Candidates.

To jest późna roadmapa, a nie bezpośredni kolejny etap po momentum.

---

# 9. Rekomendowana kolejność liczbowa feature

## Teraz

```text
1  Quality Dashboard
2  Interactive Timeline
4  Manual Tagging
3  Clip Infrastructure
11 Width / Depth / Compactness
12 Team Average Shape
13 Block Height
```

## Po stabilnym possession

```text
5  Field Tilt
6  Possession by Zone
7  Zone Entries
9  Team Turnover Map
10 High Regains
21 Transition Dashboard
30 Attacking Momentum
8  Possession Sequences V1
```

## Po identity review

```text
12b Player Average Position
25  Workload Timeline
26  Physical Output Trend
27  Player Movement Profile
24  Recovery Runs
```

## Po pass quality

```text
16 Pass Map
19 Pass Profile
18 Progressive Passes
8b Possession Sequences V2
17 Pass Network
20 Line-breaking Passes
28 Set Pieces
29 Goalkeeper Distribution
```

## Po połączeniu stabilnych eventów

```text
31 Automated Key Moments
3  Automatic Clips
32 Match Summary
33 Team Trends
34 Player Trends
35 Opponent Scouting
```

## Późna roadmapa

```text
22 Pressure Events
23 Pressing Efficiency
15 Support Around Ball Carrier
36 Shot Detector
37 Basic xG
38 Pitch Control
39 Pass Completion Probability
40 Decision Review Candidates
```

---

# 10. Model produktu przy ograniczonym budżecie

## 10.1. Basic Team Report

Nie wymaga ręcznego rozpoznania wszystkich graczy:

- possession;
- field tilt;
- possession zones;
- width/depth;
- compactness;
- zone entries;
- team turnovers;
- high regains;
- momentum;
- key moments.

## 10.2. Reviewed Player Report

Wymaga crop review i resolved Player ID:

- heatmaps;
- average position;
- distance;
- speed;
- workload;
- passes;
- progressive actions;
- turnovers;
- player clips.

## 10.3. Advanced Tactical Review

Wymaga review eventów i bardziej dojrzałych modeli:

- pass network;
- transition clips;
- pressure;
- line-breaking passes;
- passing options;
- decision review.

---

# 11. Zasady implementowania kolejnych tasków

Dla każdego nowego feature agent musi utworzyć osobny plan zawierający:

## 11.1. Inputs

- canonical artifacts;
- wymagane schema versions;
- fallback dla starszych meczów;
- wymagane quality gates.

## 11.2. Output

- nowy artifact JSON;
- summary;
- timeline/events;
- parameters;
- confidence;
- warnings;
- quality/readiness;
- package/public report integration.

## 11.3. Semantyka

- precyzyjna definicja statystyki;
- denominator;
- in-play filtering;
- handling missing data;
- handling review;
- team-level vs player-level;
- experimental vs final.

## 11.4. Validation

- unit tests;
- integration test shared flow;
- goldset evaluation;
- regression against at least one previous match;
- visual review timestamps;
- package backward compatibility;
- client typecheck/build.

## 11.5. UI

Każda eksperymentalna metryka musi pokazać:

- `experimental` albo `needs review`;
- confidence/quality;
- coverage;
- przyczynę niedostępności;
- link do timestampów źródłowych, jeżeli istnieją.

---

# 12. Anti-goals

Agent nie może:

- budować wszystkich feature jednocześnie;
- dodawać nowych statystyk bez jawnej definicji;
- uznawać raw `tracker_id` za zawodnika;
- wymuszać possession w każdej klatce;
- publikować candidate counts jako final stats;
- ukrywać brakujących danych przez agresywną interpolację;
- tworzyć „AI coach verdicts” bez modelu i review;
- nazywać heurystyki xG, pitch control lub proprietary metrics bez zgodnej definicji;
- dodawać LLM jako źródła obliczeń;
- blokować prostych team analytics z powodu braku perfekcyjnego roster identity;
- publikować player analytics przed resolved identity review;
- wdrażać pressure/pitch control przed opanowaniem missing i duplicate players.

---

# 13. Pierwszy rekomendowany task wykonawczy

Po zaakceptowaniu tego dokumentu kolejnym osobnym taskiem powinno być:

```text
Feature Readiness and Analytics Quality Dashboard
```

Zakres pierwszego taska:

1. stworzyć `feature_readiness.json`;
2. zebrać istniejące quality reports;
3. zdefiniować gate results G0–G6;
4. dodać `ready / ready_with_review / experimental / not_available`;
5. pokazać readiness w lokalnym admin/report UI;
6. nie implementować jeszcze nowych statystyk taktycznych;
7. dodać testy progów i backward compatibility.

Dopiero po tym tasku kolejne feature powinny korzystać ze wspólnego mechanizmu readiness.

---

# 14. Definition of Done dla fundamentu analitycznego

Fundament można uznać za gotowy do regularnego rozwijania statystyk, gdy:

- [ ] G0 jest mierzony na punktach kontrolnych;
- [ ] G1 raportuje coverage, missing, ambiguous, duplicates i over-cap;
- [ ] overlap regression set istnieje;
- [ ] G2 wykrywa i pokazuje podejrzane identity switches;
- [ ] G3 pozwala domknąć identity review w realistycznym czasie;
- [ ] resolved player stats są automatycznie odświeżane po review;
- [ ] G4 coverage jest liczone dla in-play;
- [ ] istnieje ball goldset;
- [ ] istnieje possession goldset;
- [ ] transition precision/recall jest mierzone;
- [ ] pass evaluator działa na kilku reprezentatywnych fragmentach;
- [ ] każdy feature posiada readiness status;
- [ ] UI pokazuje confidence i powody niedostępności;
- [ ] timeline pozwala otwierać problematyczne miejsca w video;
- [ ] package pozostaje backward compatible;
- [ ] agent może wdrożyć nowy feature bez ponownego wymyślania quality gates.

---

# 15. Bezpośrednia instrukcja dla agenta

```text
Przed implementacją dowolnej przyszłej analizy przeczytaj ten dokument oraz
aktualny kod repozytorium.

Nie traktuj wszystkich warstw jako jednego globalnego warunku jakości.
Określ dokładnie, których gates G0–G8 potrzebuje dany feature.

Najpierw sprawdź aktualny HEAD i aktualne schema artifacts.
Następnie przygotuj osobny task implementacyjny zawierający:

- definicję feature;
- MUST HAVE;
- canonical inputs;
- quality thresholds;
- output schema;
- testy i goldset;
- package/public report integration;
- status experimental/final;
- backward compatibility.

Nie wdrażaj feature, jeżeli wymagany gate nie jest mierzalny.
W takim przypadku najpierw dodaj brakującą metrykę jakości albo review flow.

Preferuj najtańszą wiarygodną wersję team-level przed droższą wersją
player-level albo modelem ML.

Priorytet rozwoju:

1. quality/readiness;
2. geometry/team shape;
3. identity review;
4. ball/possession;
5. passes;
6. key moments;
7. advanced tactical models;
8. shots/xG/counterfactual decision review.
```
