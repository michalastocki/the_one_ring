# 03 — Domain Model

Module: `tor.model`. Pure data and derivation. No dice, no I/O, no rules procedures.

---

## 3.1 Identifier types

`tor.model.ids` defines `NewType` aliases so that ids are not interchangeable strings:

```python
HeroId       = NewType("HeroId", str)
CultureId    = NewType("CultureId", str)
CallingId    = NewType("CallingId", str)
AbilityId    = NewType("AbilityId", str)   # skill, combat proficiency, VALOUR, WISDOM
EffectId     = NewType("EffectId", str)
ItemId       = NewType("ItemId", str)
AdversaryId  = NewType("AdversaryId", str)
PatronId     = NewType("PatronId", str)
```

Canonical id spellings are fixed in `20-identifiers.md`. Ids are `snake_case`, ASCII,
stable forever — content packs and save files key off them.

## 3.2 Attributes

Three attributes: STRENGTH, HEART, WITS. Range 1–8 in practice (culture tables produce
2–7; the Rangers' blessing can add 1).

```python
class Attribute(StrEnum):
    STRENGTH = "strength"
    HEART    = "heart"
    WITS     = "wits"

@dataclass(frozen=True, slots=True)
class AttributeSet:
    strength: int
    heart: int
    wits: int
    def score(self, a: Attribute) -> int: ...
```

TN derivation lives in `rolls.attribute_tn` (`02.3.8`); `AttributeSet` does not compute
TNs itself, because TNs are modifiable by effects and therefore belong to the derived-stat
pipeline on `Hero`.

## 3.3 Abilities

**18 Skills**, in a fixed 6×3 grid. Column = governing Attribute. Row = "skill group".

| Group | STRENGTH | HEART | WITS |
|---|---|---|---|
| Personality | AWE | ENHEARTEN | PERSUADE |
| Movement | ATHLETICS | TRAVEL | STEALTH |
| Perception | AWARENESS | INSIGHT | SCAN |
| Survival | HUNTING | HEALING | EXPLORE |
| Custom | SONG | COURTESY | RIDDLE |
| Vocation | CRAFT | BATTLE | LORE |

Both the column (which TN applies) and the group (used by the Blessings generation tables,
`13.5`) are structural and belong in the engine, not in a content pack. Encode as a
frozen mapping in `tor.model.abilities`:

```python
SKILLS: Mapping[AbilityId, SkillMeta]      # attribute + group for each of the 18
```

**4 Combat Proficiencies**: AXES, BOWS, SPEARS, SWORDS. All roll against STRENGTH TN.
They are structurally distinct from Skills in three ways, and the model must enforce all
three:

1. They **cannot be Favoured**. `Hero.set_favoured()` raises `RuleViolation` for a CP.
2. They are improved only with **Adventure points**, never Skill points.
3. They use a **different cost ladder** during character creation (`06.6`).

**Brawling** is not a Combat Proficiency. It is an attack *mode* using unarmed strikes,
thrown stones, daggers, cudgels, clubs, and improvised weapons. Its dice count is the
character's **highest** Combat Proficiency rating, and the roll takes `−1d`. Represent it
as `AbilityId("brawling")` resolved dynamically:

```python
def brawling_rating(hero: Hero) -> int:
    return max(hero.proficiency(p) for p in COMBAT_PROFICIENCIES)
```

**VALOUR** and **WISDOM** are ranked 1–6, roll against HEART TN and WITS TN respectively,
and are bought with Adventure points. They are ability-like for rolling purposes, so
`AbilityId` covers them, but they have their own progression rules (`15`).

Rating range for all abilities: 0–6. Enforce as an invariant.

## 3.4 Hero

```python
@dataclass(slots=True)
class Hero:
    id: HeroId
    name: str
    culture: CultureId
    calling: CallingId
    age: int

    attributes: AttributeSet
    skills: dict[AbilityId, int]              # all 18 present, 0..6
    favoured_skills: set[AbilityId]
    proficiencies: dict[AbilityId, int]       # all 4 present, 0..6
    valour: int = 1
    wisdom: int = 1

    endurance: int = 0                        # current
    hope: int = 0                             # current
    shadow: int = 0
    shadow_scars: int = 0
    fatigue: int = 0
    injury_days: int = 0                      # days until a Severe injury mends

    conditions: ConditionSet = field(default_factory=ConditionSet)

    distinctive_features: list[EffectId]
    flaws: list[EffectId]
    virtues: list[EffectId]
    rewards: list[RewardInstance]             # a Reward is bound to a specific item
    shadow_path: EffectId | None = None       # set by Calling
    shadow_path_step: int = 0                 # 0..4

    gear: Gear
    useful_items: list[UsefulItem]
    treasure: int = 0
    standard_of_living: StandardOfLiving

    skill_points: int = 0
    adventure_points: int = 0

    heir: Heir | None = None
    fellowship_focus: HeroId | None = None
```

### 3.4.1 Invariants (assert on every mutation)

| # | Invariant |
|---|---|
| I1 | `0 <= rating <= 6` for every skill and proficiency |
| I2 | `1 <= valour <= 6`, `1 <= wisdom <= 6` |
| I3 | `0 <= endurance <= max_endurance` |
| I4 | `0 <= hope <= max_hope` |
| I5 | `0 <= shadow <= max_hope` — Shadow can never exceed maximum Hope; excess gain is discarded, not carried |
| I6 | `shadow_scars <= shadow` — scars are a subset of the Shadow total |
| I7 | `favoured_skills ⊆ SKILLS` — no Combat Proficiency may be Favoured |
| I8 | `0 <= shadow_path_step <= 4`; `len(flaws) == shadow_path_step` |
| I9 | `treasure >= 0` |
| I10 | Each Reward id appears at most once per item (`15.4`) |

I5 is easy to get wrong and has real consequences: it is the ceiling that makes the
"Shadow reaches maximum Hope" trigger reachable but not exceedable.

### 3.4.2 Derived stats

All derived stats follow pattern P18: a culture-supplied base, then effect modifiers.

```python
@dataclass(frozen=True, slots=True)
class DerivedStat:
    base: int
    modifiers: tuple[Modifier, ...]      # each carries source EffectId and delta
    @property
    def value(self) -> int: ...
```

| Stat | Base | Notes |
|---|---|---|
| max Endurance | `STRENGTH + culture.endurance_bonus` | bonus is a per-culture constant, in the 18–22 range |
| max Hope | `HEART + culture.hope_bonus` | bonus is a per-culture constant, in the 6–10 range |
| Parry | `WITS + culture.parry_bonus` | bonus is a per-culture constant, in the 10–14 range |
| STRENGTH/HEART/WITS TN | `20 − score` (or 18 in short-campaign mode) | |
| Load | Σ war gear Load + Treasure Load + Fatigue | see `07.4` |
| Fellowship rating | company-level, see `3.6` | |

The three culture constants are content-pack data (`05.2`), not engine constants.
Different cultures use different values; the engine must never assume one.

Effects that feed these:
* *Hardiness* Virtue → `MODIFY_MAX_ENDURANCE +2` (repeatable — the Virtue may be taken
  more than once).
* *Confidence* Virtue → `MODIFY_MAX_HOPE +2`.
* *Nimbleness* Virtue → `MODIFY_PARRY +1`.
* *Prowess* Virtue → `MODIFY_ATTRIBUTE_TN −1` on a chosen Attribute.
* Various Cultural Virtues → `MODIFY_MAX_HOPE +1`.
* *Weakening* Curse → `MODIFY_ATTRIBUTE_TN +2` on a chosen Attribute.

**Raising a maximum raises the current value by the same amount** at the moment the
modifier is added. Raising max Endurance by 2 gives 2 Endurance now. Implement in
`progression.apply_virtue`, not in the derived-stat pipeline, so that recomputation is
idempotent.

**Shield Parry is tracked separately** from the Parry box. A shield can be smashed
(`08.7`), so the engine must be able to remove the shield's contribution without touching
base Parry. `Hero.parry_total(context)` = `parry.value + shield_parry(context)`, where
shield contribution may be doubled in the opening-volley context (`08.3`).

## 3.5 Gear

```python
@dataclass(frozen=True, slots=True)
class WeaponType:
    id: ItemId
    damage: int
    injury_one_handed: int | None       # None => weapon has no Injury rating
    injury_two_handed: int | None       # None => cannot be used two-handed
    load: int
    proficiency: AbilityId | Literal["brawling"]
    ranged: bool
    throwable: bool
    two_handed_only: bool
    can_cause_piercing_blow: bool = True
```

Notes on the weapon data model, all of which the real table exercises:

* Some weapons list **two Injury ratings**, one- and two-handed. The wielding mode is a
  runtime choice recorded on the `WeaponInstance`, and it selects the Injury rating.
* At least one weapon (unarmed) has **no Injury rating and cannot cause a Piercing Blow**
  at all. `can_cause_piercing_blow=False` must short-circuit the Piercing Blow check.
* Damage ranges roughly 1–7, Injury roughly 12–20, Load 0–4.

```python
@dataclass(frozen=True, slots=True)
class ArmourType:
    id: ItemId
    protection: int                     # number of Success dice on a PROTECTION roll
    load: int
    category: ArmourCategory            # LEATHER | MAIL | HEADGEAR
    min_standard_of_living: StandardOfLiving | None

@dataclass(frozen=True, slots=True)
class ShieldType:
    id: ItemId
    parry_bonus: int
    load: int
    min_standard_of_living: StandardOfLiving | None
```

Body armour and helm are **separate slots** with separate Protection values, because a
helm may be removed mid-combat to shed Load. The PROTECTION roll uses the sum. Model:

```python
@dataclass(slots=True)
class Gear:
    weapons: list[WeaponInstance]
    armour: WeaponInstance | None       # body armour slot
    helm: ArmourInstance | None
    shield: ShieldInstance | None
    def protection_dice(self) -> int:   # armour.protection + helm.protection
    def total_load(self) -> int:
```

Instances (as opposed to types) carry per-item state: Rewards applied, Enchanted Rewards,
Blessings, Curses, wielding mode, whether the item is currently dropped, and — for Famous
Weapons and Armour — which qualities are active vs. dormant (`13.7`).

```python
@dataclass(slots=True)
class WeaponInstance:
    type_id: ItemId
    name: str | None                    # named weapons
    two_handed: bool = False
    rewards: list[EffectId] = field(default_factory=list)
    enchanted: list[EnchantedQuality] = field(default_factory=list)
    dormant: list[EnchantedQuality] = field(default_factory=list)
    curse: EffectId | None = None
    craftsmanship: Craftsmanship | None = None
    banes: frozenset[CreatureType] = frozenset()
    dropped: bool = False
```

Effective Damage / Injury / Load / Parry are **derived**, never stored: base from
`WeaponType`, then `MODIFY_DAMAGE_RATING` / `MODIFY_INJURY_RATING` / `MODIFY_ITEM_LOAD` /
`MODIFY_SHIELD_PARRY` hooks fired for each applied Reward, Enchanted Reward, and Curse,
plus culture effects (e.g. the Dwarven blessing halves armour Load).

**Load rounding.** The Dwarven cultural blessing halves the Load of armour and helms
(not shields), rounding fractions **up**. Rounding direction is per-effect data; specify
it in the effect, do not assume a global convention.

### 3.5.1 Cultural weapon restrictions

At least one culture (Hobbits) is restricted to a subset of weapons and forbidden the
largest shield. Model this as content data on the culture — `allowed_weapons: [ItemId]`
(absent ⇒ no restriction) and `forbidden_items: [ItemId]` — validated at equip time by
`rules.creation.validate_gear` and at any later re-equip. Do not hardcode.

### 3.5.2 Useful Items

```python
@dataclass(frozen=True, slots=True)
class UsefulItem:
    description: str
    skill: AbilityId                     # the Skill it can benefit
```

A Useful Item grants `+1d` on a **non-combat** roll using its associated Skill, subject to
LM approval, and **only one item may benefit a given roll**. The count a hero may carry
is set by Standard of Living (`3.7`). Items may be swapped freely during any Fellowship
Phase, respecting the count.

### 3.5.3 Mounts

```python
@dataclass(slots=True)
class Mount:
    name: str
    vigour: int                          # reduces end-of-journey Fatigue
    treasure_capacity: int = 10          # points of Treasure Load
```

Mount quality is set by the owner's Standard of Living, and mounts are tracked as a shared
Company asset even though each is derived from an individual hero's status. At least one
Cultural Virtue grants a mount with a Vigour above the normal maximum regardless of
Standard of Living — so `vigour` is instance data, not a lookup.

## 3.6 Company

```python
@dataclass(slots=True)
class Company:
    heroes: list[HeroId]
    patron: PatronId | None
    safe_havens: list[str]
    fellowship_current: int
    fellowship_focus: dict[HeroId, HeroId]
    songs: list[Song]
    eye_awareness: int = 0
```

**Fellowship rating** = number of PCs in the Company, plus modifiers:
* Patron's Fellowship bonus (0, +1, or +2 depending on the Patron).
* Certain Cultural Blessings (e.g. one culture adds +1 per member of that culture present).
* Certain Cultural Virtues (+1).
* The *Strengthen Fellowship* undertaking (+1, expiring at the next Fellowship Phase).

Compute through `DerivedStat` like any other. Note the expiring modifier: `Modifier` needs
an optional `expires_at: PhaseBoundary` field.

Fellowship is a **shared pool**. Rules:
* Spending requires the agreement of all players (an input, not an engine check).
* Spent to recover Hope while resting: N Fellowship → N Hope distributed among PCs by
  player agreement.
* Spent to trigger Patron abilities.
* When the pool hits 0, no further spending.
* **Fully refreshed at the end of each gaming session** — not at the end of the phase.
  This distinction matters; wire it to the session boundary event.

Every Fellowship spend must decrement one shared counter that all heroes read. Do not
copy the value onto each `Hero`.

**Fellowship Focus** is a directed, non-reciprocal, non-exclusive relation: A may choose
B; B may choose C; two heroes may both choose B. Two mechanical consequences:
* Supporting your Focus grants them `+2d` instead of `+1d` (`02.3.3`).
* When your Focus is Wounded, suffers a bout of madness, or is otherwise seriously
  harmed, you gain 1 Shadow — and **this gain cannot be resisted with a Shadow Test**
  (`11.3`).

At least one Cultural Virtue allows a second Focus. Model `fellowship_focus` as
`dict[HeroId, list[HeroId]]` with a per-hero cap of 1, raised to 2 by that effect.

## 3.7 Standard of Living

Six tiers, ordered:

```python
class StandardOfLiving(IntEnum):
    POOR = 0
    FRUGAL = 1
    COMMON = 2
    PROSPEROUS = 3
    RICH = 4
    VERY_RICH = 5
```

`IntEnum` because tiers are compared (`>=`) for gear minimums.

Standard of Living has three mechanical consequences, all table-driven from content:

1. **Starting Treasure** — each tier maps to a starting Treasure rating (0 for the second
   tier, rising through 30 / 90 / 180 / 300+ for the upper tiers).
2. **Number of Useful Items** — from none at the lowest tier to four at the top.
3. **Mount quality** — the lowest two tiers afford no mount; higher tiers give
   progressively better Vigour.

Additionally some armour and shields carry a **minimum Standard of Living** to own.

**Standard of Living rises with Treasure.** A hero attains the next tier when their
Treasure rating reaches that tier's threshold. This is a *recomputation from Treasure*, not
an independent stored value:

```python
def standard_of_living_for(treasure: int, ladder: Sequence[tuple[StandardOfLiving, int]]) -> StandardOfLiving:
    """Highest tier whose threshold <= treasure."""
```

Store `treasure` as the source of truth; derive `standard_of_living`. The starting tier
comes from the hero's culture and sets the initial Treasure via the same ladder.

## 3.8 Conditions

```python
class Condition(StrEnum):
    WEARY     = "weary"
    MISERABLE = "miserable"
    WOUNDED   = "wounded"
```

All three are **derived or event-driven flags**, never set arbitrarily:

* **Weary** ⇔ `current_endurance <= total_load`. Recomputed after any change to
  Endurance, Load, Fatigue, or gear.
* **Miserable** ⇔ `shadow >= current_hope`. Recomputed after any change to Shadow or Hope.
* **Wounded** — set when a Wound is taken; cleared by recovery (`07.6`). Not derived.

Two further states are not `Condition` flags but must be modelled:

* **Dying** — a separate flag with a 1-hour clock (`08.9`).
* **Ill-favoured on all rolls** ⇔ `shadow == max_hope`. Derived; implemented as an effect
  contributing an ill-favoured source to every roll (`11.2`).

`recompute_conditions(hero)` is the single function that re-derives Weary and Miserable.
Call it at the end of every `apply_` function that touches Endurance, Hope, Shadow, Load,
or Fatigue. Nowhere else may write those two flags.

## 3.9 Adversary

Adversaries use a deliberately simplified sheet. See `12` for full treatment.

```python
@dataclass(slots=True)
class Adversary:
    id: AdversaryId
    name: str
    attribute_level: int                 # single value replacing three Attributes
    endurance: int
    max_endurance: int
    might: int                           # wounds to kill; attacks per round
    hate: int | None                     # minions of the Enemy
    resolve: int | None                  # non-monstrous foes
    max_drive: int
    parry: int                           # modifier added to attacker's TN
    armour: int                          # PROTECTION dice
    attacks: list[AdversaryAttack]
    fell_abilities: list[EffectId]
    distinctive_features: list[EffectId]
```

Exactly one of `hate` / `resolve` is non-None. They are mechanically identical — both are
a spendable drive pool — but semantically distinct: attacking or killing a Resolve-bearing
adversary may constitute a Misdeed and incur Shadow, whereas fighting minions of the Enemy
does not. Store as one `drive: ResourcePool` plus a `drive_kind: DriveKind` discriminator,
and expose `hate`/`resolve` as properties. One pool, one code path, one semantic tag.

## 3.10 Shared value objects

```python
@dataclass(slots=True)
class ResourcePool:
    current: int
    maximum: int
    def spend(self, n: int = 1) -> None: ...     # raises RuleViolation if insufficient
    def restore(self, n: int) -> None: ...       # clamps at maximum
    @property
    def depleted(self) -> bool: return self.current == 0
```

Used for Hope, Hate, Resolve, and Fellowship. Pattern P8/P9 live here.

```python
class RegionType(StrEnum):
    BORDER = "border"
    WILD   = "wild"
    DARK   = "dark"
```

Used by both the Journey subsystem (event severity) and the Eye of Mordor subsystem
(Hunt threshold). Defined once in `tor.model`, not in either subsystem.

```python
class CreatureType(StrEnum):
    ORCS = "orcs"; TROLLS = "trolls"; SPIDERS = "spiders"
    WOLVES = "wolves"; EVIL_MEN = "evil_men"; UNDEAD = "undead"
```

Used by Bane weapons, the Enemy-lore Distinctive Feature, and the Hatred Fell Ability.
