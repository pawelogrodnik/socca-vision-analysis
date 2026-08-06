# Video-driven identity correction MVP — closeout

## Zakres

PR #9 dostarcza korektę całego `candidate_subject_id` bez uruchamiania detekcji,
trackingu, ReID ani jersey recognition. Operator zatrzymuje istniejący reviewed
video, wyszukuje realne wykryte obserwacje bieżącej klatki, wybiera fragment i
zapisuje jedną z decyzji:

- roster player;
- istniejący canonical lub reviewed-only stable slot;
- nowy bounded stable player A01–A14/B01–B14;
- referee;
- false detection;
- team unknown;
- unresolved.

Exact-observation editing nie jest częścią tego PR. Istniejące exact seeds są
nadal respektowane jako osobne, wcześniejsze źródło evidence.

## API i istniejące stores

Rozszerzony read model:

```text
GET /api/matches/{match_id}/reviewed-identity/at?time_sec=...
```

Zwraca tylko realne `detected` observation z dokładnej klatki, z pełnym
tracklet/subject/identity/safety context. Stary snapshot może służyć jako jawnie
oznaczony materiał referencyjny po pierwszej korekcie.

Nowa mała fasada:

```text
GET  /api/matches/{match_id}/reviewed-identity/corrections/context
POST /api/matches/{match_id}/reviewed-identity/corrections
```

Fasada nie tworzy nowego store. Używa:

- `identity_roster_subject_review_decisions_shadow.json` dla whole-subject roster assignment;
- `reviewed_identity_slot_assignments.json` dla stable slotów i akcji specjalnych;
- istniejącej finalizacji reviewed identity;
- istniejącego reviewed output job i jego stale-digest protection.

`GET reviewed-identity/slot-review` udostępnia teraz pełny backendowy registry
canonical i reviewed-only manual slots. Frontend nie zgaduje numerów.

Walidacja odrzuca unknown/ambiguous/mixed-team subject, cross-team roster,
unknown lub cross-team slot, wyczerpany bounded pool, ósmego równoczesnego
zawodnika i nieobsługiwaną akcję przed zapisem decyzji. Komentarz jest
nie-semantyczny i nie zmienia identity digestu.

## UI flow

```text
reviewed video
→ Sprawdź przypisania w tym momencie
→ lista widocznych detected entities
→ Popraw przypisanie
→ mały formularz whole-subject
→ kilka kolejnych korekt na starym filmie referencyjnym
→ Finalize and regenerate
→ świeże video i stats
```

Roster i sloty są filtrowane po teamie. Błąd walidacji nie zamyka formularza.
Zapis nie resetuje `currentTime`, nie uruchamia automatycznego renderu i ukrywa
stare statystyki. Banner jednoznacznie opisuje stare wideo jako referencyjne.
Finalizacja hard-blocked nie uruchamia renderu. Nowy MP4 jest kluczowany nowym
digestem.

Tabela reviewed stats pokazuje confirmed detected observations, fragments,
detected time, observed distance, heatmap samples i readiness.

## Invariants

- model pozostaje `tracklet -> candidate subject -> optional stable slot -> optional canonical player`;
- stable pool pozostaje A01–A14/B01–B14;
- unresolved jest poprawnym wynikiem;
- false detection nie jest renderowane ani liczone;
- referee jest widoczny jako `Sędzia`, ale nie trafia na minimapę ani do stats;
- team unknown używa `U?` i nie tworzy U01/U02;
- unresolved może zachować bezpieczny istniejący stable slot jako fallback;
- duplicate stable/canonical safety działa po exact override;
- raw detections, `tracklets.json`, global/stable production identity i published data nie są modyfikowane;
- downstream rebuild używa wyłącznie frozen artifacts.

## Real-match frozen smoke

Pełny raport maszynowy: `task-requests/VIDEO_DRIVEN_IDENTITY_CORRECTION_SMOKE.json`.

Smoke pracował na hardlinkowanym, izolowanym klonie meczu `461e4dd9` (około
89,8 s). Nie zapisał decyzji do realnego match directory. Dwie decyzje były
oparte wyłącznie na wcześniejszych exact operator observations:

1. długi unanchored fragment Paweł został przypisany do istniejącego A03;
2. whole subject Krzysiek został przypisany do roster player Krzysiek.

Nie zapisano false detection/referee, ponieważ nie było wiarygodnego
whole-subject evidence. Nie utworzono sztucznej decyzji tylko dla testu.

| Metryka | Przed | Po |
| --- | ---: | ---: |
| stable slots | 19 | 19 |
| highest A/B | A10/B12 | A10/B12 |
| unanchored fragments | 46 | 45 |
| conflicted detected observations | 5693 | 4149 |
| confirmed detected coverage | 0,13% | 3,25% |
| confirmed detected observations | 50 | 1222 |
| Krzysiek confirmed observations | 7 | 1179 |
| Krzysiek confirmed fragments | 7 | 5 |
| Krzysiek detected time | 0,234 s | 39,339 s |
| Krzysiek observed distance | 0 m | 53,49 m |
| Krzysiek heatmap samples | 7 | 1179 |

Render QA:

- duplicate stable labels: 0;
- duplicate canonical players: 0;
- automatic permanent allocations: 0;
- ball frames: 1338;
- minimap frames: 2692;
- codec/pixel format: H.264/yuv420p;
- production mutations: 0;
- published mutations: 0.

## Operator workload

- corrections: 2;
- correction types: `assign_existing_slot`, `assign_roster_player`;
- candidate subjects corrected: 2;
- detected observations resolved: +1172 confirmed observations, a drugi fragment
  zmniejszył unanchored count o 1;
- measured correction-service workflow: 3,368 s na izolowanym klonie;
- pełny finalize/stats/video smoke: 42,169 s;
- rzeczywisty aktywny czas człowieka nie był mierzony w automatycznym smoke i nie
  jest przedstawiany jako telemetryka operatora.

## Walidacja

- backend: 870 tests, 5 skipped;
- frontend preflight: 12 tests;
- frontend strict typecheck: pass;
- frontend production build: pass;
- real browser check: timestamp entities, Team A roster/slot filtering, inline
  form, validation error pozostający w formularzu, brak zapisu realnej decyzji;
- full frozen render: pass.

## Ograniczenia

- correction scope to whole candidate subject; brak edycji pojedynczej obserwacji;
- po przeładowaniu strony stare MP4 nie musi być dostępne jako reference video;
- brak automatycznego seek do poprzedniego czasu po załadowaniu nowego MP4;
- brak rozbudowanej trwałej telemetryki aktywnego czasu operatora;
- poprawność whole-subject roster assignment nadal zależy od strukturalnej jakości candidate subjectu;
- nie wdrożono production apply ani cross-match identity.

## Rekomendowany następny PR

Exception-only reviewed queue: automatycznie priorytetyzować fragmenty o
największym wpływie na coverage/stats, konflikty slot/canonical i możliwe local
ID switches, wykorzystując nową fasadę korekt bez tworzenia kolejnego store.
