# 13 — Treasure and Magical Items

Module: `tor.rules.treasure`.

---

## 13.1 Treasure points

Treasure is an abstract measure of wealth: silver, gold, gems, and valuables. It has three
mechanical consequences:

1. It drives **Standard of Living** (`03.7`) — as Treasure rises, the tier rises with it.
2. It contributes to **Load** — 1 point of Load per point of *carried* Treasure (`07.4`).
3. It is **spent** on the *Raise an Heir* undertaking (up to 5 points per use).

Recall the carried/stored distinction (`07.4`): Standard of Living derives from total
holdings, Load only from what is carried. Heroes may cache treasure where they found it,
intending to return; pack animals carry up to 10 points of Treasure Load each.

## 13.2 Hoards

Any source of Treasure found while exploring caverns, lairs, and old ruins is a **Hoard**.
Hoards come in three grades by how rich, old, or unspoilt they are:

| Grade | Treasure value | Magical Treasure rolls |
|---|---|---|
| Lesser | roll **1** Success die | roll the Feat die **2** times |
| Greater | roll **2** Success dice | roll the Feat die **4** times |
| Marvellous | roll **3** Success dice | roll the Feat die **6** times |

**The Treasure value is multiplied by the number of heroes in the Company.**

```python
def roll_hoard_value(grade: HoardGrade, company_size: int, rng: Randomness) -> int:
    dice = HOARD_DICE[grade]                    # 1, 2, or 3
    return sum(rng.success().face for _ in range(dice)) * company_size
```

Pacing guidance worth surfacing in a UI: on average a Company should find at most **two
Hoards in one Adventuring Phase** — a lesser and a greater, or a single marvellous one.

The resulting value is divided among the heroes as they see fit; the engine takes an
explicit allocation and validates that it sums to the total.

```python
def distribute_hoard(total: int, allocation: Mapping[HeroId, int]) -> list[Event]:
    if sum(allocation.values()) != total:
        raise RuleViolation("hoard allocation must account for every Treasure point")
```

## 13.3 Magical Treasure rolls

After the Treasure is shared out, determine whether anything exceptional lies among the
silver and gold. Roll a Feat die the number of times given by the hoard grade.

> **Each rune and each eye result corresponds to the discovery of one magical piece of
> treasure.** Numeric results yield nothing.

```python
def count_marks(grade: HoardGrade, rng: Randomness) -> list[FeatValue]:
    """Pattern P17. Returns the icon faces rolled — both which and how many matter."""
    rolls = [rng.feat() for _ in range(HOARD_FEAT_ROLLS[grade])]
    return [r for r in rolls if r in (FeatFace.RUNE, FeatFace.EYE)]
```

**Keep which icon produced each find.** The rune and the eye both yield an item, but a find
on an **eye** additionally taints the discovery with Greed.

For each mark, roll one Success die on the Magical Treasure table:

| Success die | Nature of the find | Shadow if found on an **eye** |
|---|---|---|
| 1–3 | **Marvellous Artefact** — an enchanted object graced by **one** Blessing | 1 (Greed) |
| 4–5 | **Wondrous Item** — an enchanted object possessing **two** Blessings | 2 (Greed) |
| 6 | **Famous Weapon or Armour** — war gear of superior make | 3 (Greed) |

Greed is resistible with a WISDOM Shadow Test (`11.4.2`).

Once the number and nature of the finds are known, the heroes agree on who keeps each
piece. Magical Treasure is **strictly individual**: heroes should never pass a found object
to a different companion, especially Famous Weapons and Armour. The engine enforces this
by rejecting transfers of magical items between heroes, with the heirloom mechanism
(`06.12`) as the sole exception.

```python
@dataclass(frozen=True, slots=True)
class HoardOutcome:
    grade: HoardGrade
    treasure: int
    finds: tuple[MagicalFind, ...]

@dataclass(frozen=True, slots=True)
class MagicalFind:
    kind: MagicalItemKind          # ARTEFACT | WONDROUS | FAMOUS_GEAR
    from_eye: bool
    shadow_points: int             # 0 unless from_eye
```

## 13.4 The Treasure Index

Finding a magic ring or a famous sword is not meant to be a matter of stumbling on an
exotic object at random. The LM prepares a **Treasure Index** — a list detailing every
magical item that can enter the campaign. When a Magical Treasure roll uncovers something,
the LM consults the Index to see exactly what has come to light.

This keeps the level of magic under control, lets the LM *time* an item's appearance to a
plot, and keeps such objects unique.

```python
@dataclass(slots=True)
class TreasureIndex:
    artefacts: list[MagicalItemDefinition]      # unlimited
    wondrous: list[MagicalItemDefinition]       # ideally 1–3 per hero
    famous_gear: list[FamousItemDefinition]     # ideally 1–3 per hero

    def draw(self, kind: MagicalItemKind, hero: Hero) -> MagicalItemDefinition | None:
        """Select the most appropriate undrawn entry, preferring items designed for this
        hero. Returns None if the list is exhausted."""
```

Marvellous Artefacts may be included freely; Wondrous Items and Famous Weapons and Armour
should be listed precisely, at roughly one to three of each per hero in the Company.
When the list runs out, the LM may create more — either deliberately or by rolling one up
on the spot with the generation tables below.

The Index is **campaign data**, not content-pack data: it is authored per campaign and
persists in the save file (`17.4`).

## 13.5 Blessings

Marvellous Artefacts and Wondrous Items carry **Blessings**. A single Blessing lets the
bearer affect all rolls made using one specific Skill.

> The bearer **gains `(2d)`** when making rolls with the Skill corresponding to the
> Blessing, and may achieve a **magical success** with it (`02.3.7`).

A Marvellous Artefact has one Blessing; a Wondrous Item has two, affecting two different
Skills.

**Generation.** Roll a Success die **twice**: the first roll selects the Skill *group*, the
second identifies the Skill within it. Repeat for a Wondrous Item's second Blessing.

This is exactly the 6×3 Skill grid from `03.3`, and it is why the group axis belongs in the
engine rather than in content:

```python
def roll_blessing_skill(rng: Randomness) -> AbilityId:
    group = SKILL_GROUPS[rng.success().face - 1]        # Personality..Vocation
    # the second die selects among the three skills in that group, 1-2 / 3-4 / 5-6
    index = (rng.success().face - 1) // 2
    return group.skills[index]
```

The Blessings tables in content additionally suggest **object types** appropriate to each
Skill — a ring, a cloak, a circlet, boots, a staff, a war-horn, a coil of rope, a musical
instrument, a mirror, a seeing-stone. These are flavour hints for the LM, carried in the
table's `value` as an `object_suggestions` array. If an item carries two Blessings, choose
whichever object type seems more appropriate.

An item's capabilities may not be apparent on discovery. A hero can learn an item's
Blessings by choosing the **Meet Patron** undertaking during a Fellowship Phase (`15.6`),
or through the **Study Magical Items** undertaking, which reveals all there is to know
about every Marvellous Artefact and Wondrous Item in the Company's possession.

## 13.6 Precious Objects

Gemstones, jewels, and ornaments whose worth lies in significance or beauty rather than
raw value.

A Precious Object's value in Treasure points is **carved out of the hoard total** the LM
has already generated — it is not additional wealth. Its purpose is narrative: a golden
crown from an ancient ruin may be worth far more than its metal to someone who recognises
it. A hero who gifts such an item to members of a folk whose tradition reaches back to its
making receives a **bonus amount for sentimental value**.

Generation uses three Success-die tables — **form**, **main material**, and
**craftsmanship** — with two of the material rows chaining into a second roll to
distinguish sub-varieties. The `LookupTable` value schema therefore needs an optional
`reroll` field:

```json
{"match": 5, "value": {"reroll": [
    {"match": [1,2], "value": "adamant"},
    {"match": [3,4], "value": "white_gem"},
    {"match": [5,6], "value": "clear_crystal"}]}}
```

Implement `LookupTable.resolve(rng)` which follows `reroll` chains to a terminal value.
One recursion, used by any table that needs it.

Craftsmanship values are drawn from the same vocabulary as Famous item craftsmanship
(`13.7`), so share the enum.

Note: a **cursed** Precious Object may be denied any sentimental value.

## 13.7 Famous Weapons and Armour

Ordinary superior craftsmanship is already covered by **Rewards** (`15.4`). Famous Weapons
and Armour are a step beyond: their qualities come from Rewards **and Enchanted Rewards**.

### 13.7.1 Design procedure

Five steps, all LM choices:

1. **Choose the item type** — a weapon, or a piece of defensive gear. An Index should only
   contain items designed for specific members of the Company; there is no place in it for
   a wondrous shield nobody will carry.
2. **Determine craftsmanship** — Elven, Dwarven, or Númenórean. This constrains both the
   Banes and the available Enchanted Rewards.
3. **Select Banes** — Elven or Númenórean items only.
4. **Attribute qualities.**
5. **Name the item.**

```python
class Craftsmanship(StrEnum):
    ELVEN = "elven"; DWARVEN = "dwarven"; NUMENOREAN = "numenorean"
```

### 13.7.2 Banes

A Bane weapon is wrought to defeat a specific enemy. Several Enchanted Rewards are
effective **only** against Bane creatures, which is why the Bane selection precedes
quality attribution.

| Craftsmanship | Bane selection |
|---|---|
| Númenórean | **two** types, chosen from: Orcs, Trolls, Wolves, Evil Men, Undead |
| Elven | **one** type, chosen from: Orcs, Wolves, Spiders |
| Dwarven | none |

Particularly ancient and rare blades may instead be wrought for the bane of the Enemy
itself, and are dangerous to all his servants and minions. Model as
`banes = ALL_SHADOW_SERVANTS`, a sentinel set.

Banes have a second, non-combat effect: such weapons are destructive to things connected
to the creatures they oppose, and would be recognised as such by them — Orcs will not dare
touch a blade forged for their bane; a sword made against giant spiders cuts through the
thickest webs. Narrative, but worth exposing as a `bane_recognition` flag on the item so a
UI can prompt.

### 13.7.3 Qualities

> A Famous Weapon or Armour should feature a **maximum of 3 qualities**, and must include a
> **minimum of one Enchanted Reward**.

Constraints the engine validates at item construction:

1. `1 <= len(rewards) + len(enchanted) <= 3`.
2. `len(enchanted) >= 1`.
3. Every Enchanted Reward is **unique** on the item — none may be applied twice.
4. Every Enchanted Reward's `craftsmanship` list includes the item's craftsmanship.
5. Every Enchanted Reward's `item_types` includes the item's type.
6. An Enchanted Reward with `requires_bane: true` may only go on an item that has Banes.
7. **Qualities sharing a descriptor cannot coexist.** Six Enchanted Rewards are enhanced
   versions of the six ordinary Rewards; an item may not carry both. This is the
   `supersedes` field (`05.5`).
8. When designing an Elven or Númenórean item, at least one Enchanted Reward with the Bane
   requirement should be present — a warning, not an error.

```python
def validate_famous_item(item: FamousItemDefinition, pack: ContentPack) -> None:
    """Raises RuleViolation listing every constraint breached, not just the first."""
```

### 13.7.4 Dormant qualities and awakening

> When a hero first uses a Famous Weapon or Armour, the item displays **only the first
> quality** listed in its Index entry. The rest are secret and **dormant**.

Order matters: the qualities listed first are the ones the owner discovers soonest.

Two ways to awaken the next one, in listed order:

**Gaining a new VALOUR rank.** Each time a hero would gain a new Reward from a VALOUR
increase, they may instead **activate one dormant quality** of a Famous item they own
(`15.4`). This is a substitution, not an addition — they do not get both.

**Visiting the Treasury of their folk.** As a Fellowship Phase undertaking, a hero may
return home to leave a piece of war gear enhanced by one or more Rewards as a **gift** to
their folk. They **trade the number of Rewards on the gifted item for the activation of an
equal number of qualities** on the Famous item.

```python
def visit_treasury(hero: Hero, gifted: ItemId, famous: ItemId) -> list[Event]:
    n = len(item(gifted).rewards)
    if n < 1: raise RuleViolation("the gifted item must carry at least one Reward")
    activate_qualities(famous, count=n)
    remove_item(hero, gifted)
```

Note the narrative consequence recorded on the event: these gifts alter the hero's identity
in the eyes of their folk — they become known as the bearer of the newly-found enchanted
item, and the gifted objects become cultural treasures.

An heir receives a Famous item with its **first quality already activated** (`06.12`).

A hero who wishes to learn more about a Famous item may visit an appropriate location and
choose **Meet Patron** during a Yule Fellowship Phase — which is how an item's name and
history are revealed.

### 13.7.5 Enchanted Reward mechanical shapes

All are effects (`04`). The shapes the engine must support, so that content can express
every one declaratively:

* **Scaled versions of ordinary Rewards** — a larger flat bonus, *or* a bonus equal to the
  bearer's **VALOUR, whichever is higher*. This "flat-or-VALOUR, take the greater" pattern
  recurs; give it a factory: `{"factory": "greater_of", "flat": 3, "stat": "valour"}`.
* **Bane-conditional variants** — a smaller bonus normally, a VALOUR-scaled bonus against a
  Bane creature. Predicate on `ctx.target.creature_types & item.banes`.
* **Piercing threshold reductions**, including "10 minus your VALOUR" against Bane
  creatures — a computed threshold, not a constant.
* **Drive drain on a hit** — 1 point, or 3 against a Bane creature; or a flat 2.
* **Company-wide Endurance recovery on a hit**, 1 point plus 1 per Success icon (pattern
  P3).
* **Automatic Wound conversion** — when a Piercing Blow lands on a Bane creature the
  target's PROTECTION roll is Ill-favoured, and if it was *already* Ill-favoured the
  Piercing Blow scores an automatic Wound instead.
* **Ignore Miserable and Weary** for a specific roll type — attack rolls, PROTECTION rolls,
  or Combat Task rolls, depending on the item.
* **Attacks against you resolve as if the attacker were Weary.**
* **Follow-up attack on a kill** — immediately attack a second engaged adversary.
* **Forced knockback** at a damage threshold of twice the target's Attribute Level.
* **Load replacement** to a fixed value (a `ReplacementContribution`, `04.2.2`).
* **Extra opening volley**, even when none are allowed, unless surprised.
* **Automatic success on rolls to avoid ambush** by a Bane creature, extended to the whole
  Company — the item's light warns them.
* **Ignore complications** on ranged attacks — wind, darkness, and similar modifiers do not
  apply.

## 13.8 Cursed items

Only **Magical Treasure** is normally cursed — a Marvellous Artefact, Wondrous Item, or
Famous Weapon or Armour. The LM may curse a heap of gold or chest of gems if they wish,
though such treasure usually just carries an ill feeling and proves hard to sell.

A cursed item is **not** an evil artefact or a device of the Enemy. It is a wonderful
object bearing a lasting trace of darkness. Its virtues are undiminished; the Curse is an
**additional** feature, structurally like a Blessing or Reward but detrimental.

```python
@dataclass(frozen=True, slots=True)
class Curse:
    id: EffectId
    trigger: CurseTrigger | None       # None => always active
    lifting_condition: str             # secret, LM-authored
    lifted: bool = False
```

**Triggers.** A Curse may not be apparent at first, activating on a specific circumstance:
leaving the area where the item was found; exposure to moonlight; the first shedding of
blood; the presence of a specific creature type; entering a dark land.

**Lifting.** The LM decides in secret how the Curse can be lifted, and it should be no small
task — the focus of an Adventuring Phase in its own right. Perhaps only at the place of the
item's forging, or by the light of the same moon under which it was made, by an ancient
spell lost to the ages, or by slaying a particular creature. Once lifted, the item reverts
to a regular magical item.

Store `lifting_condition` as LM-visible-only text; the persistence layer must support
GM-secret fields (`17.4`).

**Curse shapes** the engine must support:

| Curse | Mechanical effect |
|---|---|
| Curse of Weakness | The bearer displays the **worst Flaw of their own Shadow Path** — the fourth step. **Temporary**: it does *not* count toward succumbing (`11.7`). |
| Darken | Shadows deepen and light fails; no light source dispels it. All appropriate rolls by the bearer **lose `(1d)`**. |
| Hunted | One enemy type perceives the item when they come near. Journey events may be made to revolve around the hunt. |
| Ill-luck | An **eye on the Feat die is an automatic failure** on any roll — as though the bearer were permanently Miserable. |
| Ill-omen | Dark warnings precede the bearer; all rolls **during a Council lose `(1d)`**. |
| Malice | On any roll concerning the item — a Skill roll boosted by its Blessing, an attack roll, a PROTECTION roll for armour — the bearer **cannot spend Hope for bonus dice**. |
| Owned | The item belongs to another creature and may be trying to return to it. **In the owner's presence the item's special features are completely ineffective.** |
| Shadow Taint | The bearer's Shadow score is **augmented**: +1 for a one-Blessing artefact, +2 for a two-Blessing item, or **twice the number of Enchanted Rewards** for Famous gear. This increase **cannot be removed or healed** and persists until the Curse is lifted. |
| Weakening | One Attribute TN, chosen by the LM, is **raised by 2**. |

Two implementation notes:

* **Ill-luck** must reuse the Miserable machinery rather than duplicating it: the effect
  contributes `miserable=True` to the `RollRequest` without setting the Condition flag,
  because the hero is not actually Miserable and should not be treated as such elsewhere.
  Split the `RollRequest` field name accordingly — call it `eye_is_auto_failure` and have
  both the Miserable condition and Ill-luck set it. Update `02.3.1` naming to match.
* **Shadow Taint** is a floor on the Shadow score, not a one-off gain. Model as a
  `MODIFY_SHADOW_FLOOR` contribution consulted by `recompute_conditions`, so that removing
  Shadow in a Fellowship Phase cannot drop below it.
