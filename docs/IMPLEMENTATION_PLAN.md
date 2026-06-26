# Implementation Plan — Progressive AI Agent Milestones

Ten plik jest główną instrukcją dla AI/coding agenta. Możesz później napisać:

> Kontynuuj pracę zgodnie z `docs/IMPLEMENTATION_PLAN.md`, zacznij od milestone'u X i nie przechodź do kolejnego milestone'u bez spełnienia acceptance criteria.

Projekt ma być rozwijany progresywnie. Nie wolno przeskakiwać od razu do podań, posiadania piłki albo sezonowego dashboardu, jeśli fundament trackingu zawodników nie jest stabilny.

## Najważniejsza zasada produktu

System nie próbuje od razu zrobić pełnej automatycznej analizy jak Opta. Najpierw budujemy wiarygodną warstwę trackingową:

```text
video -> pitch calibration -> player detection -> player tracking -> tracklets -> identity resolver -> player stats
```

Dopiero potem dokładamy:

```text
ball tracking -> possession -> passes -> shots -> advanced football events
```

## Definicje, których agent musi używać konsekwentnie

- `tracker_id` — surowe ID zwrócone przez tracker, np. Ultralytics/BoT-SORT/ByteTrack. Może flickerować. Nie jest realnym zawodnikiem.
- `tracklet_id` — ciągły fragment trackingu jednego obiektu od czasu A do B. Może być tylko częścią gry realnego zawodnika.
- `player_id` — realny zawodnik, np. Paweł, Tomek, Guest 1.
- `stint` — okres przebywania realnego zawodnika na boisku, np. 00:00-08:20 oraz 21:10-40:00.
- `identity_assignment` — ręczne lub automatyczne przypisanie trackletu do realnego zawodnika/stintu.
- `pitch_config` — konfiguracja boiska: punkty obrazu, wymiary boiska, homografia, źródło kalibracji.

Nie wolno łączyć tych pojęć w jedno.

---

## Status checklist legend

- `[x]` implemented and usable in the current app.
- `[~]` partially implemented, implemented under a different artifact name, or available only as debug/developer workflow.
- `[ ]` not implemented yet.

## Current implementation snapshot

This snapshot should be updated whenever a milestone is completed or materially re-scoped.

- `[x]` Core local app shell: upload, match selection, manual pitch calibration, Docker workflow, healthcheck.
- `[x]` YOLO/motion analysis pipeline: raw tracker overlay, `tracks.json`, `analysis_report.json`, all-tracks heatmap.
- `[x]` Tracklets and quality diagnostics: analysis now exports standalone `tracklets.json` and `tracking_quality_report.json` contracts.
- `[~]` Team assignment: automatic torso-color clustering, goalkeeper color outliers, `team_clusters.json`, and Team A/B review exist; explicit `team_config.json` and full UI sampling are not done.
- `[~]` Identity resolver: current production path is anonymous stable slot/stint identity (`A01-A07`, `B01-B07`) with conservative anti-switch logic, not real roster `player_id` resolution yet.
- `[~]` Player stats: tracking-only movement stats exist per stable slot in `movement_stats.json` and overlays, but final `player_stats.json`, sprint thresholds, and per-player heatmaps are not done.
- `[~]` Match report: app shows artifacts, stable slots, diagnostics and movement stats, but not a polished separate match report view.
- `[ ]` Ball tracking, possession, passes, event review, season aggregation and background jobs are not implemented.

---

# Milestone 0 — Project hygiene and developer workflow

**Status:** `[x]` mostly implemented.

## Cel

Upewnić się, że repo jest łatwe do uruchomienia, testowania i rozwijania przez człowieka oraz AI agenta.

## User stories

### US0.1 — Uruchomienie lokalne
Jako developer chcę uruchomić client i backend lokalnie bez Dockera, żeby szybko iterować nad kodem.

### US0.2 — Uruchomienie Dockerem
Jako developer chcę uruchomić cały projekt przez `docker compose up --build`, żeby mieć powtarzalne środowisko.

### US0.3 — Krótkie demo video
Jako developer chcę mieć przykładowy krótki film w `examples/`, żeby testować pipeline bez pełnego meczu.

## Acceptance criteria

- `[x]` `README.md` zawiera aktualne instrukcje lokalne i Dockerowe.
- `[x]` `docker compose up --build` uruchamia client na `localhost:5173` i backend na `localhost:8000`.
- `[x]` Backend ma endpoint healthcheck.
- `[x]` Client potrafi sprawdzić status backendu.
- `[x]` W repo nie są commitowane wygenerowane artefakty, duże video ani storage meczów.

## Agent instructions

- Nie dodawaj jeszcze bazy danych produkcyjnej.
- Nie dodawaj auth.
- Nie rozbudowuj UI ponad potrzeby testów pipeline'u.

---

# Milestone 1 — Pitch calibration and tracking area

**Status:** `[x]` core calibration, filtering and pitch metadata contract are implemented; dedicated `pitch_debug.jpg` remains optional debug scope.

## Cel

Stabilnie wydzielić obszar boiska, z którego system ma brać zawodników do trackingu. To jest fundament ograniczenia false positives: sędzia przy linii, rezerwowi, ludzie w tle i przypadkowe osoby nie mogą trafiać do trackera jako gracze.

## User stories

### US1.1 — Upload video i wybór klatki
Jako użytkownik chcę uploadować video i wybrać klatkę kalibracyjną, żeby oznaczyć boisko na realnym materiale.

### US1.2 — Manualne kliknięcie narożników
Jako użytkownik chcę kliknąć 4 rogi boiska na klatce video, żeby aplikacja znała obszar gry.

### US1.3 — Edycja punktów boiska
Jako użytkownik chcę móc poprawić przesunięty narożnik, zamiast resetować całą kalibrację.

### US1.4 — Zapis pitch config
Jako użytkownik chcę zapisać kalibrację dla meczu, żeby analiza korzystała z niej wielokrotnie.

### US1.5 — Footpoint-in-pitch filtering
Jako system chcę filtrować detekcje na podstawie punktu stóp zawodnika, żeby obiekty spoza boiska nie trafiały do trackera.

### US1.6 — Debug overlay pitch mask
Jako developer chcę wygenerować obraz/debug overlay z polygonem boiska i punktami stóp, żeby potwierdzić, że filtr działa.

## Acceptance criteria

- `[x]` Client pozwala kliknąć 4 punkty boiska i zapisać je do backendu.
- `[x]` Backend zapisuje `pitch_config.json` w katalogu meczu.
- `[x]` `pitch_config.json` zawiera:
  - `[x]` `image_points`,
  - `[x]` wymiary boiska jako `width_m` / `length_m` oraz spójne `pitch_dimensions_m`,
  - `[x]` `calibration_frame_time_sec`,
  - `[x]` `source: manual`,
  - `[x]` `created_at`.
- `[x]` Backend potrafi policzyć, czy `footpoint` leży wewnątrz polygonu.
- `[x]` Analiza nie przekazuje do trackera obiektów, których footpoint jest poza boiskiem.
- `[~]` Istnieją overlaye z polygonem boiska, ale nie ma dedykowanego `pitch_debug.jpg`.

## Do not do yet

- Nie implementuj pełnej automatycznej detekcji boiska.
- Nie implementuj podań, piłki ani statystyk sezonowych.

## Suggested agent prompt

> Implementuj Milestone 1 z `docs/IMPLEMENTATION_PLAN.md`. Skup się wyłącznie na kalibracji boiska, edycji punktów, zapisie `pitch_config.json`, filtrze footpoint-in-pitch i debug overlay. Nie dodawaj jeszcze piłki ani statystyk.

---

# Milestone 2 — YOLO player tracking preview and ID flickering evaluation

**Status:** `[x]` implemented for primary analysis flow, including versioned analysis run snapshots.

## Cel

Dostarczyć narzędzie do testowania, czy raw `tracker_id` flickeruje na nagraniu z góry. Outputem musi być video overlay z widocznymi ID nad zawodnikami.

## User stories

### US2.1 — Wybór adaptera analizy
Jako użytkownik chcę wybrać adapter `yolo` albo `motion`, żeby porównać eksperymentalne tryby analizy.

### US2.2 — Wybór trackera
Jako użytkownik chcę wybrać `botsort.yaml` albo `bytetrack.yaml`, żeby sprawdzić, który tracker ma mniej ID switchy.

### US2.3 — Parametry YOLO
Jako użytkownik chcę ustawić model, confidence, image size, frame stride, max seconds i device, żeby testować kompromis jakość/szybkość.

### US2.4 — Overlay z ID
Jako użytkownik chcę otrzymać `overlay_preview.mp4` z etykietami `P<ID>`, żeby wizualnie ocenić flickering player ID.

### US2.5 — Eksport tracków
Jako developer chcę otrzymać `tracks.json`, żeby później budować tracklet resolver i statystyki.

### US2.6 — Porównanie konfiguracji
Jako użytkownik chcę uruchomić kilka analiz z różnymi parametrami i zachować ich artefakty osobno, żeby porównać jakość.

## Acceptance criteria

- `[x]` Analiza YOLO używa `pitch_config` i ignoruje osoby spoza boiska.
- `[x]` `overlay_preview.mp4` zawiera widoczne ID dla każdego trackowanego zawodnika.
- `[~]` `tracks.json` zawiera co najmniej:
  - `[x]` `track_id`,
  - `[~]` `frame` zamiast `frame_index`,
  - `[x]` `time_sec`,
  - `[~]` `bbox_xyxy` zamiast `bbox`,
  - `[x]` `footpoint`,
  - `[x]` `pitch_m` jeśli homografia jest dostępna,
  - `[x]` `confidence`,
  - `[~]` team jest rozwijany później w tracklet/stable layer, nie jako finalne `team_candidate` w raw tracks.
- `[x]` `analysis_report.json` zawiera parametry analizy i liczbę tracków.
- `[x]` UI pokazuje linki do artefaktów.
- `[x]` Motion adapter nadal działa jako fallback.
- `[x]` Porównanie konfiguracji ma zachowane artefakty osobno per run w `analysis_runs/<run_id>/`.

## Do not do yet

- Nie traktuj surowego `track_id` jako realnego zawodnika.
- Nie licz jeszcze finalnego dystansu gracza z raw tracków.
- Nie buduj jeszcze dashboardu sezonowego.

## Suggested agent prompt

> Implementuj Milestone 2 z `docs/IMPLEMENTATION_PLAN.md`. Celem jest overlay video z raw player/tracker IDs i eksport `tracks.json` do oceny flickeringu ID. Nie implementuj jeszcze identity resolvera ani statystyk zawodnika.

---

# Milestone 3 — Tracklet extraction and quality diagnostics

**Status:** `[x]` formal `tracklets.json` and `tracking_quality_report.json` artifacts are generated by analysis.

## Cel

Zamienić surowe rekordy trackingu w czytelne tracklety oraz dodać diagnostykę jakości: długości trackletów, przerwy, potencjalne ID switche, konflikty i fragmenty niskiej pewności.

## User stories

### US3.1 — Budowa trackletów
Jako system chcę grupować kolejne obserwacje tego samego `tracker_id` w `tracklet_id`, żeby dało się je potem przypisywać do zawodników.

### US3.2 — Tracklet summary
Jako użytkownik chcę widzieć listę trackletów z czasem start/end, długością i liczbą klatek, żeby ocenić stabilność trackingu.

### US3.3 — Suspicious moments
Jako użytkownik chcę dostać listę potencjalnych ID switchy, żeby wiedzieć, które fragmenty wymagają sprawdzenia.

### US3.4 — Tracklet preview
Jako użytkownik chcę kliknąć tracklet i zobaczyć fragment video z podświetlonym tylko tym trackletem.

### US3.5 — Quality metrics
Jako developer chcę mieć metryki diagnostyczne, żeby porównywać modele/trackery na tym samym fragmencie.

## Acceptance criteria

- `[x]` Backend buduje tracklety na podstawie `tracks.json` i zapisuje osobny `tracklets.json`.
- `[x]` Tracklet w formalnym kontrakcie ma pola:
  - `[x]` `tracklet_id`,
  - `[x]` `source_track_id`,
  - `[x]` `start_time_sec`,
  - `[x]` `end_time_sec`,
  - `[x]` `duration_sec`,
  - `[x]` `positions_count` i `frames_count`,
  - `[x]` `mean_confidence`,
  - `[x]` `missing_frames_count`,
  - `[x]` `team_candidate`, `team_label` i `team_confidence`,
  - `[x]` pozycje w metrach są w tracklet positions.
- `[x]` Backend generuje `tracking_quality_report.json`, `stabilization_report.json`, `global_identity_report.json` i `frame_detection_counts.json`.
- `[x]` UI pokazuje tabelę trackletów.
- `[ ]` UI nie ma jeszcze pełnych filtrów krótkich trackletów typu `< 1s`, `< 3s`.
- `[~]` UI pokazuje liczby trackletów, ale średnia długość trackletu nie jest pełnym widokiem jakości.

## Suggested suspicious event heuristics

- bardzo krótki tracklet w środku boiska,
- dwa tracklety tej samej drużyny bardzo blisko siebie czasowo i przestrzennie,
- nierealistyczny skok pozycji,
- tracklet przechodzący nagle przez wiele metrów w krótkim czasie,
- jednocześnie więcej niż 7 zawodników jednej drużyny na boisku,
- długi brak detekcji w tłoku.

## Do not do yet

- Nie wymagaj jeszcze ręcznego przypisywania do zawodników.
- Nie licz jeszcze sezonowych statystyk.

## Suggested agent prompt

> Implementuj Milestone 3 z `docs/IMPLEMENTATION_PLAN.md`: generowanie `tracklets.json`, diagnostykę jakości trackingu i widok listy trackletów. Zachowaj rozdział raw `tracker_id` i `tracklet_id`.

---

# Milestone 4 — Team assignment and non-player filtering

**Status:** `[~]` automatic team assignment and review exist, but explicit team config/sampling workflow is incomplete.

## Cel

Przypisać tracklety do drużyn na podstawie kolorów/stref/logiki oraz skutecznie odfiltrować sędziego, rezerwowych i osoby spoza gry.

## User stories

### US4.1 — Definicja kolorów drużyn
Jako użytkownik chcę wskazać kolory drużyn na klatce albo wybrać przykładowych zawodników, żeby system klasyfikował drużyny.

### US4.2 — Team assignment per detection/tracklet
Jako system chcę przypisać `team_candidate` do detekcji i trackletów, żeby później liczyć statystyki drużynowe.

### US4.3 — Unknown/ignore bucket
Jako system chcę oznaczać obiekty jako `unknown` albo `ignore_non_player`, żeby sędzia/rezerwowi nie byli liczeni jako gracze.

### US4.4 — Team count sanity check
Jako użytkownik chcę widzieć alert, gdy system wykrywa więcej niż 7 zawodników jednej drużyny na boisku.

### US4.5 — Goalkeeper handling
Jako użytkownik chcę móc oznaczyć bramkarza jako rolę w drużynie, nawet jeśli ma inny kolor koszulki.

## Acceptance criteria

- `[~]` UI pozwala zdefiniować kolory drużyn w metadanych meczu, ale nie ma jeszcze pełnego sample-pickera zawodników.
- `[ ]` Backend nie zapisuje jeszcze dedykowanego `team_config.json`.
- `[~]` Tracklety/stable slots zawierają `team_label` i `team_confidence`; raw `tracks.json` nie jest głównym miejscem dla team assignment.
- `[~]` Istnieje `unknown` oraz review `ignore/referee/false_positive` na stable slots/assignments, ale non-player filtering wymaga dalszego utwardzenia.
- `[x]` Raporty diagnostyczne zawierają liczby A/B/active per frame (`frame_detection_counts.json`, HUD overlayu).
- `[~]` UI pokazuje diagnostykę liczby slotów i team counts, ale nie ma jeszcze twardego alertu przekroczenia 7 przez zadany próg.
- `[~]` Goalkeeper handling działa częściowo przez color outliers w team clusteringu, ale nie ma jeszcze pełnego UI do oznaczania roli bramkarza.

## Do not do yet

- Nie implementuj rozpoznawania numerów koszulek.
- Nie implementuj face recognition.

## Suggested agent prompt

> Implementuj Milestone 4 z `docs/IMPLEMENTATION_PLAN.md`: team assignment, ignore bucket i sanity check liczby graczy. Nie dodawaj jeszcze player identity resolvera ani ball trackingu.

---

# Milestone 5 — Identity resolver: tracklet -> player -> stint

**Status:** `[~]` re-scoped in practice to anonymous stable slot/stint resolver first; real roster `player_id` mapping remains later work.

## Cel

Dodać panel, w którym użytkownik może przypisać jeden lub wiele trackletów do realnego zawodnika oraz oznaczyć okresy gry. To jest najważniejszy krok do wiarygodnych statystyk per zawodnik.

## User stories

### US5.1 — Roster meczu
Jako użytkownik chcę utworzyć listę zawodników drużyny na dany mecz, w tym gości/najemników, żeby przypisywać tracklety do realnych osób.

### US5.2 — Assign tracklet to player
Jako użytkownik chcę przypisać tracklet do zawodnika, żeby statystyki były liczone per realny zawodnik.

### US5.3 — Merge tracklets
Jako użytkownik chcę połączyć wiele trackletów w jednego zawodnika, gdy tracker zgubił ID albo zawodnik wrócił po zmianie.

### US5.4 — Split tracklet
Jako użytkownik chcę przeciąć tracklet w konkretnym czasie, gdy tracker zamienił ID między dwoma osobami.

### US5.5 — Stints
Jako użytkownik chcę oznaczyć okresy gry zawodnika, żeby system liczył realny czas na boisku mimo dynamicznych zmian.

### US5.6 — Conflict detection
Jako użytkownik chcę widzieć konflikty, np. jeden zawodnik przypisany do dwóch trackletów jednocześnie, żeby poprawić błędy.

### US5.7 — Suggested assignments
Jako użytkownik chcę dostać automatyczne propozycje przypisań, ale z możliwością ręcznego zatwierdzenia.

## Acceptance criteria

- `[~]` Istnieją struktury `PlayerPayload`, assignment docs oraz stable slot/stint docs; nie ma jeszcze pełnego finalnego modelu realnego `Player/Stint/IdentityAssignment`.
- `[~]` UI ma roster/metadane drużyn w meczu, ale roster nie jest jeszcze pełnym workflow identity.
- `[x]` UI ma listę trackletów i assignment panel w trybie debug/developer.
- `[x]` Użytkownik może przypisać tracklet do zawodnika w manual assignment flow.
- `[~]` Użytkownik może mapować wiele trackletów do tego samego zawodnika przez assignmenty, ale nie ma jeszcze ergonomicznego merge/split review.
- `[x]` Użytkownik może oznaczyć tracklet/stable slot jako `false_positive`, `referee`, `ignore` lub podobny status.
- `[~]` Backend zapisuje assignmenty oraz stable slots/stints (`identity_assignments.json`, `stable_players.json`, `global_identity.json`); osobny `stints.json` nie istnieje.
- `[~]` Backend blokuje agresywne identity/team switche i raportuje konflikty/ambiguous, ale nie wykrywa jeszcze pełnych konfliktów realnego `player_id`.
- `[~]` UI pokazuje risky/blocked/ambiguous diagnostics, ale nie blokuje jeszcze finalnych statystyk realnego `player_id`.
- `[~]` Suggested assignments istnieją jako automatyczne anonimowe stable slots `A##/B##`, nie jako propozycje realnych osób z rosteru.

## Do not do yet

- Nie implementuj jeszcze podań ani posiadania.
- Nie zakładaj, że raw tracker ID jest stabilne przez cały mecz.

## Suggested agent prompt

> Implementuj Milestone 5 z `docs/IMPLEMENTATION_PLAN.md`: identity resolver, roster, przypisywanie trackletów do zawodników, stinty i conflict detection. To ma być panel korekty po meczu, nie automatyczna magia.

---

# Milestone 6 — Player stats from tracking only

**Status:** `[~]` first tracking-only stats exist per stable slot; final player/team stats contract is not complete.

## Cel

Policzyć pierwsze wartościowe statystyki zawodnika i drużyny, które nie wymagają śledzenia piłki.

## User stories

### US6.1 — Playing time
Jako użytkownik chcę zobaczyć czas gry zawodnika, żeby wiedzieć, ile minut faktycznie był na boisku.

### US6.2 — Heatmap per player
Jako użytkownik chcę zobaczyć heatmapę zawodnika dla meczu, żeby ocenić jego strefy aktywności.

### US6.3 — Distance
Jako użytkownik chcę zobaczyć dystans zawodnika, liczony po wygładzonej trajektorii, żeby uniknąć zawyżenia przez jitter trackingu.

### US6.4 — Speed and sprints
Jako użytkownik chcę zobaczyć prędkość maksymalną i sprinty, żeby ocenić intensywność gry.

### US6.5 — Average position
Jako użytkownik chcę zobaczyć średnią pozycję zawodnika, żeby ocenić jego rolę na boisku.

### US6.6 — Team shape metrics
Jako użytkownik chcę zobaczyć proste statystyki drużynowe: szerokość, długość, kompaktowość, średnie ustawienie.

## Acceptance criteria

- `[~]` Statystyki są liczone po stable slot/stint (`A01/B01`), nie po raw `tracker_id`; realny `player_id` pozostaje kolejnym krokiem.
- `[x]` Dystans jest liczony z pozycji w metrach po smoothingu/quality gates i z osobnym `estimated_gap_distance_m`.
- `[~]` Backend zapisuje `movement_stats.json`, ale nie finalny `player_stats.json`.
- `[ ]` Backend nie zapisuje jeszcze `team_stats.json`.
- `[x]` UI pokazuje tabelę stabilnych zawodników i podstawowe statystyki ruchu.
- `[ ]` UI nie pokazuje jeszcze heatmapy per zawodnik.
- `[ ]` UI nie pozwala jeszcze ustawić progów sprintów/high intensity.
- `[x]` Raport/artefakty jasno rozróżniają tracking-only stats i brak piłki.
- `[x]` Overlay debug pokazuje live speed/distance przy bboxie zawodnika.

## Suggested smoothing rules

- Nie licz dystansu z surowego jitteru frame-by-frame bez filtra.
- Dodaj proste wygładzanie pozycji, np. moving average albo Savitzky-Golay.
- Odrzucaj nierealistyczne skoki pozycji.
- Sprint licz tylko, gdy prędkość przekracza próg przez minimalny czas, np. 0.3-0.5s.

## Do not do yet

- Nie licz podań, strzałów ani posiadania.
- Nie pokazuj statystyk jako precyzyjnych do centymetra/metra, jeśli tracking ma niską pewność.

## Suggested agent prompt

> Implementuj Milestone 6 z `docs/IMPLEMENTATION_PLAN.md`: czas gry, heatmapy, dystans, sprinty, średnia pozycja i proste team shape metrics na bazie `player_id` oraz `stints`. Nie dodawaj jeszcze ball trackingu.

---

# Milestone 7 — Match report UI

**Status:** `[~]` artifact/stable review UI exists; polished separate match report page is still pending.

## Cel

Zamienić surowe artefakty JSON/PNG/MP4 w czytelny raport meczowy dla użytkownika.

## User stories

### US7.1 — Match summary
Jako użytkownik chcę zobaczyć podsumowanie meczu: drużyny, czas, liczba zawodników, status analizy.

### US7.2 — Player table
Jako użytkownik chcę zobaczyć tabelę zawodników z czasem gry, dystansem, sprintami i max speed.

### US7.3 — Player detail
Jako użytkownik chcę kliknąć zawodnika i zobaczyć jego heatmapę, stinty i overlay trackingu.

### US7.4 — Team comparison
Jako użytkownik chcę porównać podstawowe metryki drużynowe.

### US7.5 — Artifact browser
Jako użytkownik chcę mieć linki do overlay video, JSON i obrazów debugowych.

## Acceptance criteria

- `[~]` Client ma sekcje raportowo-review dla meczu, ale nie ma jeszcze osobnego, dopracowanego widoku Match Report.
- `[x]` Komponenty UI są rozdzielone od transformacji danych w nowych komponentach.
- `[x]` Zwykłe style są w CSS, nie jako inline CSS.
- `[x]` API client jest osobno od komponentów.
- `[~]` UI pokazuje diagnostykę braków/niepewności, ale statusy brakujących kroków wymagają uporządkowania productowego.
- `[x]` UI ma artifact browser dla overlay video, JSON i debug details.

## Do not do yet

- Nie dodawaj logowania/użytkowników.
- Nie dodawaj jeszcze sezonowej agregacji, jeśli pojedynczy match report nie jest stabilny.

## Suggested agent prompt

> Implementuj Milestone 7 z `docs/IMPLEMENTATION_PLAN.md`: czytelny Match Report UI dla istniejących artefaktów i statystyk. Zachowaj zasady z `client/AGENTS.md`.

---

# Milestone 8 — Season storage and aggregation

**Status:** `[~]` local publish/package persistence exists; real season/roster aggregation is not implemented.

## Cel

Zacząć gromadzić statystyki zawodników i drużyn między meczami.

## User stories

### US8.1 — Persistent roster
Jako użytkownik chcę mieć stałą listę zawodników drużyny, żeby ten sam `player_id` był używany w kolejnych meczach.

### US8.2 — Guest players
Jako użytkownik chcę dodać zawodnika gościnnego/najemnika, który może wystąpić tylko w jednym meczu albo zostać później połączony z istniejącym profilem.

### US8.3 — Season dashboard
Jako użytkownik chcę widzieć agregację sezonową: mecze, minuty, dystans, sprinty, heatmapę sezonową.

### US8.4 — Team season stats
Jako użytkownik chcę widzieć średnie drużynowe per sezon.

### US8.5 — Recompute stats
Jako użytkownik chcę przeliczyć statystyki sezonowe po poprawieniu identity assignments w meczu.

## Acceptance criteria

- `[~]` Istnieje lokalna persystencja/publish package w SQLite dla opublikowanych meczów i stable players, ale nie pełny roster/sezon.
- `[x]` Startowa lokalna warstwa SQLite/JSON package istnieje bez ciężkiej infrastruktury.
- `[ ]` Sezonowe statystyki z zatwierdzonych meczów nie są jeszcze liczone.
- `[ ]` UI nie pokazuje jeszcze profilu zawodnika przez wiele meczów.
- `[~]` Mecze mają statusy typu `draft/uploaded/calibrated/analyzed/reviewed/published`, ale nie pełny workflow `needs_review/approved`.

## Do not do yet

- Nie rób cloud multi-tenant.
- Nie rób skomplikowanego auth.

## Suggested agent prompt

> Implementuj Milestone 8 z `docs/IMPLEMENTATION_PLAN.md`: lokalny roster, profile zawodników, status meczu i sezonową agregację trackingowych statystyk. Nie dodawaj jeszcze eventów piłkarskich zależnych od piłki.

---

# Milestone 9 — Ball tracking foundation

**Status:** `[ ]` not implemented.

## Cel

Dodać pierwszą wersję warstwy piłki, ale bez obiecywania pełnych podań i posiadania. Na tym etapie chodzi o pozycję piłki, interpolację krótkich braków i confidence.

## User stories

### US9.1 — Ball detector adapter
Jako system chcę wykrywać piłkę jako osobną klasę/model, żeby później budować posiadanie i eventy.

### US9.2 — Ball confidence
Jako użytkownik chcę wiedzieć, kiedy pozycja piłki jest wykryta, interpolowana albo nieznana, żeby ufać statystykom tylko tam, gdzie dane są dobre.

### US9.3 — Ball interpolation
Jako system chcę uzupełniać krótkie braki detekcji piłki, żeby tor piłki był ciągły przy chwilowych zgubieniach.

### US9.4 — Ball overlay
Jako użytkownik chcę zobaczyć overlay video z piłką i statusem `detected/interpolated/unknown`.

### US9.5 — Ball diagnostics
Jako użytkownik chcę zobaczyć coverage piłki: ile czasu wykryta, ile interpolowana, ile unknown.

## Acceptance criteria

- `[ ]` Backend generuje `ball_tracks.json`.
- `[ ]` Każdy rekord pozycji piłki ma:
  - `[ ]` `time_sec`,
  - `[ ]` `position_px`,
  - `[ ]` `position_m`,
  - `[ ]` `source: detected | interpolated | predicted | unknown`,
  - `[ ]` `confidence`.
- `[ ]` Interpolacja krótkich braków ma konfigurowalny limit, np. max 0.5s/1.0s.
- `[ ]` Długie braki nie są wymyślane jako pewna pozycja.
- `[ ]` UI pokazuje coverage piłki.

## Do not do yet

- Nie implementuj jeszcze podań per zawodnik jako finalnej statystyki.
- Nie oznaczaj interpolowanych danych jako pewnych.

## Suggested agent prompt

> Implementuj Milestone 9 z `docs/IMPLEMENTATION_PLAN.md`: ball detector foundation, interpolacja krótkich braków, confidence i overlay piłki. Nie implementuj jeszcze pełnego pass/possession engine.

---

# Milestone 10 — Possession and simple event candidates

**Status:** `[ ]` not implemented.

## Cel

Zbudować pierwszą warstwę eventów piłkarskich jako kandydaty z confidence, nie jako nieomylne statystyki.

## User stories

### US10.1 — Possession candidate
Jako użytkownik chcę zobaczyć szacowane posiadanie drużynowe, żeby mieć orientacyjny obraz meczu.

### US10.2 — Free/contested ball
Jako użytkownik chcę widzieć czas, w którym piłka była wolna/sporna/niepewna, zamiast sztucznie przypisywać ją jednej drużynie.

### US10.3 — Touch/contact candidates
Jako system chcę wykrywać kandydatów kontaktu zawodnika z piłką, żeby później budować podania i strzały.

### US10.4 — Shots candidates
Jako użytkownik chcę zobaczyć kandydatów strzałów z możliwością korekty.

### US10.5 — Simple event review
Jako użytkownik chcę potwierdzić/poprawić eventy w panelu, żeby statystyki były bardziej wiarygodne.

## Acceptance criteria

- `[ ]` Backend generuje `possession_segments.json`.
- `[ ]` Segmenty mają `team`, `start_time`, `end_time`, `confidence`, `source`.
- `[ ]` Jest osobna kategoria `unknown/free/contested`.
- `[ ]` Backend generuje `event_candidates.json`.
- `[ ]` UI pokazuje segmenty posiadania i podstawowe eventy.
- `[ ]` Użytkownik może oznaczyć event jako accepted/rejected/corrected.

## Do not do yet

- Nie licz jeszcze rankingów typu `most forward passes` jako finalnych, jeśli pass events nie są zatwierdzone.

## Suggested agent prompt

> Implementuj Milestone 10 z `docs/IMPLEMENTATION_PLAN.md`: possession candidates, free/contested ball, contact candidates i podstawowy event review. Wszystko z confidence, nie jako nieomylne dane.

---

# Milestone 11 — Passes, forward passes and progressive passes

**Status:** `[ ]` not implemented.

## Cel

Dodać statystyki podań jako warstwę eventową po zbudowaniu stabilnego player identity oraz ball tracking.

## User stories

### US11.1 — Pass candidate detection
Jako system chcę wykrywać kandydatów podań na podstawie kontaktów z piłką i zmiany posiadacza.

### US11.2 — Completed/incomplete pass
Jako użytkownik chcę widzieć, czy podanie było celne, niecelne, przejęte albo unknown.

### US11.3 — Forward pass classification
Jako użytkownik chcę wiedzieć, kto miał najwięcej podań do przodu w drużynie.

### US11.4 — Progressive pass classification
Jako użytkownik chcę widzieć podania progresywne, które realnie przesuwają grę bliżej bramki.

### US11.5 — Pass review panel
Jako użytkownik chcę szybko poprawić błędne kandydaty podań.

## Acceptance criteria

- `[ ]` Backend generuje `pass_candidates.json`.
- `[ ]` Każde podanie ma:
  - `[ ]` `passer_player_id`,
  - `[ ]` `receiver_player_id | unknown`,
  - `[ ]` `team`,
  - `[ ]` `start_time`,
  - `[ ]` `end_time`,
  - `[ ]` `start_position_m`,
  - `[ ]` `end_position_m`,
  - `[ ]` `direction: forward | lateral | backward`,
  - `[ ]` `is_progressive`,
  - `[ ]` `outcome`,
  - `[ ]` `confidence`,
  - `[ ]` `review_status`.
- `[ ]` Kierunek podania uwzględnia stronę ataku drużyny i zmianę stron, jeśli występuje.
- `[ ]` Ranking `most forward passes` jest liczony tylko z zaakceptowanych albo high-confidence podań, zależnie od konfiguracji.
- `[ ]` UI pokazuje ranking podań do przodu i progressive passes.

## Suggested definitions for 7v7/orlik

- `forward pass`: koniec podania jest co najmniej 1.5-2.0 m bliżej bramki rywala niż start.
- `progressive pass`: koniec podania jest co najmniej 5.0 m bliżej bramki rywala albo wprowadza piłkę do tercji ataku/strefy strzału.
- Progi powinny być konfigurowalne, bo boiska orlikowe różnią się wymiarami.

## Do not do yet

- Nie udawaj profesjonalnej dokładności bez panelu korekty.
- Nie licz podań, jeśli ball confidence jest za niski.

## Suggested agent prompt

> Implementuj Milestone 11 z `docs/IMPLEMENTATION_PLAN.md`: pass candidates, forward/progressive pass classification i ranking `most forward passes`, ale tylko na bazie zatwierdzonych albo high-confidence eventów.

---

# Milestone 12 — Product hardening and performance

**Status:** `[ ]` not implemented beyond basic Docker/local workflow.

## Cel

Usprawnić aplikację pod pełne mecze 40-45 minut i pracę na laptopie użytkownika.

## User stories

### US12.1 — Background jobs
Jako użytkownik chcę uruchomić analizę jako job z postępem, żeby UI nie wisiało podczas przetwarzania długiego filmu.

### US12.2 — Progress reporting
Jako użytkownik chcę widzieć procent przetworzenia i aktualny etap.

### US12.3 — Resume/retry
Jako użytkownik chcę móc wznowić lub ponowić analizę bez ponownego uploadu filmu.

### US12.4 — Artifact cleanup
Jako użytkownik chcę usuwać niepotrzebne artefakty overlay/cache, żeby nie zapchać dysku.

### US12.5 — Performance presets
Jako użytkownik chcę wybrać preset: szybki test, standard, jakość, żeby dobrać parametry do laptopa.

## Acceptance criteria

- `[ ]` Analiza nie musi być synchroniczna w request/response.
- `[ ]` UI pokazuje status joba.
- `[ ]` Można zatrzymać/ponowić analizę.
- `[ ]` Artefakty są wersjonowane per run.
- `[ ]` Istnieją presety YOLO/tracking, np.:
  - `[ ]` `fast_debug`: max 30s, stride 2, imgsz 640/960,
  - `[ ]` `standard`: full clip, stride 1, imgsz 960,
  - `[ ]` `quality`: full clip, stride 1, imgsz 1280.

## Suggested agent prompt

> Implementuj Milestone 12 z `docs/IMPLEMENTATION_PLAN.md`: background jobs, progress reporting, artifact cleanup i performance presets dla pełnych meczów.

---

# Milestone dependency order

Nie przechodź do milestone'u zależnego, jeśli poprzednia warstwa nie działa.

```text
0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10 -> 11 -> 12
```

W praktyce minimalna ścieżka do wartościowego MVP to:

```text
1 Pitch calibration
2 YOLO ID preview
3 Tracklets + quality
5 Identity resolver
6 Player stats
7 Match report
```

Milestone 4 można robić równolegle po 2/3, jeśli team assignment zaczyna blokować dalsze prace.

Ball/event layer zaczyna się dopiero od Milestone 9.

---

# Global acceptance checklist before calling the MVP useful

Aplikację można uznać za użyteczne MVP dopiero, gdy:

- `[x]` użytkownik uploaduje raw video,
- `[x]` oznacza boisko,
- `[x]` uruchamia tracking,
- `[x]` dostaje overlay z ID,
- `[x]` widzi tracklety,
- `[~]` przypisuje tracklety do zawodników; działa manual/debug flow, ale docelowy realny `player_id -> stint` nie jest gotowy,
- `[x]` system liczy czas gry dla stable slotów,
- `[ ]` system generuje heatmapę per zawodnik,
- `[x]` system liczy dystans z wygładzeniem dla stable slotów,
- `[ ]` system liczy sprinty/progi intensywności,
- `[~]` użytkownik widzi raport/diagnostykę meczu, ale nie pełny polished Match Report,
- `[x]` dane da się zachować i wrócić do nich później przez lokalny storage/package.

---

# Rules for future agents

1. Do not implement football event stats before player tracking and identity resolution are usable.
2. Do not store business/domain logic in React components.
3. Do not put CV/video processing logic inside FastAPI route handlers.
4. Do not treat `tracker_id` as `player_id`.
5. Do not hide uncertainty. Add confidence/status fields.
6. Do not overwrite old analysis artifacts without preserving run metadata.
7. Do not add complex infrastructure unless a milestone requires it.
8. Prefer short clips and debug artifacts for testing.
9. Keep generated files out of git.
10. Update this file when a milestone is completed or re-scoped.
