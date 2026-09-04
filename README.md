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

Build steps 1–6 of the order in `00-README.md` §5: `tor.dice`, `tor.tables`, `tor.rolls`,
`tor.model`, `tor.effects`, `tor.content`. The rules subsystems, session layer, and CLI
are not yet implemented.

## Development

```bash
pip install -e ".[dev]"

pytest                                                  # the suite
pytest --cov=tor --cov-branch --cov-report=term-missing # coverage
mypy tor                                                # strict
ruff check . && ruff format --check .
lint-imports                                            # the layering contract
```

`tor.rolls` and `tor.effects` are held to 100% branch coverage — that is where the rules
live (`19-testing.md`).

Tests use `ScriptedRandomness`, which **raises when its dice queue runs dry**. That is
deliberate: a test that scripts two Success dice and sees the engine ask for three has
caught a real bug. Assert `rng.exhausted` at the end of a rules test — rolling too few
dice is as much a bug as rolling too many.
