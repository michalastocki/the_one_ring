# 20 — Canonical Identifier Registry

Ids are the contract between the engine, the content packs, and the save files. They are
`snake_case`, ASCII, and **stable forever**. Renaming one is a breaking change requiring a
migration (`17.5`).

Only ids that the **engine itself** knows about are fixed here. Everything else — cultures,
callings, virtues, weapons, adversaries, patrons — is content and its ids are chosen by
whoever authors the pack. The engine must never contain a literal for those.

---

## 20.1 Skills — engine-fixed

All 18, with their governing Attribute and Skill group. This structure is engine knowledge
because the roll pipeline needs the Attribute and the Blessings generation needs the group
(`13.5`).

| id | Attribute | group |
|---|---|---|
| `awe` | strength | personality |
| `athletics` | strength | movement |
| `awareness` | strength | perception |
| `hunting` | strength | survival |
| `song` | strength | custom |
| `craft` | strength | vocation |
| `enhearten` | heart | personality |
| `travel` | heart | movement |
| `insight` | heart | perception |
| `healing` | heart | survival |
| `courtesy` | heart | custom |
| `battle` | heart | vocation |
| `persuade` | wits | personality |
| `stealth` | wits | movement |
| `scan` | wits | perception |
| `explore` | wits | survival |
| `riddle` | wits | custom |
| `lore` | wits | vocation |

Group ordering for the Blessings table roll (Success die 1–6):
`personality`, `movement`, `perception`, `survival`, `custom`, `vocation`.

Within a group, the second Success die selects: 1–2 → the STRENGTH skill, 3–4 → the HEART
skill, 5–6 → the WITS skill.

## 20.2 Combat Proficiencies — engine-fixed

`axes`, `bows`, `spears`, `swords`

Plus the pseudo-ability `brawling`, which is not a Proficiency but resolves to the highest
of the four (`03.3`).

## 20.3 Rank abilities — engine-fixed

`valour` (rolls against HEART TN), `wisdom` (rolls against WITS TN)

## 20.4 Attributes — engine-fixed

`strength`, `heart`, `wits`

## 20.5 Enumerations — engine-fixed

**Conditions:** `weary`, `miserable`, `wounded`

**Stances:** `forward`, `open`, `defensive`, `rearward`

**Journey roles:** `guide`, `hunter`, `lookout`, `scout`

**Region types:** `border`, `wild`, `dark`

**Terrain:** `easy`, `hard`, `road`

**Seasons:** `spring`, `summer`, `autumn`, `winter`

**Standards of Living:** `poor`, `frugal`, `common`, `prosperous`, `rich`, `very_rich`

**Shadow sources:** `dread`, `sorcery`, `greed`, `misdeed`, `focus`, `other`

**Risk levels:** `standard`, `hazardous`, `foolish`

**Failure shapes:** `simple`, `woe_offered`, `failure_with_woe`, `disaster`

**Loss levels:** `moderate`, `severe`, `grievous`

**Resistance levels:** `low` (3), `medium` (6), `high` (9)

**Hoard grades:** `lesser`, `greater`, `marvellous`

**Magical item kinds:** `artefact`, `wondrous`, `famous_gear`

**Craftsmanship:** `elven`, `dwarven`, `numenorean`

**Creature types:** `orcs`, `trolls`, `spiders`, `wolves`, `evil_men`, `undead`

**Drive kinds:** `hate`, `resolve`

**Adversary size:** `human`, `large`

**Armour categories:** `leather`, `mail`, `headgear`

**Song kinds:** `lay`, `song_of_victory`, `walking_song`

**Magic levels:** `lesser`, `major`, `powerful`

**Scene kinds:** `combat`, `council`, `journey`, `endeavour`, `free`

**Phase kinds:** `adventuring`, `fellowship`

## 20.6 Combat tasks — engine-fixed

The four standard tasks are engine-known because stances reference them:

| id | stance | ability |
|---|---|---|
| `intimidate_foe` | `forward` | `awe` |
| `rally_comrades` | `open` | `enhearten` |
| `protect_companion` | `defensive` | `battle` |
| `prepare_shot` | `rearward` | `scan` |

Adversary-granted bespoke tasks are content and use content-chosen ids.

## 20.7 Special Damage options — engine-fixed

**Hero options:** `heavy_blow`, `fend_off`, `pierce`, `shield_thrust`

**Adversary options:** `heavy_blow`, `pierce`, `break_shield`, `seize`

`heavy_blow` is shared but resolves differently by actor kind (STRENGTH vs Attribute Level),
which is a branch inside the option, not two ids.

## 20.8 Skill Special Successes — engine-fixed

`cancel_failure`, `additional_success`, `gain_insight`, `go_quietly`, `make_haste`,
`widen_influence`

Only the first two have mechanical force (`02.3.6`); the rest are recorded flags.

## 20.9 Undertakings — engine-fixed

Fixed because the phase controller and the Calling free-undertaking mapping both reference
them:

`gather_rumours`, `heal_scars`, `meet_patron`, `ponder_maps`, `raise_an_heir`,
`recount_a_story`, `strengthen_fellowship`, `study_magical_items`, `write_a_song`,
`visit_treasury`

Yule-only: `heal_scars`, `raise_an_heir`, `recount_a_story`.

## 20.10 Hooks

The hook names in `04.3` are ids in their own right. Listed here for completeness of the
registry; see `04.3` for signatures.

Roll pipeline: `MODIFY_ROLL_REQUEST`, `MODIFY_ROLL_RESULT`, `MAGICAL_SUCCESS_ELIGIBLE`,
`ON_ROLL_RESOLVED`

Derived stats: `MODIFY_MAX_ENDURANCE`, `MODIFY_MAX_HOPE`, `MODIFY_PARRY`,
`MODIFY_ATTRIBUTE_TN`, `MODIFY_ITEM_LOAD`, `MODIFY_SHIELD_PARRY`,
`MODIFY_FELLOWSHIP_RATING`, `MODIFY_MOUNT_VIGOUR`

Combat: `MODIFY_ATTACK_TN`, `MODIFY_DAMAGE_RATING`, `MODIFY_INJURY_RATING`,
`PIERCING_THRESHOLD`, `MODIFY_PROTECTION_ROLL`, `MODIFY_TARGET_PROTECTION_ROLL`,
`SPECIAL_DAMAGE_OPTIONS`, `MODIFY_SPECIAL_DAMAGE`, `ON_ATTACK_HIT`, `ON_PIERCING_BLOW`,
`ON_KILL`, `MODIFY_WOUND_SEVERITY_ROLL`, `ON_WOUND_RECEIVED`, `ON_ROUND_START`,
`ON_ROUND_END`, `ON_COMBAT_END`, `STANCE_OPTIONS`, `SECONDARY_ACTION_OPTIONS`,
`OPENING_VOLLEY_COUNT`, `DAMAGE_INTERCEPT`, `WOUND_INTERCEPT`

Journey: `MODIFY_FATIGUE_GAIN`, `MODIFY_JOURNEY_EVENT_ROLL`, `JOURNEY_ROLE_LIMITS`,
`ON_JOURNEY_END`

Shadow and recovery: `MODIFY_SHADOW_GAIN`, `MODIFY_SHADOW_TEST`,
`MODIFY_SHADOW_REMOVAL_CAP`, `MODIFY_SHADOW_FLOOR`, `MODIFY_SHORT_REST_RECOVERY`,
`MODIFY_PROLONGED_REST_RECOVERY`, `SHORT_REST_COUNTS_AS_PROLONGED`,
`MODIFY_HOPE_RECOVERY`, `MODIFY_INSPIRATION`

Council: `MODIFY_COUNCIL_ATTEMPTS`, `MODIFY_AUDIENCE_ATTITUDE`, `MODIFY_COUNCIL_ROLL`

Phase: `FREE_UNDERTAKINGS`, `ON_VALOUR_GAIN`, `ON_WISDOM_GAIN`, `ON_PHASE_START`,
`ON_PHASE_END`

## 20.11 Naming rules for content authors

1. `snake_case`, ASCII only, no spaces or punctuation.
2. Prefix nothing. `dwarves` not `culture_dwarves` — the file the id lives in already
   scopes it.
3. Use the singular for a thing, the plural only where the game itself is plural (`axes`,
   `bows`).
4. Never encode a rating, a version, or a source book in an id.
5. Cultural Virtue ids should be globally unique across cultures even though they are
   scoped by a `culture` field — two cultures with a similarly named virtue must not
   collide.
6. Once published, an id is permanent. To retire an entry, mark it
   `"deprecated": true` rather than deleting it, so old saves still load.
