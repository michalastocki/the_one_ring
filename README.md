# `tor` — a rules engine for The One Ring (2nd edition)

A Python implementation of the mechanical rules of *The One Ring* roleplaying game,
2nd edition (Free League / Sophisticated Games).

The specification lives in this repository as `00-README.md` through `20-identifiers.md`.
Those documents are normative: where the code and the spec disagree, the spec is right.

## The engine ships zero game data

The rulebook's text, flavour descriptions, and data tables are copyrighted. This project
separates them from the mechanics:

- **The engine** — algorithms, formulas, state machines, hook signatures. That is what
  lives in `tor/`.
- **The content** — cultures, virtues, weapon stat lines, adversary stat blocks, patrons,
  names, and all prose. Those live in JSON content packs under `content/`, and are
  **transcribed by you from your own licensed copy of the book**.

`content/example/` is a small, deliberately **non-canonical** pack of invented entries
("Example Folk", "Example Blade") that exercises every schema field. The entire test suite
runs against it, so the project is testable with no licensed data present. To play with
real data, author a pack alongside it — `content/README.md` describes how — and point the
engine at both.

## Architecture in one screen

Five layers; an import may only point downward (`01-architecture.md`).

```
L5  shell        tor.cli, tor.api, tor.session
L4  subsystems   tor.rules.*            (combat, journey, council, shadow, ...)
L3  content      tor.content            (loaders, schemas, ContentPack)
L2  mechanics    tor.rolls, tor.effects, tor.tables
L1  domain       tor.model
L0  primitives   tor.dice
```

Three seams carry the whole design, and nothing may duplicate them:

- **`tor.rolls.resolve()`** — every die roll in the game funnels through one function.
  Weary, Miserable, Favoured, Ill-favoured, Hope, support, and bonus dice are implemented
  once, inside it.
- **`tor.effects.EffectBus`** — Cultural Blessings, Virtues, Rewards, Curses, Fell
  Abilities, Flaws and Conditions are all named packages of listeners on named hooks.
  There is no `if culture == ...` anywhere in the engine.
- **`tor.tables.LookupTable`** — the dozen "roll a die, read a row" mechanics share one
  generic table type, including rows keyed by icon rather than by number.

## Status

Build steps 1–13 and 16 of the order in `00-README.md` §5 are complete: `tor.dice`,
`tor.tables`, `tor.rolls`, `tor.model`, `tor.effects`, `tor.content`, `tor.events`, all four
shared rules leaves — `tor.rules.resources`, `tor.rules.contest`, `tor.rules.injury` and
`tor.rules.shadow` — plus `tor.rules.creation`, `tor.rules.combat`, `tor.rules.journey`,
`tor.rules.council` and `content/example/`. That is the pure core, all three seams, the whole
leaf tier every later subsystem rests on, the pipeline that turns a content pack into a
playable hero and Company, the fight they get into, the road they walk to reach it, and the
hall they are heard in. Endeavour (14) onward is not yet implemented, nor are the session
layer, the API and the CLI.

Steps 8 and 9 were taken **out of order**: `tor.rules.contest` and `tor.rules.injury`
landed before `tor.rules.creation`, so most of the shared-leaf tier was complete before the
first subsystem was built on it.

Content packs are validated in two passes. The JSON Schemas in `tor/content/schema/` check
shape — required fields, types, enumerated values, arity, and any field the format does not
define; `05.1.1`'s referential and semantic checks then run over the merged stack. The
schemas own everything a schema can express, so the loader holds no second implementation
of the same rule.

Deliberate deviations from the spec are recorded, each in the module that makes it:

- `tor/rolls.py` — `01.1` lists `tor.rolls`, `tor.effects` and `tor.tables` as independent
  siblings, while `02.3.1` puts `build_request` in `tor.rolls` and has it read the
  `EffectBus`. Both cannot hold, so `tor.rolls` sits one layer above `tor.effects` and the
  import contract says so. The same module narrows `RollingCharacter` to the one member
  `build_request` actually consults: `01.5`'s `effects` is unsatisfiable, because an
  `EffectBus` on `Hero` would make `tor.model` import `tor.effects`, which imports
  `tor.model.derive` — a runtime import cycle, not merely a layering breach.
- `tor/events.py` — `07.1` types every `tor.rules.resources` function `-> list[Event]`,
  while `17.4` defines `Event` in `tor.session`, above the subsystems that may not import
  it. The record lives in `tor.events`, below `tor.rules`; `seq` and `timestamp` are log
  coordinates a pure rule cannot know, so the log stamps them on append.
- `tor/rules/resources.py` — `7.4` and `7.5` write `recompute_load(hero)` and
  `recompute_conditions(hero)`, but Load needs the effect bus and the gear types, and
  `17.5` and `04.7` both forbid caching it on the `Hero`. Both take a keyword-only `ctx`.
- `tor/rules/contest.py` — `09.1`'s `evaluate` code block returns `DISASTER` for a
  scoreless contest, while the prose two lines below says the two adapters differ on
  exactly that. The shared engine returns `TOTAL_FAILURE`; the council adapter promotes it,
  because for a council "every attempt failed" *is* a Disaster.
- `tor/tables.py` — `01.3`'s DRY catalogue puts `CostLadder` in `tor.rules.progression`,
  but `06.6` spends a Previous Experience budget down the same ladder during creation, and
  `01.1` forbids one subsystem from importing another. The ladder sits at L2 instead, which
  both may read downward.
- `tor/rules/shadow.py` — `11.5`'s code block sets Shadow to 1 when a hero hardens their
  will, while its own note three lines below says "Shadow becomes exactly the scar count,
  which is at least 1". Both cannot hold for a hero who has hardened before: the block would
  leave them with three Scars and Shadow 1, breaching invariant I6. The note wins.
- `tor/rules/combat/attack.py` — `08.5` writes one `resolve_attack` that rolls the attack,
  spends the Success icons, checks the Piercing Blow and rolls PROTECTION. It cannot be one
  function: `08.6` puts a player decision strictly between the roll and the Piercing Blow
  check, because Pierce is retroactive within the same attack, and `01.4` forbids a rule
  function from stopping to ask. `roll_attack` → `resolve_attack` → `apply_attack` is the
  same split `01.4` mandates everywhere else, with the roll pulled out in front of it.
- `tor/rules/creation.py` — three, all consequences of the ones above. `06.2` registers the
  Cultural Blessing "immediately"; there is nothing to register it on until a hero exists,
  so `build_hero` takes the bus and registers everything once, in stage order. `06.3` says
  to freeze the three Attribute TNs alongside the maxima; they pass through
  `MODIFY_ATTRIBUTE_TN`, so `04.7` forbids storing them and `tor.rolls.attribute_tn`
  derives them per roll. `06.1` gives the draft one `favoured` set and one `features`
  tuple although stage 5 adds to both, which would make the mandated cascade unable to
  withdraw the Calling's grant alone; `calling_favoured` and `calling_feature` are separate
  fields, unioned at materialisation.
- `tor/rules/injury.py` — `01.1` names injury as the module a subsystem calls to inflict a
  Wound, and `04.7` forbids combat, journey and the sources of injury each keeping their own
  copy of `08.8`. So `wound_hero` mutates and emits an event, against the grain of the rest
  of the module, and injury depends on `tor.rules.resources` after all: checking the Wounded
  box drops a Dying hero's Endurance to zero, which only `resources` may write.
- `tor/rules/journey.py` — four. `10.1` declares a `TerrainKind` enum identical to `04.4`'s
  `Terrain`, which would stop a hex's terrain from reaching an `Environment`; the name
  aliases the type. `10.2` types `validate_roles(assignment, bus)` with one bus, but
  `JOURNEY_ROLE_LIMITS` is carried by a Cultural Virtue permitting **its holder** several
  roles, so the question is per hero and takes the context. `10.3.2` writes one mutating
  `step`; it is split into `resolve_step` → `apply_step` per `01.4`, as `08.5`'s attack was.
  And `10.4.2` gives `MODIFY_JOURNEY_EVENT_ROLL` two meanings the roll pipeline does not
  have — a numeric contribution shifts the Feat die *result* rather than adding a die, and a
  Favoured flag *replaces* the region's policy rather than cancelling against it — so it is
  the one per-subsystem roll hook `build_request` does not fold.

- `tor/rules/council.py` — `18.3` types `begin_council` as returning the
  `ResistanceContest` itself, which cannot carry the goal, the audience or its attitude;
  those outlive an attempt and none belongs on the shared machine. A `Council` holds them
  with the contest under `.contest`, which is what the API facade returns. `09.2.3` also
  says `MODIFY_COUNCIL_ATTEMPTS` raises "the maximum number of Skill rolls a hero may
  attempt", but `09.1`'s machine has one shared budget and no per-hero cap to raise; the
  hook is collected from every participant and summed, so a Virtue letting its holder
  attempt one more roll gives the Company one more attempt.

Two of the spec's own self-contradictions are resolved in `tor/rules/journey.py` rather than
deviated from. `10.2` says a hero holding several roles needs an effect's permission and then
says a Company of fewer than four *must* double up; necessity wins where it applies. `10.7`'s
code block drops the mounted halving whenever a forced march is also declared, while its
prose says to apply the halving afterwards and to flag the combination for the Loremaster —
the prose wins, and the flag is a `Warning_`.

`tor/rules/council.py` resolves a third, already half-settled in `tor/rules/contest.py`:
`09.1`'s `evaluate` block returns `DISASTER` for a scoreless contest while its prose says the
two adapters differ on exactly that. The shared engine returns `TOTAL_FAILURE`; the council
adapter promotes it, because `09.2.5` gives a council no row for "every attempt failed" short
of being seen as a threat. An endeavour that merely ran out of time is not.

## Development

```bash
pip install -e ".[dev]"

pytest                                                  # the suite
pytest --cov=tor --cov-branch --cov-report=term-missing # coverage
mypy tor                                                # strict
ruff check . && ruff format --check .
lint-imports                                            # the layering contract
```

`tor.rolls`, `tor.effects` and `tor.rules` are held to 100% branch coverage — that is where
the rules live (`19-testing.md`).

Tests use `ScriptedRandomness`, which **raises when its dice queue runs dry**. That is
deliberate: a test that scripts two Success dice and sees the engine ask for three has
caught a real bug. Assert `rng.exhausted` at the end of a rules test — rolling too few
dice is as much a bug as rolling too many.
