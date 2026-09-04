# 05 — Content Pack Schemas

Module: `tor.content`.

The engine ships with **zero** game data. All of it lives in JSON content packs, validated
against JSON Schema (draft 2020-12) at load time. This is what makes the engine legally
distributable, testable without licensed data, and extensible to the game's supplements.

---

## 5.1 Pack structure and loading

```
content/<pack-name>/
  pack.json                 # manifest
  cultures.json
  callings.json
  skills.json               # optional override of display names only; structure is engine-fixed
  weapons.json
  armour.json
  shields.json
  virtues.json              # standard Virtues
  cultural_virtues.json
  rewards.json
  enchanted_rewards.json
  distinctive_features.json
  flaws.json
  shadow_paths.json
  adversaries.json
  fell_abilities.json
  patrons.json
  standards_of_living.json
  tables/
    journey_events.json
    event_target.json
    wound_severity.json
    endurance_loss.json
    sources_of_injury.json
    sources_of_dread.json
    misdeeds.json
    hoards.json
    magical_treasure.json
    precious_objects.json
    blessings.json
    eye_awareness.json
    hunt.json
    experience_costs.json
    previous_experience_costs.json
  undertakings.json
  songs.json
  names/                    # per-culture name lists
```

```python
@dataclass(frozen=True, slots=True)
class ContentPack:
    manifest: Manifest
    cultures: Mapping[CultureId, Culture]
    # ... one mapping per file
    def merge(self, other: ContentPack) -> ContentPack: ...

def load_pack(path: Path, *, strict: bool = True) -> ContentPack: ...
def load_packs(paths: Sequence[Path]) -> ContentPack: ...
```

`merge` supports supplements: a later pack may add entries and may override an entry only
if its manifest declares `"overrides": ["<id>", ...]`. A silent collision is a
`ContentError`. This keeps a supplement from quietly changing a base rule.

### 5.1.1 Load-time validation

Beyond JSON Schema, `load_pack` performs referential and semantic checks. All produce
`ContentError` with the offending file, JSON pointer, and id:

1. Every `EffectId` referenced exists in `EFFECT_REGISTRY` or is declaratively defined.
2. Every `AbilityId` is one of the 18 Skills, 4 Combat Proficiencies, `valour`, or `wisdom`.
3. Every culture defines exactly 18 skill ratings and exactly 4 proficiency entries.
4. Every `LookupTable` covers its whole key domain (all of 1–10 plus both icon faces for
   Feat tables; all of 1–6 for Success tables).
5. No two `ReplacementContribution` effects target the same hook for the same item type.
6. Every `min_standard_of_living` names a defined tier.
7. Cultural Virtues declare their `culture` and it exists.
8. Weapons declare a `proficiency` that exists or the literal `brawling`.
9. Every adversary has exactly one of `hate` / `resolve`.

Validation is **eager and total**. A malformed pack must never produce a runtime failure
mid-session.

## 5.2 `cultures.json`

```json
{
  "$schema": "../schema/cultures.schema.json",
  "cultures": [{
    "id": "example_folk",
    "name": "Example Folk",
    "cultural_blessing": {"effect": "example_blessing"},
    "cultural_weakness": {"effect": "example_weakness"},
    "standard_of_living": "common",
    "derived": {"endurance_bonus": 20, "hope_bonus": 8, "parry_bonus": 12},
    "attribute_sets": [
      {"roll": 1, "strength": 5, "heart": 7, "wits": 2},
      {"roll": 2, "strength": 4, "heart": 7, "wits": 3},
      {"roll": 3, "strength": 5, "heart": 6, "wits": 3},
      {"roll": 4, "strength": 4, "heart": 6, "wits": 4},
      {"roll": 5, "strength": 5, "heart": 5, "wits": 4},
      {"roll": 6, "strength": 6, "heart": 6, "wits": 2}
    ],
    "skills": {"awe": 1, "athletics": 1, "awareness": 0, "hunting": 2, "song": 1,
               "craft": 1, "enhearten": 2, "travel": 1, "insight": 2, "healing": 0,
               "courtesy": 2, "battle": 2, "persuade": 3, "stealth": 0, "scan": 1,
               "explore": 1, "riddle": 0, "lore": 1},
    "favoured_skill_choices": ["hunting", "battle"],
    "combat_proficiencies": [
      {"choose": ["bows", "swords"], "rating": 2},
      {"choose": "any", "rating": 1}
    ],
    "distinctive_feature_choices": ["bold", "eager", "fair", "fierce",
                                    "generous", "proud", "tall", "wilful"],
    "distinctive_feature_count": 2,
    "allowed_weapons": null,
    "forbidden_items": [],
    "languages": ["..."],
    "typical_names": {"male": [], "female": []},
    "adventuring_age": {"min": 18, "typical_retirement": 45}
  }]
}
```

Field notes:

* `attribute_sets` — exactly six entries keyed 1–6. The player either **picks one set** or
  **rolls a Success die** to select. Both are legal; the engine offers both.
* `favoured_skill_choices` — the culture underlines two Skills; the player marks **one** as
  Favoured. Array length is normally 2 but the schema permits any ≥2 with a
  `favoured_skill_count` (default 1) for supplement flexibility.
* `combat_proficiencies` — an ordered list of grants. `choose` is either an explicit array
  (pick one of these) or `"any"`. Ratings are assigned in list order. The typical shape is
  one rating-2 choice between two named proficiencies and one rating-1 free choice.
* `cultural_weakness` is optional; at least one culture in the base game has one (a cap on
  Fellowship-Phase Shadow removal).
* `derived` bonuses vary per culture across roughly 18–22 / 6–10 / 10–14. Never default them.
* `allowed_weapons: null` means unrestricted. A non-null array restricts the culture to
  those weapon ids.

## 5.3 `callings.json`

```json
{"callings": [{
  "id": "example_calling",
  "name": "Example Calling",
  "favoured_skill_choices": ["battle", "enhearten", "persuade"],
  "favoured_skill_count": 2,
  "distinctive_feature": {"effect": "example_feature", "requires_subject": false},
  "shadow_path": "example_path",
  "free_undertaking": "strengthen_fellowship"
}]}
```

Each Calling offers three Skills of which the player marks **two** as Favoured, grants one
additional Distinctive Feature, and assigns a Shadow Path.

`requires_subject: true` marks the one Calling feature that must name a creature type at
acquisition (`params: {"subject": "orcs"}`).

`free_undertaking` — a Company containing a hero of this Calling may choose that
undertaking for free each Fellowship Phase (`15.6`). Six Callings, six undertakings.

## 5.4 `weapons.json` / `armour.json` / `shields.json`

```json
{"weapons": [{
  "id": "example_blade",
  "name": "Example Blade",
  "damage": 4,
  "injury": {"one_handed": 16, "two_handed": null},
  "load": 2,
  "proficiency": "swords",
  "ranged": false,
  "throwable": false,
  "two_handed_only": false,
  "can_cause_piercing_blow": true,
  "notes": ""
}]}
```

Weapons whose Injury differs by grip supply both values; `two_handed: null` means the
weapon cannot be used two-handed, and `one_handed: null` means it cannot be used
one-handed. At least one entry in the real table has `injury: {"one_handed": null,
"two_handed": null}` together with `can_cause_piercing_blow: false` — the unarmed entry.
Schema must permit that combination.

```json
{"armour": [{"id": "example_mail", "name": "...", "protection": 3, "load": 9,
             "category": "mail", "min_standard_of_living": "common"}],
 "helms":  [{"id": "example_helm", "protection": 1, "load": 4,
             "category": "headgear", "removable_in_combat": true}]}
```

Helms are in the same file but a separate array: their Protection is **additive** to body
armour on the PROTECTION roll, and they occupy their own slot.

```json
{"shields": [{"id": "example_shield", "parry_bonus": 2, "load": 4,
              "min_standard_of_living": "common"}]}
```

## 5.5 Virtue, Reward, and ability files

All follow the same shape — a list of effect definitions:

```json
{"virtues": [
  {"id": "hardiness", "name": "Hardiness", "repeatable": true,
   "factory": "numeric_modifier",
   "params": {"hook": "MODIFY_MAX_ENDURANCE", "delta": 2}},

  {"id": "mastery", "name": "Mastery", "repeatable": true,
   "factory": "make_favoured", "requires_choice": {"skills": 2}},

  {"id": "prowess", "name": "Prowess", "repeatable": true,
   "factory": "numeric_modifier", "requires_choice": {"attribute": 1},
   "params": {"hook": "MODIFY_ATTRIBUTE_TN", "delta": -1}}
]}
```

Standard Virtues are **repeatable** — a player may take the same one more than once.
Cultural Virtues are not, and additionally require `wisdom >= 2` and matching culture:

```json
{"cultural_virtues": [
  {"id": "example_cv", "culture": "example_folk", "min_wisdom": 2,
   "factory": "...", "params": {...}}
]}
```

`rewards.json` entries declare `applies_to: ["weapon"|"armour"|"helm"|"shield"]` and are
**not** repeatable on the same item (invariant I10).

`enchanted_rewards.json` adds three constraint fields:

```json
{"enchanted_rewards": [{
  "id": "example_enchantment",
  "craftsmanship": ["elven", "dwarven"],
  "item_types": ["armour", "helm"],
  "requires_bane": false,
  "supersedes": "close_fitting",
  "factory": "...", "params": {...}
}]}
```

`supersedes` names the ordinary Reward whose "descriptor" it shares. The rule is that an
item may not carry both a Reward and the Enchanted Reward that shares its descriptor.
Validate at item construction (`13.7`), not at pack load.

## 5.6 Table files

Every table file is a `LookupTable` serialisation:

```json
{"id": "wound_severity", "die": "feat",
 "rows": [
   {"match": "rune",  "value": {"severity": "moderate"}},
   {"match": [1, 10], "value": {"severity": "severe", "days": "$roll"}},
   {"match": "eye",   "value": {"severity": "grievous"}}
 ]}
```

`"$roll"` is a sentinel meaning "the numeric die result itself". The loader converts it to
a marker the consuming subsystem resolves.

```json
{"id": "journey_events", "die": "feat",
 "rows": [
  {"match": "eye",  "value": {"event": "terrible_misfortune", "fatigue": 3,
                              "on_failure": [{"op": "wound", "who": "target"}]}},
  {"match": 1,      "value": {"event": "despair", "fatigue": 2,
                              "on_failure": [{"op": "shadow", "who": "company",
                                              "points": 1, "source": "dread"}]}},
  {"match": [2, 3], "value": {"event": "ill_choices", "fatigue": 2,
                              "on_failure": [{"op": "shadow", "who": "target",
                                              "points": 1, "source": "dread"}]}},
  {"match": [4, 7], "value": {"event": "mishap", "fatigue": 2,
                              "on_failure": [{"op": "journey_days", "delta": 1},
                                             {"op": "fatigue", "who": "target", "points": 1}]}},
  {"match": [8, 9], "value": {"event": "short_cut", "fatigue": 1,
                              "on_success": [{"op": "journey_days", "delta": -1}]}},
  {"match": 10,     "value": {"event": "chance_meeting", "fatigue": 1,
                              "on_success": [{"op": "suppress_fatigue"},
                                             {"op": "narrative", "tag": "favourable_encounter"}]}},
  {"match": "rune", "value": {"event": "joyful_sight", "fatigue": 0,
                              "on_success": [{"op": "hope", "who": "company", "points": 1}]}}
 ]}
```

The `op` vocabulary is a tiny declarative effect language shared by journey events,
revelation episodes, and sources of injury. Implement an interpreter in
`tor.content.ops`:

| op | fields | meaning |
|---|---|---|
| `wound` | `who` | inflict a Wound |
| `shadow` | `who`, `points`, `source` | Shadow gain from a named source |
| `hope` | `who`, `points` | Hope gain (negative to lose) |
| `endurance` | `who`, `points` | Endurance change |
| `fatigue` | `who`, `points` | Fatigue change |
| `journey_days` | `delta` | lengthen/shorten the journey |
| `suppress_fatigue` | — | this event's Fatigue is not gained |
| `condition` | `who`, `condition`, `value` | set a condition |
| `narrative` | `tag` | no mechanical effect; recorded for the UI |

`who` ∈ `target` | `company` | `actor`. Keeping this declarative means adding a
supplement's new journey-event table requires no code.

## 5.7 `adversaries.json`

```json
{"adversaries": [{
  "id": "example_foe",
  "name": "Example Foe",
  "category": "orcs",
  "creature_types": ["orcs"],
  "size": "human",
  "attribute_level": 4,
  "endurance": 16,
  "might": 1,
  "drive": {"kind": "hate", "value": 4},
  "parry": 2,
  "armour": 3,
  "attacks": [
    {"name": "Example Blade", "rating": 3, "damage": 3, "injury": 16,
     "special_damage": [], "ranged": false},
    {"name": "Example Spear", "rating": 2, "damage": 3, "injury": 14,
     "special_damage": ["pierce"], "ranged": false}
  ],
  "always_available_special_damage": [],
  "fell_abilities": ["example_ability"],
  "distinctive_features": ["cunning", "wary"]
}]}
```

Notes:

* `size` ∈ `human` | `large`. It drives the engagement limits (`08.4`): a large creature
  can be engaged by more heroes and itself engages fewer.
* `parry` is a **modifier added to the attacker's TN**, not a TN. Some adversaries have no
  Parry bonus at all — encode as `0`, never as null.
* `attacks[].special_damage` lists the icon-spend options available *with that weapon*;
  `always_available_special_damage` lists options available regardless of attack form.
  Every adversary can always trigger a Heavy Blow — the engine adds it implicitly, so it
  must not be listed in content.
* Category-wide Fell Abilities (all creatures of a family share some) are expressed by
  putting the shared ability id in every member's `fell_abilities`, or by a
  `categories.json` with inheritance. Prefer explicit repetition: it survives merging
  supplements more predictably, and content packs are generated, not hand-maintained.

## 5.8 `patrons.json`

```json
{"patrons": [{
  "id": "example_patron",
  "name": "...",
  "favoured_callings": ["captain", "champion"],
  "fellowship_bonus": 1,
  "benefit": {"effect": "example_patron_benefit"},
  "agenda": "..."
}]}
```

Patron benefits are effects like any other, usually of the form "spend Fellowship to do
X". Observed mechanical shapes the engine must support: make a combat roll Favoured; make
a Shadow Test Favoured; reroll all dice in a roll; raise Fellowship by 1 when meeting the
Patron; resolve Journey Events in a defined area at a gentler region tier; spend the
entire Fellowship pool for a narrative intervention; receive a rumour on meeting.

## 5.9 `standards_of_living.json`

```json
{"tiers": [
  {"id": "poor",       "treasure_threshold": null, "useful_items": 0, "mount_vigour": null},
  {"id": "frugal",     "treasure_threshold": 0,    "useful_items": 1, "mount_vigour": null},
  {"id": "common",     "treasure_threshold": 30,   "useful_items": 2, "mount_vigour": 1},
  {"id": "prosperous", "treasure_threshold": 90,   "useful_items": 3, "mount_vigour": 2},
  {"id": "rich",       "treasure_threshold": 180,  "useful_items": 4, "mount_vigour": 3},
  {"id": "very_rich",  "treasure_threshold": 300,  "useful_items": 4, "mount_vigour": 3}
]}
```

`treasure_threshold: null` for the lowest tier means it is never reached by accumulating
Treasure — it is a starting condition only.

## 5.10 `experience_costs.json`

Two separate ladders. Do not conflate them.

```json
{
  "previous_experience": {
    "skills":        [{"to": 1, "cost": 1}, {"to": 2, "cost": 2},
                      {"to": 3, "cost": 3}, {"to": 4, "cost": 5}],
    "proficiencies": [{"to": 1, "cost": 2}, {"to": 2, "cost": 4}, {"to": 3, "cost": 6}],
    "budget": 10,
    "heir_caps": {"skills": 4, "proficiencies": 3}
  },
  "advancement": {
    "ability": [{"to": 1, "cost": 4},  {"to": 2, "cost": 8},  {"to": 3, "cost": 12},
                {"to": 4, "cost": 20}, {"to": 5, "cost": 26}, {"to": 6, "cost": 30}],
    "rank":    [{"to": 2, "cost": 8},  {"to": 3, "cost": 12},
                {"to": 4, "cost": 20}, {"to": 5, "cost": 26}, {"to": 6, "cost": 30}]
  }
}
```

`previous_experience` is used **only** during character creation and heir creation, with a
budget of 10 (or the heir's accumulated reserve). `advancement` is used during Fellowship
Phases: `ability` for Skills (Skill points) and Combat Proficiencies (Adventure points),
`rank` for VALOUR and WISDOM (Adventure points). Costs are **per level attained**, and
raising several levels costs the sum of each level's price.

## 5.11 The example pack

`content/example/` must be complete, non-canonical, and exercise every schema branch:

* 2 cultures, one of which has a `cultural_weakness`, an `allowed_weapons` restriction,
  and a `forbidden_items` entry.
* 2 callings, one with `requires_subject: true`.
* Weapons covering: one-handed only, two-handed only, dual Injury ratings, ranged,
  throwable, and the no-Injury/no-Piercing case.
* Armour at every category plus a helm; shields with and without a Standard-of-Living
  minimum.
* One Virtue per factory type; one Cultural Virtue; all six Reward shapes; at least four
  Enchanted Rewards spanning all three craftsmanship values and both `requires_bane`
  states.
* 4 adversaries: one Hate/human/Might 1, one Hate/large/Might 2, one Resolve, one with a
  Fell Ability requiring bespoke Python.
* Every table, fully covering its key domain.

Tests run exclusively against this pack. No test may depend on licensed content.
