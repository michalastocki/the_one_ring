# 19 — Testing

Target: 100% branch coverage of `tor.rolls`, `tor.rules.*`, and `tor.effects`. Lower bars
elsewhere are acceptable; these three are where the rules live.

Every test uses `content/example/`. **No test may depend on licensed content.**

---

## 19.1 Structure

```
tests/
  unit/            one module, stubbed randomness
  rules/           one mechanic, worked example from the spec
  property/        hypothesis invariants
  integration/     full scenes end to end
  replay/          determinism and log fidelity
  content/         schema validation, example-pack completeness
  golden/          transcripts compared byte-for-byte
```

## 19.2 The scripted RNG discipline

```python
rng = ScriptedRandomness(feats=[8], successes=[4, 5])
```

`ScriptedRandomness` **raises on exhaustion** (`02.1.3`). This is deliberate: a test that
scripts two Success dice and sees the engine ask for three has caught a real bug. Never add
a fallback.

Assert the queue is fully drained at the end of each rules test:

```python
def test_x(...):
    ...
    assert rng.exhausted, "engine rolled fewer dice than expected"
```

Both directions matter — rolling too few dice is as much a bug as rolling too many.

## 19.3 Roll resolution vectors

The seven worked examples in `02.4` are mandatory test cases. Beyond them:

| # | Scenario | Expectation |
|---|---|---|
| R1 | Rating 0, no bonus | exactly one Feat die rolled, zero Success dice |
| R2 | Weary, Success dice `1,2,3` | all contribute 0; total is the Feat value alone |
| R3 | Weary, Success die `6` | contributes 6 **and** scores an icon — solid face |
| R4 | Miserable, Feat = rune | SUCCESS. Auto-success outranks auto-failure (`02.3.4` precedence) |
| R5 | Magical success, TN 99 | SUCCESS; Success dice still rolled; degree reflects icons |
| R6 | 2 favoured sources, 1 ill-favoured | policy NORMAL, one Feat die |
| R7 | 3 favoured sources | policy FAVOURED, two Feat dice — no stacking beyond two |
| R8 | `bonus=2, penalty=5`, rating 1 | `dice_count == 0`, floored not negative |
| R9 | Inverted icons, Feat = eye | AUTO_SUCCESS |
| R10 | Inverted icons, Feat = rune | numeric contribution 0, TN comparison proceeds |
| R11 | Icon spending | `degree` unchanged after `IconBudget.spend()` |
| R12 | `magnitude()` on a failed roll | returns 0 regardless of icons |
| R13 | `tier()` at 0/1/2/5 icons | 1, 2, 3, 3 |

## 19.4 Per-mechanic vectors

**Attack TN asymmetry** (`08.5`). Hero STRENGTH TN 14 attacking an adversary with Parry 2 →
TN 16. The same adversary attacking that hero, whose Parry is 12 with a +2 shield → TN 14.
Assert the hero's STRENGTH TN never appears in the second calculation. This is the single
most-likely-inverted rule in the system.

**PROTECTION timing** (`08.7`). Set up a hero at Endurance 12, Load 11, hit for 4 damage
(taking them to 8, which is ≤ Load → Weary) and scoring a Piercing Blow. Assert the
PROTECTION roll was **not** made Weary. Then assert the hero is Weary immediately after.

**Pierce before threshold** (`08.6`). Feat die numeric 8, attacking with a weapon whose
Pierce value is +3, one icon spent on Pierce → effective 11 → Piercing Blow scored despite
the raw roll being below 10.

**Pierce on an icon face.** Feat die shows the rune; spending Pierce changes nothing. The
attack already auto-succeeds and already qualifies for a Piercing Blow.

**Heavy Blow with a two-handed weapon.** STRENGTH 5, two-handed → additional loss of 6.
With the relevant Virtue → 7.

**Knockback** (`08.12`). Loss of 7, knockback chosen → 4 (halved, rounded **up**). Assert
the hero's next main action is consumed, and that a second knockback in the same round is
rejected.

**Wound severity table** (`08.8`). Rune → Moderate, cleared at combat end. Numeric 7 →
Severe with 7 injury days. Eye → Grievous, hero is Dying, and assert **no** severity roll
happens on a second Wound.

**Endurance loss direction** (`16.4.1`). A *moderate* source rolls **Favoured** and tends
toward Unscathed. Assert with a scripted pair `[2, RUNE]` that the kept face is the rune.
This test exists because the direction is counterintuitive.

**Shadow test reduction** (`11.3`). Gain 3 from Dread, passed test with 1 icon → reduce by
2 → gain 1. Failed test → gain 3. Misdeed with a test supplied → `RuleViolation`.

**Shadow ceiling** (`11.2`). Max Hope 12, Shadow 11, gain 5 → Shadow is 12, not 16.

**Harden Will** (`11.5`). Shadow 7, scars 0 → Shadow becomes 1, scars 1. Not 0.

**Bout of madness** (`11.6`). Shadow 14 = max Hope 14, scars 2 → Shadow becomes 2, one Flaw
added, path step 1. Then assert every hero whose Fellowship Focus is this hero gained 1
unresistible Shadow.

**Council successes** (`09.1`). Resistance 6. Attempts: success+0 icons (1), success+2
icons (3), failure (0), success+1 icon (2) → total 6, met on the fourth attempt.

**Council botched introduction** (`09.2.5`). Failed Introduction, then partial successes →
outcome is DISASTER, not PARTIAL.

**Marching distance** (`10.3.1`). Success with 2 icons → 5 hexes. Failure in Winter → 1 hex.
Failure in Summer → 2 hexes.

**Journey arrival** (`10.3.2`). Path of 10, position 6, marching distance 4 → arrived,
**no event**. Distance 3 → position 9, event at `path[8]`.

**Journey path off-by-one.** Construct a 5-hex path and assert `len(path) == 5` excludes the
origin hex, and that arriving requires covering exactly 5.

**Fatigue and Load** (`07.4`). Fatigue 3 raises Load by 3; a hero at Endurance 10 / Load 8
becomes Weary at Fatigue 2.

**Hoard value** (`13.2`). Greater hoard, Success dice `4,3`, Company of 4 → 28 Treasure.

**Magical treasure marks** (`13.3`). Greater hoard, Feat dice `[RUNE, 5, EYE, 2]` → two
finds, one of which is `from_eye` and carries Greed.

**Standard of Living derivation** (`03.7`). Treasure 89 → Common. Treasure 90 → Prosperous.
Assert the boundary is `>=`.

**Carried vs cached Treasure** (`07.4`). Cache 60 of 90 → Standard of Living stays
Prosperous, Load drops by 60.

**XP caps** (`15.4`). Two ranks of the same Skill in one phase → `RuleViolation`. VALOUR and
WISDOM in the same phase → `RuleViolation`. A Combat Proficiency bought with Skill points →
`RuleViolation`.

**Previous Experience ladder** (`06.6`). Raising a Skill from 1 to 4 costs exactly the full
10-point budget.

**Song spending** (`15.8`). A failed SONG roll still marks the song off the list.

**Rearward requirements** (`08.2.3`). 4 heroes, 9 enemies → Rearward refused (more than
twice the Company). 4 heroes, 6 enemies, 1 already in Rearward, 2 in close combat → refused
(needs 4 in close combat for 2 Rearward).

**Engagement limits** (`08.2.4`). A fourth human-sized foe cannot engage a hero already
engaged by three. A seventh hero cannot engage a large creature.

**Reward plot immunity** (`15.4.1`). Break Shield against a Reward-bearing shield → no
effect. Transferring a Reward-bearing item to another hero → `RuleViolation`. The same
transfer flagged as an heirloom → permitted.

**Famous item constraints** (`13.7.3`). Zero Enchanted Rewards → violation. Four qualities →
violation. A Reward plus the Enchanted Reward that supersedes it → violation. Assert all
four are reported together by `RuleViolationGroup`.

**Eye Awareness filtering** (`14.3`). An eye rolled in combat → no increase. The same roll
out of combat → +1. Shadow gained in combat → no increase.

**Eye reset** (`14.7`). Revelation resolved → Awareness returns to `starting_awareness`,
not 0.

**Adversary Weary at zero drive** (`12.2`). A creature entering a round at 0 drive is Weary
for that round; spending its last point mid-round does **not** make it Weary until the next
round starts.

**Adversary Might** (`12.3`). A Might 2 creature survives its first Wound. Assert no Wound
Severity roll is made for adversaries at all.

## 19.5 Property tests

Use `hypothesis`. Generate arbitrary but valid heroes and states.

| Property |
|---|
| Every invariant I1–I10 holds after any sequence of legal operations |
| `0 <= shadow <= max_hope` always |
| `weary == (endurance <= load)` after every `recompute_conditions` |
| `miserable == (shadow >= hope)` after every `recompute_conditions` |
| `dice_count >= 0` for any combination of bonuses and penalties |
| `resolve()` never raises for any well-formed `RollRequest` |
| `magnitude()` is monotonic in icon count for successful rolls |
| A `ResistanceContest` terminates: `finished` becomes True within `attempts_allowed + 1` records |
| Serialise → deserialise → serialise is a fixed point |
| Derived stats are idempotent: recomputing twice gives the same value |
| Registering then unregistering an effect restores the prior derived stats exactly |

That last one catches the whole class of bugs where an effect leaves residue — a smashed
shield, a dropped helm, an expired Strengthen Fellowship bonus.

## 19.6 Replay tests

The determinism guarantee (`17.4`):

```python
def test_replay_is_exact(tmp_path):
    a = run_scripted_campaign(seed=1234)
    save(a, tmp_path / "s.json")
    b = EventLog.replay(load(tmp_path / "s.json"))
    assert canonical_json(a) == canonical_json(b)
```

Also assert that replaying to an intermediate sequence number produces exactly the state
that existed at that point — this is what makes undo trustworthy.

And assert redaction is lossless in one direction: `redact(for_players=True)` never leaks a
`gm_only` payload, checked by scanning the output for known secret values.

## 19.7 Content tests

* Every file in `content/example/` validates against its schema.
* Every `LookupTable` in the pack covers its whole key domain — 1–10 plus both icon faces
  for Feat tables, 1–6 for Success tables.
* Every referenced `EffectId` resolves.
* Every `EffectKind` is represented at least once.
* Every hook in the registry (`04.3`) has at least one effect exercising it. A hook nothing
  uses is either dead or unimplemented; either way the test should force the question.
* Loading a pack that omits a required field, references a missing id, or has a gap in a
  table raises `ContentError` with a usable JSON pointer. Test each failure mode.

## 19.8 Golden transcripts

Record full scenes as fixtures: setup, seed, decision sequence, expected transcript.

Minimum set:
1. A three-round combat between a four-hero Company and a mixed group, including a
   Piercing Blow, a Wound, a Combat Task, a knockback, and one adversary fleeing.
2. A council at Resistance 6 with a botched Introduction ending in Disaster.
3. A twelve-hex journey through two region types with four events, one of them a Terrible
   Misfortune, ending with Fatigue reduction.
4. A full Fellowship Phase including Yule: XP spend, a VALOUR rank taken as a dormant
   quality activation, Heal Scars, and an aging tick.
5. A Skill Endeavour aborted by a Disaster.
6. A hoard discovery producing two magical finds, one on an eye.

Golden tests catch cross-subsystem regressions that unit tests miss. Regenerate them
deliberately, never automatically — a diff in a golden file should require a human to
confirm the rule actually changed.

## 19.9 What not to test

Do not write tests asserting specific content values from the licensed book. Do not build
fixtures that reproduce the real culture tables, weapon table, or adversary stat blocks.
Test the *mechanism*: that a culture's endurance bonus is applied, not that a particular
culture's bonus is a particular number.
