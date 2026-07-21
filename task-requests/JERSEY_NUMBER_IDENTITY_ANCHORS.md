# Jersey Number Identity Anchors

## Status

```text
SUPPLEMENT TO task-requests/PLAYER_IDENTITY_STABILIZATION_ROADMAP.md
SHADOW-FIRST / HIGH-CONFIDENCE IDENTITY EVIDENCE
```

Ten dokument dodaje do roadmapy możliwość używania wcześniej zdefiniowanych numerów na koszulkach jako silnego sygnału identyfikacji zawodnika.

Założenia domenowe:

- numer jest unikalny w obrębie drużyny;
- nie każdy zawodnik ma numer;
- zawodnik z numerem używa tego samego numeru w całym meczu;
- przykładowo `Team A + 92 -> Paweł`, `Team A + 15 -> Piotrek`;
- część zawodników gra w białych koszulkach bez numeru.

Numer koszulki powinien być traktowany jako mocniejszy sygnał niż ogólny appearance/ReID, ale nie może omijać hard constraints identity.

---

# 1. Semantyka numeru

System musi rozróżniać co najmniej cztery stany:

```text
number_confirmed
number_absent
number_unreadable
number_conflict
```

## `number_confirmed`

Kilka niezależnych, zgodnych i wiarygodnych obserwacji wskazuje konkretny numer istniejący w rosterze tej samej drużyny.

## `number_absent`

Na wystarczająco dobrych cropach widać, że koszulka faktycznie nie ma numeru.

`number_absent` nie identyfikuje konkretnego zawodnika, jeśli więcej niż jeden zawodnik gra bez numeru.

## `number_unreadable`

Crop, pozycja ciała, rozmycie, zasłonięcie albo rozdzielczość nie pozwalają określić numeru.

Brak odczytu OCR nie może automatycznie oznaczać `number_absent`.

## `number_conflict`

Ten sam subject/tracklet zawiera kilka wiarygodnych, sprzecznych numerów albo odczyt jest sprzeczny z przypisanym roster playerem.

`number_conflict` jest structural identity warningiem i może blokować whole-subject promotion.

---

# 2. Roster contract

Roster player powinien opcjonalnie posiadać:

```json
{
  "player_id": "...",
  "name": "...",
  "team_label": "A",
  "jersey_number": 92,
  "jersey_number_source": "operator|match_config",
  "jersey_number_trusted": true
}
```

Dla zawodnika bez numeru:

```json
{
  "jersey_number": null,
  "jersey_number_source": "operator",
  "jersey_number_trusted": true
}
```

Wymagania:

- numer może być `null`;
- niepusty numer musi być unikalny w obrębie jednej drużyny;
- ten sam numer w Team A i Team B jest dozwolony;
- duplikat numeru w tej samej drużynie blokuje użycie numeru jako automatycznego identity anchor;
- automatycznie wykryty numer nie może sam zmieniać roster configuration.

---

# 3. Pipeline detekcji

Rekomendowany flow:

```text
reliable player observations
→ representative crop selection
→ front/back torso ROI
→ jersey-number detector / digit classifier / constrained OCR
→ per-frame number evidence
→ tracklet consensus
→ candidate-subject consensus
→ roster lookup inside known team
→ shadow identity anchor
```

Nie uruchamiać zwykłego OCR na całym obrazie lub całym player cropie bez wydzielenia obszaru koszulki.

System powinien obsługiwać:

- numery jednocyfrowe i dwucyfrowe;
- częściowy odczyt jednej cyfry;
- przedni i tylny nadruk;
- różne skale cropa;
- lustrzane lub skośne ułożenie sylwetki;
- przypadki, w których jedna cyfra jest widoczna, a druga zasłonięta.

Każdy odczyt powinien zawierać:

```json
{
  "frame": 0,
  "tracklet_id": "...",
  "candidate_subject_id": "...",
  "team_label": "A",
  "number": 92,
  "confidence": 0.0,
  "digits": [9, 2],
  "roi_quality": 0.0,
  "view": "front|back|side|unknown",
  "artifact": "...",
  "status": "readable|partial|unreadable|absent_candidate"
}
```

---

# 4. Consensus i confidence

Pojedynczy crop nie powinien wystarczać do automatycznego przypisania zawodnika.

Minimalny bezpieczny consensus powinien być konfigurowalny, ale startowo wymagać:

```text
known team
+ unique trusted roster number
+ minimum 2-3 zgodne high-quality reads
+ reads z różnych klatek
+ preferowane reads oddalone czasowo lub z różnych trackletów
+ brak wiarygodnego sprzecznego numeru
+ brak structural identity conflict
```

Raportować:

```text
supporting_reads
conflicting_reads
unique_supporting_tracklets
first_support_frame
last_support_frame
consensus_number
consensus_confidence
```

Consensus nie może być zwykłym majority vote bez uwzględnienia jakości cropa i confidence.

Przykład:

```text
92: 3 dobre odczyty
15: 1 słaby, częściowy odczyt
→ 92 może pozostać consensus
→ słaby konflikt jest zapisany, nie jest ignorowany
```

Dwa mocne, sprzeczne numery powinny dawać `number_conflict`, a nie automatyczne rozstrzygnięcie.

---

# 5. Użycie jako identity anchor

## 5.1. Shadow suggestion

Pierwszy etap ma być read-only:

```text
number consensus
→ roster player suggestion
→ whole-subject review card
```

UI powinno pokazywać:

```text
Detected number: 92
Roster match: Paweł
Supporting frames: ...
Confidence: ...
Conflicts: ...
```

## 5.2. Controlled automatic assignment

Automatyczne przypisanie candidate subjectu do roster playera może być rozważone dopiero po benchmarku i tylko gdy:

```text
team known and trusted
roster number unique and trusted
multi-frame consensus passed
all supporting crops valid
no conflicting trusted number
no cross-team evidence
no temporal overlap with same player
no parallel distant observation
no structural-conflict flag
fresh lineage digests
```

Numer nie może omijać:

- team constraints;
- temporal overlap constraints;
- parallel-player constraints;
- structural conflict gate;
- stale input detection;
- candidate-before-production workflow.

## 5.3. Propagacja na wcześniejsze i późniejsze tracklety

Potwierdzony numer może zakotwiczyć subject, ale identity może być propagowane tylko przez:

```text
operator-confirmed subject membership
strict accepted identity edge
safe tracklet lineage
same team
no overlap conflict
no contradictory number evidence
```

Nie propagować numeru przez:

```text
uncertain_transition
cross_production_transition
weak ReID-only edge
structural conflict
parallel distant observation
```

Przykład:

```text
tracklet 8: number_unreadable
tracklet 21: confirmed 92
tracklet 35: number_unreadable

8 -> 21 -> 35 przechodzi wszystkie strict identity gates
→ cały bezpieczny subject może otrzymać roster suggestion Paweł
```

Numer nie może automatycznie scalić niezależnych trackletów tylko dlatego, że oba mają podobny odczyt OCR.

---

# 6. Zawodnicy bez numeru

Koszulka bez numeru jest ważną informacją, ale słabszą niż wykryty numer.

Zasady:

```text
confirmed number
→ strong positive identity anchor

conflicting number
→ strong conflict evidence

confirmed no-number
→ weak negative filter against numbered players

unreadable
→ no identity evidence
```

Jeżeli kilku zawodników nie ma numeru, `number_absent` nie może wskazać konkretnej osoby.

Automatyczne przypisanie zawodnika bez numeru nie może opierać się wyłącznie na braku odczytu.

---

# 7. Konflikty i safety

Następujące przypadki wymagają review albo blokady:

```text
subject contains trusted reads of 92 and 15
subject assigned to player 92 but trusted number 15 is detected
same player number is visible simultaneously in distant positions
number suggests Team A player but team evidence is Team B
roster contains duplicate trusted number in the same team
number evidence originates only from overlapping multi-person crop
```

Number detector powinien korzystać wyłącznie z cropów spełniających reliability gates, między innymi:

- `detected`;
- reliable bbox/torso region;
- brak dużego same-frame overlapu;
- brak multi-person contamination;
- wystarczający rozmiar i ostrość;
- znana lub wiarygodna drużyna.

---

# 8. Artefakty

Proponowane artefakty:

```text
identity_jersey_number_evidence_shadow.json
identity_jersey_number_consensus_shadow.json
identity_jersey_number_audit.json
identity_jersey_number_report.json
```

Opcjonalna galeria:

```text
jersey_number_audit/index.html
jersey_number_crops/
```

Artefakty muszą przechowywać source digests dla:

```text
video/crop source
candidate identity
tracklet timeline
roster
team config
number model/version
parameters
```

---

# 9. Benchmark i metryki

Najpierw zbudować ręcznie sprawdzony goldset cropów i subjectów.

Mierzyć osobno:

```text
per-crop readable-number precision
per-crop digit accuracy
subject consensus precision
subject consensus coverage
number-to-roster precision
number conflict detection recall
additional subjects correctly suggested
additional subjects safely auto-resolved
manual review reduction
false identity assignments caused by number evidence
```

Najważniejszy gate:

```text
0 false automatic roster assignments caused by number evidence
```

Wysoka precision jest ważniejsza niż coverage.

Nie uznawać sukcesu na podstawie samego OCR accuracy, jeśli błędny numer może przypisać tracklet do złego realnego zawodnika.

---

# 10. Zalecana kolejność wdrożenia

Ten moduł jest uzupełnieniem głównej roadmapy, a nie zamiennikiem safety/candidate pipeline.

Rekomendowana sekwencja:

```text
N0  roster jersey-number metadata and uniqueness validation
N1  shadow crop evidence + visual audit
N2  tracklet/subject consensus + goldset evaluation
N3  whole-subject review suggestions
N4  controlled candidate auto-assignment under strict gates
N5  strict propagation across confirmed subject lineage
```

Moduł może być rozwijany równolegle jako shadow experiment, ale nie powinien:

- nadpisywać produkcyjnych assignments;
- blokować bieżących identity safety milestones;
- opóźniać candidate/video A/B checkpointu;
- zastępować P1.20A/P1.20B safety gates;
- zostać włączony produkcyjnie bez benchmarku.

Przed dalszym strojeniem ogólnego pairwise ReID należy sprawdzić, czy jersey-number anchors dają większą precision i redukcję review.

---

# 11. Acceptance Criteria

- [ ] roster wspiera opcjonalny, unikalny numer per team;
- [ ] `number_absent` jest odróżniony od `number_unreadable`;
- [ ] numer jest wykrywany tylko na reliable torso/back crops;
- [ ] consensus wymaga wielu niezależnych odczytów;
- [ ] sprzeczne mocne numery tworzą structural warning;
- [ ] wykryty numer mapuje wyłącznie do gracza tej samej drużyny;
- [ ] gracze bez numeru nie są automatycznie identyfikowani przez brak OCR;
- [ ] number evidence nie omija hard constraints;
- [ ] number evidence nie propaguje się przez uncertain transitions;
- [ ] wszystkie decyzje zachowują lineage digests;
- [ ] shadow artifacts nie zmieniają produkcyjnego identity, statystyk ani heatmap;
- [ ] benchmark raportuje identity-level false assignments;
- [ ] produkcyjne użycie wymaga 0 false automatic assignments w zaakceptowanym benchmarku.

---

# 12. Informacja dla agenta

Przy planowaniu kolejnych etapów traktować numer koszulki jako opcjonalny, ale potencjalnie bardzo silny identity anchor.

W szczególności:

```text
Team A + trusted unique number + multi-frame consensus
→ strong roster identity evidence
```

Nie interpretować:

```text
no OCR result
```

jako:

```text
player has no number
```

Każde użycie numeru musi zachować dotychczasową zasadę:

> brak przypisania jest bezpieczniejszy niż błędne przypisanie.
