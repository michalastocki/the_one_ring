# 06 — Character Creation

Module: `tor.rules.creation`.

Creation is a **staged pipeline over an immutable draft**. Each stage validates its own
inputs and returns a new draft. The hero is only materialised at the end. This shape means
a UI can walk forward and backward freely, and tests can assert on any intermediate stage.

---

## 6.1 The draft

```python
@dataclass(frozen=True, slots=True)
class HeroDraft:
    stage: CreationStage
    culture: CultureId | None = None
    attributes: AttributeSet | None = None
    skills: Mapping[AbilityId, int] = field(default_factory=dict)
    favoured: frozenset[AbilityId] = frozenset()
    proficiencies: Mapping[AbilityId, int] = field(default_factory=dict)
    features: tuple[FeatureChoice, ...] = ()
    calling: CallingId | None = None
    previous_experience_spend: Mapping[AbilityId, int] = field(default_factory=dict)
    gear: GearChoice | None = None
    useful_items: tuple[UsefulItem, ...] = ()
    starting_reward: RewardChoice | None = None
    starting_virtue: VirtueChoice | None = None
    name: str | None = None
    age: int | None = None
    heritage_favoured: AbilityId | None = None      # heirs only
    inherited_standard_of_living: StandardOfLiving | None = None   # heirs only

class CreationStage(IntEnum):
    CULTURE = 1; ATTRIBUTES = 2; SKILLS = 3; FEATURES = 4
    CALLING = 5; PREVIOUS_EXPERIENCE = 6; GEAR = 7; REWARD_AND_VIRTUE = 8
    IDENTITY = 9; COMPLETE = 10
```

Each stage function has the signature
`def stage_n(draft: HeroDraft, choice: <StageChoice>, pack: ContentPack) -> HeroDraft`,
raises `RuleViolation` on an illegal choice, and advances `stage`. A stage may be re-run
to change a choice; doing so **invalidates all later stages**, which are cleared. That
cascade is mandatory — changing culture after buying Previous Experience must not leave a
proficiency the new culture forbids.

## 6.2 Stage 1 — Culture

Input: a `CultureId`. Effects registered immediately: the culture's Cultural Blessing and,
if present, its Cultural Weakness.

One culture's blessing adds **+1 to one Attribute of the player's choice**. This is a
blessing that must apply *before* derived stats are computed, so it is expressed as a
`params: {"attribute": ...}` choice captured at this stage and applied in stage 2, not as
a runtime hook. Model it as `Culture.attribute_bonus: {"count": 1, "choose": "any"} | null`
and require the choice as part of the stage-2 input when present.

## 6.3 Stage 2 — Attributes

Two legal paths, both supported:

```python
@dataclass(frozen=True, slots=True)
class AttributeChoice:
    method: Literal["pick", "roll"]
    set_index: int | None            # 1..6, required when method == "pick"
    bonus_attribute: Attribute | None  # required iff culture grants a free +1
```

`roll` consumes one Success die from the injected `Randomness` and selects the matching
set. Both methods land on the same six-row table; the roll is not an alternative
distribution.

Then compute and freeze:
* `max_endurance = STRENGTH + culture.derived.endurance_bonus`
* `max_hope = HEART + culture.derived.hope_bonus`
* `parry = WITS + culture.derived.parry_bonus`
* the three Attribute TNs

Current Endurance and Hope start at maximum. Shadow starts at 0.

## 6.4 Stage 3 — Skills and Combat Proficiencies

Copy the culture's 18 Skill ratings verbatim. Then:

* Mark **one** Skill Favoured from the culture's two underlined choices.
* Resolve the culture's Combat Proficiency grants in order. The typical shape is: rating 2
  in one of two named proficiencies, then rating 1 in any one proficiency. A `"any"` choice
  may legally name a proficiency already granted — in which case the ratings do **not**
  stack; the higher applies. Validate and warn.

Validation:
* Exactly 18 skills present.
* Favoured count matches `favoured_skill_count`.
* No Combat Proficiency marked Favoured (I7).
* If the culture restricts weapons, a proficiency choice that no allowed weapon uses is
  legal but should emit a `CreationWarning` (not an error) — the player may have a reason.

## 6.5 Stage 4 — Distinctive Features

Choose exactly `culture.distinctive_feature_count` (normally 2) from
`culture.distinctive_feature_choices`. Register each as an effect of kind
`DISTINCTIVE_FEATURE`.

Distinctive Features have one uniform mechanical use: a player may **invoke** one to
become **Inspired** for a Skill roll, if the feature plausibly helps. Plausibility is an LM
input. Inspiration doubles the Hope bonus (`+2d` instead of `+1d`). See `02.3.3`.

The engine therefore does not need per-feature behaviour for the standard 24 — they are
all `{"factory": "inspiration_source"}`. Only the six Calling-granted features and any
feature with a bespoke rider need more.

## 6.6 Stages 5–6 — Calling and Previous Experience

**Calling** grants: two Favoured Skills chosen from the Calling's three; one additional
Distinctive Feature; a Shadow Path assignment; and makes one undertaking free for the
Company.

A Skill already Favoured from the culture may be chosen again by the Calling — it does not
double, and the engine should surface this as a `CreationWarning` so the player can
reconsider before it is wasted.

**Previous Experience**: a budget of 10 points, spent to raise Skills and Combat
Proficiencies, using the `previous_experience` ladders (`05.10`).

```python
def cost_to_raise(current: int, target: int, ladder: CostLadder) -> int:
    """Sum of the ladder cost for each level from current+1 through target."""
    return sum(ladder[level] for level in range(current + 1, target + 1))
```

Rules:
* Ratings may be bought from 0.
* Multiple levels in one ability are legal; pay each level's cost individually.
* Total spend ≤ budget. Unspent points are **lost**, not carried.
* Skill ladder tops out at rating 4; Proficiency ladder at rating 3. Attempting a higher
  target raises `RuleViolation` — the ladder simply has no entry, which is the same thing.

Worked example: raising one Skill from rating 1 to rating 4 costs `2 + 3 + 5 = 10` — the
entire budget.

## 6.7 Stage 7 — Gear

```python
@dataclass(frozen=True, slots=True)
class GearChoice:
    weapons: tuple[WeaponSelection, ...]
    armour: ItemId | None
    helm: ItemId | None
    shield: ItemId | None
```

Rules:
* **One weapon per Combat Proficiency in which the hero has a rating ≥ 1.** Not per point;
  per proficiency.
* One suit of body armour, optionally one helm, optionally one shield.
* Armour and shields with a `min_standard_of_living` require the hero's cultural Standard
  of Living to meet or exceed it.
* A culture's `allowed_weapons` / `forbidden_items` restrictions apply.
* Weapons usable in either grip default to one-handed; grip is changeable in play.

Then:
* **Useful Items** — count from the Standard of Living table. Each names a description and
  an associated Skill.
* **Mount** — quality from the Standard of Living table. The two lowest tiers get none.
  Mounts are recorded on the Company, not the hero, but derive from the hero's tier.
* **Starting Treasure** — from the Standard of Living table.
* **Travelling gear** is explicitly *not* Load-rated and need not be itemised. Model it as
  an optional free-text list with no mechanical weight.

Compute initial Load: Σ war gear Load + Treasure Load. Then run
`resources.recompute_conditions` — a heavily armoured hero from a low-Endurance culture
can legitimately start Weary, and the engine must report that rather than hide it.

## 6.8 Stage 8 — Starting Reward and Virtue

VALOUR and WISDOM both start at **1**. The hero selects one Reward and one Virtue.

* The Reward binds to a specific owned item and must satisfy that Reward's `applies_to`.
* The Virtue is any standard Virtue. Cultural Virtues are **not** available at creation —
  they require WISDOM 2.
* Virtues that raise a maximum also raise the current value (`03.4.2`).
* Virtues requiring a choice (which Skills, which Attribute) capture it now into `params`.

## 6.9 Stage 9 — Identity

Name and age. Age should fall within the culture's adventuring range; outside it is a
warning, not an error — the rulebook frames those bounds as typical, not binding.

## 6.10 Materialisation

```python
def build_hero(draft: HeroDraft, pack: ContentPack) -> Hero:
    """Requires draft.stage == COMPLETE. Runs the full invariant check (03.4.1)."""
```

Post-conditions: all invariants I1–I10 hold; `endurance == max_endurance`;
`hope == max_hope`; `shadow == 0`; `valour == wisdom == 1`; conditions recomputed.

## 6.11 Company formation

Separate from hero creation, run once per Company:

```python
def form_company(heroes: Sequence[Hero], choice: CompanyChoice, pack: ContentPack) -> Company:
```

1. **Choose a Patron** from the pack. Additional Patrons may be added in play.
2. **Choose a Safe Haven** — a named location, free text plus an optional id.
3. **Compute Fellowship rating** = number of PCs + Patron bonus + Cultural Blessing
   contributions + Cultural Virtue contributions. Set current = maximum.
4. **Choose Fellowship Focus** — each player may name one other PC. May be deferred; the
   engine accepts `None` and allows setting it at any later point.

## 6.12 Heirs

An heir is created with the same pipeline, with four documented deviations:

1. One of the retiring hero's Favoured Skills is designated **family heritage**; the heir
   receives it as a **free additional Favoured Skill**, on top of the culture and Calling
   grants.
2. The heir's starting Standard of Living is the **retiring hero's**, not the culture's.
3. Instead of the 10-point Previous Experience budget, the heir spends the **accumulated
   reserve** (10–20 points). Caps apply: Skills may not exceed rating 4, Combat
   Proficiencies may not exceed rating 3 — the same ladder ceilings.
4. **Heirlooms**: a reserve of 15+ passes one item; a reserve of 20 passes two. An heirloom
   is an item upgraded with one or more Rewards, or a Marvellous Artefact, Wondrous Item,
   or Famous Weapon or Armour. On inheritance:
   * a Famous Weapon or Armour has its **first quality activated automatically**; further
     qualities awaken normally as the heir gains VALOUR (`13.7`);
   * a Wondrous Artefact reveals its Blessings on being passed on.

Building the reserve is a Fellowship Phase undertaking, not a creation step — see `15.7`.

```python
@dataclass(slots=True)
class Heir:
    name: str
    reserve: int = 0                  # 0..20
    heritage_skill: AbilityId | None = None
    heirlooms: list[ItemId] = field(default_factory=list)

    @property
    def ready(self) -> bool: return self.reserve >= 10
```

Note the asymmetry with ordinary Rewards: a Reward-upgraded item normally **cannot** be
transferred, not even on death — but the heirloom mechanism is the explicit exception. The
engine must permit transfer only along that path and reject it elsewhere.
