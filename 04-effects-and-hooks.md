# 04 — Effects and Hooks

Module: `tor.effects`.

This is the mechanism that lets the engine support every Cultural Blessing, Virtue,
Cultural Virtue, Reward, Enchanted Reward, item Blessing, Curse, Fell Ability, Flaw,
Distinctive Feature, and Condition **without a single conditional on a content id**.

Get this right and the rest of the engine stays small. Get it wrong and you will write
`if virtue == "..."` in forty places.

---

## 4.1 The core abstraction

```python
@dataclass(frozen=True, slots=True)
class Effect:
    id: EffectId
    kind: EffectKind
    listeners: Mapping[Hook, Listener]
    params: Mapping[str, Any] = field(default_factory=dict)   # per-instance choices
    usage: UsagePolicy | None = None
    predicate: Predicate | None = None

class EffectKind(StrEnum):
    CULTURAL_BLESSING   = "cultural_blessing"
    VIRTUE              = "virtue"
    CULTURAL_VIRTUE     = "cultural_virtue"
    REWARD              = "reward"
    ENCHANTED_REWARD    = "enchanted_reward"
    ITEM_BLESSING       = "item_blessing"
    CURSE               = "curse"
    FELL_ABILITY        = "fell_ability"
    FLAW                = "flaw"
    DISTINCTIVE_FEATURE = "distinctive_feature"
    CONDITION           = "condition"
    PATRON_BENEFIT      = "patron_benefit"
    UNDERTAKING_BENEFIT = "undertaking_benefit"
```

`params` carries the player's choices at acquisition time. Examples: which two Skills
*Mastery* makes Favoured; which Attribute *Prowess* lowers; which creature type the
Enemy-lore feature or a Hatred ability names; which three Skills a Cultural Virtue marks.
This is why an effect can be a shared, stateless definition while still being personal.

`predicate` gates the effect on live state — "if you are not Miserable", "when fighting
underground", "while in darkness", "if you wear leather armour or none and carry no
shield", "when the target has Might 2 or more". Predicates are pure functions of a
`HookContext`; they never mutate.

`usage` implements pattern P16: "once per round", "once per combat", "N times per
Adventuring Phase where N is your WISDOM rating".

```python
@dataclass(frozen=True, slots=True)
class UsagePolicy:
    scope: UsageScope       # ROLL | ROUND | COMBAT | SCENE | JOURNEY | COUNCIL |
                            # ADVENTURING_PHASE | FELLOWSHIP_PHASE | CAREER
    limit: int | Callable[[HookContext], int]   # callable for "equal to your WISDOM"
```

`CAREER` scope exists for once-ever effects — at least one Cultural Virtue saves a hero
from a deadly Wound exactly once, then is spent forever.

Usage counters live on the `EffectBus` instance attached to the character, keyed by
`(EffectId, scope)`, and are reset by scope-boundary events emitted from `tor.session`.

## 4.2 The bus

```python
class EffectBus:
    def register(self, effect: Effect, source: EffectSource) -> None: ...
    def unregister(self, effect_id: EffectId, source: EffectSource) -> None: ...

    def collect(self, hook: Hook, ctx: HookContext) -> list[Contribution]:
        """Fire every registered listener for `hook` whose predicate passes and whose
        usage budget allows, in deterministic order. Returns their contributions."""

    def apply_numeric(self, hook: Hook, ctx: HookContext, base: int) -> DerivedStat:
        """Convenience for additive hooks."""
```

`EffectSource` records where the effect came from — the hero's culture, a Virtue, a
specific item instance, an active condition, a temporary combat state. Unregistering by
source is how a smashed shield removes its Parry bonus and how a dropped helm removes its
Protection.

### 4.2.1 Determinism

`collect()` must produce a stable order. Sort by `(hook_priority, kind_rank, effect_id)`
where `kind_rank` is a fixed ordering of `EffectKind`. Never rely on insertion order or
`set` iteration. Two runs with the same seed and the same state must produce byte-identical
results — this is required for the replay guarantee in `17`.

### 4.2.2 Contribution types

```python
Contribution = NumericContribution | FlagContribution | ReplacementContribution | ActionContribution
```

* **Numeric** — `delta: int`, additive. Used for `+2 max Endurance`, `+1 Parry`,
  `−1 Attribute TN`, `+1d`, `+2 Injury`.
* **Flag** — sets a boolean or adds to a set. Used for Favoured/Ill-favoured sources,
  "you are Inspired", "you never gain Fatigue", "you don't need to sleep".
* **Replacement** — overrides a value outright. Used sparingly: a Mithril armour quality
  replaces the item's Load with a fixed value rather than adjusting it. When two
  replacements collide on the same hook, that is a `ContentError` at load time — detect
  conflicting replacement effects when validating a pack.
* **Action** — offers a new legal action or option. Used for "you may attempt the
  *Protect Companion* combat task as a *secondary* action", "you may attempt a special
  combat task against this creature type", "a Company including this Calling may choose
  this undertaking for free".

## 4.3 Hook registry

The complete set. Adding a hook is a spec change; implementing an effect is not.

### 4.3.1 Roll pipeline

| Hook | Payload | Purpose |
|---|---|---|
| `MODIFY_ROLL_REQUEST` | `RollRequestDraft` | add/remove Success dice; add Favoured or Ill-favoured sources; mark Inspired; permit magical success |
| `MODIFY_ROLL_RESULT` | `RollResult` | reroll all dice; adjust the numeric Feat result |
| `MAGICAL_SUCCESS_ELIGIBLE` | `AbilityId` | declare which abilities may be pushed to a magical success |
| `ON_ROLL_RESOLVED` | `RollResult` | observers (Eye Awareness increments on an eye result) |

`MODIFY_ROLL_RESULT` is the only hook that runs *after* dice are known. It exists for two
mechanics: the Rangers' foresight gift (reroll all dice in a roll, including an
adversary's roll targeting the hero) and the Patron benefit that rerolls all dice. Both
are player-triggered, so the hook is offered as an `ActionContribution` first and applied
only on confirmation. Never auto-fire it.

### 4.3.2 Derived stats

| Hook | Purpose |
|---|---|
| `MODIFY_MAX_ENDURANCE` | Hardiness and similar |
| `MODIFY_MAX_HOPE` | Confidence, several Cultural Virtues |
| `MODIFY_PARRY` | Nimbleness; situational bonuses (fighting underground, fighting larger creatures) |
| `MODIFY_ATTRIBUTE_TN` | Prowess; the Weakening curse |
| `MODIFY_ITEM_LOAD` | Cunning Make; the Dwarven blessing; Mithril; Ancient Cunning Make |
| `MODIFY_SHIELD_PARRY` | Reinforced; Superior Reinforced |
| `MODIFY_FELLOWSHIP_RATING` | Patron bonus; a Cultural Blessing; a Cultural Virtue; Strengthen Fellowship |
| `MODIFY_MOUNT_VIGOUR` | the Cultural Virtue granting an exceptional pony |
| `MODIFY_USEFUL_ITEM_LIMIT` | — reserved |

### 4.3.3 Combat

| Hook | Purpose |
|---|---|
| `MODIFY_ATTACK_TN` | situational TN changes |
| `MODIFY_DAMAGE_RATING` | Grievous; Superior Grievous |
| `MODIFY_INJURY_RATING` | Fell; Superior Fell |
| `PIERCING_THRESHOLD` | Keen (also on 9); Superior Keen (8+, or 9+, or 10 minus VALOUR against a Bane creature) |
| `MODIFY_PROTECTION_ROLL` | Close-fitting (+2 to result); Ancient Close Fitting (+3 or VALOUR, whichever is higher); Stone-hard (Favoured); Rune-scored Armour (ignore Miserable/Weary) |
| `MODIFY_TARGET_PROTECTION_ROLL` | attacker-side effects that make the *target's* PROTECTION roll Ill-favoured |
| `SPECIAL_DAMAGE_OPTIONS` | which icon-spend options a combatant has, given their gear |
| `MODIFY_SPECIAL_DAMAGE` | Dour-handed: +1 STRENGTH on a Heavy Blow, +1 to the Feat result on a Pierce |
| `ON_ATTACK_HIT` | Gleam of Wrath (drain 1 drive +1 per icon); Biting Dart; Gleam of Terror; Flame of Hope (Company recovers Endurance) |
| `ON_PIERCING_BLOW` | Fierce Shot (ranged Piercing Blow ⇒ target's PROTECTION Ill-favoured); Foe-slaying |
| `ON_KILL` | Cleaving (immediately attack a second engaged adversary) |
| `MODIFY_WOUND_SEVERITY_ROLL` | Tough as old tree-roots (roll two Feat dice, keep better); Deadly Wound (Ill-favoured) |
| `ON_WOUND_RECEIVED` | High Destiny (survive a deadly Wound once, then +2 max Hope) |
| `ON_ROUND_START` / `ON_ROUND_END` | Hate Sunlight; Fear of Fire; Craven |
| `ON_COMBAT_END` | Defiance (recover Endurance equal to HEART or VALOUR, whichever is higher, if not Wounded or Miserable) |
| `STANCE_OPTIONS` | Small Folk (may take Rearward with only one companion in close combat) |
| `SECONDARY_ACTION_OPTIONS` | the several Cultural Virtues that convert a specific combat task into a secondary action in a specific stance |
| `OPENING_VOLLEY_COUNT` | Hollow Steel (always one extra opening volley) |
| `MODIFY_ENGAGEMENT` | reserved |

### 4.3.4 Journey

| Hook | Purpose |
|---|---|
| `MODIFY_FATIGUE_GAIN` | Cram (1 less per event); Endurance of the Ranger (never gain Fatigue, conditionally) |
| `MODIFY_JOURNEY_EVENT_ROLL` | Memory of Ancient Days (resolve as if in a Border Land); Ponder Storied and Figured Maps (+1 shift) |
| `JOURNEY_ROLE_LIMITS` | Ways of the Wild (may always cover more than one role) |
| `ON_JOURNEY_END` | — |

### 4.3.5 Shadow and recovery

| Hook | Purpose |
|---|---|
| `MODIFY_SHADOW_GAIN` | reductions and increases |
| `MODIFY_SHADOW_TEST` | Untameable Spirit (+1d vs Sorcery); Strength of Will (+1d vs Dread); Hobbit-sense (Favoured WISDOM, +1d vs Greed); the Gandalf Patron benefit (Favoured) |
| `MODIFY_SHADOW_REMOVAL_CAP` | The Long Defeat (remove at most 1 point in a Fellowship Phase) |
| `MODIFY_SHORT_REST_RECOVERY` | Cram (Company recovers extra equal to WISDOM) |
| `MODIFY_PROLONGED_REST_RECOVERY` | Tough as old tree-roots (double STRENGTH when Wounded) |
| `SHORT_REST_COUNTS_AS_PROLONGED` | Elvish Dreams |
| `MODIFY_HOPE_RECOVERY` | The Art of Smoking (+1 whenever Hope is regained); the Rangers' blessing (Fellowship Phase recovery is half HEART, rounded up) |
| `MODIFY_INSPIRATION` | Elbereth Gilthoniel (Inspired on WISDOM-many rolls per Adventuring Phase); Brave at a Pinch (Inspired while Miserable, Weary, or Wounded); Dark for Dark Business (Inspired in darkness) |

### 4.3.6 Council and social

| Hook | Purpose |
|---|---|
| `MODIFY_COUNCIL_ATTEMPTS` | Friendly and Familiar (+1 to the maximum number of Skill rolls in a council) |
| `MODIFY_AUDIENCE_ATTITUDE` | Dwarf-friend (Dwarves always Friendly); Friendly and Familiar (folk encountered always Friendly) |
| `MODIFY_COUNCIL_ROLL` | the Ill-omen curse (−1d during a council) |

### 4.3.7 Progression and phase

| Hook | Purpose |
|---|---|
| `FREE_UNDERTAKINGS` | each Calling makes one specific undertaking free for the Company |
| `ON_VALOUR_GAIN` / `ON_WISDOM_GAIN` | award a Reward / a Virtue; the option to activate a Famous item quality instead |
| `ON_PHASE_START` / `ON_PHASE_END` | usage-budget resets; expiring modifiers |

## 4.4 HookContext

```python
@dataclass(frozen=True, slots=True)
class HookContext:
    actor: RollingCharacter
    hook: Hook
    scene: SceneKind | None              # COMBAT | COUNCIL | JOURNEY | NONE
    environment: Environment             # darkness, underground, sunlight, weather, terrain
    target: RollingCharacter | None
    ability: AbilityId | None
    purpose: RollPurpose | None
    stance: Stance | None
    weapon: WeaponInstance | None
    extra: Mapping[str, Any]
```

`Environment` is a small frozen dataclass of booleans and enums, set by the LM per scene:
`in_darkness`, `underground`, `cramped`, `full_sunlight`, `terrain`, `weather`, `outdoors`.
Many effects predicate on it. Making it a first-class object rather than loose flags is
what stops "fighting underground grants +2 Parry" from becoming a special case in the
combat module.

## 4.5 Effect implementation registry

`tor.effects.library` maps `EffectId → Effect` factory. Content packs reference effects by
id; the library supplies the behaviour.

```python
EFFECT_REGISTRY: dict[EffectId, Callable[[Mapping[str, Any]], Effect]]

def build_effect(effect_id: EffectId, params: Mapping[str, Any]) -> Effect: ...
```

**Most effects should be declarative, not code.** Provide a small set of parameterised
generic factories that content can instantiate directly, so that the majority of the
book's ~80 special abilities require no Python at all:

| Factory | Declarative form |
|---|---|
| `numeric_modifier` | `{"factory": "numeric_modifier", "hook": "MODIFY_MAX_HOPE", "delta": 2}` |
| `favour_rolls` | `{"factory": "favour_rolls", "purpose": "valour_test"}` |
| `bonus_dice` | `{"factory": "bonus_dice", "purpose": "shadow_test", "source": "sorcery", "dice": 1}` |
| `grant_inspiration` | `{"factory": "grant_inspiration", "predicate": {"in_darkness": true}}` |
| `enable_magical_success` | `{"factory": "enable_magical_success", "abilities": [...], "predicate": {"not_miserable": true}}` |
| `drain_drive_on_hit` | `{"factory": "drain_drive_on_hit", "amount": 1, "per_icon": 1}` |
| `secondary_action_task` | `{"factory": "secondary_action_task", "task": "protect_companion", "stance": "defensive", "usage": {"scope": "combat", "limit": 1}}` |
| `spend_drive_for` | Fell Abilities: `{"factory": "spend_drive_for", "cost": 1, "grant": {...}}` |
| `modify_piercing_threshold` | `{"factory": "modify_piercing_threshold", "threshold": 9}` |
| `shadow_on_hit` | Strike Fear: `{"factory": "shadow_on_hit", "points": 3, "source": "dread", "on_fail": "cannot_spend_hope"}` |

Reserve hand-written Python for the genuinely irregular ones. From the book that is
roughly: the once-per-career deadly-Wound survival; the reroll-all-dice foresight gift;
the disappear-by-spending-an-icon Hobbit virtue; Hideous Toughness (damage that would
reduce to zero instead causes a Piercing Blow and restores full Endurance); Deathless
(spend drive to cancel a Wound, ineffective against weapons enchanted against that
creature type); Cleaving; and Hollow Steel.

If your effect library exceeds ~15 hand-written effects, a factory is missing. Add the
factory rather than the fifteenth bespoke class.

## 4.6 Worked examples

**A cultural blessing that favours a category of roll.** A blessing making VALOUR rolls
Favoured:
```json
{"id": "stout_hearted", "kind": "cultural_blessing",
 "factory": "favour_rolls", "params": {"purpose": "valour_test"}}
```
The engine never learns which culture this is. `build_request` calls
`MODIFY_ROLL_REQUEST`; the effect's predicate matches `purpose == VALOUR_TEST`; a
`FlagContribution` adds `"cultural_blessing:stout_hearted"` to `favoured_sources`.

**A Reward bound to an item.** *Fell* adds 2 to a weapon's Injury rating, and if the
weapon has both one- and two-handed Injury ratings, **both** are raised.
```json
{"id": "fell", "kind": "reward", "applies_to": ["weapon"],
 "factory": "numeric_modifier", "params": {"hook": "MODIFY_INJURY_RATING", "delta": 2, "all_modes": true}}
```
Registered with `EffectSource.item(instance_id)`. Removing the Reward unregisters cleanly.

**A conditional Parry bonus.** "+2 Parry when fighting underground or in cramped quarters":
```json
{"factory": "numeric_modifier",
 "params": {"hook": "MODIFY_PARRY", "delta": 2},
 "predicate": {"any_of": [{"environment": "underground"}, {"environment": "cramped"}]}}
```

**A Fell Ability with a cost.** "Spend 1 drive point to make the attack roll targeting
this creature Ill-favoured":
```json
{"id": "snake_like_speed", "kind": "fell_ability",
 "factory": "spend_drive_for",
 "params": {"cost": 1, "trigger": "targeted_by_attack",
            "grant": {"hook": "MODIFY_ROLL_REQUEST", "ill_favoured": true}}}
```
Note the trigger fires on the *attacker's* roll while the cost is paid by the *target*.
`HookContext.target` is what makes that expressible.

## 4.7 Anti-patterns

Reject any of these in review:

* `if hero.culture == ...` anywhere outside `tor.content` and `tor.rules.creation`.
* A subsystem reading `hero.virtues` directly to check for a specific virtue.
* Re-implementing Favoured/Ill-favoured logic outside `tor.rolls`.
* Storing a derived stat's *computed* value on the hero rather than recomputing.
* An effect that mutates state during `collect()`. Collection is pure; application is
  separate. The one exception is decrementing a `UsagePolicy` counter, which happens in
  `EffectBus.consume(effect_id)` called explicitly after the caller commits to using it —
  never inside `collect()`.
