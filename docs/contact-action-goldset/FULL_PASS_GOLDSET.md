# Socca Vision — Contact / Action Goldset (W1–W6)

## Scope

This document consolidates the six manually reviewed windows selected from the **merged/published Corgi–Verisk match timeline**.

**Important annotation convention**

- All timestamps are **approximate anchors** (`~`), not frame-accurate event times.
- A timestamp often marks the **start of a sequence**, not the exact time of every event described in the same row.
- The order and football meaning of the sequence are authoritative; exact frame/time alignment can be refined later against video/artifacts.
- Existing **canonical shots from Shot Review are not manually duplicated as new shot labels**. Where useful, they are referenced as `CANONICAL_SHOT` only to preserve sequence context.
- Identity/tracking issues are recorded as notes and must not be interpreted as real team/player changes.
- `AMBIGUOUS` means the video does not support a confident football interpretation and should not be force-classified.

## Working action vocabulary

Primary actions used in this document:

- `PASS`
- `RESTART`
- `INTERVENTION`
- `CONTROL`
- `CONTEST`
- `GK_COLLECTION`
- `GK_HOLD`
- `NOT_IN_PLAY`
- `OTHER`
- `CANONICAL_SHOT` — reference only; source of truth remains canonical Shot Review

Common outcomes / modifiers:

- `COMPLETED`
- `INTERCEPTED`
- `MISCONTROLLED`
- `OUT`
- `CLEARANCE`
- `BLOCK`
- `DEFLECTION`
- `ONE_TOUCH`
- `HEADER`
- `LONG`
- `THROUGH_BALL`
- `FOUL`
- `AMBIGUOUS`

---

# W1 — 00:00–01:15

**Merged timeline:** `00:00–01:15`<br>
**Source:** `9c7485e4`<br>
**Source-local time:** `00:00–01:15`

## Manual sequence annotations

| Approx. time | Actor / team | Action | Outcome / description | Notes |
|---|---|---|---|---|
| ~00:03–00:04 | Patryk / Corgi → Team B | `RESTART / PASS` | Intentional ball handover; ball reaches b06 around 00:04 | Start-of-half fair-play / “GL HF”-style tradition; intentional pass to opposition |
| ~00:11–00:13 | b06 → b03 | `PASS` | b03 fails to control; Patryk nearly recovers; ball finally ends at b01 | `MISCONTROLLED`; important: do not simplify as b06 → b01 pass |
| ~00:15 | b01 → b04 | `PASS` | `COMPLETED` | Identity note: b01 briefly flickers to Corgi; b04 changes to b08 around ~00:16 |
| ~00:18 | b08 → b06 | `PASS` | `COMPLETED` |  |
| ~00:21 | b06 → b05 | `PASS` | `COMPLETED` |  |
| ~00:25 | b05 → player “1” | `PASS` | `INTERCEPTED` by Mateusz |  |
| ~00:26 | Mateusz → Kuba | `PASS` | `COMPLETED` | `ONE_TOUCH` |
| ~00:26–00:27 | Kuba → Mateusz | `PASS` | `COMPLETED` | Immediate return; `ONE_TOUCH` |
| ~00:28 | Mateusz → Patryk | `PASS` | `INTERCEPTED` by b06 | Intended for Patryk |
| ~00:31 | b06 → b03 | `PASS` | `INTERCEPTED` by Piotrek |  |
| ~00:33 | Piotrek vs b06 | `CONTEST` | Ball goes out; throw-in for Team B | Last touch uncertain unless later video/frame review resolves it |
| ~00:41 | Team B → b04 | `RESTART` | Throw-in toward b04 |  |
| ~00:42 | b04 → b02 | `PASS` | Header toward middle of penalty area; becomes contested | `HEADER` |
| ~00:42–00:43 | b02 vs Przemek | `CONTEST` | Przemek wins/clears ball away from penalty area |  |
| ~00:43 | Przemek | `INTERVENTION` | `CLEARANCE` from penalty area |  |
| ~00:46 | Mateusz | `OTHER / TOUCH_ATTEMPT` | Misses the ball; b03 gains possession | Useful hard negative for “nearest player == contact” |
| ~00:50 | b03 | `CANONICAL_SHOT` | Off target; corner for Team B | Do not duplicate manual shot label |
| ~01:02 | Team B | `RESTART` | Corner |  |
| ~01:02–01:03 | Przemek | `INTERVENTION` | Header clearance | `HEADER`, `CLEARANCE` |
| ~01:07 | Kuba vs b10 | `CONTEST` | Ball goes out | Last touch / restart side may remain `AMBIGUOUS` if not visually certain |

## Identity / tracking notes

- b01 temporarily flickers to Corgi around ~00:15.
- b04 changes to b08 around ~00:16.
- These are tracking/identity artifacts, not real substitutions/team changes.

---

# W2 — 09:00–10:15

**Merged timeline:** `09:00–10:15`<br>
**Source:** `9c7485e4`<br>
**Source-local time:** `09:00–10:15`

## Game-state context

| Approx. time | State | Description |
|---|---|---|
| ~09:00–09:16/17 | `NOT_IN_PLAY` | Ball outside play; players wait for a new ball. At times more than one ball is visible before play resumes. |

## Manual sequence annotations

| Approx. time | Actor / team | Action | Outcome / description | Notes |
|---|---|---|---|---|
| ~09:16–09:17 | b07 → b05 | `RESTART / PASS` | Throw-in restart; pass reaches b05 | b07 becomes visible only after the throw and re-entering field |
| ~09:18 | b05 | — | Identity changes to b06 | Tracking note |
| ~09:20 | b06 → b04 | `PASS` | `COMPLETED` |  |
| ~09:20–09:23 | b04 | `OTHER` | Probable shot / hard driven ball into penalty area | `AMBIGUOUS`: shot vs hard cross/ball into box |
| ~09:23–09:24 | Roman | `INTERVENTION` | Blocks the driven ball | `BLOCK` |
| ~09:24 | Piotrek | `INTERVENTION` | Clears ball | `CLEARANCE` |
| ~09:24–09:28 | Paweł → Roman | `PASS` | Pass into free space is too strong; B goalkeeper collects | Intended for Roman |
| ~09:28 | B goalkeeper | `GK_COLLECTION` | Collects overhit pass |  |
| ~09:30 | B goalkeeper → b02 | `RESTART / PASS` | `COMPLETED` |  |
| ~09:30–09:34 | b02 | `CONTROL` | Beats Paweł, then Piotrek cuts the ball out |  |
| ~09:34 | Piotrek | `INTERVENTION` | Cuts out / clears to touchline | `OUT` |
| ~09:38 | Team B player “1” → b06 | `RESTART / PASS` | Restart from touchline | Player “1” appears only after entering field |
| ~09:38–09:41 | b06/B? vs Paweł | `CONTEST` | Paweł wins possession | Identity note: b06 changes to B? during duel |
| ~09:41 | Paweł → Piotrek | `PASS` | `COMPLETED` |  |
| ~09:42 | Piotrek → Krzysiek | `PASS` | Long diagonal pass; `COMPLETED` | `LONG` |
| ~09:42–09:47 | Krzysiek | `CONTROL` | Receives, carries/dribbles past b06 |  |
| ~09:48 | Krzysiek | `CANONICAL_SHOT` | On target; goalkeeper saves | Do not duplicate manual shot label |
| ~09:48–09:51 | Paweł | `CONTROL / RECOVERY` | Reaches rebound after save |  |
| ~09:51 | Paweł | `CANONICAL_SHOT` | Very poor/wayward attempt into central penalty area | Do not duplicate manual shot label |
| ~09:53 | Team B defender | `INTERVENTION` | Clears ball outside penalty area | `CLEARANCE` |
| ~09:53–09:54 | — | `OTHER` | Further ricochet/shot sequence | Use canonical shot data for shot identity |
| ~09:54 | Krzysiek | `CANONICAL_SHOT` | Goal | Do not duplicate manual shot label |
| ~10:15 | b04 → b07 | `PASS` | `COMPLETED` | End of reviewed fragment |

## Identity / tracking notes

- b05 changes to b06 around ~09:18.
- b06 later changes to B? during duel with Paweł.
- Restart taker “1” may appear only after entering field.
- Multiple balls are visible during the initial dead-ball period; not every visible ball is the active game ball.

---

# W3 — 21:01–22:16

**Merged timeline:** `21:01–22:16`<br>
**Source:** `6d8fc20c`<br>
**Source-local time:** `01:45–03:00` (`105–180 s`)

## Game-state context

| Approx. time | State | Description |
|---|---|---|
| ~21:01–21:08 | `NOT_IN_PLAY` | Ball out of active play / restart preparation. |
| ~21:22–21:30 | `GK_HOLD` | Mati GK controls/holds the ball before a long throw. |
| ~21:38–21:54 | `NOT_IN_PLAY` | Foul / dead-ball period before free-kick restart. |

## Manual sequence annotations

| Approx. time | Actor / team | Action | Outcome / description | Notes |
|---|---|---|---|---|
| ~21:08 | Mati GK → Piotrek | `RESTART / PASS` | `COMPLETED` | Goalkeeper restart |
| ~21:12 | Piotrek → Roman | `PASS` | Long pass attempt; Roman does not get control; B goalkeeper collects | `LONG`, `NOT_COMPLETED` |
| ~21:14 | B goalkeeper | `GK_COLLECTION` | Collects long pass |  |
| ~21:14+ | B goalkeeper → b01 | `RESTART / PASS` | `COMPLETED` |  |
| ~21:xx | b01/B? → b12 | `PASS` | b12 fails to control; ball reaches Mati GK | `MISCONTROLLED` |
| ~21:22 | Mati GK | `GK_COLLECTION` | Gains possession |  |
| ~21:22–21:30 | Mati GK | `GK_HOLD` | Holds ball | State, not repeated contacts |
| ~21:30 | Mati GK → Roman | `RESTART / PASS` | Long throw; Roman controls poorly | `LONG` |
| ~21:36 | Roman | `CONTROL` | Poor reception / miscontrol | `MISCONTROLLED` |
| ~21:36 | b07 | `INTERVENTION / CONTROL` | Wins the loose ball |  |
| ~21:36–21:38 | b07 → B defender | `PASS` | `COMPLETED` | Defender is B? and is not goalkeeper |
| ~21:38 | B defender → b13 | `PASS` | Long pass attempt during sequence where foul is called | Sequence interrupted by foul |
| ~21:38 | Roman | `CONTEST` | Foul on defender | `FOUL` |
| ~21:54 | b07 → b12 | `RESTART / PASS` | Free-kick restart |  |
| ~21:56 | b12 | `CONTROL` | Fails to receive; ball reaches Mati GK | `MISCONTROLLED` |
| ~22:04 | Mati GK → Piotrek | `RESTART / PASS` | `COMPLETED` |  |
| ~22:07 | Piotrek → Roman | `PASS` | `INTERCEPTED` by Team B player |  |
| ~22:08–22:10 | Team B player | `INTERVENTION / CONTROL` | Interception; gains possession | Temporarily detected as A01 despite being Team B |
| ~22:11 | b03 → b08 | `PASS` | `COMPLETED` | Player identity returns from A01 artifact to b03 around ~22:10 |
| ~22:15 | b08 → b12 | `PASS` | Through ball; b12 fails to receive | `THROUGH_BALL`, `MISCONTROLLED` |
| ~22:17 | Mati GK | `GK_COLLECTION` | Ball reaches goalkeeper | Slightly beyond nominal 22:16 window edge; included because it completes the sequence |

## Identity / tracking notes

- b01 changes to B? but remains the same physical player.
- At one point GUI shows two `B?` players: goalkeeper and defender; roles must be distinguished by sequence/context.
- Team B player is temporarily mislabeled `A01` around ~22:08–22:10.

---

# W4 — 22:31–23:46

**Merged timeline:** `22:31–23:46`<br>
**Source:** `6d8fc20c`<br>
**Source-local time:** `03:15–04:30` (`195–270 s`)

## Manual sequence annotations

| Approx. time | Actor / team | Action | Outcome / description | Notes |
|---|---|---|---|---|
| ~22:30–22:31 | Przemek | `INTERVENTION` | Ball cleared toward midfield | `CLEARANCE`; window begins mid-action |
| ~22:31–22:32 | Mateusz vs b12 | `CONTEST` | Aerial duel; b12 wins header |  |
| ~22:32 | b12 → b02 | `PASS` | Header pass; `COMPLETED` | `HEADER` |
| ~22:33 | b02 → b04 | `PASS` | Immediate first-time pass; `COMPLETED` | `ONE_TOUCH` |
| ~22:34–22:36 | b04/B? → b12 | `PASS` | `INTERCEPTED` by Andrzej | b04 changes to B? during duel with Piotrek |
| ~22:36 | Andrzej | `INTERVENTION` | Interception |  |
| ~22:36–22:37 | Andrzej → Piotrek | `PASS` | `COMPLETED` |  |
| ~22:37 | Piotrek → Mateusz | `PASS` | `COMPLETED` |  |
| ~22:38 | Mateusz → Paweł | `PASS` | Inaccurate; `INTERCEPTED` by b07 |  |
| ~22:39 | b07 | `INTERVENTION / CONTROL` | Interception |  |
| ~22:41 | b07 → b02 | `PASS` | `COMPLETED` |  |
| ~22:43 | b02 → b03 | `PASS` | `COMPLETED` |  |
| ~22:45 | b03 → b07 | `PASS` | `COMPLETED` |  |
| ~22:46–22:48 | b07 → Team B player | `PASS` | `COMPLETED` | Receiver temporarily detected as `A?` despite being Team B |
| ~22:48 | Team B player | `CANONICAL_SHOT` | Off target | Do not duplicate manual shot label |
| ~23:08 | Mati GK → Paweł | `RESTART / PASS` | Long goalkeeper throw | `LONG` |
| ~23:12 | Paweł vs defender | `CONTEST` | Paweł fouls defender | `FOUL` |
| ~23:12–23:17 | — | `NOT_IN_PLAY` | Dead-ball period before free kick |  |
| ~23:17 | b03 → b12 | `RESTART / PASS` | Free-kick restart; `COMPLETED` |  |
| ~23:20 | b12 | `CANONICAL_SHOT` | Direct shot | Do not duplicate manual shot label |
| ~23:30 | Mati GK → Piotrek | `RESTART / PASS` | `COMPLETED` |  |
| ~23:37 | Piotrek → Andrzej | `PASS` | `COMPLETED` |  |
| ~23:39 | Andrzej → Piotrek | `PASS` | `COMPLETED` |  |
| ~23:42 | Piotrek → Roman | `PASS` | Long pass attempt; ball goes out | `LONG`, `OUT` |

## Identity / tracking notes

- b04 changes to B? during pressure/duel.
- Team B receiver around ~22:46–22:48 is temporarily shown as `A?`.

---

# W5 — 29:22–30:37

**Merged timeline:** `29:22–30:37`<br>
**Source:** `5e62625e`<br>
**Source-local time:** `00:00–01:15`

## Technical context

`29:22` is the **boundary between two physical source videos** on the merged timeline.

This is a technical source boundary, not a football event.

## Game-state context

| Approx. time | State | Description |
|---|---|---|
| ~29:22–29:43 | `NOT_IN_PLAY` | Free-kick preparation near Corgi penalty area: ball placement, wall setup, referee discussion. Over 20 seconds without active play. |

## Manual sequence annotations

| Approx. time | Actor / team | Action | Outcome / description | Notes |
|---|---|---|---|---|
| ~29:43 | Team B | `CANONICAL_SHOT` | Free kick hits wall | Canonical Shot Review is source of truth |
| ~29:43+ | Przemek (?) | `INTERVENTION` | Ball apparently cleared outside penalty area | `CLEARANCE`; actor uncertain |
| ~29:45 | b04 | `CANONICAL_SHOT` | On target; goalkeeper saves | Do not duplicate manual shot label |
| ~29:45–29:48 | — | `OTHER` | Save / loose-ball sequence |  |
| ~29:48 | Mateusz | `CONTROL / RECOVERY` | Wins/collects ball and starts solo attack |  |
| ~29:48–29:5x | Mateusz | `CONTROL` | Solo offensive carry |  |
| ~29:5x | Mateusz | `CANONICAL_SHOT` | Off target | Do not duplicate manual shot label |
| ~30:07 | B goalkeeper → b05 | `RESTART / PASS` | `COMPLETED` |  |
| ~30:14 | b05 → b04 | `PASS` | `COMPLETED` |  |
| ~30:16 | b04 → B defender | `PASS` | `COMPLETED` | Defender initially B?, later becomes B03 |
| ~30:17–30:20 | B03 → b08 | `PASS` | Lofted/through ball not controlled; Mati GK collects | `LONG / THROUGH_BALL`, `NOT_COMPLETED` |
| ~30:20 | Mati GK | `GK_COLLECTION` | Collects pass |  |
| ~30:23 | Mati GK → Patryk | `RESTART / PASS` | `COMPLETED` | Patryk temporarily changes to B05 during pass |
| ~30:26 | Patryk → Krzysiek | `PASS` | `COMPLETED` |  |
| ~30:31 | Krzysiek → Kuba | `PASS` | Inaccurate; intended receiver does not control | `NOT_COMPLETED` |
| ~30:32 | Team B defender | `INTERVENTION` | Defender mishits / fails to control intervention | Important: ball then reaches Patryk; should not be simplified to Krzysiek → Patryk completed pass |
| ~30:32+ | Patryk | `CANONICAL_SHOT` | Goalkeeper saves | Do not duplicate manual shot label |
| ~30:35 | B goalkeeper → b02 | `RESTART / PASS` | `COMPLETED` |  |
| ~30:3x–30:40 | b02 → b01 | `PASS` | `INTERCEPTED` by Mateusz |  |
| ~30:40 | Mateusz | `INTERVENTION / CONTROL` | Interception / recovery |  |
| ~30:40–30:4x | Mateusz | `CONTROL` | Dribble attempt fails; ball goes out | `OUT` |
| ~30:47 | b02 → b07 | `RESTART / PASS` | Throw-in attempt |  |
| ~30:48 | Piotrek | `INTERVENTION` | Intercepts/clears to touchline | `CLEARANCE`, `OUT` |
| ~30:48–31:03 | — | `NOT_IN_PLAY` | Waiting for next throw-in | Extends beyond nominal W5 end; sequence completion retained |
| ~31:03 | b02 | `RESTART` | Throw-in | Taker appears only after re-entering field |
| ~31:04 | b02 → b03 | `PASS` | `COMPLETED` | b03 changes to B? while holding possession |
| ~31:04+ | b03/B? | `CONTROL` | Holds possession to end of described sequence |  |

## Identity / tracking notes

- Source video boundary at merged 29:22.
- B defender transitions B? → B03.
- Patryk is temporarily mislabeled B05 around ~30:23.
- Throw-in takers may appear in GUI only after entering the pitch.

---

# W6 — 31:52–33:07

**Merged timeline:** `31:52–33:07`<br>
**Source:** `5e62625e`<br>
**Source-local time:** `02:30–03:45` (`150–225 s`)

## Manual sequence annotations

| Approx. time | Actor / team | Action | Outcome / description | Notes |
|---|---|---|---|---|
| ~31:52–31:53 | b13 → b04 | `PASS` | Attempt into middle of penalty area |  |
| ~31:53 | Piotrek | `INTERVENTION` | Clears pass | `CLEARANCE` |
| ~31:54 | Patryk | `CONTROL / RECOVERY` | Gains ball |  |
| ~31:54–31:59 | Patryk | `CONTROL` | Dribbles past b02 |  |
| ~31:59 | b03/B? | `INTERVENTION` | Wins ball from Patryk | `TACKLE / INTERCEPTION` |
| ~32:03 | b03/B? | `OTHER` | Probable shot / driven ball into penalty area | `AMBIGUOUS`: shot vs hard ball into box |
| ~32:03+ | Piotrek | `INTERVENTION` | Blocks ball | `BLOCK` |
| ~32:04–32:05 | Krzysiek | `OTHER` | Ball deflects off Krzysiek | `DEFLECTION` |
| ~32:06 | Mateusz | `INTERVENTION` | Clears ball | `CLEARANCE`; temporarily labeled as Team B |
| ~32:06+ | — | — | Cleared ball reaches b13 |  |
| ~32:11 | b13 | `CANONICAL_SHOT` | Dangerous shot | Do not duplicate manual shot label |
| ~32:28 | Mati GK → Mateusz | `RESTART / PASS` | Long goalkeeper throw | `LONG` |
| ~32:28–32:34 | Mateusz | `CONTROL` | Gains ball and attempts pass into penalty area |  |
| ~32:3x | Mateusz → area/Kuba | `PASS` | Kuba nearly gains control, but b13 wins it |  |
| ~32:34 | b13 | `INTERVENTION / CONTROL` | Recovers/intercepts ball |  |
| ~32:37 | b13 → b03 | `PASS` | `COMPLETED` |  |
| ~32:38 | b03 → b13 | `PASS` | `COMPLETED` |  |
| ~32:41 | b13 → a03 | `PASS` | `COMPLETED` | `a03` is actually Team B player; identity error |
| ~32:43 | Przemek | `INTERVENTION / CONTROL` | Wins ball |  |
| ~32:43–32:46 | Kuba | `CONTROL` | Takes control after Przemek’s recovery |  |
| ~32:46 | Kuba → Krzysiek | `PASS` | `COMPLETED` |  |
| ~32:47 | Krzysiek → Roman | `PASS` | Long diagonal pass | `LONG` |
| ~32:49 | Roman | `CONTROL` | Fails to receive/control | `MISCONTROLLED` |
| ~32:52 | — | `OUT` | Ball leaves field |  |
| ~32:58 | B goalkeeper → b13 | `RESTART / PASS` | `COMPLETED` |  |
| ~33:07 | b13 → b04 | `PASS` | `COMPLETED` |  |
| ~33:10 | b04 → b02 | `PASS` | b02 fails to receive | Slightly beyond nominal W6 end; retained to complete sequence |
| ~33:12 | b02 | `CONTROL` | Failed reception / miscontrol; ball reaches Mateusz | `MISCONTROLLED` |
| ~33:13 | Mateusz | `INTERVENTION` | Clears ball to touchline | `CLEARANCE`, `OUT` |

## Identity / tracking notes

- b03 may appear as B? around ~31:59–32:03.
- Mateusz is temporarily assigned to Team B around ~32:06.
- `a03` at ~32:41 is actually a Team B player.

---

# Cross-window notes

## Canonical shots

Shot timestamps/outcomes should be imported from the existing canonical Shot Review dataset.

Manual notes above reference shots only to preserve the surrounding sequence:

- what happened immediately before the shot;
- block/save/deflection;
- who recovered the rebound;
- whether the next event was a clearance, pass, carry or another shot.

Do **not** use the approximate manual shot timestamps here as a replacement for canonical shot timestamps.

## Identity problems observed

The manual review repeatedly identified identity/team flickers, including:

- temporary A/B team flips;
- one physical player changing slot ID (`b04 → b08`, etc.);
- `B?` used for multiple physical players;
- throw-in takers becoming visible only after entering the field.

When evaluating contact/action logic, identity errors must be separated from action-classification errors.

## Important hard cases represented in the goldset

The six windows contain examples of:

- completed passes;
- intercepted passes;
- failed receptions / miscontrols;
- one-touch passes;
- long diagonal passes;
- through balls;
- goalkeeper collections and holds;
- goalkeeper restarts;
- throw-ins and free kicks;
- dead-ball periods with a visible ball;
- multiple-ball situations while play is stopped;
- contested aerial duels;
- tackles/interceptions;
- clearances;
- blocks;
- deflections;
- shot/save/rebound chains;
- carries/dribbles;
- fouls;
- ball out of play;
- false-nearest-player contact situations;
- source-video boundary effects;
- player/team identity flicker.

## Recommended interpretation for future evaluation

This goldset should be used as **manual football ground truth**, not as exact frame truth.

For automated evaluation:

1. use each manual timestamp as a local search anchor;
2. align the described action to the nearest plausible contact/event in the video/artifacts;
3. preserve event order within each sequence;
4. use canonical Shot Review as the authoritative source for shots;
5. never force an `AMBIGUOUS` manual event into a precise class without additional evidence.
