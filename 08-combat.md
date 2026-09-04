# 08 — Combat

Module: `tor.rules.combat` (package: `state`, `sequence`, `engagement`, `attack`, `tasks`).

The largest subsystem. Read `02` first; every roll here goes through `rolls.resolve()`.

---

## 8.1 State

```python
class Stance(StrEnum):
    FORWARD   = "forward"       # close combat
    OPEN      = "open"          # close combat
    DEFENSIVE = "defensive"     # close combat
    REARWARD  = "rearward"      # ranged combat

CLOSE_STANCES = (Stance.FORWARD, Stance.OPEN, Stance.DEFENSIVE)
STANCE_ORDER  = (Stance.FORWARD, Stance.OPEN, Stance.DEFENSIVE, Stance.REARWARD)

@dataclass(slots=True)
class Combatant:
    ref: HeroId | AdversaryInstanceId
    is_hero: bool
    stance: Stance | None                 # heroes only; adversaries inherit from their target
    engaged_with: set[CombatantId]
    surprised: bool = False
    knocked_back: bool = False            # must spend next main action recovering
    fled: bool = False
    out_of_combat: bool = False
    round_flags: RoundFlags               # per-round temporary modifiers

@dataclass(slots=True)
class CombatState:
    round_number: int
    phase: CombatPhase                    # ONSET | VOLLEYS | ROUND_STANCE | ROUND_ENGAGE |
                                          # ROUND_ACTIONS | RESOLVED
    heroes: dict[CombatantId, Combatant]
    adversaries: dict[CombatantId, Combatant]
    environment: Environment
    complications: list[Complication]
    advantages: list[Advantage]
    round_log: list[Event]
```

`RoundFlags` holds everything that expires at end of round: Parry modifiers from *Fend
Off*, penalty dice from *Shield Thrust*, bonus dice granted by *Rally Comrades* (which
apply to the **following** round), Weary imposed by *Intimidate Foe* (also the following
round), and *Prepare Shot* bonuses. Clearing `RoundFlags` at the round boundary is the only
place these expire; never let a subsystem hand-manage them.

Careful: two tasks grant their benefit to the **next** round, not the current one. Model
with two buckets — `current` and `pending` — and swap at the round boundary.

## 8.2 Sequence

```
1. Onset            (LM sets circumstances; surprise resolution)
2. Opening Volleys
3. Close Quarters Rounds, repeated:
     a. Stance
     b. Engagement
     c. Action Resolution
```

### 8.2.1 Onset and surprise

The LM sets scene circumstances that feed `Environment`, the number of opening volleys
allowed, and which combatants are eligible targets. These are inputs, not engine
decisions.

Surprise resolution — skipped entirely if all sides are aware of each other:

**The Company is ambushed.** Every PC rolls AWARENESS. Those who **fail** may make no
opening volley and take no action in the first Close Quarters Round.

**The Company ambushes.** Every participating PC rolls STEALTH. **All** must succeed for
the surprise to take effect. If it does, the surprised enemies make no opening volleys and
take no action in the first round.

The LM may substitute a different Skill where circumstances warrant — BATTLE for
prepared military action against larger groups, HUNTING in wild terrain or against wild
creatures, STEALTH when closing silently. The engine takes the Skill as an input rather
than fixing it, and the LM may also rule that no roll is needed at all and surprise is
automatic.

```python
@dataclass(frozen=True, slots=True)
class SurpriseInput:
    direction: Literal["company_ambushed", "company_ambushes", "none"]
    ability: AbilityId              # LM-chosen
    automatic: bool = False
```

### 8.2.2 Opening volleys

Before closing to melee, the sides may exchange ranged fire.

* The LM sets the number of volleys, based on the scene. Zero is legal.
* Under most circumstances, all combatants get **at least one** volley with a bow or a
  thrown weapon (a spear or short spear).
* Greater separation may permit two or more volleys for bow users.
* All volley attacks resolve as normal ranged attacks (`8.5`).
* **A hero carrying a shield doubles its Parry modifier** against volley attacks, if aware
  of the incoming attack. A hero advancing into a confrontation is always aware. Implement
  as a context flag consumed by `shield_parry(context)`; do not hardcode "×2" in the
  attack path.
* PCs volley first by default; the LM may reverse this when circumstances favour the
  opposition.
* `OPENING_VOLLEY_COUNT` lets an effect grant an extra volley even when none are allowed —
  but not when the wielder is surprised.

### 8.2.3 Stance

At the start of each round every PC chooses a stance. **Adversaries do not choose
stances** — the stance system models the heroes' point of view. An adversary "inherits"
the stance of the hero it is attacking for action-ordering purposes.

| Stance | Attack roll | Close attacks against you | Combat task |
|---|---|---|---|
| Forward | `+1d` | `+1d` (they hit you more easily) | Intimidate Foe |
| Open | — | — | Rally Comrades |
| Defensive | `−1d` **per opponent engaging you** | `−1d` | Protect Companion |
| Rearward | ranged only | ranged only | Prepare Shot |

Close-combat stances may be assumed freely each round. **Rearward has entry requirements**:

```python
def may_take_rearward(state: CombatState, hero: CombatantId) -> bool:
    total_enemies = count_active_adversaries(state)
    company_size  = count_active_heroes(state)
    if total_enemies > 2 * company_size:
        return False
    # for each hero in Rearward (including this one) there must be 2 others in close combat
    rearward_after = count_rearward(state) + 1
    close_combat   = count_in_close_stances(state, excluding=hero)
    return close_combat >= 2 * rearward_after
```

Both conditions must hold. The LM may waive them when the terrain makes ranged attacks
easier — a narrow ledge, a mountain path — or when the Company heavily outnumbers the foe
(roughly three adventurers per human-sized enemy, or five per larger-than-human enemy).
Expose as `RearwardOverride` on the round input. At least one Cultural Virtue relaxes the
companion requirement to one; that comes through `STANCE_OPTIONS`.

### 8.2.4 Engagement

Only close-combat participants engage. **Heroes in Rearward cannot be engaged.**

Limits:

| Constraint | Human-sized foe | Larger-than-human foe |
|---|---|---|
| How many foes may engage one hero | 3 | 2 |
| How many heroes may engage one foe | 3 | 6 |

Two branches:

**More enemies than heroes** — the LM assigns:
1. One opponent to each unengaged hero in a close-combat stance.
2. Each remaining foe either joins an already-engaged hero (within limits) or **stands
   back** to use a ranged weapon. A foe standing back may target **any** hero in the fight,
   including those in Rearward.

**Heroes outnumber or match the enemy** — the players choose:
1. Each hero in a close stance picks an unengaged eligible adversary. If there are not
   enough, they must join one already engaged (within limits).
2. If heroes are in Rearward, foes may be left over; the LM decides whether those engage an
   already-engaged hero or stand back with ranged weapons.

Engagement **persists** across rounds until all opposition is defeated or a combatant
leaves. Re-run engagement each round only for combatants who became unengaged.

**Unengaged mid-round.** If a hero's opponent is killed or flees before the hero acts, the
hero may pick another adversary to attack in close combat — engaged or not — respecting
the limits. This is not a new engagement step; it is a target choice at action time.

A hero engaged by several foes chooses which to attack. An adversary engaged by several
heroes has its target chosen by the LM.

### 8.2.5 Action resolution order

**All heroes act, then all adversaries.**

Within the Company: strict stance order, Forward → Open → Defensive → Rearward. Ties
within a stance are resolved by player choice.

Within the opposition: stance order using the stance of the hero each adversary is
attacking. Adversaries that stood back unengaged with ranged weapons resolve **last**.

Each combatant takes **one main action** and **one secondary action**, in either order.

## 8.3 Actions

**Main actions** — anything requiring full attention:
* Make an attack (`8.5`).
* Attempt a Combat Task (`8.10`).
* Recover position after a knockback.
* Recover a dropped weapon, helm, or shield.
* Carry a fallen comrade to safety.
* Move across the battlefield.
* Remove a complication or gain an advantage with a BATTLE roll (`8.11`).

**Secondary actions** — quick or concurrent:
* Advance or retreat while fighting.
* Locate someone on the battlefield.
* Draw a weapon.
* Remove a helm, or drop a shield or weapon (to shed Load).
* Sing a Company song (`15.8`).
* Any task an effect has converted to a secondary action.

Novel actions are explicitly encouraged. The engine accepts a `CustomAction` with an
LM-supplied ability, TN, and success/failure consequences, and resolves it through
`rolls.resolve`. Guidance for the LM, worth surfacing in a UI: a round is at most about
30 seconds; if success grants an advantage, failure should impose a comparable
disadvantage.

Adversaries may take a main and a secondary action, though the LM will often give them
only a main action to keep play moving.

## 8.4 Adversary attack allocation

An adversary makes a number of attacks **up to its Might score**, and may split them
across different foes or concentrate them on one. Might is doing double duty: it is also
the number of Wounds needed to kill the creature outright (`12.3`).

## 8.5 The attack roll

```python
def resolve_attack(state, attacker, target, weapon, rng) -> AttackOutcome:
```

**Ability**: the Combat Proficiency matching the weapon. Brawling uses the highest
Combat Proficiency and takes `−1d`.

**Target Number** — asymmetric, and this is a common implementation error:

| Attacker | TN |
|---|---|
| Hero attacking an adversary | attacker's **STRENGTH TN** + target's **Parry** rating |
| Adversary attacking a hero | target hero's **Parry score** (base Parry + shield bonus + situational modifiers) |

The hero's STRENGTH TN never enters an adversary's attack. The adversary's own attribute
level never enters its attack TN.

**Dice modifiers** flowing into the `RollRequest`:
* Stance (`8.2.3`).
* Complications and advantages (`8.11`).
* Hope expenditure, support, Inspiration.
* Brawling `−1d`.
* Effects (Favoured when fighting a creature of Might ≥ 2; Favoured in a specific stance
  once per combat; Favoured against a named enemy type; etc.).
* Adversaries may spend a Hate/Resolve point for `+1d`.

**On success:**
1. **Endurance loss** equal to the weapon's effective Damage rating.
2. **Special Damage** — spend Success icons (`8.6`).
3. **Piercing Blow check** (`8.7`).

**On failure:** nothing. There is no fumble mechanic in combat beyond the Miserable rule.

```python
@dataclass(frozen=True, slots=True)
class AttackOutcome:
    roll: RollResult
    hit: bool
    endurance_loss: int
    icon_budget: IconBudget
    special_damage: tuple[SpecialDamageApplied, ...]
    piercing_blow: bool
    protection: ProtectionOutcome | None
    wound: WoundOutcome | None
    knockback_taken: bool
```

## 8.6 Special Damage

If a successful attack scores one or more Success icons, they may be spent — one icon per
result. Multiple icons may buy different results or the same result repeatedly. Spending
icons does not reduce the total or the degree of success.

**Options available to heroes**, gated by war gear:

| Option | Requires | Effect |
|---|---|---|
| Heavy Blow | any weapon | Additional Endurance loss equal to the attacker's **STRENGTH** rating. **+1 more** if using a two-handed weapon. |
| Fend Off | any close combat weapon | Modify your own Parry for the round: **+1** with Axes or Brawling weapons, **+2** with Swords, **+3** with Spears. |
| Pierce | Bows, Spears, Swords | Modify the **numeric Feat die result** of *this* attack by **+1** (Swords), **+2** (Bows), **+3** (Spears) — which can push it over the Piercing Blow threshold. Icon faces are unaffected. |
| Shield Thrust | a shield | If your **STRENGTH** exceeds the target's **Attribute Level**, push the target back: it loses `−1d` for the round. If triggered more than once, each use must target a **different** opponent. |

Three subtleties:

* **Pierce is retroactive within the same attack.** The order is: roll → note the numeric
  Feat result → spend Pierce icons to raise it → *then* evaluate the Piercing Blow
  threshold. Implement Piercing Blow evaluation strictly after icon spending. Pierce does
  nothing if the Feat die shows an icon face rather than a number.
* **Fend Off modifies the attacker's own Parry**, defensively, for the remainder of the
  round. It goes into `RoundFlags`, not into the damage calculation.
* The *Dour-handed* Virtue adds +1 to STRENGTH for Heavy Blow purposes and +1 to the Feat
  result for Pierce purposes. Route through `MODIFY_SPECIAL_DAMAGE` — never special-case.

**Options available to adversaries** (`12.5`): Heavy Blow (using **Attribute Level**
rather than STRENGTH), Pierce (a flat **+2** to the Feat result), Break Shield, Seize.
Every adversary may always choose Heavy Blow; the rest come from its stat block.

## 8.7 Piercing Blows and PROTECTION

**Threshold.** An attack scores a Piercing Blow on a Feat die result of **10 or the
auto-success icon face** (the rune for heroes; the eye for adversaries under inversion).

Effects lower the threshold: one Reward makes it 9+; Enchanted qualities make it 8+, or
9+, or "10 minus your VALOUR" against a Bane creature. All flow through
`PIERCING_THRESHOLD`, which returns the minimum numeric face that qualifies. The
auto-success icon face always qualifies regardless.

At least one weapon **cannot cause a Piercing Blow at all**; check
`WeaponType.can_cause_piercing_blow` first and short-circuit.

**PROTECTION roll.** The target immediately rolls:
* One Feat die plus a number of Success dice equal to the **PROTECTION value of the armour
  worn** — body armour plus helm, summed. Zero armour means a Feat die alone.
* **TN = the Injury rating of the attacker's weapon**, in the grip being used.
* **Failure ⇒ a Wound.**

Timing rule, easy to miss: if the attack's Endurance loss made the target Weary, the
**PROTECTION roll is made before the Weariness takes effect**. Sequence the state update
accordingly — compute the roll against pre-loss conditions, then apply the Endurance
change, then recompute conditions.

Modifiers to the PROTECTION roll:
* A Reward adds **+2 to the result**; an Enchanted quality adds **+3 or your VALOUR,
  whichever is higher**.
* Cultural Virtues can make all PROTECTION rolls Favoured (conditionally on not being
  Miserable).
* Enchanted armour can make the roll ignore Miserable and Weary.
* Attacker-side effects can make the target's PROTECTION roll **Ill-favoured** — a Cultural
  Virtue on ranged Piercing Blows, several adversary Fell Abilities, the Foe-slaying
  quality. Note one interaction: Foe-slaying converts an *already* Ill-favoured PROTECTION
  roll into an automatic Wound instead. Check that before rolling.

## 8.8 Wounds

**Heroes**: resist one Wound without lasting consequence; a second is life-threatening.

**First Wound:**
1. Set the Wounded flag.
2. Roll a Feat die on the **Wound Severity** table:

| Feat result | Severity | Effect |
|---|---|---|
| the rune | Moderate | No lasting damage, but exposure to worse if injured again. Recovers fully within hours at the end of the combat; clear the Wounded flag then. |
| 1–10 | Severe | The numeric value is the **number of days** until the injury mends. Record it. |
| the eye | Grievous | Knocked unconscious at zero Endurance and now **Dying**, exactly as if Wounded twice. |

Effects can modify this roll: one Cultural Virtue rolls two Feat dice and keeps the
better; an adversary Fell Ability makes it Ill-favoured. Both via
`MODIFY_WOUND_SEVERITY_ROLL`.

**Second Wound** (a Wound received while the Wounded box is already checked):
Endurance drops to zero, the hero falls unconscious, and is **Dying**. No second box is
recorded and **the severity roll is skipped**.

**Adversaries** are killed outright when Wounded — more precisely, a creature is slain
when it has taken Wounds equal to its **Might** (`12.3`).

**First Aid.** A HEALING roll reduces the severity of an injury by **1 day, plus 1 day per
Success icon**, to a minimum of 1 day. Pattern P3: `result.magnitude(1)`.
* A conscious hero may attempt it on themselves.
* Each hero may receive a successful First Aid only **once**.
* A **failed** HEALING roll cannot be retried until at least a day has passed — the failure
  is not immediately apparent.

## 8.9 Dying

A hero is Dying if Wounded twice, if the severity roll came up on the eye, or from certain
sources of injury (`16.4`).

* A successful **HEALING** roll within approximately **1 hour** saves them. Otherwise they
  die.
* If saved: they return to their senses in about an hour at **1 Endurance**. If Wounded,
  **add 10 days** to the mending time, less the days removed by that HEALING roll. The
  injury leaves a permanent narrative mark.

Model the clock in the campaign timeline (`17.3`) so that a Dying hero left untended while
the Company does something else actually dies.

## 8.10 Combat Tasks

Each is tied to a stance and is a **main action** unless an effect converts it. Success is
graded by icon tiers (pattern P4).

**Intimidate Foe** — Forward stance. Roll **AWE**.

| Tier | Effect |
|---|---|
| success | all opponents with **Might 1** are Weary on their next attack roll |
| +1 icon | also all opponents with **Might 2** |
| +2 icons | **all** adversaries in the fight |

**Rally Comrades** — Open stance. Roll **ENHEARTEN**. **Only one hero may attempt this in a
given round.**

| Tier | Effect |
|---|---|
| success | all Company members in **Forward** gain `+1d` on attack rolls **next round** |
| +1 icon | also those in **Open** |
| +2 icons | all heroes in any **close combat** stance |

**Protect Companion** — Defensive stance. Roll **BATTLE**. Names another hero in a close
combat stance. The next attack aimed at the protected hero loses `−1d`, **plus another
`−1d` per Success icon**.

**Prepare Shot** — Rearward stance. Roll **SCAN**. The attacker gains `+1d` on their next
ranged attack, **plus another `+1d` per Success icon**.

Adversaries can carry task-specific interactions: some are immune to Intimidate Foe unless
a magical success is achieved; some additionally lose a drive point when intimidated; some
expose a bespoke task (a RIDDLE roll against a slow-witted creature to strip drive points,
scaling by icons). All are `ActionContribution`s from the adversary's Fell Abilities, not
hardcoded branches.

## 8.11 Complications and advantages

The LM may declare a level of complication or advantage that modifies **all rolls made by
the heroes**:

| Level | Modifier | Typical circumstances | Ranged-specific |
|---|---|---|---|
| Moderately hindered | `−1d` | difficult terrain, poor weather, cramped quarters | medium range, good cover |
| Severely hindered | `−2d` | near-blinded by snow, deep mud or swift water, darkness | long range, very small target, darkness or heavy cover |
| Moderate advantage | `+1d` | high ground, favourable terrain feature | slow-moving or large target, scarce cover |
| Greater advantage | `+2d` | defender hindered, blinded, or badly impeded | stationary or very large target, no cover |

A hero may spend their **main action** on a **BATTLE roll** to remove a complication or
gain an advantage:
* Removing: success cancels it for the **next attack roll**; with one or more icons, for
  the **remainder of the fight**.
* Gaining: success grants it for the **next attack roll**; with one or more icons, for the
  **remainder of the fight**.

Model as `Complication` / `Advantage` objects with a `duration: NEXT_ATTACK | REST_OF_FIGHT`.

## 8.12 Knockback

Once per round, a **hero** may choose to be knocked back to **halve the Endurance loss from
a successful attack, rounding up**. The cost: their **next main action** is spent
recovering their fighting position.

* Once per round, not once per attack — track in `RoundFlags`.
* **Adversaries cannot choose knockback.**
* The choice is made after the loss is known but before it is applied.
* An Enchanted quality can *force* knockback on a creature that takes an Endurance loss of
  at least twice its Attribute Level, costing it its next main action.

## 8.13 Leaving the fight

Two routes:

1. **From Rearward** — assume Rearward, then simply leave when your turn comes. **No roll.**
   The same applies to adversaries that stood back unengaged.
2. **From Defensive** — assume Defensive and roll your attack normally. On a **success**
   you inflict no damage but escape the battlefield. On a **failure** you remain engaged.

## 8.14 End of combat

* Clear all `RoundFlags`.
* Fire `ON_COMBAT_END` (one Cultural Virtue restores Endurance equal to HEART or VALOUR,
  whichever is higher, to a hero who is neither Wounded nor Miserable).
* Clear Moderate injuries (`8.8`).
* Adversaries reduced to zero Endurance are "taken out of combat" — the LM may rule they
  are alive but incapacitated and could survive if rescued.
* Recompute conditions for every participant.
* Emit `CombatEnded` with a full summary.

## 8.15 Combat and Shadow

Note for the Eye of Mordor subsystem (`14`): most Eye Awareness triggers explicitly apply
**outside** of combat. Eye results rolled during combat and Shadow gained during combat do
**not** raise Eye Awareness. Pass `in_combat=True` in the context so the Eye subsystem can
filter. Do not let combat quietly inflate the Hunt.
