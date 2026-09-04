# 02 — Dice and Roll Resolution

Modules: `tor.dice`, `tor.tables`, `tor.rolls`.
This is the most important document in the spec. Every other subsystem depends on it and
none of them may duplicate any part of it.

---

## 2.1 `tor.dice`

### 2.1.1 The two dice types

**Feat die** — a d12. Faces 1–10 are numeric. Two faces are special:

* the highest face — the **rune** — `FeatFace.RUNE`
* the lowest face — the **eye** — `FeatFace.EYE`

**Success die** — a d6. Faces 1–6 are numeric. The 6 additionally carries a **success
icon**. Faces 1, 2, 3 are printed "outlined"; 4, 5, 6 are "solid". The outline/solid
distinction is mechanically live: it is what the Weary condition keys off.

```python
class FeatFace(StrEnum):
    EYE = "eye"
    RUNE = "rune"
    # numeric faces represented as int 1..10

FeatValue = int | FeatFace          # 1..10 | EYE | RUNE

@dataclass(frozen=True, slots=True)
class SuccessDie:
    face: int                        # 1..6
    @property
    def is_outlined(self) -> bool:  return self.face <= 3
    @property
    def has_icon(self) -> bool:     return self.face == 6
```

Naming note: use `RUNE` / `EYE` / `SUCCESS_ICON` rather than in-world names. Keeps
`tor.dice` free of setting vocabulary and makes the module reusable.

### 2.1.2 Numeric conversion

```python
def feat_numeric(v: FeatValue, *, inverted: bool) -> int:
    """Numeric contribution of a Feat die to a roll total.

    inverted=False  (Player-heroes):  RUNE -> auto-success sentinel, EYE -> 0
    inverted=True   (adversaries):    EYE  -> auto-success sentinel, RUNE -> 0
    """
```

The **inversion flag** implements the rule that for servants of the Shadow the meanings
of the two icons swap: the eye becomes the best result (auto-success) and the rune reads
as zero. One boolean, one code path. See `12.4`.

For total arithmetic, the auto-success face contributes `0` to the numeric sum — it does
not need to contribute anything because it short-circuits the TN comparison entirely.

### 2.1.3 Randomness protocol

```python
class Randomness(Protocol):
    def feat(self) -> FeatValue: ...
    def success(self) -> SuccessDie: ...

class SystemRandomness:              # wraps random.Random(seed)
    def __init__(self, seed: int | None = None) -> None: ...

class ScriptedRandomness:            # test double: consumes a fixed queue
    def __init__(self, feats: Sequence[FeatValue], successes: Sequence[int]) -> None: ...
```

`ScriptedRandomness` raises `StateError` if a test drains a queue — a silent refill would
hide a bug where the engine rolls more dice than the test expected. That over-roll
detection is the point.

Feat die distribution: uniform over 12 outcomes `{1..10, EYE, RUNE}`.
Success die: uniform over `{1..6}`.

---

## 2.2 `tor.tables`

```python
K = TypeVar("K")          # FeatValue or int
V = TypeVar("V")

@dataclass(frozen=True, slots=True)
class TableRow(Generic[K, V]):
    match: K | range | FeatFace     # exact int, numeric range, or an icon face
    value: V

@dataclass(frozen=True, slots=True)
class LookupTable(Generic[K, V]):
    rows: tuple[TableRow[K, V], ...]
    def lookup(self, key: K) -> V: ...
```

Resolution order: icon rows are checked first (exact face identity), then exact ints, then
ranges. A table missing coverage for any possible key fails validation at construction —
`LookupTable` is validated eagerly, so a malformed content table is a load-time error.

`lookup()` must also accept a `shift: int = 0` parameter applied to numeric keys before
matching, clamping into `1..10` while leaving icon faces untouched. This exists for the
*Ponder Storied and Figured Maps* undertaking (`10.6`), which shifts Journey Event results
by +1. Do not reimplement that shift inside the journey subsystem.

---

## 2.3 `tor.rolls` — the universal roll

### 2.3.1 RollRequest

```python
class FeatDicePolicy(StrEnum):
    NORMAL       = "normal"        # roll 1 Feat die
    FAVOURED     = "favoured"      # roll 2, keep best
    ILL_FAVOURED = "ill_favoured"  # roll 2, keep worst

@dataclass(frozen=True, slots=True)
class RollRequest:
    ability: AbilityId | None        # None for pure Feat-die rolls (tables, severity)
    rating: int                      # 0..6 — number of Success dice from the ability
    target_number: int | None        # None => no TN comparison (table rolls)

    favoured_sources: tuple[str, ...] = ()      # ids of effects making it Favoured
    ill_favoured_sources: tuple[str, ...] = ()

    bonus_dice: int = 0              # net (+) from Hope, support, items, advantages
    penalty_dice: int = 0            # net (-) from complications, stance, flaws

    weary: bool = False
    eye_is_auto_failure: bool = False   # set by the Miserable condition, and by the
                                        # Ill-luck curse (13.8) which mimics it without
                                        # the character actually being Miserable
    icon_inverted: bool = False      # True for adversaries
    magical_success: bool = False    # bypasses the TN comparison entirely
    purpose: RollPurpose = RollPurpose.GENERIC
```

`RollRequest` is built by `rolls.build_request(character, ability, context)`, which is the
**only** place that consults the `EffectBus` for roll modification. Subsystems call
`build_request` then `resolve`; they never assemble a `RollRequest` by hand except for
pure table rolls.

### 2.3.2 Deriving the Feat die policy

```
favoured   = len(favoured_sources)     > 0
illfav     = len(ill_favoured_sources) > 0

favoured and     illfav  -> NORMAL          # they cancel, regardless of counts
favoured and not illfav  -> FAVOURED
not favoured and illfav  -> ILL_FAVOURED
neither                  -> NORMAL
```

Multiple sources on one side do **not** stack — two Favoured sources and one Ill-favoured
source still yields NORMAL. Keep the source id tuples so the UI can explain *why*.

A character who is Ill-favoured *as a state* (rather than on a specific roll) — notably a
hero whose Shadow equals maximum Hope — contributes an ill-favoured source to every
`RollRequest`. That is an effect registered on `MODIFY_ROLL_REQUEST`, not a special case
in `resolve()`.

### 2.3.3 Success dice count

```python
dice_count = max(0, rating + bonus_dice - penalty_dice)
```

Bonuses and penalties from all sources are summed first, then applied. Floor at zero: a
character can be reduced to rolling only the Feat die, never fewer. A rating of 0 with no
bonus also rolls only the Feat die.

Sources of bonus dice, all funnelled into `bonus_dice`:

| Source | Amount | Constraint |
|---|---|---|
| Spending 1 Hope | +1 | Only one Hope may be spent per roll for this purpose |
| Spending 1 Hope while **Inspired** | +2 | Same one-point limit; Inspiration doubles the yield |
| Support from another PC (that PC spends 1 Hope) | +1 | Only one PC may support a given roll |
| Support where the supporter's Fellowship Focus is the acting hero | +2 | Replaces, not stacks with, the +1 |
| A Useful Item associated with the Skill (LM approval, non-combat) | +1 | Only one item may benefit a roll |
| Advantage: moderate / greater | +1 / +2 | Combat and other contexts |
| Various Virtues, Cultural Virtues, Blessings | varies | Via effects |

Sources of penalty dice:

| Source | Amount |
|---|---|
| Complication: moderate / severe | −1 / −2 |
| Forward stance (attacks against you) — see `08` | context-dependent |
| Defensive stance attack roll | −1 per engaging opponent |
| Brawling attack | −1 |
| Reluctant audience in a council | −1 |
| Hard terrain on a Journey Event roll | −1 |
| An LM character's obstructive Distinctive Feature | −1 |

**Support rule detail.** Support requires that the supporter possess at least rank 1 in a
Skill the LM deems appropriate, and that circumstances allow it. The engine validates the
rank; the appropriateness is an LM input (`support_approved: bool`).

### 2.3.4 Resolution algorithm

```python
def resolve(req: RollRequest, rng: Randomness) -> RollResult:
```

1. **Roll Feat dice** per policy: 1 die for NORMAL, 2 for FAVOURED/ILL_FAVOURED.
   Selection uses an ordering where, with `icon_inverted=False`,
   `EYE < 1 < 2 < … < 10 < RUNE`; with `icon_inverted=True`, `RUNE < 1 < … < 10 < EYE`.
   FAVOURED keeps the maximum under that ordering, ILL_FAVOURED the minimum. Retain both
   dice in the result for display.
2. **Roll Success dice**: `dice_count` of them.
3. **Compute icon count**: number of Success dice showing the success icon. This is
   unaffected by Weary — a Weary hero still scores icons on 6s, because 6 is a solid face.
4. **Compute the total**:
   * Feat contribution: `feat_numeric(kept_face, inverted=icon_inverted)`.
   * Success contribution: for each Success die, `0 if (weary and die.is_outlined) else die.face`.
   * `total = feat_contribution + success_contribution`.
5. **Determine outcome**, in this strict precedence order:

   | Order | Condition | Outcome |
   |---|---|---|
   | 1 | `magical_success` is True | `SUCCESS` (TN ignored) |
   | 2 | kept Feat face is the auto-success face (RUNE normally, EYE if inverted) | `SUCCESS` (TN ignored) |
   | 3 | `eye_is_auto_failure` and kept Feat face is the auto-failure face (EYE normally, RUNE if inverted) | `FAILURE` (TN ignored) |
   | 4 | `target_number is None` | `NO_TN` |
   | 5 | `total >= target_number` | `SUCCESS` |
   | 6 | otherwise | `FAILURE` |

   **Rule 2 outranks rule 3.** A Miserable hero who rolls the auto-success face succeeds:
   the two conditions cannot co-occur because they key off opposite faces of the same die,
   so the ordering is defensive rather than load-bearing — but state it explicitly so no
   implementer invents a conflict.

   Note that `magical_success` bypasses the TN but the Success dice are still rolled,
   because the icon count still matters for degree of success.

6. **Degree of success** (only meaningful when outcome is `SUCCESS`):

   | icons | degree |
   |---|---|
   | 0 | `SUCCESS` (bare) |
   | 1 | `GREAT_SUCCESS` |
   | ≥2 | `EXTRAORDINARY_SUCCESS` |

### 2.3.5 RollResult

```python
@dataclass(frozen=True, slots=True)
class RollResult:
    request: RollRequest
    feat_dice: tuple[FeatValue, ...]     # 1 or 2 entries, as rolled
    kept_feat: FeatValue
    success_dice: tuple[SuccessDie, ...]
    total: int
    outcome: Outcome                     # SUCCESS | FAILURE | NO_TN
    degree: Degree
    icons: int
    auto: AutoKind | None                # AUTO_SUCCESS | AUTO_FAILURE | MAGICAL

    @property
    def succeeded(self) -> bool: ...

    def magnitude(self, base: int = 1) -> int:
        """Pattern P3: base, plus 1 per icon. Zero if the roll failed."""
        return (base + self.icons) if self.succeeded else 0

    def tier(self) -> int:
        """Pattern P4: 0 on failure, 1 on bare success, 2 with one icon, 3 with two or more."""
        if not self.succeeded: return 0
        return 1 if self.icons == 0 else (2 if self.icons == 1 else 3)

    def feat_numeric_value(self, shift: int = 0) -> FeatValue:
        """Kept Feat face for table lookup and Piercing Blow checks, with `shift` applied
        to numeric faces only (used by the Pierce special damage and by
        Ponder Storied and Figured Maps)."""
```

`magnitude()` and `tier()` exist so that no subsystem writes `1 + result.icons`. When you
find yourself typing that expression, you are duplicating P3.

### 2.3.6 Icon spending

Icons have two distinct uses that must not be conflated:

* **Degree of success** — passive; reading how well the roll went.
* **Spending** — active; each icon buys one special result (Skill Special Successes,
  combat Special Damage).

Spending does not reduce the numeric total and does not lower the degree. A great success
remains a great success after its icon is spent. Model this with an `IconBudget`:

```python
@dataclass(slots=True)
class IconBudget:
    available: int
    spent: list[str] = field(default_factory=list)   # option ids, repeats allowed
    def spend(self, option_id: str, count: int = 1) -> None: ...
    @property
    def remaining(self) -> int: ...
```

The **Skill Special Success** options (non-combat) are content-driven, loaded from the
pack as `special_successes[]`. Their mechanical shapes, which the engine must support:

| Shape | Engine handling |
|---|---|
| Convert another participant's failed roll to a success | `contest`/group-roll aware; needs a reference to the sibling `RollResult` |
| Score one additional success toward a Resistance total | `ResistanceContest.add_successes(+1)` |
| Gain additional information | narrative; engine records the flag only |
| Achieve the goal silently / without attracting attention | narrative flag |
| Halve the time taken | narrative flag; if the action has a modelled duration, halve it |
| Widen the number of subjects influenced by one per icon | narrative flag with a numeric count |

Only the first two have mechanical force in the engine. Implement those; represent the
rest as recorded flags on the outcome so a UI can display them.

### 2.3.7 Magical success

A character with an applicable magical talent or artefact may spend 1 Hope **before** a
Skill roll to make it a magical success: the action passes regardless of TN. The Success
dice are still rolled to determine degree.

Engine rules:
* `magical_success` is only legal when the `EffectBus` reports at least one effect
  granting magical success for that specific ability (`MAGICAL_SUCCESS_ELIGIBLE` hook).
  Otherwise raise `RuleViolation`.
* It costs 1 Hope. That Hope expenditure is **separate from** the Hope-for-bonus-dice
  expenditure; a character may do both on the same roll, spending 2 Hope total.
* Sources include: the Elven cultural blessing (Skill rolls generally, when not
  Miserable), certain Cultural Virtues (restricted to named Skills), and Marvellous
  Artefacts / Wondrous Items (restricted to the item's Blessing Skill).
* Some sources carry a condition — e.g. "if you are not Miserable". The condition lives in
  the effect, not in `resolve()`.

Magical success also increases Eye Awareness when it is an overt display (see `14.3`).

### 2.3.8 Target Numbers

```python
def attribute_tn(score: int, *, short_campaign: bool = False) -> int:
    base = 18 if short_campaign else 20
    return base - score
```

* `STRENGTH TN` governs all STRENGTH Skills and all attack rolls.
* `HEART TN` governs all HEART Skills and VALOUR rolls.
* `WITS TN` governs all WITS Skills and WISDOM rolls.

The `short_campaign` flag (deriving TNs from 18) is an official option for short
campaigns and one-shots. It is a campaign-level setting on `Campaign.options`, applied
when the `AttributeSet` computes TNs — never applied ad hoc at a call site.

Attribute TNs are a derived stat and therefore pass through `MODIFY_ATTRIBUTE_TN`
(`04`), which is how the *Prowess* Virtue (lower one Attribute TN by 1) and the
*Weakening* Curse (raise one TN by 2) work.

Attack roll TNs are **not** Attribute TNs directly; see `08.5`.

### 2.3.9 Repeating a roll

The default is one attempt per action. A failed Skill roll may be reattempted by the same
hero only with a *different* Skill, representing a different approach, and only with LM
permission. The engine models this as:

```python
@dataclass(slots=True)
class AttemptLedger:
    entries: dict[TaskId, set[AbilityId]]
    def may_attempt(self, task: TaskId, ability: AbilityId) -> bool: ...
```

`may_attempt` returns False if that ability has already been tried for that task. It does
not enforce LM permission — that is an input. The ledger is scoped to a task, not to the
session, and is discarded when the task resolves.

---

## 2.4 Worked examples (also test vectors — see `19`)

**E1 — plain Skill roll.** Rating 2, TN 13, not Weary, not Miserable, normal policy.
Scripted dice: Feat `8`, Success `4`, `5`. Total `8+4+5 = 17 ≥ 13` → SUCCESS, 0 icons,
degree SUCCESS.

**E2 — Weary.** Same as E1 but `weary=True`, Success dice `2`, `5`. Total
`8 + 0 + 5 = 13 ≥ 13` → SUCCESS. The outlined `2` contributed nothing.

**E3 — Miserable + eye.** `eye_is_auto_failure=True`, Feat `EYE`, rating 3, Success `6,6,5`.
Precedence rule 3 fires → FAILURE despite two icons. `icons` is still reported as 2, but
`succeeded` is False so `magnitude()` returns 0.

**E4 — Favoured.** `favoured_sources=("skill:favoured",)`, Feat dice `3` and `RUNE`.
Kept face is `RUNE` (highest under the non-inverted ordering) → AUTO_SUCCESS, TN ignored.

**E5 — Favoured and Ill-favoured.** Two favoured sources, one ill-favoured source ⇒
policy NORMAL, one Feat die rolled.

**E6 — Adversary with inversion.** `icon_inverted=True`, Feat `RUNE`. Under inversion
`RUNE` reads as 0 and is the auto-failure face — but adversaries are never Miserable, so
precedence rule 3 does not fire; the roll proceeds with a Feat contribution of 0 and is
compared to the TN normally.

**E7 — Dice floor.** Rating 1, `penalty_dice=3`, `bonus_dice=0` ⇒ `dice_count = 0`.
Only a Feat die is rolled.
