# The One Ring 2e — Rules Engine Specification

A complete implementation specification for a set of Python modules that model the
mechanical rules of *The One Ring* (2nd edition, Free League / Sophisticated Games).

**Audience:** a developer or coding agent implementing the system from scratch.
**Deliverable:** a package `tor` of independently usable modules plus JSON content packs.

---

## 1. What this spec covers

Everything in the rulebook that resolves to a number, a state change, or a decision
procedure:

| Area | Doc |
|---|---|
| Layering, boundaries, DRY catalogue | `01-architecture.md` |
| Dice and roll resolution | `02-core-dice-and-rolls.md` |
| Domain entities and derived stats | `03-domain-model.md` |
| The effect/hook system (the DRY spine) | `04-effects-and-hooks.md` |
| Content pack schemas | `05-content-schemas.md` |
| Character creation | `06-character-creation.md` |
| Resources, conditions, resting | `07-resources-and-conditions.md` |
| Combat | `08-combat.md` |
| Councils and the shared Resistance contest | `09-council-and-contests.md` |
| Journey | `10-journey.md` |
| Shadow, madness, flaws | `11-shadow.md` |
| Adversaries | `12-adversaries.md` |
| Treasure and magical items | `13-treasure.md` |
| The Eye of Mordor | `14-eye-of-mordor.md` |
| Fellowship Phase and progression | `15-fellowship-and-progression.md` |
| Skill Endeavours, risk, sources of injury | `16-endeavours-and-injury.md` |
| Session/campaign orchestration and persistence | `17-session-and-persistence.md` |
| Public API, CLI, error model | `18-api-and-errors.md` |
| Test strategy and concrete vectors | `19-testing.md` |
| Canonical identifier registry | `20-identifiers.md` |

What it does *not* cover: the setting gazetteer (Eriador geography, named NPC
biographies, adventure text). Those are narrative content, not mechanics. Where a
mechanic references them (e.g. Patrons, region types), the spec defines the schema and
the mechanical fields only.

## 2. Content-data policy — read this before you start

The rulebook's text, flavour descriptions, and full data tables are copyrighted.
This spec deliberately separates:

* **The engine** — algorithms, formulas, state machines, hook signatures. Fully
  specified here. Rules systems are not themselves copyrightable, and everything
  mechanical is described here in the spec's own words.
* **The content** — the actual list of cultures, virtues, weapon stat lines, adversary
  stat blocks, patrons, names, and all flavour prose. These live in JSON content packs
  under `content/`, and are **transcribed by the implementer from their own licensed
  copy of the book**.

This is not only the legally clean split, it is the correct architecture. The engine
must run with zero hardcoded content. Ship a small **example pack** (`content/example/`)
using invented, non-canonical entries that exercise every schema field, so tests can run
without any licensed data present.

Where this spec quotes a specific number (e.g. "Endurance = STRENGTH + 20"), it does so
because the formula *is* the mechanic and cannot be described otherwise. Where it lists
names (e.g. `dour_handed`), it does so to fix identifiers. Neither substitutes for the
book.

## 3. Non-goals and explicit design boundaries

The engine **adjudicates**; it does not **decide**. Many rules require a human
(the Loremaster) to make a judgement call: setting a council's Resistance, choosing a
Risk level, deciding whether a Distinctive Feature applies, ruling a Misdeed. The engine
must:

* expose these as explicit typed inputs (`ResistanceLevel.BOLD`, `RiskLevel.HAZARDOUS`),
* never guess a default that hides the decision,
* record who supplied the value in the event log.

Similarly the engine does **not** implement tactical AI for adversaries. It exposes legal
action sets and validates chosen actions.

## 4. Global engineering requirements

1. **Python ≥ 3.11.** Use `dataclasses`, `enum.StrEnum`, `typing.Protocol`,
   `typing.Self`. Full type annotations; `mypy --strict` must pass.
2. **No global mutable state.** No module-level RNG, no singletons. Every entry point
   takes its dependencies explicitly.
3. **Deterministic by injection.** All randomness goes through a `Randomness` protocol
   (`02`). Every test seeds or stubs it.
4. **Pure core, imperative shell.** `tor.dice`, `tor.rolls`, `tor.model`, `tor.effects`,
   `tor.tables` contain no I/O. Loading, logging, and persistence live in
   `tor.content`, `tor.session`.
5. **Immutable results, explicit mutation.** Resolution functions return result objects
   describing what *would* happen; a separate `apply()` step mutates state. This makes
   every subsystem testable without a live character and makes undo trivial.
6. **Every state change emits an event** (`17`). The event log is the source of truth for
   replay and for the CLI transcript.
7. **Modules are independently importable.** `import tor.rules.journey` must not drag in
   combat. Cross-subsystem needs go through `tor.effects` and `tor.model`, never through
   direct subsystem-to-subsystem imports. Enforce with an import-linter contract.

## 5. Build order

Implement strictly in this order; each stage is fully testable before the next.

```
1. tor.dice          →  2. tor.tables        →  3. tor.rolls
4. tor.model         →  5. tor.effects       →  6. tor.content
7. tor.rules.resources                        →  8. tor.rules.creation
9. tor.rules.contest (shared Resistance engine)
10. tor.rules.shadow →  11. tor.rules.combat  →  12. tor.rules.journey
13. tor.rules.council (thin over contest)     →  14. tor.rules.endeavour (thin over contest)
15. tor.rules.treasure, tor.rules.eye, tor.rules.progression, tor.rules.fellowship
16. tor.session      →  17. tor.api, tor.cli
```

## 6. Package layout

```
tor/
  __init__.py
  dice.py                # Feat/Success dice, Randomness protocol
  tables.py              # generic die→row lookup tables
  rolls.py               # RollRequest / RollResult / resolve()
  model/
    __init__.py
    ids.py               # NewType id aliases
    attributes.py        # Attribute, AttributeSet, TN derivation
    abilities.py         # Skill, CombatProficiency, Valour, Wisdom
    hero.py              # Hero aggregate
    company.py           # Company aggregate, Fellowship pool
    gear.py              # Weapon, Armour, Shield, UsefulItem, Mount, Load
    adversary.py         # Adversary aggregate
    conditions.py        # Condition flags
  effects/
    __init__.py
    hooks.py             # hook name registry + payload dataclasses
    bus.py               # EffectBus: registration, ordering, dispatch
    library.py           # built-in effect implementations keyed by content id
  content/
    __init__.py
    schema/              # JSON Schema files
    loader.py            # ContentPack loading + validation
    pack.py              # ContentPack object
  tables_data/           # (nothing hardcoded; here only for generic table defs)
  rules/
    resources.py
    creation.py
    contest.py           # shared Resistance/time-limit engine
    combat/
      __init__.py
      state.py           # CombatState, Side, Combatant
      sequence.py        # onset, volleys, rounds
      engagement.py
      attack.py          # attack roll → damage → piercing → protection → wound
      tasks.py           # combat tasks
    council.py
    journey.py
    endeavour.py
    injury.py
    shadow.py
    treasure.py
    eye.py
    progression.py
    fellowship.py
  session/
    __init__.py
    campaign.py          # Campaign, AdventuringPhase, FellowshipPhase, Session
    events.py            # Event types + EventLog
    store.py             # JSON persistence
  api.py
  cli.py
content/
  example/               # non-canonical pack used by tests
  README.md              # how to author a licensed pack
tests/
```

## 7. Terminology conventions used throughout

* **PC** — Player-hero. **LM** — Loremaster. **NPC/adversary** used per the book.
* `TN` — Target Number.
* Icons: `RUNE` (the highest Feat die face), `EYE` (the lowest Feat die face),
  `SUCCESS_ICON` (the Success die's sixth face). See `02` for why these neutral names.
* "gain (1d)" / "lose (1d)" from the book map to `bonus_dice=+1` / `-1`.
