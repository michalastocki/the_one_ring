# Content packs

The engine contains no game data. Everything — cultures, callings, virtues, rewards,
weapons, armour, adversaries, patrons, tables, names — is loaded from JSON packs in this
directory and validated against the schemas in `tor/content/schema/` at load time.

## Packs here

- **`example/`** — invented, non-canonical data ("Example Folk", "Example Blade") that
  exercises every schema branch. The test suite runs exclusively against it. Committed.
- **Anything else** — your own transcription from your licensed copy of the rulebook.
  Ignored by git (see `.gitignore`); never commit it.

## Authoring a pack

Copy `example/` as a skeleton and replace the entries. `05-content-schemas.md` documents
every file and field; the JSON Schemas in `tor/content/schema/` enforce their shape.

Only `pack.json` is required. Every other file is optional, which is what lets a
supplement carry just the entries it adds. Effects may be written in any of thirteen
files, one per `EffectKind` — `virtues.json`, `cultural_virtues.json`, `rewards.json`,
`enchanted_rewards.json`, `distinctive_features.json`, `flaws.json`, `fell_abilities.json`,
`cultural_blessings.json`, `patron_benefits.json`, `item_blessings.json`, `curses.json`,
`conditions.json`, `undertaking_benefits.json` — and all land in one namespace, because
effect ids are globally unique. `skills.json` overrides Skill *display names* only: the
18-skill grid is engine knowledge, so a pack may rename a Skill but never add one.

Then:

```bash
python -c "from pathlib import Path; from tor.content import load_pack; \
           load_pack(Path('content/mypack'))"
```

Loading validates eagerly and totally, in two passes. The JSON Schemas check *shape* —
required fields, types, enumerated values, arity, and any field the format does not define,
so a typo is an error rather than a silently ignored key. Then the referential and semantic
checks of `05.1.1` run: a reference to an id that does not exist, a lookup table with a gap
in its key domain, a Cultural Virtue naming no culture. Either pass raises `ContentError`
naming the file, a JSON pointer to the offending node, and the entity id. A malformed pack
fails at load, never mid-session.

## Ids

Ids are the contract between engine, packs, and save files: `snake_case`, ASCII, and
stable forever. Renaming one is a breaking change requiring a save migration.
`20-identifiers.md` fixes the ids the engine itself knows (the 18 Skills, the 4 Combat
Proficiencies, hooks, stances, undertakings). Everything else is yours to name — see
§20.11 for the conventions.

To retire an entry, mark it `"deprecated": true` rather than deleting it, so old saves
still load.

## Supplements

`load_packs([base, supplement])` merges packs in order. A supplement may add entries
freely, but may override an existing id only if its `pack.json` declares
`"overrides": ["<id>", ...]`. A silent collision is a `ContentError` — that is what keeps
a supplement from quietly changing a base rule.
