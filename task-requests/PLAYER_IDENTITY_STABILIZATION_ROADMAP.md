# Player Identity Stabilization Roadmap

## 0. Cel dokumentu

Ten dokument definiuje ścieżkę od candidate/reviewed player identity do bezpiecznego controlled production apply.

Nie definiuje już kolejności najbliższego product MVP. Ta kolejność znajduje się w:

```text
task-requests/PLAYER_IDENTITY_DEVELOPMENT_PLAN.md
```

Najważniejsze rozdzielenie:

```text
reviewed local output
→ może powstać przed pełnym production gate

production identity apply
→ pozostaje zablokowane do końcowej rewalidacji i transakcyjnego apply
```

Reviewed video, minimapa i reviewed candidate stats nie są automatycznym production promotion.

---

# 1. Najważniejsza zasada

> Brak przypisania jest bezpieczniejszy niż błędne przypisanie.

System może pozostawić fragment jako:

```text
unresolved
conflicted
blocked
```

Nie może zwiększać coverage przez ukryty false merge lub agresywną interpolację tożsamości.

---

# 2. Aktualny stan na HEAD c4559ff4

## 2.1. Zaimplementowany fundament

```text
P1.20A promotion safety audit
P1.20B structural conflict remediation
P1.21 partial candidate apply
candidate assignments/timeline/stats/heatmaps
candidate-vs-production diff
operator telemetry
Initial Identity Audit i operator seeds
seed-aware review reduction
appearance/ReID advisory infrastructure
Match Identity Resolver shadow contract
```

## 2.2. Co nie jest produkcyjnie udowodnione

```text
pełnomaczowe KPI na wystarczającej liczbie meczów
0 known false assignments po finalnym review
wymagane coverage dla wszystkich graczy
stabilna obsługa zmian zawodników
pełna integracja finalnych decyzji review
transactional production apply
rollback
```

## 2.3. Historyczne benchmarki

Poprzednie benchmarki wykazały, że:

- whole-subject review może być kosztowne;
- candidate pipeline może generować false assignments;
- coverage może być znacząco niższe od docelowego;
- production promotion nie jest bezpieczne bez finalnego review i rewalidacji.

Historyczne szczegóły pozostają w Git history oraz benchmark artifacts.

---

# 3. Warstwy danych

System rozróżnia cztery poziomy:

```text
production
candidate
reviewed
shadow/research
```

## 3.1. Production

Aktualnie używane opublikowane identity i statystyki.

Nie mogą zostać zmienione bez controlled apply.

## 3.2. Candidate

Automatyczne i częściowo ręcznie zatwierdzone artefakty przeznaczone do review/benchmarku.

Przykłady:

```text
player_identity_assignments_candidate_v2.json
resolved_player_timeline_candidate_v2.json
resolved_player_stats_candidate_v2.json
player_heatmaps_candidate_v2.json
```

## 3.3. Reviewed

Finalny lokalny snapshot po zakończonym review:

```text
reviewed_identity_snapshot.json
reviewed_player_timeline.json
reviewed_player_stats.json
reviewed_player_heatmaps.json
reviewed_identity_video.mp4
```

Reviewed artifacts mogą być używane do lokalnej analizy i wizualnej walidacji przed production apply.

## 3.4. Shadow/research

```text
ReID rankings
identity resolver proposals
jersey evidence
training reports
research benchmarks
```

Nie są bezpośrednim źródłem confirmed production identity.

---

# 4. Hard safety gates

Hard safety gates blokują confirmed promotion lub production apply:

```text
stale lineage
cross-team player assignment
same source observation assigned to multiple players
parallel distant observations assigned to the same player
structural-conflict subject promoted without remediation
trusted operator decision contradiction
sustained active-player overflow
trusted multiple-goalkeeper conflict
invalid roster player
unexplained final snapshot conflict
```

Hard constraint ma pierwszeństwo przed:

```text
ReID score
model confidence
coverage gain
stats completeness
```

---

# 5. Coverage semantics

Nie liczyć player coverage względem pełnego wideo, jeśli nie znamy on-pitch interval.

Raportować osobno:

## 5.1. Team assignment coverage

```text
reliable observations z pewnym teamem
/
wszystkie reliable player observations
```

## 5.2. Confirmed identity coverage

```text
confirmed named observations/time
/
review-scope observations/time
```

## 5.3. Unresolved coverage

```text
unresolved observations/time
/
review-scope observations/time
```

## 5.4. Player confirmed-interval coverage

Tylko przy znanym on-pitch interval:

```text
confirmed player observations
/
frames/time w potwierdzonym interval
```

Przy nieznanym denominator:

```json
{
  "coverage_ratio": null,
  "coverage_denominator": "unknown",
  "reason": "on_pitch_interval_not_confirmed"
}
```

## 5.5. Feature coverage

Oddzielnie dla:

```text
heatmap
observed distance
possession attribution
passes attribution
events attribution
```

---

# 6. Reviewed identity snapshot — nowy etap przed rewalidacją

## Status

```text
NEXT PRODUCT MILESTONE
```

## Cel

Połączyć wszystkie końcowe decyzje operatora i bezpieczne candidate outcomes w jeden deterministyczny snapshot.

## Źródła

```text
Initial Identity Audit decisions
whole-subject review decisions
remediation decisions
team constraints
safe confirmed resolver outcomes
explicit unresolved/conflicted states
```

## Wymagania

- operator decisions są kanoniczne;
- ReID/continuity są tylko suggestion evidence;
- cross-team i parallel conflicts blokują confirmed;
- unresolved ma stabilny fallback Axx/Bxx;
- snapshot ma pełne source digests;
- output jest deterministic i atomic;
- zmiana inputu oznacza snapshot jako stale;
- production files pozostają bez zmian.

## Output

```text
reviewed_identity_snapshot.json
reviewed_identity_report.json
```

Ten snapshot staje się jedynym wejściem do reviewed wideo i lokalnych reviewed statystyk.

---

# 7. Reviewed output validation

Reviewed wideo jest nowym głównym narzędziem human QA.

Operator ma sprawdzić:

```text
confirmed names
ID switch boundaries
false merges
false splits
tracklet/subject propagation
conflicted/unresolved labels
bbox-person correspondence
```

Korekta z wideo:

```text
operator decision update
→ snapshot stale
→ downstream snapshot rebuild
→ video/stats rerender
```

Bez YOLO/tracking rerun.

Reviewed output validation nie zastępuje pełnego production benchmarku, ale dostarcza mocniejsze ground truth dla kolejnego etapu.

---

# 8. Revalidation roadmap

## S1 — Reviewed snapshot correctness

### Gate

```text
0 cross-team confirmed assignments
0 parallel same-player confirmed conflicts
0 invalid roster assignments
all conflicts visible
all inputs digest-bound
production hashes unchanged
```

## S2 — Reviewed video audit

### Gate

Na bounded real material:

```text
all displayed names visually checked
known wrong names = 0 after correction
fallback labels stable
every correction traceable to source decision
rerender succeeds without heavy pipeline rerun
```

## S3 — Reviewed stats validation

Dla każdego zawodnika sprawdzić:

```text
first/last confirmed observation
playing intervals
long gaps
large spatial jumps
parallel observations
heatmap shape
observed distance
possession/pass attribution, jeśli dostępne
feature coverage/readiness
```

Każda duża delta ma prowadzić do źródłowych trackletów/subjects/decisions.

## S4 — Operator benchmark

Mierzyć na realnych materiałach:

```text
active operator time
manual decisions
confirmed coverage
unresolved coverage
video-driven corrections
known false assignments after final correction
ID switches
false merges/splits
stats coverage
```

Docelowo minimum:

```text
więcej niż jeden fizyczny mecz
co najmniej jeden held-out materiał
różne warunki światła/kamery
```

Pierwsze lokalne MVP może działać przed zamknięciem S4.

## S5 — Production readiness decision

Dopiero po S1–S4 odpowiedzieć:

```text
READY_FOR_CONTROLLED_APPLY
NOT_READY_FALSE_ASSIGNMENTS
NOT_READY_COVERAGE
NOT_READY_OPERATOR_COST
NOT_READY_DOWNSTREAM_STATS
```

---

# 9. Candidate and reviewed stats rules

## 9.1. Detected

Confirmed detected observations mogą zasilać:

```text
playing time
heatmap
observed distance
player possession/events
```

zgodnie z feature readiness.

## 9.2. Predicted/occluded

Mogą wspierać continuity, ale:

```text
nie są observed distance
nie są raw heatmap samples
nie są automatycznie confirmed player evidence
```

## 9.3. Unresolved/conflicted

```text
nie zasilają named player stats
mogą zasilać team-level stats przy pewnym teamie
pozostają jawne w coverage
```

## 9.4. Optional inputs

Brak ball/event artifacts:

```text
possession/passes = not_available
```

Nie obniża identity/heatmap readiness.

---

# 10. ReID i resolver w stabilization flow

ReID może pomagać w:

```text
prioritization
ranking
review suggestion
cross-capture advisory
```

Nie może:

```text
ominąć operator review po failed gate
potwierdzić imienia na finalnym wideo samym top-1
wykonać irreversible merge
obniżyć hard constraints
```

Match Identity Resolver:

- wykrywa conflicts;
- buduje explainable edge scores;
- proponuje assignment;
- może abstain;
- nie jest osobnym production apply path.

Praktyczna wartość ReID jest mierzona przez:

```text
manual decisions saved
unresolved reduction
ID-switch reduction
false merge/split delta
```

---

# 11. Controlled production apply

## Status

```text
BLOCKED UNTIL REVIEWED REVALIDATION PASSES
```

## 11.1. UX

Operator musi zobaczyć:

```text
reviewed snapshot digest
review completeness
unresolved coverage
blocking warnings
reviewed-vs-production diff
stats readiness
files to replace
```

Apply wymaga jawnego potwierdzenia.

## 11.2. Transaction

Przed zapisem:

```text
backup production identity
backup timeline/stats/heatmaps
write transaction manifest
mark packages stale
```

## 11.3. Rebuild

Po apply przebudować:

```text
player identity assignments
player timeline
player stats
player heatmaps
player events/passes, jeśli zależne
analysis readiness
package/publication freshness
```

## 11.4. Validation

```text
0 hard conflicts
0 stale downstream artifacts
all output hashes recorded
public package remains blocked until rebuild complete
```

## 11.5. Rollback

Rollback przywraca backups, przebudowuje downstream i zachowuje operator audit history.

Nie wdrażać auto-apply.

---

# 12. Advanced review

Pełny event/timeline editor nie jest najbliższym milestone.

Rozbudowywać tylko przypadki potwierdzone przez reviewed video i benchmark:

```text
long unresolved fragments
identity switch boundaries
substitution boundaries
structural conflicts
orphan fragments affecting stats/events
```

Pierwszy correction flow powinien prowadzić z timestampu wideo do istniejącego review, bez budowy rozbudowanego edytora.

---

# 13. KPI

## Główne

```text
active operator time per upload/match
manual decisions
confirmed identity coverage
unresolved time coverage
known false names after final review
ID switches
false merges
false splits
video-driven corrections
stats coverage/readiness
```

## Diagnostyczne

```text
raw tracklets
candidate subjects
subjects assigned
structural conflicts
safe/unsafe duplicates
ReID suggestion precision
ReID suggestions accepted/rejected
coverage denominator unknown
rerender duration
```

Nie uznawać za sukces spadku liczby subjectów wynikającego z false merge.

---

# 14. Aktualna kolejność stabilization

```text
1. Build reviewed identity snapshot
2. Audit reviewed video on real material
3. Generate reviewed stats/readiness
4. Fix errors through cheap correction/rerender
5. Run operator benchmark on additional material
6. Revalidate identity and stats
7. Decide controlled production readiness
8. Implement transactional apply and rollback
```

ReID i jersey research nie blokują kroków 1–4.

---

# 15. Acceptance criteria

## Reviewed snapshot

- [ ] one canonical reviewed identity source;
- [ ] deterministic and digest-bound;
- [ ] operator decisions canonical;
- [ ] unresolved/conflicted explicit;
- [ ] production unchanged.

## Reviewed output

- [ ] confirmed names visually verified;
- [ ] fallback Axx/Bxx stable;
- [ ] correction traceable;
- [ ] rerender without YOLO/tracking;
- [ ] stats coverage visible.

## Revalidation

- [ ] 0 known false assignments after final review;
- [ ] 0 cross-team confirmed assignments;
- [ ] 0 impossible parallel confirmed players;
- [ ] large stat deltas explained;
- [ ] at least one held-out real material audited;
- [ ] operator cost measured.

## Controlled apply

- [ ] explicit confirmation;
- [ ] backups;
- [ ] transaction manifest;
- [ ] atomic writes;
- [ ] downstream rebuild;
- [ ] post-apply validation;
- [ ] rollback tested.

---

# 16. Anti-goals

Nie:

```text
nadpisywać production przed candidate/reviewed validation
ukrywać conflicts przez wybór wyższego confidence
wymuszać assignment unresolved fragmentu
liczyć predicted jako observed distance
publikować candidate/reviewed stats automatycznie
obniżać hard constraints dla coverage
blokować reviewed MVP pełnym three-match gate
budować persistent gallery przed stabilnym single-match flow
budować kolejne research layers bez wpływu na operator cost lub correctness
```

---

# 17. Raport agenta po milestone

Każdy raport zawiera:

```text
input/output commit
changed files
real artifact/demo
source digests
hard conflicts
production hashes
confirmed/unresolved coverage
operator actions/time
known false assignments
ID switches/false merges/splits
stats readiness
commands/tests
known limitations
next stop/go decision
```

Zielone testy bez realnego reviewed artifactu nie zamykają product milestone.
