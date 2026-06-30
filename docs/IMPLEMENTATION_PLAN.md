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
- `[x]` Team assignment: automatic Team A/B assignment, `team_clusters.json`, explicit `team_config.json`, Team A/B lock/review, stable-slot team correction, ignore/referee/false-positive review, team sanity diagnostics and `team_stats.json` exist. Torso-color clustering remains implementation evidence/debug, not a required user workflow.
- `[x]` Identity resolver/review: anonymous stable slot/stint identity (`A01-A07`, `B01-B07`, plus bench subjects such as `A08+`/`B08+`) exists with conservative anti-switch logic, `change_candidates.json` flags likely on/off changes for review, and `player_identity_assignments.json` maps stable slots/stints to real roster `player_id`.
- `[~]` Player stats: tracking-only movement stats, conservative `peak_sustained_speed`, sprint/high-intensity metrics, sprint candidate/rejection diagnostics, per-player heatmaps, formal `player_stats.json`, and basic `team_stats.json` exist; configurable thresholds UI is not done.
- `[~]` Match report/admin UI: app shows artifacts, stable slots, team config, analysis runs, `analysis_quality_report.json`, quality diagnostics and movement/player stats; local `/matches/:matchId/report` and public `/published/matches/:matchId/report` now share one layout, and admin uses a step-by-step workflow, while export/share polish is still pending.
- `[~]` Tracking-only cross-match aggregation exists for player profiles and local team dashboard; ball tracking, conservative possession/contact candidates, contact-candidate review, derived `event_candidates.json` and experimental `pass_candidates.json` with pass geometry exist as candidate layers, background analysis jobs and chunk manifest foundation exist, while shots, full event review, export/share polish and true per-chunk CV merge are not implemented.

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

**Status:** `[x]` closed for MVP. Team A/B assignment, review and diagnostics are implemented; manual color sampling is not required for the product flow.

## Cel

Przypisać stabilne sloty/tracklety do `Team A` albo `Team B`, dać użytkownikowi prostą korektę i nie liczyć obiektów oznaczonych jako `unknown`, `ignore`, `referee` albo `false_positive`.

Kolor koszulki/znacznika jest tylko automatycznym sygnałem pomocniczym po stronie backendu. Użytkownik nie powinien musieć rozumieć klastrów kolorów ani ręcznie wybierać próbek kolorów, jeśli automatyczny Team A/B działa.

## User stories

### US4.1 — Team A/B match setup
Jako użytkownik chcę wybrać drużyny z rejestru `/teams` dla `Team A` i `Team B`, żeby analiza i statystyki miały czytelne nazwy drużyn.

### US4.2 — Automatic team assignment
Jako system chcę automatycznie przypisać stable slot/tracklet do `Team A`, `Team B` albo `unknown`, żeby statystyki drużynowe nie wymagały ręcznego klikania każdego zawodnika.

### US4.3 — Team review and correction
Jako użytkownik chcę móc zamienić Team A/B lub poprawić drużynę pojedynczego stable slotu, żeby naprawić błędny automatyczny assignment.

### US4.4 — Unknown/ignore bucket
Jako system chcę oznaczać obiekty jako `unknown`, `ignore`, `referee` albo `false_positive`, żeby sędzia/rezerwowi/fałszywe detekcje nie były liczone jako gracze.

### US4.5 — Team count sanity check
Jako użytkownik chcę widzieć alert, gdy system wykrywa więcej niż 7 zawodników jednej drużyny na boisku.

## Acceptance criteria

- `[x]` UI ma osobny rejestr `/teams` dla drużyn/rosterów oraz wybór Team A/B przy meczu.
- `[x]` Backend zapisuje dedykowane `team_config.json`, `team_clusters.json` i `team_stats.json`.
- `[x]` Tracklety/stable slots zawierają `team_label`, `team_confidence`, `team_id` i `team_name`; raw `tracks.json` nie jest głównym miejscem dla team assignment.
- `[x]` UI pozwala zamienić Team A/B, przypisać roster team do labela A/B i zablokować zweryfikowany `team_config`.
- `[x]` UI pozwala poprawić team pojedynczego stable slotu oraz oznaczyć go jako `ignore`, `referee` albo `false_positive`.
- `[x]` Raporty diagnostyczne zawierają liczby A/B/active per frame (`frame_detection_counts.json`, HUD overlayu).
- `[x]` UI pokazuje team stats, unknown stable players i twardy alert `Team over-cap`, gdy raport wykryje więcej niż 7 aktywnych obiektów w jednej drużynie.
- `[x]` Torso-color clustering działa jako automatyczna evidence/debug layer; ręczny sample-picker kolorów nie jest wymagany do zamknięcia MVP.
- `[x]` Bramkarz w innym kolorze jest obsługiwany praktycznie przez stable-slot team correction albo `unknown`/review; osobny goalkeeper-role UI nie jest wymagany w MVP.

## Do not do yet

- Nie implementuj rozpoznawania numerów koszulek.
- Nie implementuj face recognition.
- Nie dodawaj ręcznego color sample-pickera jako domyślnego workflow, dopóki Team A/B review wystarcza.

## Suggested agent prompt

> Milestone 4 jest zamknięty dla MVP. Kontynuuj od Milestone 5/6/7 zależnie od celu: identity assignment do realnych zawodników, tracking-only player stats albo czytelny Match Report UI. Nie dodawaj jeszcze ball trackingu.

---

# Milestone 5 — Identity resolver: tracklet -> player -> stint

**Status:** `[x]` closed for MVP as stable-slot/stint identity review. Anonymous resolver remains automatic, supports bench subjects above the 7 active on-pitch slots per team, change candidates are generated for possible on/off substitutions, and the user can map stable slots to real roster `player_id`.

## Cel

Dodać panel, w którym użytkownik może przypisać stabilny slot/stint (`A01`, `B04`, itd.) do realnego zawodnika z rosteru. Raw tracklety pozostają debugiem; główny workflow identity działa na konserwatywnych stable slots, bo to jest warstwa używana później do statystyk personalnych.

## User stories

### US5.1 — Roster meczu
Jako użytkownik chcę utworzyć listę zawodników drużyny na dany mecz, w tym gości/najemników, żeby przypisywać tracklety do realnych osób.

### US5.2 — Assign stable slot/stint to player
Jako użytkownik chcę przypisać stable slot/stint do zawodnika, żeby późniejsze statystyki mogły być agregowane po realnym `player_id`.

### US5.3 — Stable slot as suggested assignment
Jako użytkownik chcę dostać automatyczny stable slot jako propozycję identity, żeby nie musieć ręcznie łączyć dziesiątek raw trackletów.

### US5.4 — Stints
Jako użytkownik chcę widzieć stinty stable slotu, żeby rozumieć okresy gry i przygotować późniejsze przypisania per wejście/zejście.

### US5.5 — Conflict detection
Jako użytkownik chcę widzieć konflikt, gdy stable slot z Team A zostanie przypisany do zawodnika Team B, żeby poprawić błąd team assignment albo identity assignment.

### US5.6 — Legacy raw tracklet debug
Jako developer chcę zachować stary raw tracklet assignment w debug details, żeby analizować przypadki, których stable resolver nie rozwiązał.

### US5.7 — Change candidates
Jako użytkownik chcę widzieć kandydatów zmian typu `A01 off -> A08 on`, żeby przy pełnym meczu ręcznie reviewować tylko kilka wejść/zejść zamiast setek trackletów.

## Acceptance criteria

- `[x]` Istnieją struktury `PlayerPayload`, roster team docs, stable slot/stint docs oraz formalny `player_identity_assignments.json`.
- `[x]` UI ma osobny rejestr drużyn/rosterów pod `/teams`; mecz trzyma snapshot wybranych drużyn i zawodników.
- `[x]` UI pozwala dopiac albo zmienic roster snapshot dla juz istniejacego meczu bez ponownego uploadu video.
- `[x]` UI pozwala przypisać wybrany stable slot do realnego zawodnika z rosteru meczu.
- `[x]` Backend ma endpointy `GET/PUT /api/matches/{match_id}/player-identity`.
- `[x]` Częściowe przypisanie jest poprawnym workflow: użytkownik może przypisać tylko swoją drużynę, a przeciwnik może zostać anonimowy.
- `[x]` `player_identity_assignments.json` zapisuje `stable_subject_id`, `stable_player_id`, opcjonalne `stint_id`, status, `player_id`, dane zawodnika i `review_warnings`.
- `[x]` Dokument rozwija przypisania do `expanded_stint_assignments`, żeby późniejsza agregacja mogła działać po stintach.
- `[x]` UI ma listę trackletów i assignment panel wyłącznie w trybie debug/developer.
- `[x]` Użytkownik może oznaczyć tracklet/stable slot jako `false_positive`, `referee`, `ignore` lub podobny status.
- `[x]` Backend zapisuje assignmenty, stable slots/stints (`player_identity_assignments.json`, `stable_players.json`, `global_identity.json`) i dołącza je do `match_package.json`.
- `[x]` Resolver utrzymuje limit 7 aktywnych slotów na drużynę, ale może tworzyć bench subjects `A08+`/`B08+` zamiast agresywnie recyklingować `A01-A07` po długiej nieobecności.
- `[x]` Backend generuje `change_candidates.json` i `change_review_report.json` na bazie stable slotów; dla krótkich sample bez zmian lista może być pusta.
- `[x]` API ma `GET /api/matches/{match_id}/change-candidates` oraz `PUT /api/matches/{match_id}/change-candidates/review`.
- `[x]` UI pokazuje `Change candidates review` z decyzjami `needs_review`, `confirmed`, `uncertain`, `rejected`, `ignored`.
- `[x]` Review zmian nie przepisuje jeszcze automatycznie `player_identity_assignments.json`; to pozostaje osobnym, bezpiecznym krokiem.
- `[x]` Backend wykrywa team mismatch między stable slotem a przypisanym zawodnikiem i zapisuje `review_warnings`.
- `[x]` UI pokazuje risky/blocked/ambiguous diagnostics oraz identity warning dla konfliktów roster/team.
- `[x]` Suggested assignments istnieją jako automatyczne anonimowe stable slots `A##/B##`, które użytkownik zatwierdza przez roster mapping.

## Do not do yet

- Nie implementuj jeszcze podań ani posiadania.
- Nie zakładaj, że raw tracker ID jest stabilne przez cały mecz.
- Nie buduj jeszcze sezonowej agregacji po `player_id`; to jest Milestone 8.
- Nie rób merge/split raw trackletów jako głównego workflow, dopóki stable resolver działa wystarczająco dobrze.

## Suggested agent prompt

> Milestone 5 jest zamknięty dla MVP. Kolejny krok to Milestone 6/7 dla raportu albo Milestone 8, jeśli chcesz użyć `player_identity_assignments.json` do `resolved_player_stats.json` i profilu `/players/:playerId`.

---

# Milestone 6 — Player stats from tracking only

**Status:** `[~]` tracking-only `player_stats.json` exists per stable slot; conservative peak sustained speed, sprint/high-intensity metrics, sprint candidate/rejection diagnostics, per-player heatmaps and team stats exist, stable overlay has safe short-gap visual hold for `frame_stride` preview gaps, while configurable thresholds UI is still pending.

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
Jako użytkownik chcę zobaczyć defensywną prędkość szczytową (`peak_sustained_speed`) i sprinty, żeby ocenić intensywność gry bez zawyżania przez jitter bboxów.

### US6.5 — Average position
Jako użytkownik chcę zobaczyć średnią pozycję zawodnika, żeby ocenić jego rolę na boisku.

### US6.6 — Team shape metrics
Jako użytkownik chcę zobaczyć proste statystyki drużynowe: szerokość, długość, kompaktowość, średnie ustawienie.

## Acceptance criteria

- `[x]` Statystyki są liczone po stable slot/stint (`A01/B01`), nie po raw `tracker_id`; realny `player_id` pozostaje kolejnym krokiem.
- `[x]` Dystans jest liczony z pozycji w metrach po smoothingu/quality gates i z osobnym `estimated_gap_distance_m`.
- `[x]` Backend zapisuje `movement_stats.json` oraz formalny tracking-only `player_stats.json`.
- `[x]` Backend zapisuje `player_heatmaps.json` oraz per-slot PNG w `player_heatmaps/`.
- `[x]` Backend zapisuje podstawowy tracking-only `team_stats.json`.
- `[x]` `top_speed_*` jest kompatybilnym aliasem dla `peak_sustained_speed_*`, a nie surowym maksimum z pojedynczego segmentu.
- `[x]` Statystyki prędkości zawierają `speed_quality`, `raw_segment_top_speed_*` i liczbę okien/odrzuconych segmentów.
- `[x]` UI pokazuje tabelę stabilnych zawodników i podstawowe statystyki ruchu.
- `[x]` UI pokazuje heatmapę wybranego stable slotu w player detail.
- `[x]` Backend liczy sprint/high-intensity metrics konserwatywnie z zaufanych krótkich segmentów detekcji, bez używania długich braków/interpolacji.
- `[x]` `player_stats.json`, `team_stats.json`, `resolved_player_stats.json`, profil zawodnika i Match Report pokazują sprint count, sprint distance/time oraz high-intensity distance/time.
- `[x]` Raporty pokazują diagnostykę kandydatów sprintu: `sprint_candidate_count`, `rejected_sprint_candidate_count`, `best_sprint_candidate_*` i powód odrzucenia, bez podbijania konserwatywnego `sprint_count`.
- `[ ]` UI nie pozwala jeszcze ustawić progów sprintów/high intensity; aktualne progi są stałe w backendzie.
- `[x]` Raport/artefakty jasno rozróżniają tracking-only stats i brak piłki.
- `[x]` Overlay debug pokazuje live speed/distance przy bboxie zawodnika.
- `[x]` `stable_overlay_preview.mp4` redukuje miganie bboxów przy `frame_stride > 1` przez krótki, konserwatywny visual hold między dwiema zaufanymi detekcjami; `frame_detection_counts.json` rozróżnia `trusted_detected`, `visible_stable_boxes`, `visual_interpolated_boxes` i `predicted_visible_boxes`.

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

**Status:** `[~]` artifact/stable/team review UI exists, legacy debug is hidden, local/published match report pages share one layout, team comparison uses a report-style side-by-side layout, analysis quality scoring/reporting exists, the local admin panel uses a step-by-step match workflow, and MVP export/share actions exist; deeper public-share polish is still pending.

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

- `[x]` Client ma osobny route `/matches/:matchId/report` dla czytelnego raportu pojedynczego meczu.
- `[x]` Client ma publiczny route `/published/matches/:matchId/report` dla snapshotów opublikowanych w SQLite.
- `[x]` Match Report pokazuje summary, stable overlay, team comparison, real assigned players i wszystkich stable zawodników w meczu.
- `[x]` Team comparison w Match Report jest kolumnowym zestawieniem Team A vs Team B, a nie szeroką tabelą.
- `[x]` Match Report rozróżnia anonimowe stable sloty meczowe od realnych `player_id` i linkuje przypisanych zawodników do `/players/:playerId`.
- `[x]` Client ma sekcje raportowo-review dla meczu, osobny `/teams` registry i link do pełnego raportu z publicznej strony głównej.
- `[x]` Admin panel ma krokowy workflow: video, kalibracja/analiza, review/przypisania, raport/publikacja.
- `[x]` Metadata, team config, advanced YOLO settings, artifact browser i legacy raw identity panels sa opcjonalne albo schowane w details/debug zamiast blokowac glowny flow.
- `[x]` Komponenty UI są rozdzielone od transformacji danych w nowych komponentach.
- `[x]` Zwykłe style są w CSS, nie jako inline CSS.
- `[x]` API client jest osobno od komponentów.
- `[x]` UI pokazuje analizę runów, metryki jakości trackingu, low-visible frames, suspicious tracklets i blocked switches.
- `[x]` Backend generuje `analysis_quality_report.json` z quality score oraz komponentami tracking/identity/stats/team assignment.
- `[x]` Match Report pokazuje sekcję `Jakość analizy`, główne warningi i najgorsze problem frames.
- `[x]` `analysis_quality_report.json` jest dostępny przez API, `match_package.json` i artifact browser.
- `[x]` Jest lokalny smoke checker `npm run quality:analysis`, który sprawdza `analysis_quality_report.json` dla zapisanych meczów i wyłapuje regresje score/ghost/low-visible.
- `[~]` UI pokazuje diagnostykę braków/niepewności, ale statusy brakujących kroków wymagają uporządkowania productowego.
- `[x]` UI ma artifact browser dla overlay video, JSON i debug details.
- `[x]` Legacy identity candidates oraz raw tracklet assignment nie są już domyślnym workflow i są schowane w developer debug.
- `[x]` Raport lokalny ma akcje: kopiowanie linku, generowanie `match_package.json`, publikacja/nadpisanie w SQLite, pobranie package JSON i druk/PDF.
- `[x]` Raport publiczny ma akcje: kopiowanie linku, eksport snapshotu JSON i druk/PDF.
- `[~]` Raport ma widok publiczny bez admin panelu; public-share polish jest lokalny/SQLite, bez hostingu ani auth.

## Do not do yet

- Nie dodawaj logowania/użytkowników.
- Nie dodawaj jeszcze sezonowej agregacji, jeśli pojedynczy match report nie jest stabilny.

## Suggested agent prompt

> Implementuj Milestone 7 z `docs/IMPLEMENTATION_PLAN.md`: czytelny Match Report UI dla istniejących artefaktów i statystyk. Zachowaj zasady z `client/AGENTS.md`.

---

# Milestone 8 — Player profiles and cross-match aggregation

**Status:** `[~]` local team registry, match-level roster assignment, `resolved_player_stats.json`, `/players/:playerId` profile aggregation and local `/teams/:teamId/stats` team dashboard exist; season heatmaps and polished exports are not implemented.

## Cel

Zacząć gromadzić statystyki zawodników i drużyn między meczami na podstawie trwałego `player_id` z lokalnego rosteru. To nie jest system logowania ani prywatny portal gracza: profile zawodników są lokalnym/publicznym widokiem danych przypisanych w aplikacji.

Docelowy model:

```text
team roster player_id -> match stable slot/stint -> resolved player stats per match -> /players/:playerId aggregate
```

Ważne doprecyzowanie: nie wymagamy przypisania obu drużyn. Typowy workflow może polegać na przypisaniu tylko własnej drużyny, a przeciwnik pozostaje anonimowy jako stable slots/stints.

Najważniejsza zasada: zawodnik pojawia się w agregacji tylko w tych meczach, w których stable slot/stint został przypisany do jego `player_id`.

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

### US8.6 — Player profile across matches, no auth
Jako użytkownik chcę wejść w profil zawodnika, np. `/players/:playerId`, i zobaczyć jego sumaryczne statystyki oraz listę meczów, w których został przypisany do stable slotu/stintu.

### US8.7 — Player match breakdown
Jako użytkownik chcę na profilu zawodnika zobaczyć statystyki per mecz: czas gry, dystans, peak sustained speed, heatmapę/pozycje i jakość danych, żeby porównać występy.

### US8.8 — Match roster assignments as source
Jako system chcę agregować statystyki wyłącznie z zatwierdzonych przypisań `stable_subject_id/stint -> player_id`, żeby nie mieszać anonimowych slotów `A03/B05` z realnymi zawodnikami.

## Acceptance criteria

- `[~]` Istnieje lokalny rejestr `/teams` zapisany w JSON oraz persystencja/publish package w SQLite dla opublikowanych meczów i stable players, ale nie pełny roster/sezon.
- `[x]` Match workflow pozwala przypisac istniejaca druzyne z `/teams` do aktualnego meczu jako Team A oraz opcjonalnie Team B.
- `[x]` Startowa lokalna warstwa SQLite/JSON package istnieje bez ciężkiej infrastruktury.
- `[x]` Istnieje artefakt per mecz `player_identity_assignments.json`, mapujący `stable_subject_id`/stint na realny `player_id` z rosteru.
- `[x]` Istnieje artefakt per mecz `resolved_player_stats.json`, z tracking-only statystykami po realnym `player_id`.
- `[x]` API ma `GET /api/matches/{match_id}/resolved-player-stats`, a zapis player identity automatycznie przelicza `resolved_player_stats.json`.
- `[x]` Agregator potrafi zebrać wszystkie mecze, w których występuje dany `player_id`, i policzyć sumy/średnie: mecze, czas gry, dystans, observed/estimated distance, peak sustained speed i jakość danych.
- `[x]` API ma `GET /api/players/{player_id}/stats` dla tracking-only profilu zawodnika.
- `[x]` UI ma route `/players/:playerId` pokazujący profil zawodnika bez logowania.
- `[x]` Profil zawodnika pokazuje summary across matches oraz tabelę per match.
- `[x]` Profil zawodnika pokazuje tylko dane z meczów, w których ten `player_id` został jawnie przypisany do stable slotu/stintu.
- `[x]` Profil zawodnika i agregator nie traktują nieprzypisanych slotów przeciwnika jako brakującej pracy; to poprawny anonimowy stan.
- `[x]` Profil zawodnika rozróżnia `stable_player_id` z meczu od realnego `player_id` z rosteru.
- `[ ]` Profil zawodnika nie agreguje jeszcze heatmapy sezonowej/łącznej.
- `[x]` UI ma route `/teams/:teamId/stats` z tracking-only dashboardem drużyny/sezonu.
- `[x]` API ma `GET /api/teams/{team_id}/stats`, które agreguje tylko jawnie przypisanych realnych zawodników.
- `[x]` Dashboard drużyny nie agreguje anonimowych slotów przeciwnika między meczami.
- `[x]` Dashboard pokazuje listę meczów wliczonych do agregacji oraz braki typu `missing_resolved_player_stats`.
- `[ ]` Nie ma jeszcze sezonowej/łącznej heatmapy drużyny.
- `[~]` Mecze mają statusy typu `draft/uploaded/calibrated/analyzed/reviewed/published`, ale nie pełny workflow `needs_review/approved`.

## Do not do yet

- Nie rób cloud multi-tenant.
- Nie rób logowania ani prywatnych kont graczy.
- Nie rób uprawnień typu "gracz widzi tylko siebie"; profil jest lokalnym/publicznym widokiem po `player_id`.
- Nie agreguj anonimowych slotów `A03/B05` jako realnych zawodników bez jawnego assignmentu.

## Suggested agent prompt

> Implementuj Milestone 8 z `docs/IMPLEMENTATION_PLAN.md`: lokalny roster, mapping stable slot/stint do realnego `player_id`, `resolved_player_stats.json` per mecz oraz publiczny/lokalny profil `/players/:playerId` z agregacją tracking-only statystyk przez mecze. Nie dodawaj logowania, uprawnień ani eventów piłkarskich zależnych od piłki.

---

# Next recommended implementation step — Season/team tracking-only dashboard

**Status:** `[x]` MVP implemented as local tracking-only team dashboard. Next polish can extend this section, but do not add ball tracking here.

## Dlaczego teraz

Mamy już stabilne sloty, roster, `player_identity_assignments.json`, `resolved_player_stats.json` oraz profil `/players/:playerId`. Największa wartość produktowa bez ryzyka ball trackingu to czytelny widok agregacji po wielu meczach: zawodnicy, drużyna, sezon i porównanie meczów.

## Scope

- `[x]` Dodać tracking-only dashboard sezonu/drużyny oparty wyłącznie o przypisanych realnych zawodników.
- `[x]` Nie agregować anonimowych slotów `A03/B05` między meczami; anonimowe sloty zostają tylko w raporcie pojedynczego meczu.
- `[x]` Użyć istniejących artefaktów `resolved_player_stats.json` i `player_identity_assignments.json` jako źródła danych.
- `[x]` Pokazać anonimowych przeciwników tylko jako kontekst per mecz, nie jako profile sezonowe.

## Backend/API

- `[x]` Dodać endpoint `GET /api/teams/{team_id}/stats` albo `GET /api/seasons/{season}/teams/{team_id}/stats`.
- `[x]` Zwracać summary: mecze, zawodnicy, total distance, playing time, avg/peak speed, sprint count, high-intensity distance, warnings, quality distribution.
- `[x]` Zwracać tabelę zawodników z agregacją po `player_id`.
- `[x]` Zwracać listę meczów użytych do agregacji i informację, które mecze nie mają `resolved_player_stats.json`.

## Frontend

- `[x]` Dodać link z `/teams` do raportu/statystyk drużyny.
- `[x]` Dodać widok drużyny/sezonu z tabelą zawodników i linkami do `/players/:playerId`.
- `[x]` Dodać prosty filtr sezonu i sortowanie tabeli po dystansie, czasie gry, sprintach i peak speed.

## Acceptance criteria

- `[x]` Użytkownik może wejść z listy drużyn w tracking-only raport drużyny.
- `[x]` Raport drużyny pokazuje tylko zawodników jawnie przypisanych w meczach.
- `[x]` Raport nie agreguje anonimowych slotów przeciwnika między meczami.
- `[x]` Każdy zawodnik w tabeli linkuje do profilu `/players/:playerId`.
- `[x]` Raport pokazuje listę meczów wliczonych do agregacji i brakujące dane.
- `[x]` Nie dodajemy jeszcze logowania, ball trackingu, podań, posiadania ani eventów.

## Suggested agent prompt

> Następny sensowny krok po dashboardzie: dopracuj raport/export/share polish w Milestone 7 albo przejdź do Milestone 9 ball tracking foundation. Nie dodawaj jeszcze podań, posiadania ani finalnych eventów bez wiarygodnego `ball_tracks.json`.

---

# Milestone 9 — Ball tracking foundation

**Status:** `[~]` baseline implemented with Ultralytics COCO `sports ball` plus local custom YOLO `.pt` support as an experimental diagnostic layer. It generates ball candidates/tracks/report/overlay and coverage metrics, but model quality still needs validation on real match clips before possession or event analytics.

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

- `[x]` Backend generuje `ball_tracks.json`.
- `[x]` Każdy rekord pozycji piłki ma:
  - `[x]` `time_sec`,
  - `[x]` `position_px`,
  - `[x]` `position_m`,
  - `[x]` `source: detected | interpolated | predicted | unknown`,
  - `[x]` `confidence`.
- `[x]` Interpolacja krótkich braków ma konfigurowalny limit, np. max 0.5s/1.0s.
- `[x]` Długie braki nie są wymyślane jako pewna pozycja.
- `[x]` UI pokazuje coverage piłki.
- `[~]` `ball_candidates.json` i `ball_overlay_preview.mp4` są artefaktami diagnostycznymi do oceny, czy wybrany detektor w ogóle łapie piłkę na naszych nagraniach.
- `[x]` Istnieje ball-only endpoint/preset `POST /api/matches/{match_id}/analyze-ball`, który nie przelicza stable ID ani statystyk zawodników.
- `[x]` Backend generuje `ball_quality_report.json` z coverage, confidence, unknown streaks i rekomendacją `custom_dataset_recommended`.
- `[x]` UI pozwala zmieniać parametry ball-only testu i korzystać z presetów `Fast`, `Balanced`, `Full sample`.
- `[x]` Root npm script `extract:frames` exports evenly sampled JPG frames and `metadata.json` to ignored `training_frames/` for Roboflow/custom dataset labeling.
- `[x]` Ball-only analysis can use a local custom YOLO `.pt` model from mounted `backend/models/`, with automatic `ball` class resolution instead of hardcoded COCO class 32.

## Do not do yet

- Nie implementuj jeszcze podań per zawodnik jako finalnej statystyki.
- Nie oznaczaj interpolowanych danych jako pewnych.

## Suggested agent prompt

> Implementuj Milestone 9 z `docs/IMPLEMENTATION_PLAN.md`: ball detector foundation, interpolacja krótkich braków, confidence i overlay piłki. Nie implementuj jeszcze pełnego pass/possession engine.

---

# Milestone 10 — Possession and simple event candidates

**Status:** `[~]` conservative possession/contact candidate layer is implemented. It generates candidate JSON artifacts and overlay from `ball_tracks.json` + trusted stable player positions, contact-candidate review is auto-classified with optional manual override, and `event_candidates.json` is derived from reviewed contacts. Pass candidates now exist in Milestone 11; full event correction and shot candidates are not implemented yet.

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

- `[x]` Backend generuje `possession_segments.json`.
- `[x]` Segmenty mają stable slot/team, `start_time_sec`, `end_time_sec`, `confidence`, `source/status`.
- `[x]` Jest osobna kategoria `unknown/free/contested`.
- `[x]` Backend generuje `contact_candidates.json` jako konserwatywne kandydaty kontaktu zawodnik-piłka.
- `[x]` Backend generuje `possession_candidates.json`, `possession_report.json` i `possession_overlay_preview.mp4`.
- `[x]` Possession layer używa krótkich, jawnie oznaczonych `short_gap_interpolated` pozycji zawodnika z niższym confidence; długie braki nadal zostają `unknown`.
- `[x]` API ma `GET /api/matches/{match_id}/contact-candidates` oraz `PUT /api/matches/{match_id}/contact-candidates/review`.
- `[x]` UI pokazuje overlay i summary kandydatów posiadania/kontaktu jako warstwę eksperymentalną.
- `[x]` UI pozwala oznaczyć `contact_candidates` jako `needs_review`, `accepted`, `uncertain` albo `rejected` i zapisać notatkę.
- `[x]` `contact_candidates` są domyślnie klasyfikowane automatycznie jako `accepted`, `uncertain` albo `rejected`; ręczne review jest tylko opcjonalnym override.
- `[x]` Backend generuje `event_candidates.json` jako formalny artefakt `ball_contact` na podstawie reviewed `contact_candidates`.
- `[x]` Backend generuje `event_review_report.json` z licznikami accepted/rejected/uncertain/needs_review.
- `[~]` Podstawowy review istnieje tylko dla kandydatów kontaktu; nie obejmuje jeszcze korekty geometrii/czasu eventu ani podań/strzałów.
- `[ ]` UI nie ma jeszcze pełnego panelu review eventów.
- `[ ]` Użytkownik nie może jeszcze oznaczyć finalnego eventu jako corrected.

## Do not do yet

- Nie licz jeszcze rankingów typu `most forward passes` jako finalnych, jeśli pass events nie są zatwierdzone.

## Suggested agent prompt

> Implementuj Milestone 10 z `docs/IMPLEMENTATION_PLAN.md`: possession candidates, free/contested ball, contact candidates i podstawowy event review. Wszystko z confidence, nie jako nieomylne dane.

---

# Milestone 11 — Passes, forward passes and progressive passes

**Status:** `[~]` experimental `pass_candidates.json` exists from consecutive reviewed `ball_contact` events, with start/end pitch positions, displacement, distance, attack direction, forward/lateral/backward classification, progressive-pass candidate flags and a basic pass review API/UI. It is not a final pass-stat engine yet: validated rankings and final player/team pass stats are not implemented.

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

- `[x]` Backend generuje `pass_candidates.json` jako eksperymentalną warstwę kandydatów z kolejnych reviewed `ball_contact` events.
- `[~]` Każdy kandydat podania ma:
  - `[x]` passer stable slot,
  - `[x]` receiver stable slot albo turnover/interception candidate przy zmianie drużyny,
  - `[x]` team/source/target team context,
  - `[x]` `start_time_sec`,
  - `[x]` `end_time_sec`,
  - `[x]` `start_position_m`,
  - `[x]` `end_position_m`,
  - `[x]` `displacement_m`,
  - `[x]` `distance_m`,
  - `[x]` `direction: forward | lateral | backward`,
  - `[x]` `is_progressive`,
  - `[~]` `outcome` jako `same_team_pass`, `turnover_or_interception` albo `unknown_team_pass`,
  - `[x]` `confidence`,
  - `[x]` `review_status`.
- `[x]` Backend rozdziela `auto_review_status` od manualnego `review_status`, żeby mocny kandydat nie był automatycznie finalną statystyką.
- `[x]` API ma `GET /api/matches/{match_id}/pass-candidates` oraz `PUT /api/matches/{match_id}/pass-candidates/review`.
- `[x]` UI ma panel `Pass candidates review` z decyzjami `needs_review`, `accepted`, `uncertain`, `rejected` i notatkami.
- `[x]` `final_stat_eligible` jest true tylko dla zaakceptowanych `same_team_pass`; rejected/uncertain/needs_review nie liczą się jako finalne podania.
- `[x]` Backend zapisuje `match_phase_config.json` z okresami gry, kierunkiem ataku Team A/B i opcjonalnym `second_half_start_time_sec`.
- `[x]` API ma `GET/PUT /api/matches/{match_id}/match-phase-config`; zapis odświeża `pass_candidates.json` bez ponownej analizy YOLO.
- `[x]` UI pozwala ustawić timestamp początku drugiej połowy i kierunek Team A w pierwszej połowie.
- `[x]` Kierunek podania uwzględnia stronę ataku drużyny i zmianę stron, jeśli występuje.
- `[ ]` Ranking `most forward passes` jest liczony tylko z zaakceptowanych albo high-confidence podań, zależnie od konfiguracji.
- `[~]` UI pokazuje summary, artefakty i pass review panel, ale nie ranking podań do przodu/progressive passes.

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

**Status:** `[~]` background analysis job API/UI polling, persisted job status files, quality smoke checker and `analysis_chunk_manifest.json` foundation are implemented. True per-chunk CV execution/merge, retry/resume controls, artifact cleanup and performance presets are still pending.

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

- `[x]` Analiza nie musi być synchroniczna w request/response; główny admin flow używa `POST /api/matches/{match_id}/analyze/background`.
- `[x]` UI pokazuje status joba, etap, progress procentowy i link do chunk manifestu, jeśli został wygenerowany.
- `[x]` Backend zapisuje trwałe pliki `analysis_jobs/<job_id>.json`, a API ma `GET /api/analysis-jobs/{job_id}` i `GET /api/matches/{match_id}/analysis-jobs`.
- `[x]` Backend potrafi zapisać `analysis_chunk_manifest.json` z podziałem filmu na planowane chunki, overlapem i statusem single-pass fallback.
- `[~]` Chunked mode jest fundamentem planowania; właściwe per-chunk YOLO execution, merge trackletów i globalny resolver po chunkach nie są jeszcze zaimplementowane.
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
- `[x]` przypisuje stable slot/stint do realnego `player_id` przez roster mapping; raw tracklet assignment zostaje debug/legacy,
- `[x]` system liczy czas gry dla stable slotów,
- `[x]` system generuje heatmapę per zawodnik,
- `[x]` system liczy dystans z wygładzeniem dla stable slotów,
- `[x]` system liczy sprinty/progi intensywności i pokazuje diagnostykę odrzuconych kandydatów sprintu,
- `[x]` użytkownik widzi raport/diagnostykę meczu z quality score i problem frames,
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
