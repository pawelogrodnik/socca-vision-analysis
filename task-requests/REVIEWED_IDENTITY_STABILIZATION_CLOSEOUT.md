# Domknięcie stabilizacji reviewed identity w PR #8

## Zakres i diagnoza

Raport porównuje istniejący bounded-slot pipeline z reviewed pipeline na branchu
`feature/reviewed-match-output-mvp` przy headzie
`a42bcd25121b43a6edb4dd249f7c0f27ce197fd3`. Celem nie jest ponowne
rozwiązywanie identity ani zmiana candidate subjects. Celem jest przywrócenie
poprawnej granicy domenowej:

```text
candidate subject != player
unanchored fragment != new player
stable slot = canonical anonymous player unit
```

## Stary model: bounded `global_identity`

`global_identity.py` tworzy z góry ograniczoną pulę `A01–A14` i `B01–B14`.
Domyślnym kontraktem meczu 7v7 jest siedmiu aktywnych graczy na drużynę oraz
maksymalnie czternaście meczowych slotów na drużynę. Resolver działa
frame-by-frame i przypisuje najwyżej jedną obserwację do jednego aktywnego slotu.

Przy wyborze slotu wykorzystuje:

- ciągłość `tracklet_id` i ostrożną ciągłość raw track ID;
- team i rolę, w tym osobne anchory bramkarza;
- pozycję, predykcję ruchu, wymaganą prędkość i bbox;
- appearance oraz margines względem konkurencyjnej obserwacji;
- aktywny brak, niedawno nieaktywny slot i nieużyty slot w tej kolejności;
- wieloklatkowe potwierdzenie przed zmianą trackletu lub utworzeniem slotu.

Niepewne dane nie tworzą automatycznie nowej osoby. Pipeline rozróżnia
`detected`, `missing`, `ambiguous`, `unmatched` i `rejected`. Po głównym
przebiegu próbuje bezpiecznie naprawić potwierdzone unmatched detections oraz
scala redundantne sloty utworzone po starcie. Sloty `A08–A14` / `B08–B14`
pozostają dostępne dla prawdziwych zmian lub nowych uczestników, ale aktywacja
ósmej równoległej osoby teamu jest blokowana.

`stable_players.json` jest kompatybilnym widokiem tego samego modelu, a nie
nieograniczonym rejestrem candidate fragments.

## Aktualny model reviewed przed closeoutem

Reviewed pipeline poprawnie zachowuje kilka ważnych zabezpieczeń:

- waliduje freshness seeded assignments i source digests;
- zachowuje exact observation overrides;
- liczy rzeczywiste detected observations;
- renderuje wyłącznie operator-backed nazwiska;
- ma stale-artifact protection, canonical video resolver i container FPS;
- statystyki imienne są confirmed-only.

Regresja znajduje się w `identity_stable_anonymous.py`. Resolver normalizuje
anchory takie jak `A03~2 → A03`, ale używa polityki:

```text
brak anchoru + co najmniej kilka detekcji
→ kolejny permanentny Axx/Bxx/Uxx
```

To nie jest bounded-slot contract. Nie ma tu wymogu jawnego potwierdzenia
nowego uczestnika ani poprawnego limitu aktywnego rosteru. Brak `positions_m`
jest dodatkowo sztucznie podnoszony do minimalnego progu przez
`max(MIN_TRACK_RUN_FRAMES, frame_span)`. Resolver stosuje też „first source
wins”, przez co niezgodne claimy z global identity, stable players, gallery i
candidate shadow nie są porównywane symetrycznie.

W rezultacie liczba widocznych „zawodników” rośnie razem z techniczną
fragmentacją trackingu.

## Realna diagnostyka przed closeoutem

Na meczu `461e4dd9` (reviewed render około 89,8 s):

- candidate subjects: **109**;
- tracklets: **134**;
- tracklets z odzyskanym anchorem: **90**;
- fragmenty bez anchoru: **44**;
- automatyczne nowe permanent IDs: **39**;
- ephemeral fragments: **5**;
- reviewed stable anonymous entities: **59**;
- najwyższe reviewed labels: **A32 / B18**;
- conflicted tracklets: **69**.

Canonical `global_identity.json` i `stable_players.json` zawierają natomiast
łącznie **22** sloty: **A01–A10** i **B01–B12**, przy skonfigurowanym maksimum
14 na team. W danych operatora jest 50 exact roster assignments, ale nie ma
whole-subject manual assignments ani jawnego `create_new_stable_player`.

Duża liczba candidate subjects może być poprawna: są to techniczne fragmenty,
hipotezy i materiał do review. Duża liczba stable players nie jest poprawna,
ponieważ zmienia fragmentację trackera w fałszywą liczbę uczestników i zanieczyszcza
statystyki.

## Decyzja closeoutowa

Zachowujemy candidate subjects i reviewed snapshot, ale zmieniamy wyłącznie
promocję do stable anonymous slot:

1. Zbieramy wszystkie claims z global identity, stable players, review gallery
   i candidate shadow. Zgodne claims zachowują canonical slot. Niezgodne claims
   stają się hard conflict zamiast wyboru pierwszego pliku.
2. Fragment bez jednoznacznego anchoru pozostaje `A?`, `B?` albo `U?`, ma
   `stable_anonymous_slot_id = null`, `unanchored = true`, `requires_review = true`
   i nie jest używany w per-player stats.
3. Ambiguous membership, mixed-team subject, unknown team i brak detected
   positions nie zużywają numeru slotu.
4. Nowy slot może powstać wyłącznie z jawnego operator input, w pierwszym wolnym
   miejscu bounded puli `01–14`, z kontrolą limitu siedmiu jednocześnie aktywnych.
5. Manualne mapowanie fragmentu do istniejącego slotu ma pierwszeństwo nad
   automatycznymi sugestiami i nie mutuje raw detections, production identity ani
   published packages.
6. Frame-level uniqueness demotuje równoległe claimy tego samego slotu do
   `A? !` / `B? !`; renderer nigdy nie pokazuje dwóch identycznych stable labels
   w jednej klatce.

To jest minimalna zmiana architektoniczna: nie buduje nowego ciężkiego resolvera
stitchingowego i nie próbuje automatycznie scalać 59 encji po fakcie. Przywraca
bounded canonical slots jako jednostkę zawodnika, a candidate fragments pozostawia
jako osobną warstwę diagnostyczną i operatorską.

## Wynik po implementacji

Read-only benchmark objął 12 zapisanych scenariuszy: 2 krótkie, 5 średnich i
5 długich. Wszystkie scenariusze przeszły kontrakt:

- automatyczne permanent allocations: **0**;
- sztuczne nowe sloty: **0**;
- wszystkie odziedziczone sloty mieszczą się w **A01–A14 / B01–B14**;
- numerowane unknown IDs: **0**;
- duplicate stable labels po frame-level guardzie: **0**.

Pełne dane per scenariusz, rozkład liczby fragmentów na bounded slot, reuse rate,
unanchored fragments, blockers i demotions zapisano w
`task-requests/REVIEWED_IDENTITY_BOUNDED_BENCHMARK.json`. False split względem
prawdziwej osoby jest jawnie oznaczony jako niemierzalny bez ground truth;
raport nie udaje, że candidate fragment jest osobą.

Na realnym meczu `461e4dd9` po zmianie:

- tracklets: **134**;
- bounded stable entities: **19** (A: 9, B: 10);
- najwyższe sloty: **A10 / B12**;
- unanchored fragments: **46**;
- exact named observations: **50**;
- automatic permanent allocations: **0**;
- konflikty claimów źródłowych: **2**;
- frame-level duplicate claim groups: **143**, bezpiecznie zdemotowane do
  fallbacku; duplicate stable labels w finalnym renderze: **0**;
- production identity przed i po: **bez zmian**.

Realny render użył wyłącznie frozen artifacts. Wynik:

- 2692 klatki, 29.970 fps, 89.823 s, 1920×1080;
- H.264, `yuv420p`, browser-playable MP4;
- SHA-256:
  `f24aefb84ab8bcf911472f6ce93b0622337815f457c0836210a9fc042fff0a2d`;
- 50 exact confirmed labels i 37 603 fallback labels;
- minimapa: 2692 klatki;
- detected ball positions: 1338, wszystkie 1338 wyrenderowane;
- max jednocześnie renderowanych stabilnych etykiet: 13;
- sześć reprezentatywnych klatek QA zapisanych;
- wszystkie automatyczne semantic checks: **passed**;
- YOLO, tracking, ReID i jersey pipeline: **nieuruchamiane**.

Stats zawierają wyłącznie potwierdzone exact observations siedmiu graczy. Żaden
niepotwierdzony candidate fragment, anonymous fallback ani konflikt nie trafia do
per-player stats.
