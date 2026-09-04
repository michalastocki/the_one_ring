# 07 — Resources, Conditions, Load, and Resting

Module: `tor.rules.resources`. A **shared leaf** — every subsystem may depend on it; it
depends on no other subsystem.

---

## 7.1 Public surface

```python
def change_endurance(hero: Hero, delta: int, source: ChangeSource) -> list[Event]: ...
def change_hope(hero: Hero, delta: int, source: ChangeSource) -> list[Event]: ...
def change_fatigue(hero: Hero, delta: int, source: ChangeSource) -> list[Event]: ...
def change_treasure(hero: Hero, delta: int, source: ChangeSource) -> list[Event]: ...
def recompute_load(hero: Hero) -> int: ...
def recompute_conditions(hero: Hero) -> list[Event]: ...
def short_rest(company: Company, heroes: Sequence[Hero]) -> RestOutcome: ...
def prolonged_rest(company: Company, heroes: Sequence[Hero], *, sheltered: bool) -> RestOutcome: ...
def spend_fellowship_for_hope(company: Company, allocation: Mapping[HeroId, int]) -> list[Event]: ...
```

Every one of these ends by calling `recompute_conditions`. Nothing else in the codebase
may write the Weary or Miserable flags.

## 7.2 Endurance

Endurance is physical stamina. It is lost to blows in combat, to any other physical harm,
and to strenuous exertion.

* Clamped to `[0, max_endurance]`.
* **At zero Endurance a hero falls unconscious** and wakes after about one hour with
  **1 Endurance** — unless they are also Wounded, in which case the Dying rules apply
  (`08.9`).
* Adversaries do not become Weary from Endurance loss; they are **taken out of combat** at
  zero Endurance (`12.3`).

## 7.3 Hope

Hope is spiritual reserve. It is spent voluntarily.

* Clamped to `[0, max_hope]`.
* **At zero Hope** a hero cannot spend Hope: no bonus dice, no support, no magical
  success, no Hope-fuelled Virtue. Attempting raises `RuleViolation`.
* Spending Hope is the mechanism behind: bonus dice on any roll (`02.3.3`), supporting
  another hero, magical success, and several Cultural Virtues.

Three recovery routes, and only three:

| Route | Amount | When |
|---|---|---|
| Spend Fellowship while the Company rests | 1 Hope per Fellowship point, distributed by agreement | Adventuring Phase, while resting |
| Fellowship Phase | HEART score (one culture's blessing makes it half HEART, rounded up); **all** Hope at Yule | each Fellowship Phase |
| Prolonged Rest at zero Hope | exactly 1 point | only when currently at zero |

The third is narrow and easy to over-implement: it applies **only** to a hero at zero
Hope, and grants exactly one point. A hero at 1 Hope taking a prolonged rest recovers no
Hope.

All Hope gains pass through `MODIFY_HOPE_RECOVERY`, which is how the Cultural Virtue that
adds +1 to any Hope regain works — it must apply to all three routes.

## 7.4 Load

```python
def recompute_load(hero: Hero) -> int:
    war_gear = sum(effective_load(item) for item in hero.gear.carried())
    treasure = treasure_load(hero)
    return war_gear + treasure + hero.fatigue
```

**What counts toward Load:**
* War gear — weapons, body armour, helm, shield — each at its effective Load after
  `MODIFY_ITEM_LOAD` (Cunning Make −2; a cultural blessing halving armour and helms
  rounding up; Ancient Cunning Make −3 or VALOUR whichever is higher; Mithril replacing
  the value outright).
* Treasure — **1 point of Load per point of Treasure**, and per individual Marvellous
  Artefact or Wondrous Item. Famous Weapons and Armour instead use their underlying war
  gear Load.
* Fatigue — temporarily raises Load one-for-one.

**What does not count:** travelling gear and ordinary personal effects. Useful Items are
not Load-rated either.

**Treasure carried vs. stored.** Heroes may cache Treasure rather than carry it; pack
animals carry up to 10 points of Treasure Load each. Model:

```python
@dataclass(slots=True)
class TreasureHoldings:
    carried: int
    on_mounts: int
    cached: int          # left behind at a location
    @property
    def total(self) -> int: ...      # drives Standard of Living
```

Standard of Living derives from `total`; Load derives from `carried` only. Conflating
these is the most likely bug in this area — a hero who caches a hoard keeps the wealth but
sheds the burden.

Dropping a helm or shield mid-combat to reduce Load is a legal **secondary action**
(`08.6`); it unregisters that item's effect source and triggers a Load recompute, which
may immediately clear Weary.

## 7.5 Conditions

```python
def recompute_conditions(hero: Hero) -> list[Event]:
    weary     = hero.endurance <= recompute_load(hero)
    miserable = hero.shadow    >= hero.hope
    ...
```

**Weary** — all Success dice showing an outlined face (1, 2, or 3) count as zero
(`02.3.4`). Note carefully: the threshold is `<=`, not `<`. Endurance exactly equal to
Load means Weary.

**Miserable** — an eye result on the Feat die means automatic failure regardless of total
(`02.3.4`, precedence rule 3).

**Wounded** — set by the Wound rules; not derived. A Wounded hero recovers Endurance
slowly (`7.6`) and risks being knocked out of a fight.

**Ill-favoured on all rolls** — when `shadow == max_hope`. Derived, implemented as an
always-on effect (`11.2`), not as a fourth condition flag.

Whenever any of these flips, emit `ConditionChanged`. The UI and the Eye of Mordor
subsystem both listen.

## 7.6 Resting

**Short rest** — at most one per day of adventuring under most circumstances; roughly one
hour of inactivity.

| Hero state | Endurance recovered |
|---|---|
| not Wounded | STRENGTH score |
| Wounded | none |

Passes through `MODIFY_SHORT_REST_RECOVERY`. One Cultural Virtue makes a short rest also
restore an amount equal to the hero's WISDOM to **every** member of the Company — note the
target is the Company, not the acting hero, so the hook payload must carry the full party.

**Prolonged rest** — normally one per day, typically a night's sleep. The LM may permit
more in a safe and comfortable place.

| Hero state | Endurance recovered |
|---|---|
| not Wounded | **all** lost Endurance |
| Wounded | STRENGTH score |

Passes through `MODIFY_PROLONGED_REST_RECOVERY`. One Cultural Virtue doubles STRENGTH for
this purpose when Wounded. One Cultural Virtue makes a short rest count as a prolonged
rest entirely (`SHORT_REST_COUNTS_AS_PROLONGED`), which the resting code must check
**before** selecting the recovery formula.

A prolonged rest also:
* restores 1 Hope **only if** the hero is at zero Hope;
* removes 1 Fatigue **only if** taken in a sheltered, safe refuge — explicitly not "on the
  road" (`10.7`).

```python
@dataclass(frozen=True, slots=True)
class RestOutcome:
    kind: RestKind
    per_hero: Mapping[HeroId, RestDelta]
    fellowship_spent: int = 0
```

## 7.7 Injury recovery

A Severe injury records a number of **days** until it mends (`08.8`). Track calendar days
in the campaign clock (`17.3`) and decrement. When it reaches zero, clear the Wounded flag
and emit `WoundHealed`.

A Moderate injury clears at the **end of the combat** in which it was received — no day
count.

A Dying hero saved by First Aid adds **10 days** to the mending time (less the days removed
by the successful HEALING roll) and takes a permanent mark, which is narrative only.
