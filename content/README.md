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
every file and field; the JSON Schemas enforce them. Then:

```bash
python -c "from pathlib import Path; from tor.content import load_pack; \
           load_pack(Path('content/mypack'))"
```

Loading validates eagerly and totally: a missing field, a reference to an id that does not
exist, or a lookup table with a gap in its key domain raises `ContentError` naming the
file, a JSON pointer to the offending node, and the entity id. A malformed pack fails at
load, never mid-session.

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
