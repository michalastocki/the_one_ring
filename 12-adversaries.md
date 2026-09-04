# 12 — Adversaries

Module: `tor.rules.combat` uses these; the model lives in `tor.model.adversary` and the
content in `adversaries.json` (`05.7`).

Adversaries use a deliberately simplified sheet. The design intent is speed of play: one
Attribute Level instead of three Attributes, one drive pool instead of Hope and Shadow, no
stances, no Skills. Resist the temptation to give them a full hero sheet — several rules
depend on the simplification.

---

## 12.1 The stat block

| Field | Meaning |
|---|---|
| **Attribute Level** | A single value replacing the three hero Attributes. Gauges the threat a creature of that type poses, and feeds several mechanics as a modifier. |
| **Endurance** | Resistance to the exhaustion and harm of combat. |
| **Might** | Does double duty: the number of Wounds needed to slay the creature outright, **and** the number of attacks it may make in a round. |
| **Hate** or **Resolve** | The drive pool. Exactly one of the two. |
| **Parry** | A **modifier added to the attacker's TN** — not a TN in itself. |
| **Armour** | PROTECTION dice, used when the creature is hit by a Piercing Blow. |
| **Combat Proficiencies** | A primary and a secondary attack form, each with a rating, Damage/Injury stats, and its available Special Damage options. |
| **Fell Abilities** | Special talents; effects (`04`). |
| **Distinctive Features** | One or two, used per the LM-character rules (`12.7`). |

Attribute Level is used in at least four places, so route every use through one accessor:
* Heavy Blow special damage (adversary version) adds it as Endurance loss.
* Shield Thrust compares the hero's STRENGTH against it.
* The Hammering enchanted quality triggers at twice it.
* Several Fell Abilities scale from it.

## 12.2 Hate versus Resolve

Mechanically identical: both are spendable pools that fuel `+1d` on a roll and pay for
Fell Abilities. Semantically distinct, and the distinction has teeth.

**Hate** marks minions and servants of the Enemy. Their actions are not easily
misinterpreted — they always aim to hurt or damage the heroes, desiring only to satisfy
their Master's will. They may flee from a disadvantageous position, but never yield,
surrender, or give quarter unless forced by circumstance or direct order.

**Resolve** marks foes who are not sworn enemies of the Free Folk — evil Men and other
non-monstrous adversaries who take up arms through their allegiance or through
unfortunate circumstance. They lack the relentless motivation of the Dark Lord's servants
and **may surrender when outnumbered or when casualties mount**.

The consequence the engine must enforce:

> Attacking or killing an adversary with a **Resolve** score should always be evaluated by
> the LM as a possible **Misdeed** (`11.4.4`). Fighting minions of the Enemy can hardly
> call the heroes' integrity into question.

```python
def misdeed_check_required(adversary: Adversary) -> bool:
    return adversary.drive_kind is DriveKind.RESOLVE
```

When a Resolve-bearing adversary is killed or attacked, emit a `MisdeedCheckPrompt` event
carrying the LM's guiding questions: was the fight provoked by the heroes or were they
attacked? Was there another option than combat? Was killing necessary, or were the
adversaries prone to surrender? The engine prompts; it never decides.

**Drive spending:**
* Reduce the pool by 1 to gain `+1d` on a die roll during combat — generally an attack or
  PROTECTION roll.
* Several Fell Abilities require an expenditure.
* The LM **is** entitled to use a Fell Ability even when it costs the creature's last
  point. Do not add a "keep one in reserve" guard.
* **A creature that begins a round with zero drive is Weary** for that round. This is the
  adversary analogue of pattern P9. Check it in `ON_ROUND_START`, not at the moment the
  pool empties.

## 12.3 Endurance, Might, and death

* An adversary is **taken out of combat** when its Endurance reaches zero.
* **Adversaries do not become Weary from Endurance loss** — only from an empty drive pool.
  This is a genuine asymmetry with heroes; do not unify the two paths.
* **Adversaries cannot choose to be knocked back** (`08.12`).
* Most adversaries are killed outright when Wounded. Precisely: a creature is slain when
  it has taken Wounds equal to its **Might**. A Might 1 creature dies on its first Wound; a
  Might 2 creature survives the first and dies on the second.
* Adversaries do **not** roll on the Wound Severity table and do not carry the Wounded
  condition. Track `wounds_taken` and compare against Might.

```python
def apply_wound(adv: Adversary) -> AdversaryWoundOutcome:
    adv.wounds_taken += 1
    return AdversaryWoundOutcome(slain=adv.wounds_taken >= adv.might)
```

**Out of combat.** After a victorious battle, the LM may rule that adversaries taken out
of combat by being reduced to zero Endurance are still barely alive and might survive if
rescued. Record `taken_out_reason: ENDURANCE | SLAIN` on the combatant so this remains
available; do not collapse both into "dead".

## 12.4 The Feat die icon inversion

When the LM rolls for a servant of the Shadow, the meanings of the two special Feat die
icons **swap**: the eye becomes the highest result and yields an automatic success, while
the rune becomes the lowest and reads as zero.

This is the `icon_inverted` flag on `RollRequest` (`02.1.2`, `02.3.4`). One boolean, one
code path.

Consequence to state explicitly, because it is the most-used one: **most adversaries score
a Piercing Blow on a Feat die result of 10 or the eye**, mirroring the heroes' 10-or-rune.

Set the flag from the adversary's category, not universally — it is described as
appropriate for servants of the Shadow. Content declares it:
`{"icon_inverted": true}` on the adversary or its category. Non-Shadow adversaries with a
Resolve score may legitimately roll normally; make it data.

## 12.5 Adversary Special Damage

If a successful adversary attack scores one or more Success icons, the LM may spend them —
one icon per result, multiple icons buying different results or the same one repeatedly.

| Option | Effect |
|---|---|
| **Heavy Blow** | Additional Endurance loss equal to the attacker's **Attribute Level**. |
| **Pierce** | Modify the Feat die result of the attack by **+2** — a flat value, unlike the hero version which varies by weapon. |
| **Break Shield** | The target loses their shield's Parry bonus; the shield is smashed. **A shield enhanced by a Reward or a magical quality cannot be smashed** and is unaffected. |
| **Seize** | The attacker holds the target. A seized hero **can only fight in a Forward stance making Brawling attacks**. They may free themselves by spending one Success icon from a successful attack roll. |

**Every adversary can always choose Heavy Blow**, so the engine adds it implicitly and
content must not list it (`05.7`). The remaining options come from the attack form's
`special_damage` list or the creature's `always_available_special_damage`.

Break Shield implementation: unregister the shield's `EffectSource` and set
`shield.smashed = True`. Guard on `shield.rewards or shield.enchanted` first. Because
Parry is a `DerivedStat` recomputed from registered sources (`03.4.2`), no other code
needs to know the shield is gone.

Seize implementation: a `SeizedBy` flag on the combatant that (a) forces stance to Forward
and (b) restricts the weapon choice to Brawling. Freeing is an `IconBudget` spend option
on the seized hero's next successful attack.

## 12.6 Fell Abilities

Each adversary lists its own. They are effects (`04.5`), overwhelmingly expressible with
the declarative factories. Unless otherwise noted, **a Fell Ability may be used in addition
to attacking**, provided the creature has the points to spend.

The recurring mechanical shapes in the base game, and the factory each maps to:

| Shape | Factory | Notes |
|---|---|---|
| Spend 1 drive to make an attack roll against this creature Ill-favoured | `spend_drive_for` | triggers on being targeted; cost paid by the target |
| Spend 1 drive to gain `+1d` **and** make the attack roll Favoured | `spend_drive_for` | two contributions from one spend |
| Spend 1 drive to restore 1 drive to **all other** creatures of its kind in the fight | `restore_allies_drive` | scope filter by category |
| Spend 1 drive to make all heroes in sight gain 2 or 3 Shadow (Dread); those who fail the test are daunted and **cannot spend Hope for the rest of the fight** | `shadow_on_hit` with `on_fail` | the daunted rider is per-fight, not per-round |
| Spend 1 drive to make the target's PROTECTION roll Ill-favoured after a Piercing Blow | `modify_target_protection` | conditional on having scored a Piercing Blow |
| Spend 1 drive to gain `(2d)` on a PROTECTION roll | `spend_drive_for` | |
| Spend 1 drive to attack **any** hero in any stance, including Rearward | `spend_drive_for` + `ignore_stance_targeting` | breaks the normal Rearward protection |
| Damage that would reduce it to zero Endurance instead causes a **Piercing Blow**; if still alive it returns to **full Endurance** | bespoke Python | see below |
| Spend 1 drive to **cancel a Wound**; damage reducing it to zero instead restores full Endurance — **ineffective against a weapon enchanted for the bane of that creature type** | bespoke Python | |
| Immune to Intimidate Foe unless a **magical success** is obtained | declarative predicate on the task | |
| When affected by Intimidate Foe, also lose 1 drive | `on_task_affected` | |
| At the start of the round, flees the battlefield if at zero drive **and** unengaged | `flee_when_broken` | ordering matters — evaluate before actions |
| Loses 1 drive per round exposed to full sunlight | `drain_drive_on_environment` | |
| Loses 1 drive at the start of each round engaged in close combat with an opponent wielding a torch or burning item | `drain_drive_on_environment` | |
| All attack rolls Favoured while in darkness | `favour_rolls` + predicate | |
| **Hatred (subject)**: all attacks against a named target type are Favoured — or in some cases *all* the creature's rolls | `favour_rolls` + subject param | the parameterised subject is the point |
| Wounded targets roll Wound Severity **Ill-favoured** | `modify_wound_severity` | |
| Exposes a bespoke combat task: a RIDDLE roll in Forward stance strips 1 drive, plus 1 per Success icon | `grant_combat_task` | pattern P3 again |

Two shapes need real Python because they restructure the damage pipeline:

```python
def hideous_toughness(ctx: HookContext, loss: int) -> DamageInterception:
    """If this loss would take the creature to zero Endurance, it instead causes a
    Piercing Blow. Then, if the creature is still alive, it returns to full Endurance."""

def deathless(ctx: HookContext, wound: WoundEvent) -> WoundInterception:
    """Spend 1 drive to cancel the Wound. Damage that would reduce the creature to zero
    Endurance instead restores it to full. Both are suppressed entirely when the attacker
    wields a weapon enchanted against this creature type."""
```

Both need a `DAMAGE_INTERCEPT` and `WOUND_INTERCEPT` hook that fires **before** the
Endurance change is applied. Add them to the registry in `04.3.3`. They are the only two
hooks that can veto a state change rather than modify a value, so keep them narrow: an
interceptor returns either `PROCEED` or a replacement outcome, never arbitrary mutation.

Note the bane interaction on the second: the ability is disabled by a weapon enchanted
against the creature's type, which requires the attack context to carry the weapon's
`banes` set (`03.5`).

## 12.7 Adversaries as LM characters

Before an encounter turns to violence, an adversary may function as an ordinary LM
character. Two rules bridge the two modes:

**LM characters as obstacles.** When an LM character hinders the Company, resolve with a
normal Skill roll. If that character has a Distinctive Feature that makes the hero's action
particularly difficult, the hero's roll **loses `−1d`**. If the character has a Feature
that can be read as a flaw making the action easier, the hero **gains `+1d`**.

**LM characters providing assistance.** Only a character with an appropriate Distinctive
Feature can support a hero's action: the roll **gains `+1d`**, or **`+2d`** if the trait
represents a superior level of support.

Both are LM judgements. The engine takes them as inputs:

```python
@dataclass(frozen=True, slots=True)
class LMCharacterInfluence:
    character: str
    feature: EffectId
    direction: Literal["hinder", "ease", "assist"]
    magnitude: int = 1          # 1 or 2; 2 only for "assist"
```

**Escalation to combat.** Should interaction lead to outright conflict, the LM character
must be cast as an adversary by generating the combat characteristics above. Provide:

```python
def promote_to_adversary(character: LMCharacter, template: AdversaryId,
                         pack: ContentPack) -> Adversary:
    """Instantiate a stat block from a template, carrying over the character's name and
    Distinctive Features."""
```

A minimal LM character needs only a name, a short description, and a sense of purpose in
the scene. The LM may add an occupation or field of expertise and one or two Distinctive
Features, drawn from those available to heroes or invented. Model as:

```python
@dataclass(slots=True)
class LMCharacter:
    name: str
    description: str
    occupation: str | None = None
    features: list[EffectId] = field(default_factory=list)
    culture: CultureId | None = None       # for name generation
```

Offer `generate_name(culture, gender, rng)` drawing on the culture's name lists (`05.2`)
so an LM is never stuck inventing one mid-scene.

## 12.8 Instancing

A combat may contain many copies of one adversary type. Never share mutable state between
them.

```python
@dataclass(slots=True)
class AdversaryInstance:
    instance_id: AdversaryInstanceId
    template: AdversaryId
    endurance: int
    drive: ResourcePool
    wounds_taken: int = 0
    effects: EffectBus = field(default_factory=EffectBus)

def instantiate(template: Adversary, n: int, rng) -> list[AdversaryInstance]: ...
```

`instantiate` registers each instance's Fell Abilities on its own bus, so that a
usage-limited ability spent by one Orc is still available to another.
