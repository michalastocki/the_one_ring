# 01 — Architecture, Boundaries, and the DRY Catalogue

## 1.1 Layering

Five layers. An import may only point downward.

```
L5  shell        tor.cli, tor.api, tor.session
L4  subsystems   tor.rules.*            (combat, journey, council, shadow, ...)
L3  content      tor.content            (loaders, schemas, ContentPack)
L2  mechanics    tor.rolls, tor.effects, tor.tables
L1  domain       tor.model
L0  primitives   tor.dice
```

Additional constraint at L4: **no subsystem imports another subsystem.** `tor.rules.journey`
must not import `tor.rules.combat`. When journey needs to inflict a Wound, it calls
`tor.rules.injury` (a shared L4 leaf) or emits an event that the session layer routes.

Declare and enforce with `import-linter`:

```ini
[importlinter:contract:layers]
name = tor layering
type = layers
layers =
    tor.cli | tor.api | tor.session
    tor.rules
    tor.content
    tor.rolls | tor.effects | tor.tables
    tor.model
    tor.dice

[importlinter:contract:subsystem-independence]
name = rules subsystems are independent
type = independence
modules =
    tor.rules.combat
    tor.rules.journey
    tor.rules.council
    tor.rules.treasure
    tor.rules.eye
    tor.rules.fellowship
```

`tor.rules.resources`, `tor.rules.shadow`, `tor.rules.injury`, `tor.rules.contest` are
**shared leaves** — excluded from the independence contract because every subsystem may
depend on them, but they may depend on nothing at L4.

## 1.2 The three cross-cutting seams

Every subsystem plugs into exactly three shared things. Getting these right is what makes
the codebase DRY rather than six near-copies of the same logic.

### Seam A — `tor.rolls.resolve()`

*Every* die roll in the game funnels through one function. Skill rolls, Combat
Proficiency attack rolls, PROTECTION rolls, Shadow Tests, Marching Tests, Wound Severity,
Endurance Loss, Journey Event nature, Magical Treasure — all of them. Subsystems build a
`RollRequest`, hand it to `resolve()`, and interpret the `RollResult`.

The consequences of Weary, Miserable, Favoured, Ill-favoured, Hope spending, Inspiration,
support, bonus/penalty dice, and magical success are implemented **once**, inside
`resolve()`. No subsystem ever re-implements "outlined dice count as zero".

### Seam B — `tor.effects.EffectBus`

Cultural Blessings, Virtues, Cultural Virtues, Rewards, Enchanted Rewards, Blessings on
magical items, Curses, Fell Abilities, Flaws, and Conditions are **all the same thing**:
named packages of listeners on named hooks. There is no `if culture == "dwarf"` anywhere
in the engine.

### Seam C — `tor.tables.LookupTable`

The book resolves a dozen situations by "roll a die, read a row". Wound Severity, Endurance
Loss, Journey Events, Event Target, Magical Treasure, Precious Object generation, Hoard
value, Shadow Path steps. One generic table type covers all of them, including the rows
keyed by icon rather than by number.

## 1.3 DRY catalogue — recurring mechanical patterns

These patterns appear in ≥ 2 places in the rulebook. Each gets exactly one
implementation. When implementing a subsystem, look here first.

| # | Pattern | Where it appears | Implementation |
|---|---|---|---|
| P1 | Roll 1 Feat + N Success dice vs a TN | every roll in the game | `rolls.resolve()` |
| P2 | Two Feat dice, keep best / keep worst | Favoured, Ill-favoured | `rolls.FeatDicePolicy` |
| P3 | "reduce/increase by 1, plus 1 per Success icon" | Shadow Test reduction; First Aid injury days; end-of-journey Fatigue reduction; Short Cut | `rolls.RollResult.magnitude()` |
| P4 | "effect at tier 1 on success, tier 2 with 1 icon, tier 3 with 2+" | Intimidate Foe; Rally Comrades | `rolls.RollResult.tier()` |
| P5 | Accumulate successes to meet a Resistance under a limited number of attempts | Council; Skill Endeavour | `rules.contest.ResistanceContest` |
| P6 | Resistance grades 3 / 6 / 9 | Council (reasonable/bold/outrageous); Endeavour (simple/laborious/daunting) | `rules.contest.ResistanceLevel` |
| P7 | Spend 1 icon to trigger a special result; multiple icons stack | PC Special Damage; adversary Special Damage | `rules.combat.attack.IconBudget` |
| P8 | Spend a point of a personal pool for +1d | Hope (PCs); Hate/Resolve (adversaries) | `model.ResourcePool` + `rolls` bonus dice |
| P9 | Pool depleted ⇒ penalised | Hope 0 ⇒ cannot push; Hate/Resolve 0 ⇒ adversary is Weary | `rules.resources` |
| P10 | Region type ⇒ Favoured / normal / Ill-favoured Feat roll | Journey Event nature (Border/Wild/Dark) | `rolls.FeatDicePolicy` from `RegionType` |
| P11 | Threshold crossing flips a persistent condition | Endurance ≤ Load ⇒ Weary; Shadow ≥ Hope ⇒ Miserable | `rules.resources.recompute_conditions()` |
| P12 | Table lookup by Feat die with icon rows | Wound Severity; Endurance Loss; Journey Events | `tables.LookupTable[FeatFace]` |
| P13 | Table lookup by Success die | Event Target; Magical Treasure; Precious Objects; culture attribute sets | `tables.LookupTable[int]` |
| P14 | Cost ladder: attaining rating N costs C(N) | Previous Experience; Fellowship Phase XP | `rules.progression.CostLadder` |
| P15 | Item upgrade slot with uniqueness constraint | Rewards on gear; Enchanted Rewards on Famous items | `model.gear.UpgradeSlots` |
| P16 | "Once per round/combat/phase" budgets | Knockback; Rally Comrades; several Cultural Virtues | `effects.UsageBudget` |
| P17 | Roll a die K times, each RUNE or EYE yields a find | Magical Treasure rolls | `rules.treasure.count_marks()` |
| P18 | Derived stat = base + Σ modifiers from effects | Endurance, Hope, Parry, Load, Attribute TNs, Fellowship | `model.derive.DerivedStat` |

## 1.4 Result / apply separation

Every rule function has this shape:

```python
def resolve_<thing>(state: <ReadOnlyView>, inputs: <Inputs>, rng: Randomness) -> <Outcome>: ...
def apply_<thing>(state: <MutableState>, outcome: <Outcome>) -> list[Event]: ...
```

`Outcome` objects are frozen dataclasses that fully describe the consequence *and* carry
the `RollResult`s that produced it, so a UI can narrate and a test can assert without
re-running the RNG. `apply_` is pure state mutation plus event emission — no randomness,
no branching on dice.

This split is mandatory, not stylistic. It is what makes it possible to:
* preview an action for the player before committing,
* implement undo by replaying the log to a prior point,
* test combat arithmetic with a stubbed RNG and no character sheet.

## 1.5 Read-only views

Subsystems receive narrow protocols, not the full `Hero`:

```python
class RollingCharacter(Protocol):
    """Everything rolls.resolve() needs. Implemented by Hero and Adversary."""
    def ability_rating(self, ability: AbilityId) -> int: ...
    def target_number(self, ability: AbilityId) -> int: ...
    @property
    def conditions(self) -> ConditionSet: ...
    @property
    def effects(self) -> EffectBus: ...
```

This is why `resolve()` can serve both PCs and adversaries with one code path despite
their different sheets, and why the Feat-die icon inversion for adversaries (`12.4`) is a
single policy flag rather than a parallel implementation.

## 1.6 Error model summary

See `18` for detail. Three families:

* `ContentError` — a pack is malformed or references a missing id. Raised at load time,
  never during play.
* `RuleViolation` — an action is illegal by the rules (Rearward stance without enough
  close-combat companions; buying two ranks of the same Skill in one Fellowship Phase).
  Carries `rule_reference` naming the mechanic.
* `StateError` — the engine was driven into an impossible state (resolving a round in a
  finished combat). Indicates a caller bug.

Never raise for a *legal but unlucky* outcome. Failing a roll is a result, not an error.
