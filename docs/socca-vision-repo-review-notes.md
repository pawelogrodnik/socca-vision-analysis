# Socca Vision Analysis — uwagi do aktualnego stanu repo

## TL;DR

Kierunek jest dobry. Repo wygląda już bardziej jak produktowy pipeline do lokalnej analizy meczu niż prosty eksperyment YOLO. Najważniejszy kierunek, który należy utrzymać:

```text
local admin / heavy analysis
→ stable slots
→ team/player review
→ resolved stats
→ match report
→ publish snapshot
→ lightweight viewer
```

Nie wracać do produktu opartego o raw `tracker_id`. Raw tracklety i legacy `identity_candidates` powinny zostać tylko debugiem.

---

## 1. Frontend routing jest teraz w dobrym stanie

Aktualny `App.tsx` jest czysty i działa jako router do osobnych obszarów produktu:

```tsx
<Route path='/' element={<Viewer />} />
<Route path='/admin-panel' element={<AdminPanel />} />
<Route path='/matches/:matchId/report' element={<MatchReportPage />} />
<Route path='/published/matches/:matchId/report' element={<PublishedMatchReportPage />} />
<Route path='/teams' element={<TeamsPage />} />
<Route path='/teams/add' element={<TeamEditPage mode='create' />} />
<Route path='/teams/:teamId/stats' element={<TeamStatsPage />} />
<Route path='/teams/:teamId' element={<TeamEditPage mode='edit' />} />
<Route path='/players/:playerId' element={<PlayerProfilePage />} />
```

To jest dobry kierunek: admin, viewer, raport meczu, raport publiczny, drużyny i profile zawodników są rozdzielone na osobne ekrany.

### Rekomendacja dla agenta

Nie przywracać `IdentityCandidateAdminSection` jako osobnego root-level komponentu. Jeśli potrzebny jest dostęp do legacy identity candidates, powinien być wewnątrz `AdminPanel`, najlepiej w `<details>` jako debug.

`main.tsx` powinien pozostać tylko entrypointem z `BrowserRouter` i `<App />`.

---

## 2. Admin workflow został dobrze przebudowany

`AdminPanel` ma już krokowy workflow:

```text
video
analysis
review
publish
```

i osobny `MatchWorkflowStepper`. To jest duży plus dla produktu, bo operator nie musi rozumieć całej technicznej kolejności JSON-ów i artefaktów.

Aktualny flow w UI:

```text
1. Dodaj lub wybierz video
2. Kalibracja boiska i analiza
3. Weryfikacja analizy i przypisanie zawodników
4. Raport i publikacja
```

To jest właściwy kierunek.

### Rekomendacja dla agenta

Nie wracać do starego długiego admin panelu z wszystkimi sekcjami jedna pod drugą. Utrzymać krokowy UX i dopracowywać checklistę statusów w stepperze.

---

## 3. Główny identity workflow powinien być: `stable_players → player_identity → resolved_player_stats`

Najważniejsza rzecz produktowa: główny workflow review jest teraz oparty o `StablePlayersPanel`, a legacy panele są schowane w debug sekcji:

```tsx
<StablePlayersPanel ... />

<details>
  <summary>Developer debug: legacy identity candidates i raw tracklety</summary>
  <IdentityCandidatePanel ... />
  <TrackletAssignmentPanel ... />
</details>
```

To jest bardzo dobry kierunek.

### Rekomendacja dla agenta

Traktować jako source of truth:

```text
stable_players.json
player_identity_assignments.json
resolved_player_stats.json
team_config.json
team_stats.json
```

Traktować jako legacy/debug:

```text
tracks.json
player_assignments.json
identity_candidates.json
identity_assignments.json
```

Nie budować nowych funkcji produktowych na `identity_candidates`. Ten etap był potrzebny przejściowo, ale aktualnie właściwy kierunek to stable slots + roster mapping.

---

## 4. Team registry i roster są dobrym krokiem produktowym

Frontend ma już routing do:

```text
/teams
/teams/add
/teams/:teamId
/teams/:teamId/stats
```

Backend ma endpointy do team registry i team stats:

```http
GET    /api/teams
POST   /api/teams
GET    /api/teams/{team_id}
PUT    /api/teams/{team_id}
DELETE /api/teams/{team_id}
GET    /api/teams/{team_id}/stats
```

To jest bardzo dobry kierunek, bo produkt docelowo powinien akumulować historię zawodników i drużyn, a nie tylko analizować pojedynczy film.

### Rekomendacja dla agenta

Przy dalszym rozwoju:

- nie duplikować rosteru w wielu miejscach;
- registry team/player powinno być bazą dla nowych meczów;
- match-level roster może być snapshotem składu z danego meczu;
- przypisanie stable slotów powinno wskazywać na roster/player ID, a później agregować się w profilach.

---

## 5. Background analysis i preflight to dobry kierunek

Admin używa teraz background jobów:

```ts
startAnalysisJob(...)
waitForAnalysisJob(...)
getAnalysisJob(...)
```

i pokazuje postęp analizy z `status`, `stage`, `progress_percent`, `chunk_count`. To jest bardzo ważne dla dłuższych filmów.

Jest też `AnalysisPreflightPanel`, który pokazuje:

- czas/długość video;
- full vs partial analysis;
- chunking;
- YOLO frames;
- modele;
- szacowany czas;
- warningi/blockery.

### Rekomendacja dla agenta

Ten kierunek rozwijać. Dla produkcyjnego lokalnego workflow operator powinien przed analizą widzieć:

- czy będzie analizowany cały film;
- ile będzie chunków;
- ile YOLO frames;
- czy GPU/CPU jest wykryty;
- szacowany czas;
- czy model piłki jest aktywny;
- ile miejsca mogą zająć artefakty.

---

## 6. Kalibracja boiska jest już poprawiona na `30 x 47.4 m`

Admin zapisuje pitch config z:

```ts
width_m: 30
length_m: 47.4
pitch_dimensions_m: { width_m: 30, length_m: 47.4 }
```

i UI pokazuje operatorowi, że boisko ma `30 x 47.4 m`.

Backend też normalizuje i zapisuje `pitch_dimensions_m`.

### Rekomendacja dla agenta

Nie zmieniać homografii na “powiększone” boisko. Prawdziwe boisko musi zostać `30 x 47.4` dla statystyk, heatmap i odległości.

---

## 7. Nadal trzeba poprawić detection ROI przy krawędziach boiska

Aktualnie YOLO działa na pełnej klatce, co jest dobre:

```python
"source": frame
```

ale filtr nadal odrzuca detekcję, jeśli footpoint nie jest w dokładnym `pitch_polygon`:

```python
if not point_in_polygon((foot[0], foot[1]), pitch_polygon):
    detections_rejected_outside_pitch += 1
    continue
```

To występuje zarówno w `collect_yolo_tracks_range`, jak i w pełnym `analyze_match_yolo`.

Raport mówi też wprost:

```json
{
  "pitch_mask_before_yolo": false,
  "pitch_filter": "footpoint_in_pitch_polygon"
}
```

### Rekomendacja dla agenta — ważne

Dodać osobny detection ROI:

```text
true_pitch_polygon        -> homografia i statystyki
expanded_detection_roi    -> akceptacja detekcji przy liniach
clamped_pitch_position    -> pozycja do statystyk
```

Proponowane parametry:

```python
pitch_filter_margin_px: int = 60
clamp_positions_to_pitch: bool = True
```

Implementacja:

```python
distance = cv2.pointPolygonTest(pitch_polygon, foot, measureDist=True)

if distance < -pitch_filter_margin_px:
    reject
else:
    accept

if mapped_x < 0 or mapped_x > width_m or mapped_y < 0 or mapped_y > length_m:
    clamp and increment positions_clamped_to_pitch
```

Raport powinien zawierać:

```json
{
  "detections_kept": 123,
  "detections_rejected_outside_pitch": 12,
  "detections_accepted_by_pitch_margin": 8,
  "positions_clamped_to_pitch": 6,
  "pitch_filter_margin_px": 60
}
```

To jest ważne dla bramkarzy i zawodników przy linii bocznej/bramkowej.

---

## 8. Ball tracking i possession są dobrze kierunkowo, ale powinny zostać eksperymentalne

Admin ma szybki test piłki z presetami:

```text
Fast 3s
Balanced 6s
Full sample
Custom PT
```

Backend próbuje w YOLO analysis zbudować ball tracking i possession artifacts, ale łapie wyjątki i dodaje warningi, czyli traktuje to jako warstwę eksperymentalną.

### Rekomendacja dla agenta

Na razie nie robić z piłki warunku koniecznego do publikacji meczu.

MVP publikowalny powinien działać bez piłki:

```text
players/stable slots/team stats/player stats -> OK
ball/possession/pass events -> optional/experimental
```

W UI oznaczać piłkę jako eksperymentalną, dopóki coverage nie jest stabilny.

---

## 9. Match package zawiera dużo rzeczy — dobrze, ale trzeba ustalić kontrakt

`build_match_package` pakuje bardzo dużo artefaktów:

```text
analysis_report
performance_report
player_identity_assignments
stable_players
global_identity_report
analysis_quality_report
team_config
team_stats
ball reports
possession reports
event/pass/contact candidates
...
```

To jest dobre jako snapshot MVP, ale zaczyna robić się “wszystko do jednego JSON-a”.

Publikacja działa przez:

```http
POST /api/matches/{match_id}/package
POST /api/matches/{match_id}/publish
POST /api/matches/{match_id}/publish-local
POST /api/admin/import-match
```

### Rekomendacja dla agenta

Wprowadzić jasny kontrakt package, np.:

```json
{
  "schema_version": "0.2.0",
  "match": {},
  "pitch_config": {},
  "required": {
    "analysis_report": {},
    "stable_players": {},
    "player_identity_assignments": {},
    "resolved_player_stats": {},
    "team_config": {},
    "team_stats": {}
  },
  "optional": {
    "ball_tracking_report": {},
    "possession_report": {},
    "pass_candidates": {},
    "contact_candidates": {}
  },
  "assets": {}
}
```

Nie musi być od razu refactor fizyczny, ale agent powinien przestać traktować wszystkie artefakty jako równorzędne.

---

## 10. `get_match` ładuje bardzo dużo opcjonalnych JSON-ów — uważać na payload

`GET /api/matches/{match_id}` dokleja do `match` bardzo dużo plików JSON, m.in. stats, events, ball, possession, pass/contact candidates, reports.

To jest wygodne lokalnie, ale z czasem może być ciężkie dla UI i produkcji.

### Rekomendacja dla agenta

Docelowo rozdzielić:

```http
GET /api/matches/{id}                 -> lightweight metadata
GET /api/matches/{id}/analysis-state  -> status/checklist
GET /api/matches/{id}/report          -> report bundle
GET /api/matches/{id}/debug           -> ciężkie debug artefakty
```

Na teraz nie trzeba tego robić natychmiast, ale nie doklejać bezrefleksyjnie kolejnych dużych dokumentów do `get_match`.

---

## 11. Statusy workflow są dobre, ale można doprecyzować review readiness

Aktualny stepper ocenia mniej więcej:

```ts
video done if selected
analysis done if analysis completed
publish done if published
review done if activeStep === publish
```

To jest dobry początek, ale “review done” powinno docelowo zależeć od danych, nie tylko od przejścia na krok publish.

### Rekomendacja dla agenta

Dodać funkcję:

```ts
function isReviewComplete(match: Match): boolean {
  return Boolean(
    match.stable_players &&
    match.team_config &&
    match.player_identity_assignments &&
    match.resolved_player_stats
  );
}
```

Potem stepper może pokazywać:

```text
Review: needs assignment / has conflicts / ready
Publish: locked until review ready
```

---

## 12. Brakuje testów regresji / CI

Frontend ma tylko:

```json
{
  "dev": "vite --host 0.0.0.0",
  "build": "tsc && vite build",
  "preview": "vite preview --host 0.0.0.0"
}
```

Brakuje osobnych komend `typecheck`, `lint`, `test`.

### Rekomendacja dla agenta

Dodać minimalnie:

```json
{
  "scripts": {
    "typecheck": "tsc --noEmit",
    "build": "tsc && vite build"
  }
}
```

Backend: dodać prosty test smoke, nawet bez pełnego YOLO:

```text
- create match dir fixture
- write pitch_config
- write minimal tracks/stable_players/player_stats
- build_match_package()
- assert package contains required keys
```

Docelowo dodać mały regression test na sample video, ale to może być osobny cięższy job/manual.

---

## 13. Priorytety implementacyjne dla agenta

### P0 — porządek i bezpieczeństwo workflow

- Utrzymać `StablePlayersPanel` jako główny review UI.
- Zostawić `IdentityCandidatePanel` i `TrackletAssignmentPanel` tylko w debug.
- Nie montować paneli poza routerem/App.
- Dodać `isReviewComplete()` i blokadę/ostrzeżenie przed publikacją bez review.

### P1 — poprawa detekcji przy krawędziach

- Dodać `pitch_filter_margin_px`.
- Akceptować detekcje w rozszerzonym ROI.
- Homografia nadal z prawdziwego boiska `30 x 47.4`.
- Clampować mapped coordinates do boiska.
- Dodać metryki do reportu.

### P1 — package contract

- Rozdzielić required vs optional artefacts.
- Dodać `schema_version` package np. `0.2.0`.
- Dodać walidację przed publish:
  - jest `analysis_report.status === completed`;
  - jest `stable_players`;
  - jest `team_config`;
  - jest `player_identity_assignments`;
  - jest `resolved_player_stats`.

### P2 — UX review

- Pokazać checklistę:
  - video selected;
  - pitch calibrated;
  - analysis completed;
  - stable overlay exists;
  - stable slots reviewed;
  - team config reviewed;
  - real players assigned;
  - package ready;
  - published.
- Dodać licznik konfliktów identity/team mismatch do głównego panelu.

### P2 — testy i typy

- Dodać `npm run typecheck`.
- Dodać backend smoke tests dla `build_match_package`.
- Dodać sample regression command, nawet manualną.

---

## Finalna ocena

Kierunek produktu: bardzo dobry.

Najważniejszy pozytywny sygnał: aplikacja przestaje być “detektorem ludzi na video”, a staje się workflow:

```text
zarejestruj drużyny
→ dodaj mecz
→ skalibruj boisko
→ uruchom analizę
→ sprawdź stable overlay
→ przypisz realnych zawodników
→ wygeneruj raport
→ opublikuj snapshot
→ pokaż profile i statystyki
```

Najważniejszy techniczny dług: nadal trzeba poprawić filtering detekcji przy krawędziach boiska i zamrozić jeden oficjalny kontrakt danych dla publikacji.

Nie dodawałbym teraz kolejnych dużych feature’ów. Najpierw stabilizacja pipeline’u, walidacja przed publikacją i regression tests.
