"""The attack roll and everything hanging off it (``08.5``-``08.9``, ``12.5``).

The pipeline, in the order ``08`` fixes it and for the reasons it gives:

1. :func:`roll_attack` — build the request and roll it. Nothing is applied.
2. The players choose: which Success icons to spend (``08.6``), and whether the target
   takes a knockback (``08.12``, "after the loss is known but before it is applied").
3. :func:`resolve_attack` — spend the icons, *then* evaluate the Piercing Blow threshold,
   then the PROTECTION roll, then the Wound.
4. :func:`apply_attack` — mutate and emit.

``08.5`` writes one ``resolve_attack`` that does all of it. It cannot: ``08.6`` puts a
player decision strictly between the attack roll and the Piercing Blow check — Pierce is
retroactive within the same attack — and ``01.4`` forbids a rule function from stopping to
ask. The split is the same one ``01.4`` mandates everywhere else, with the roll pulled out
in front of it so the choice can be made on real dice.

Two orderings in here are easy to get wrong and are asserted by ``19.4``:

* **Pierce before the threshold.** Roll, note the numeric Feat result, spend Pierce icons
  to raise it, and only then compare against the Piercing Blow threshold. An icon face is
  untouched by Pierce, and already qualifies.
* **PROTECTION before the Weariness it causes.** If the attack's Endurance loss would make
  the target Weary, the PROTECTION roll is still made against their pre-loss condition.
  The resolve/apply split gets this for free: :func:`resolve_attack` rolls, and only
  :func:`apply_attack` changes Endurance and recomputes the conditions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from tor.dice import FeatFace, Randomness, auto_success_face
from tor.effects.bus import EffectSource, fold_numeric
from tor.effects.hooks import (
    ActionContribution,
    FlagContribution,
    Hook,
)
from tor.errors import RuleViolation, StateError
from tor.events import Event, EventKind
from tor.model.abilities import BRAWLING
from tor.model.adversary import AdversaryAttack, AdversaryInstance
from tor.model.gear import WeaponInstance
from tor.model.hero import Hero
from tor.model.ids import AbilityId, CombatantId, EffectId
from tor.rolls import (
    IconBudget,
    RollPurpose,
    RollRequest,
    RollResult,
    attribute_tn,
    build_request,
    resolve,
)
from tor.rules.combat.state import Combatant, CombatState, Stance
from tor.rules.context import RulesContext
from tor.rules.resources import ChangeSource, change_endurance, recompute_conditions
from tor.tables import LookupTable, Sentinel

__all__ = [
    "BRAWLING_PENALTY_DICE",
    "FEND_OFF_BY_PROFICIENCY",
    "MISDEED_QUESTIONS",
    "PIERCE_BY_PROFICIENCY",
    "AttackDeclaration",
    "AttackForm",
    "AttackOutcome",
    "AttackRoll",
    "ProtectionOutcome",
    "SpecialDamage",
    "SpendPlan",
    "WoundOutcome",
    "WoundSeverity",
    "adversary_attack_form",
    "apply_attack",
    "attack_target_number",
    "hero_attack_form",
    "offered_special_damage",
    "resolve_attack",
    "roll_attack",
    "shield_parry",
]

#: ``03.3``: Brawling resolves at the highest Combat Proficiency and takes a penalty die.
#: The penalty is a die, not a lower rating.
BRAWLING_PENALTY_DICE = 1

#: ``08.7``: the base Piercing Blow threshold. Effects lower it through
#: ``PIERCING_THRESHOLD``; the auto-success icon face always qualifies regardless.
PIERCING_THRESHOLD = 10

#: ``08.6``. Not content: the four Combat Proficiencies are engine knowledge (``20.2``),
#: ``05.1``'s file tree has no table for these, and ``08.6`` states them as rules.
FEND_OFF_BY_PROFICIENCY: Mapping[AbilityId, int] = {
    AbilityId("axes"): 1,
    AbilityId("swords"): 2,
    AbilityId("spears"): 3,
    BRAWLING: 1,
}
PIERCE_BY_PROFICIENCY: Mapping[AbilityId, int] = {
    AbilityId("swords"): 1,
    AbilityId("bows"): 2,
    AbilityId("spears"): 3,
}
#: ``12.5``: the adversary version is a flat value, unlike the hero version.
ADVERSARY_PIERCE = 2


class SpecialDamage(StrEnum):
    """What a Success icon buys (``08.6``, ``12.5``)."""

    HEAVY_BLOW = "heavy_blow"
    FEND_OFF = "fend_off"
    PIERCE = "pierce"
    SHIELD_THRUST = "shield_thrust"
    BREAK_SHIELD = "break_shield"
    SEIZE = "seize"
    #: ``12.5``: a seized hero frees themselves with an icon from a successful attack.
    BREAK_FREE = "break_free"


#: ``12.5``: every adversary can always choose Heavy Blow, so the engine adds it and
#: content must never list it (``05.7``).
_ALWAYS_AVAILABLE: frozenset[SpecialDamage] = frozenset({SpecialDamage.HEAVY_BLOW})


class WoundSeverity(StrEnum):
    """The three rows of the Wound Severity table (``08.8``)."""

    MODERATE = "moderate"
    SEVERE = "severe"
    GRIEVOUS = "grievous"


@dataclass(frozen=True, slots=True)
class AttackForm:
    """What the attacker is swinging, read off either sheet.

    A hero's weapon and an adversary's attack form carry the same five numbers under
    different names, and the attack pipeline needs only those. Keeping one view of them is
    what stops ``resolve_attack`` from branching on ``is_hero`` five times.
    """

    name: str
    rating: int
    damage: int
    #: ``None`` for a grip with no Injury rating — unarmed has neither.
    injury: int | None
    proficiency: AbilityId
    ranged: bool = False
    two_handed: bool = False
    can_cause_piercing_blow: bool = True
    special_damage: frozenset[SpecialDamage] = frozenset()
    #: Heroes only: the instance whose Rewards are registered under its own source.
    instance: WeaponInstance | None = None

    @property
    def item_ref(self) -> str | None:
        return None if self.instance is None else self.instance.ref


def hero_attack_form(
    hero: Hero, weapon: WeaponInstance, *, ctx: RulesContext, brawling: bool = False
) -> AttackForm:
    """The view of a hero's weapon (``03.5``, ``08.5``).

    ``brawling`` resolves the attack at the **highest** Combat Proficiency rather than the
    weapon's own, which is what ``03.3`` means by Brawling being a mode rather than a
    proficiency. The penalty die it costs is applied by :func:`roll_attack`, not folded
    into the rating here.
    """
    kind = ctx.gear.weapon(weapon.type_id)
    proficiency = BRAWLING if brawling else AbilityId(str(kind.proficiency))
    rating = hero.brawling_rating() if proficiency == BRAWLING else hero.rating(proficiency)
    return AttackForm(
        name=weapon.name or kind.name,
        rating=rating,
        damage=kind.damage,
        injury=kind.injury(two_handed=weapon.two_handed),
        proficiency=proficiency,
        ranged=kind.ranged,
        two_handed=weapon.two_handed,
        can_cause_piercing_blow=kind.can_cause_piercing_blow,
        # Left empty on purpose: ``08.6`` gates a hero's options on war gear, which
        # ``offered_special_damage`` reads off the hero. Only an adversary carries a list.
        special_damage=frozenset(),
        instance=weapon,
    )


def adversary_attack_form(instance: AdversaryInstance, attack: AdversaryAttack) -> AttackForm:
    """The view of one of an adversary's attack forms (``12.1``).

    ``12.5``: Heavy Blow is added implicitly, the rest come from the attack form's own list
    plus the creature's ``always_available_special_damage``.
    """
    declared = {*attack.special_damage, *instance.template.always_available_special_damage}
    options = {SpecialDamage(name) for name in declared} | _ALWAYS_AVAILABLE
    return AttackForm(
        name=attack.name,
        rating=attack.rating,
        damage=attack.damage,
        injury=attack.injury,
        proficiency=BRAWLING,
        ranged=attack.ranged,
        can_cause_piercing_blow=True,
        special_damage=frozenset(options),
    )


def offered_special_damage(
    attacker: Combatant, form: AttackForm, *, ctx: RulesContext
) -> frozenset[SpecialDamage]:
    """The icon-spend options this attacker has with this weapon (``08.6``, ``12.5``).

    Gated by war gear for a hero — Fend Off needs a close-combat weapon, Pierce needs Bows,
    Spears or Swords, Shield Thrust needs a shield — and by the stat block for an adversary.
    ``SPECIAL_DAMAGE_OPTIONS`` may add to the set, which is how an item or a Fell Ability
    grants an option the sheet does not carry.
    """
    if attacker.is_hero:
        offered = {SpecialDamage.HEAVY_BLOW}
        if not form.ranged:
            offered.add(SpecialDamage.FEND_OFF)
        if form.proficiency in PIERCE_BY_PROFICIENCY:
            offered.add(SpecialDamage.PIERCE)
        if attacker.hero.gear.shield is not None and not attacker.hero.gear.shield.dropped:
            offered.add(SpecialDamage.SHIELD_THRUST)
        if attacker.seized_by is not None:
            offered.add(SpecialDamage.BREAK_FREE)
    else:
        offered = set(form.special_damage)

    hook_ctx = ctx.hook_context(Hook.SPECIAL_DAMAGE_OPTIONS, attacker.actor, form=form)
    for contribution in ctx.bus(attacker.ref).collect(Hook.SPECIAL_DAMAGE_OPTIONS, hook_ctx):
        if isinstance(contribution, ActionContribution):
            offered |= {SpecialDamage(name) for name in contribution.payload.get("options", ())}
    return frozenset(offered)


# -- target numbers and Parry ------------------------------------------------------------


def shield_parry(combatant: Combatant, *, ctx: RulesContext, doubled: bool = False) -> int:
    """A hero's shield contribution to Parry (``03.5``, ``08.2.2``).

    ``doubled`` is the opening-volley rule: a hero carrying a shield doubles its Parry
    modifier against volley attacks, if aware of the incoming attack. It is a parameter
    rather than a branch inside the attack path, because ``08.2.2`` says so in as many
    words — the awareness is the caller's ruling, and a hero advancing into a
    confrontation is always aware.

    A smashed or dropped shield contributes nothing, and needs no special case: Break
    Shield unregisters its effects and sets the flag (``12.5``).
    """
    if not combatant.is_hero:
        return 0
    shield = combatant.hero.gear.shield
    if shield is None or shield.dropped or shield.smashed:
        return 0
    base = ctx.gear.shield_type(shield.type_id).parry_bonus
    hook_ctx = ctx.hook_context(
        Hook.MODIFY_SHIELD_PARRY, combatant.actor, item=shield, item_ref=shield.ref
    )
    bonus = ctx.bus(combatant.ref).apply_numeric(Hook.MODIFY_SHIELD_PARRY, hook_ctx, base).value
    return max(0, bonus) * (2 if doubled else 1)


def hero_parry_score(
    combatant: Combatant, *, ctx: RulesContext, shield_doubled: bool = False
) -> int:
    """Base Parry plus the shield plus this round's situational modifiers (``08.5``).

    This is the number an **adversary** attacking the hero must meet. The hero's STRENGTH
    TN never enters it.
    """
    hero = combatant.hero
    hook_ctx = ctx.hook_context(Hook.MODIFY_PARRY, hero)
    base = ctx.bus(combatant.ref).apply_numeric(Hook.MODIFY_PARRY, hook_ctx, hero.parry.base).value
    return max(
        0,
        base
        + shield_parry(combatant, ctx=ctx, doubled=shield_doubled)
        + combatant.round_flags.parry_bonus,
    )


def attack_target_number(
    attacker: Combatant,
    target: Combatant,
    *,
    ctx: RulesContext,
    shield_doubled: bool = False,
) -> int:
    """The TN for one attack (``08.5``), which is **asymmetric**.

    A hero attacking an adversary rolls against their own STRENGTH TN plus the target's
    Parry *rating* — a modifier, not a TN in itself (``12.1``). An adversary attacking a
    hero rolls against the hero's Parry *score*. The hero's STRENGTH TN never enters an
    adversary's attack, and the adversary's Attribute Level never enters its own attack TN.
    Getting this backwards is the common implementation error ``08.5`` warns about.
    """
    if attacker.is_hero:
        hero = attacker.hero
        base = (
            attribute_tn(hero.attributes.strength, short_campaign=ctx.short_campaign)
            + target.adversary.template.parry
        )
    else:
        base = hero_parry_score(target, ctx=ctx, shield_doubled=shield_doubled)

    hook_ctx = ctx.hook_context(Hook.MODIFY_ATTACK_TN, attacker.actor, target=target.actor)
    return max(1, ctx.bus(attacker.ref).apply_numeric(Hook.MODIFY_ATTACK_TN, hook_ctx, base).value)


# -- the roll ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttackDeclaration:
    """One attack, as the player or Loremaster declares it."""

    attacker: CombatantId
    target: CombatantId
    form: AttackForm
    brawling: bool = False
    spend_hope: bool = False
    bonus_dice: int = 0
    penalty_dice: int = 0
    #: ``08.2.2``: an opening volley, against which an aware shield-bearer doubles Parry.
    volley: bool = False
    shield_doubled: bool = False
    #: ``12.2``: an adversary may spend a drive point for ``+1d``.
    spend_drive: int = 0


@dataclass(frozen=True, slots=True)
class AttackRoll:
    """The dice, before any icon is spent (``08.5``).

    Carries the raw Endurance loss a hit would deal so a UI can show what is at stake while
    the player decides how to spend the icons and whether to take a knockback.
    """

    declaration: AttackDeclaration
    roll: RollResult
    hit: bool
    endurance_loss: int
    offered: frozenset[SpecialDamage]

    @property
    def icons(self) -> int:
        return self.roll.icons if self.hit else 0


def _stance_dice(attacker: Combatant, target: Combatant) -> tuple[int, int]:
    """Stance modifiers on an attack (``08.2.3``), as ``(bonus, penalty)``.

    Forward attacks with ``+1d``. Defensive loses a die **per opponent engaging you** — per
    opponent, not a flat penalty, which is what makes Defensive costly when surrounded.
    A hero in Forward or Defensive is also easier or harder to hit, which is the *target*
    side of the same table and is applied when they are attacked.
    """
    bonus = penalty = 0
    if attacker.is_hero:
        if attacker.stance is Stance.FORWARD:
            bonus += 1
        elif attacker.stance is Stance.DEFENSIVE:
            penalty += len(attacker.engaged_with)
    if target.is_hero and not attacker.is_hero:
        if target.stance is Stance.FORWARD:
            bonus += 1
        elif target.stance is Stance.DEFENSIVE:
            penalty += 1
    return bonus, penalty


def roll_attack(
    state: CombatState, decl: AttackDeclaration, *, ctx: RulesContext, rng: Randomness
) -> AttackRoll:
    """Roll one attack (``08.5``). Applies nothing.

    Every modifier ``08.5`` lists flows into the request: stance, the complications and
    advantages of ``08.11``, Hope, support and Inspiration, the Brawling penalty die, the
    round's flags, and whatever ``MODIFY_ROLL_REQUEST`` contributes. An adversary may
    spend drive for ``+1d`` (``12.2``); the point is spent here, before the dice, because
    a spend decided after seeing them would not be a spend.
    """
    attacker = state.combatant(decl.attacker)
    target = state.combatant(decl.target)
    _check_attack_is_legal(attacker, target, decl)

    if decl.spend_drive:
        attacker.adversary.spend_drive(decl.spend_drive)

    stance_bonus, stance_penalty = _stance_dice(attacker, target)
    flags = attacker.round_flags
    bonus = (
        decl.bonus_dice
        + stance_bonus
        + flags.attack_bonus_dice
        + decl.spend_drive
        + (flags.prepared_shot if decl.form.ranged else 0)
    )
    penalty = (
        decl.penalty_dice
        + stance_penalty
        + flags.attack_penalty_dice
        + target.round_flags.incoming_attack_penalty
        + (BRAWLING_PENALTY_DICE if decl.brawling else 0)
    )
    if attacker.is_hero:
        bonus += max(0, state.hero_dice_modifier())
        penalty += max(0, -state.hero_dice_modifier())

    weary = flags.weary or (attacker.is_hero and attacker.hero.conditions.weary)
    request = build_request(
        attacker.actor,
        decl.form.proficiency,
        bus=ctx.bus(attacker.ref),
        rating=decl.form.rating,
        target_number=attack_target_number(
            attacker, target, ctx=ctx, shield_doubled=decl.shield_doubled
        ),
        purpose=RollPurpose.ATTACK,
        spend_hope=decl.spend_hope,
        bonus_dice=bonus,
        penalty_dice=penalty,
        weary=weary,
        eye_is_auto_failure=attacker.is_hero and attacker.hero.conditions.miserable,
        icon_inverted=not attacker.is_hero and attacker.adversary.template.icon_inverted,
        scene=ctx.scene,
        environment=state.environment,
        target=target.actor,
        extra={"ranged": decl.form.ranged, "volley": decl.volley},
        extra_hooks=(Hook.MODIFY_ATTACK_TN,),
    )
    roll = resolve(request, rng)
    loss = _damage_rating(attacker, decl.form, ctx=ctx) if roll.succeeded else 0
    return AttackRoll(
        declaration=decl,
        roll=roll,
        hit=roll.succeeded,
        endurance_loss=loss,
        offered=offered_special_damage(attacker, decl.form, ctx=ctx),
    )


def _check_attack_is_legal(attacker: Combatant, target: Combatant, decl: AttackDeclaration) -> None:
    if not attacker.active:
        raise StateError(f"{attacker.ref} is no longer in the fight")
    if not target.active:
        raise RuleViolation(
            f"{target.ref} is already out of the fight",
            rule_reference="attack_target_active",
        )
    if attacker.is_hero == target.is_hero:
        raise RuleViolation(
            f"{attacker.ref} and {target.ref} are on the same side",
            rule_reference="attack_across_sides",
        )
    if attacker.seized_by is not None and not decl.brawling:
        # 12.5: a seized hero can only fight in Forward stance making Brawling attacks.
        raise RuleViolation(
            f"{attacker.ref} is seized and may only make Brawling attacks",
            rule_reference="seized_brawling_only",
        )
    if attacker.is_hero and attacker.stance is Stance.REARWARD and not decl.form.ranged:
        raise RuleViolation(
            f"{attacker.ref} is in Rearward, which is ranged attacks only",
            rule_reference="rearward_is_ranged",
        )


def _damage_rating(attacker: Combatant, form: AttackForm, *, ctx: RulesContext) -> int:
    """The weapon's Damage after ``MODIFY_DAMAGE_RATING`` (``03.5``, ``08.5``)."""
    hook_ctx = ctx.hook_context(
        Hook.MODIFY_DAMAGE_RATING, attacker.actor, item_ref=form.item_ref, form=form
    )
    rated = ctx.bus(attacker.ref).apply_numeric(Hook.MODIFY_DAMAGE_RATING, hook_ctx, form.damage)
    return max(0, rated.value)


def _injury_rating(attacker: Combatant, form: AttackForm, *, ctx: RulesContext) -> int | None:
    """The Injury rating in the grip used, after ``MODIFY_INJURY_RATING`` (``08.7``)."""
    if form.injury is None:
        return None
    hook_ctx = ctx.hook_context(
        Hook.MODIFY_INJURY_RATING, attacker.actor, item_ref=form.item_ref, form=form
    )
    return (
        ctx.bus(attacker.ref).apply_numeric(Hook.MODIFY_INJURY_RATING, hook_ctx, form.injury).value
    )


# -- spending the icons --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpendPlan:
    """What the players do with the icons, and whether the target rides the blow.

    ``spends`` is applied in order and may repeat an option; ``08.6`` says multiple icons
    may buy different results or the same result repeatedly. ``shield_thrust_targets``
    names a different opponent per use, which ``08.6`` requires.
    """

    spends: tuple[SpecialDamage, ...] = ()
    #: ``08.12``: the target chooses this after the loss is known, before it is applied.
    knockback: bool = False
    shield_thrust_targets: tuple[CombatantId, ...] = ()


@dataclass(frozen=True, slots=True)
class SpecialDamageApplied:
    option: SpecialDamage
    amount: int = 0
    target: CombatantId | None = None


@dataclass(frozen=True, slots=True)
class ProtectionOutcome:
    """The target's PROTECTION roll (``08.7``). ``roll`` is ``None`` for an automatic Wound."""

    wounded: bool
    roll: RollResult | None
    modifier: int = 0
    automatic: bool = False


@dataclass(frozen=True, slots=True)
class WoundOutcome:
    """What a Wound came to (``08.8``, ``12.3``)."""

    severity: WoundSeverity | None = None
    injury_days: int = 0
    dying: bool = False
    #: Adversaries do not roll severity and carry no Wounded box; they are slain at Might.
    slain: bool = False
    roll: RollResult | None = None
    cancelled_by: EffectId | None = None


@dataclass(frozen=True, slots=True)
class AttackOutcome:
    """Everything one attack did, fully described and applied to nothing (``08.5``)."""

    declaration: AttackDeclaration
    roll: RollResult
    hit: bool
    endurance_loss: int
    icon_budget: IconBudget
    special_damage: tuple[SpecialDamageApplied, ...] = ()
    piercing_blow: bool = False
    protection: ProtectionOutcome | None = None
    wound: WoundOutcome | None = None
    knockback_taken: bool = False
    #: ``12.6``: a ``DAMAGE_INTERCEPT`` replaced the ordinary loss.
    intercepted_by: EffectId | None = None
    restore_full_endurance: bool = False
    #: ``12.2``: killing a Resolve-bearing foe must always be weighed as a Misdeed.
    misdeed_check: bool = False


def resolve_attack(
    state: CombatState,
    attack: AttackRoll,
    plan: SpendPlan,
    *,
    ctx: RulesContext,
    rng: Randomness,
    severity_table: LookupTable[Any] | None = None,
) -> AttackOutcome:
    """Spend the icons, check the Piercing Blow, roll PROTECTION and the Wound (``08.5``).

    Applies nothing. ``severity_table`` is ``08.8``'s Wound Severity table, injected the
    way ``tor.rules.injury`` injects the Endurance Loss table — it is content, and the
    engine must not carry it. It is needed only when a hero takes their **first** Wound.
    """
    decl = attack.declaration
    attacker = state.combatant(decl.attacker)
    target = state.combatant(decl.target)
    budget = IconBudget.from_result(attack.roll)
    if not attack.hit:
        return AttackOutcome(
            declaration=decl, roll=attack.roll, hit=False, endurance_loss=0, icon_budget=budget
        )

    applied, feat_shift, loss = _spend_icons(state, attacker, target, attack, plan, budget, ctx=ctx)
    pierced = _piercing_blow(attacker, target, attack.roll, decl.form, feat_shift, ctx=ctx)

    intercept = _intercept_damage(target, loss, ctx=ctx)
    if intercept is not None:
        pierced = pierced or bool(intercept.payload.get("piercing_blow"))

    knockback = plan.knockback and _knockback_allowed(target)
    if knockback:
        loss = -(-loss // 2)  # halved, rounding up (08.12)

    protection = _protection(attacker, target, attack.roll, decl.form, pierced, ctx=ctx, rng=rng)
    wound = None
    if protection is not None and protection.wounded:
        wound = _wound(target, decl.form, ctx=ctx, rng=rng, table=severity_table)

    return AttackOutcome(
        declaration=decl,
        roll=attack.roll,
        hit=True,
        endurance_loss=loss,
        icon_budget=budget,
        special_damage=applied,
        piercing_blow=pierced,
        protection=protection,
        wound=wound,
        knockback_taken=knockback,
        intercepted_by=None if intercept is None else intercept.source,
        restore_full_endurance=(
            intercept is not None and bool(intercept.payload.get("restore_full_endurance"))
        ),
        misdeed_check=not target.is_hero and target.adversary.template.misdeed_check_required,
    )


def _spend_icons(
    state: CombatState,
    attacker: Combatant,
    target: Combatant,
    attack: AttackRoll,
    plan: SpendPlan,
    budget: IconBudget,
    *,
    ctx: RulesContext,
) -> tuple[tuple[SpecialDamageApplied, ...], int, int]:
    """Apply ``08.6``'s icon spends, returning what happened, the Pierce shift and the loss.

    Nothing is written to the combatants here except the attacker's own Parry from Fend Off
    and the round flags Shield Thrust imposes, because both are ``RoundFlags`` and expire
    on their own. The Endurance loss is returned rather than applied.
    """
    applied: list[SpecialDamageApplied] = []
    feat_shift = 0
    loss = attack.endurance_loss
    thrust_targets = list(plan.shield_thrust_targets)

    for option in plan.spends:
        if option not in attack.offered:
            raise RuleViolation(
                f"{attacker.ref} cannot spend an icon on {option.value!r} with "
                f"{attack.declaration.form.name}",
                rule_reference="special_damage_offered",
                suggestion=f"offered: {sorted(o.value for o in attack.offered)}",
            )
        budget.spend(option.value)
        bonus = _special_damage_bonus(attacker, option, ctx=ctx)

        if option is SpecialDamage.HEAVY_BLOW:
            amount = _heavy_blow(attacker, attack.declaration.form) + bonus
            loss += amount
            applied.append(SpecialDamageApplied(option, amount=amount))
        elif option is SpecialDamage.PIERCE:
            amount = _pierce_value(attacker, attack.declaration.form) + bonus
            feat_shift += amount
            applied.append(SpecialDamageApplied(option, amount=amount))
        elif option is SpecialDamage.FEND_OFF:
            amount = FEND_OFF_BY_PROFICIENCY.get(attack.declaration.form.proficiency, 1) + bonus
            attacker.round_flags.parry_bonus += amount
            applied.append(SpecialDamageApplied(option, amount=amount))
        elif option is SpecialDamage.SHIELD_THRUST:
            applied.append(_shield_thrust(state, attacker, target, thrust_targets))
        elif option is SpecialDamage.BREAK_SHIELD:
            applied.append(_break_shield(target, ctx=ctx))
        elif option is SpecialDamage.SEIZE:
            target.seized_by = attacker.ref
            target.stance = Stance.FORWARD
            applied.append(SpecialDamageApplied(option, target=target.ref))
        else:  # BREAK_FREE
            attacker.seized_by = None
            applied.append(SpecialDamageApplied(option, target=attacker.ref))

    return tuple(applied), feat_shift, loss


def _special_damage_bonus(attacker: Combatant, option: SpecialDamage, *, ctx: RulesContext) -> int:
    """``MODIFY_SPECIAL_DAMAGE`` (``08.6``).

    The *Dour-handed* Virtue adds +1 to STRENGTH for Heavy Blow and +1 to the Feat result
    for Pierce — one effect, both uses, which is why the option travels in the context
    rather than the engine special-casing the Virtue.
    """
    if option not in (SpecialDamage.HEAVY_BLOW, SpecialDamage.PIERCE):
        return 0
    hook_ctx = ctx.hook_context(Hook.MODIFY_SPECIAL_DAMAGE, attacker.actor, option=option.value)
    contributions = ctx.bus(attacker.ref).collect(Hook.MODIFY_SPECIAL_DAMAGE, hook_ctx)
    ctx.consume(attacker.ref, contributions, hook_ctx)
    return fold_numeric(contributions, 0).value


def _heavy_blow(attacker: Combatant, form: AttackForm) -> int:
    """``08.6`` for heroes, ``12.5`` for adversaries.

    A hero adds STRENGTH, and **+1 more** with a two-handed weapon. An adversary adds its
    Attribute Level, and gets no two-handed bonus — it has no grips.
    """
    if not attacker.is_hero:
        return attacker.adversary.template.attribute_level
    return attacker.hero.attributes.strength + (1 if form.two_handed else 0)


def _pierce_value(attacker: Combatant, form: AttackForm) -> int:
    """``08.6``: +1 Swords, +2 Bows, +3 Spears. ``12.5``: a flat +2 for adversaries."""
    if not attacker.is_hero:
        return ADVERSARY_PIERCE
    return PIERCE_BY_PROFICIENCY[form.proficiency]


def _shield_thrust(
    state: CombatState,
    attacker: Combatant,
    default_target: Combatant,
    remaining: list[CombatantId],
) -> SpecialDamageApplied:
    """``08.6``: push the target back if STRENGTH exceeds its Attribute Level.

    Each repeat must name a **different** opponent, so the targets are consumed from a
    list. The push costs the victim a die for the round, which is a ``RoundFlags`` entry
    and expires with everything else.
    """
    victim = state.combatant(remaining.pop(0)) if remaining else default_target
    if victim.is_hero:
        raise RuleViolation(
            f"Shield Thrust pushes an opponent, not {victim.ref}",
            rule_reference="shield_thrust_target",
        )
    if attacker.hero.attributes.strength <= victim.adversary.template.attribute_level:
        return SpecialDamageApplied(SpecialDamage.SHIELD_THRUST, amount=0, target=victim.ref)
    victim.round_flags.attack_penalty_dice += 1
    return SpecialDamageApplied(SpecialDamage.SHIELD_THRUST, amount=1, target=victim.ref)


def _break_shield(target: Combatant, *, ctx: RulesContext) -> SpecialDamageApplied:
    """``12.5``: smash the shield, unless a Reward or magical quality protects it.

    Unregistering the shield's source is the whole implementation: Parry is a
    ``DerivedStat`` recomputed from what is registered (``03.4.2``), so nothing else needs
    to be told the shield is gone (``12.5``).
    """
    shield = target.hero.gear.shield if target.is_hero else None
    if shield is None or shield.smashed:
        return SpecialDamageApplied(SpecialDamage.BREAK_SHIELD, amount=0, target=target.ref)
    if shield.unsmashable:
        # 15.4.1: an item bearing a Reward can never be lost, broken, or taken.
        return SpecialDamageApplied(SpecialDamage.BREAK_SHIELD, amount=0, target=target.ref)
    shield.smashed = True
    ctx.bus(target.ref).unregister_source(EffectSource.item(shield.ref))
    return SpecialDamageApplied(SpecialDamage.BREAK_SHIELD, amount=1, target=target.ref)


# -- piercing blows, PROTECTION and Wounds --------------------------------------------------


def _piercing_blow(
    attacker: Combatant,
    target: Combatant,
    roll: RollResult,
    form: AttackForm,
    feat_shift: int,
    *,
    ctx: RulesContext,
) -> bool:
    """``08.7``. Evaluated strictly **after** the icons are spent, because Pierce is
    retroactive within the same attack.

    At least one weapon cannot cause one at all, which short-circuits before anything else.
    The auto-success icon face always qualifies whatever the threshold, and Pierce does
    nothing to it — an icon is not a number.
    """
    if not form.can_cause_piercing_blow:
        return False
    face = roll.feat_numeric_value(feat_shift)
    if isinstance(face, FeatFace):
        return face is auto_success_face(inverted=roll.request.icon_inverted)
    hook_ctx = ctx.hook_context(
        Hook.PIERCING_THRESHOLD, attacker.actor, target=target.actor, item_ref=form.item_ref
    )
    threshold = ctx.bus(attacker.ref).apply_numeric(
        Hook.PIERCING_THRESHOLD, hook_ctx, PIERCING_THRESHOLD
    )
    return face >= threshold.value


def _protection(
    attacker: Combatant,
    target: Combatant,
    attack_roll: RollResult,
    form: AttackForm,
    pierced: bool,
    *,
    ctx: RulesContext,
    rng: Randomness,
) -> ProtectionOutcome | None:
    """The PROTECTION roll (``08.7``). ``None`` when no Piercing Blow was scored.

    One Feat die plus a Success die per point of armour — body armour and helm summed for a
    hero, the stat block's Armour for a creature. The TN is the Injury rating of the
    attacker's weapon in the grip being used; a weapon with no Injury rating cannot Wound.

    Attacker-side effects reach the roll through ``MODIFY_TARGET_PROTECTION_ROLL``. One
    interaction has to be checked **before** rolling: Foe-slaying turns an *already*
    Ill-favoured PROTECTION roll into an automatic Wound instead of a roll.

    The roll is made against the target's condition **before** this attack's Endurance
    loss, which is why nothing here touches Endurance.
    """
    if not pierced:
        return None
    target_number = _injury_rating(attacker, form, ctx=ctx)
    if target_number is None:
        return None

    ill_favoured: list[str] = []
    automatic = False
    # Both hooks reach the same roll. ``ON_PIERCING_BLOW`` is the one ``04.3.3`` names for
    # Fierce Shot — "a ranged Piercing Blow makes the target's PROTECTION Ill-favoured" —
    # so it fires here, between scoring the blow and rolling against it, rather than as an
    # observer after the fact that could no longer affect anything.
    for hook in (Hook.ON_PIERCING_BLOW, Hook.MODIFY_TARGET_PROTECTION_ROLL):
        hook_ctx = ctx.hook_context(
            hook, attacker.actor, target=target.actor, ranged=form.ranged, item_ref=form.item_ref
        )
        contributions = ctx.bus(attacker.ref).collect(hook, hook_ctx)
        ctx.consume(attacker.ref, contributions, hook_ctx)
        for contribution in contributions:
            if not isinstance(contribution, FlagContribution):
                continue
            if contribution.flag == "ill_favoured":
                ill_favoured.append(str(contribution.source))
            elif contribution.flag == "wound_if_ill_favoured":
                automatic = True
    # Foe-slaying turns an *already* Ill-favoured PROTECTION roll into an automatic Wound,
    # so the two have to be read together and checked before rolling (08.7).
    if automatic and ill_favoured:
        return ProtectionOutcome(wounded=True, roll=None, automatic=True)

    armour, modifier, favoured, condition_immune = _protection_profile(target, ctx=ctx)
    weary = target.is_hero and target.hero.conditions.weary and not condition_immune
    miserable = target.is_hero and target.hero.conditions.miserable and not condition_immune
    request = RollRequest(
        rating=armour,
        target_number=target_number,
        favoured_sources=tuple(favoured),
        ill_favoured_sources=tuple(ill_favoured),
        weary=weary,
        eye_is_auto_failure=miserable,
        icon_inverted=not target.is_hero and target.adversary.template.icon_inverted,
        purpose=RollPurpose.PROTECTION,
    )
    roll = resolve(request, rng)
    # A Reward adds to the *result*, not to the dice, so it can rescue a numeric near-miss
    # — but not an automatic failure, which never had a total to add to (02.3.4).
    rescued = modifier > 0 and roll.auto is None and roll.total + modifier >= target_number
    passed = roll.succeeded or rescued
    return ProtectionOutcome(wounded=not passed, roll=roll, modifier=modifier)


def _protection_profile(
    target: Combatant, *, ctx: RulesContext
) -> tuple[int, int, list[str], bool]:
    """``(armour dice, result modifier, favoured sources, ignores conditions)`` (``08.7``)."""
    if target.is_hero:
        gear = target.hero.gear
        armour = sum(
            ctx.gear.armour_type(slot.type_id).protection
            for slot in (gear.armour, gear.helm)
            if slot is not None and not slot.dropped
        )
    else:
        armour = target.adversary.template.armour

    hook_ctx = ctx.hook_context(Hook.MODIFY_PROTECTION_ROLL, target.actor)
    modifier = 0
    favoured: list[str] = []
    condition_immune = False
    for contribution in ctx.bus(target.ref).collect(Hook.MODIFY_PROTECTION_ROLL, hook_ctx):
        if isinstance(contribution, FlagContribution):
            if contribution.flag == "favoured":
                favoured.append(str(contribution.value))
            elif contribution.flag == "ignore_conditions":
                condition_immune = True
        else:
            modifier += fold_numeric([contribution], 0).value
    return armour, modifier, favoured, condition_immune


def _wound(
    target: Combatant,
    form: AttackForm,
    *,
    ctx: RulesContext,
    rng: Randomness,
    table: LookupTable[Any] | None,
) -> WoundOutcome:
    """Resolve one Wound (``08.8``, ``12.3``).

    An adversary never rolls severity and carries no Wounded box: it is slain once its
    Wounds equal its Might. A hero's **second** Wound skips the severity roll entirely —
    Endurance to zero, unconscious, Dying.

    ``WOUND_INTERCEPT`` fires first, and is one of only two hooks that may veto a state
    change rather than modify a value (``12.6``). A weapon enchanted against the creature's
    type suppresses it.
    """
    cancelled = _intercept_wound(target, form, ctx=ctx)
    if cancelled is not None:
        return WoundOutcome(cancelled_by=cancelled)

    if not target.is_hero:
        creature = target.adversary
        return WoundOutcome(slain=creature.wounds_taken + 1 >= creature.template.might)

    if target.hero.conditions.wounded:
        return WoundOutcome(dying=True)

    if table is None:
        raise StateError(
            "a hero's first Wound reads the Wound Severity table, which is content and "
            "must be injected (08.8)"
        )
    hook_ctx = ctx.hook_context(Hook.MODIFY_WOUND_SEVERITY_ROLL, target.actor)
    favoured: list[str] = []
    ill_favoured: list[str] = []
    for contribution in ctx.bus(target.ref).collect(Hook.MODIFY_WOUND_SEVERITY_ROLL, hook_ctx):
        if isinstance(contribution, FlagContribution):
            (favoured if contribution.flag == "favoured" else ill_favoured).append(
                str(contribution.source)
            )
    roll = resolve(
        RollRequest(
            favoured_sources=tuple(favoured),
            ill_favoured_sources=tuple(ill_favoured),
            purpose=RollPurpose.WOUND_SEVERITY,
        ),
        rng,
    )
    row = table.lookup(roll.kept_feat)
    severity = WoundSeverity(str(row["severity"]))
    days = row.get("days")
    injury_days = roll.kept_feat if days is Sentinel.ROLL else int(days or 0)
    return WoundOutcome(
        severity=severity,
        injury_days=int(injury_days) if severity is WoundSeverity.SEVERE else 0,
        dying=severity is WoundSeverity.GRIEVOUS,
        roll=roll,
    )


def _intercept_damage(
    target: Combatant, loss: int, *, ctx: RulesContext
) -> ActionContribution | None:
    """``DAMAGE_INTERCEPT`` (``12.6``), fired **before** the Endurance change is applied.

    Narrow by design: the interceptor either proceeds or replaces the outcome, never
    mutates arbitrarily. The only shape in the base game triggers when the loss would take
    the creature to zero — *Hideous Toughness* — so the trigger is matched rather than
    every listener being given the chance to rewrite every hit.
    """
    if target.is_hero or loss < target.adversary.endurance:
        return None
    hook_ctx = ctx.hook_context(Hook.DAMAGE_INTERCEPT, target.actor, loss=loss)
    for contribution in ctx.bus(target.ref).collect(Hook.DAMAGE_INTERCEPT, hook_ctx):
        if not isinstance(contribution, ActionContribution):
            continue
        if contribution.payload.get("trigger") != "would_reach_zero":
            continue
        cost = int(contribution.payload.get("cost", 0))
        if cost > target.adversary.total_drive:
            continue
        target.adversary.spend_drive(cost)
        return ActionContribution(
            source=contribution.source,
            action=contribution.action,
            payload=dict(contribution.payload.get("grant", {})),
        )
    return None


def _intercept_wound(target: Combatant, form: AttackForm, *, ctx: RulesContext) -> EffectId | None:
    """``WOUND_INTERCEPT`` (``12.6``): spend a drive point to cancel the Wound.

    Suppressed entirely when the attacker wields a weapon enchanted for the bane of this
    creature's type, which is why the attack context has to carry the weapon's ``banes``.
    """
    if target.is_hero:
        return None
    banes = frozenset() if form.instance is None else form.instance.banes
    if banes & target.adversary.template.creature_types:
        return None
    hook_ctx = ctx.hook_context(Hook.WOUND_INTERCEPT, target.actor)
    for contribution in ctx.bus(target.ref).collect(Hook.WOUND_INTERCEPT, hook_ctx):
        if not isinstance(contribution, ActionContribution):
            continue
        cost = int(contribution.payload.get("cost", 1))
        if cost > target.adversary.total_drive:
            continue
        target.adversary.spend_drive(cost)
        return contribution.source
    return None


def _knockback_allowed(target: Combatant) -> bool:
    """``08.12``: once per round, heroes only. Adversaries cannot choose it."""
    return target.is_hero and not target.round_flags.knockback_used


# -- applying it ----------------------------------------------------------------------------


def apply_attack(state: CombatState, outcome: AttackOutcome, *, ctx: RulesContext) -> list[Event]:
    """Commit one attack (``08.5``, ``01.4``). No randomness, no branching on dice.

    The order matters: the PROTECTION roll already happened against the target's pre-loss
    condition, so this applies the Endurance change and *then* recomputes conditions
    (``08.7``). A knockback spends the target's next main action.
    """
    events: list[Event] = []
    target = state.combatant(outcome.declaration.target)
    if not outcome.hit:
        return [_attack_event(outcome, hit=False)]

    events.append(_attack_event(outcome, hit=True))
    if outcome.knockback_taken:
        target.round_flags.knockback_used = True
        target.knocked_back = True

    if outcome.restore_full_endurance:
        target.adversary.endurance = target.adversary.template.endurance
    elif target.is_hero:
        events += change_endurance(
            target.hero, -outcome.endurance_loss, ChangeSource.COMBAT, ctx=ctx
        )
    else:
        target.adversary.take_endurance_loss(outcome.endurance_loss)

    if outcome.wound is not None:
        events += _apply_wound(state, target, outcome, ctx=ctx)

    if not target.active:
        from tor.rules.combat.engagement import unengage

        unengage(state, target.ref)
        attacker = state.combatant(outcome.declaration.attacker)
        kill_ctx = ctx.hook_context(Hook.ON_KILL, attacker.actor, target=target.actor)
        ctx.consume(attacker.ref, ctx.bus(attacker.ref).collect(Hook.ON_KILL, kill_ctx), kill_ctx)
        events.append(
            Event(
                kind=EventKind.COMBATANT_OUT_OF_FIGHT,
                actor=target.ref,
                payload={
                    "reason": "slain"
                    if outcome.wound is not None and outcome.wound.slain
                    else "endurance",
                },
            )
        )
        if outcome.misdeed_check:
            events.append(_misdeed_prompt(outcome))
    return events


#: ``12.2``'s guiding questions, carried on the prompt so a UI can put them to the players.
MISDEED_QUESTIONS: tuple[str, ...] = (
    "was the fight provoked by the heroes, or were they attacked?",
    "was there another option than combat?",
    "was killing necessary, or were the adversaries prone to surrender?",
)


def _misdeed_prompt(outcome: AttackOutcome) -> Event:
    """``12.2``: killing a Resolve-bearing foe is always weighed as a possible Misdeed.

    An event, never a decision. Fighting minions of the Enemy can hardly call the heroes'
    integrity into question; foes who took up arms through allegiance or circumstance can.
    """
    return Event(
        kind=EventKind.MISDEED_CHECK_PROMPT,
        actor=outcome.declaration.attacker,
        payload={
            "target": outcome.declaration.target,
            "questions": list(MISDEED_QUESTIONS),
        },
        gm_only=True,
    )


def _apply_wound(
    state: CombatState, target: Combatant, outcome: AttackOutcome, *, ctx: RulesContext
) -> list[Event]:
    wound = outcome.wound
    assert wound is not None
    if wound.cancelled_by is not None:
        return [
            Event(
                kind=EventKind.WOUND_CANCELLED,
                actor=target.ref,
                payload={"by": str(wound.cancelled_by)},
            )
        ]
    events: list[Event] = []
    if target.is_hero:
        hero = target.hero
        if wound.dying and hero.conditions.wounded:
            # 08.8: a second Wound skips the severity roll; Endurance drops to zero.
            events += change_endurance(hero, -hero.endurance, ChangeSource.COMBAT, ctx=ctx)
        hero.conditions.wounded = True
        hero.injury_days = max(hero.injury_days, wound.injury_days)
        hero.dying = hero.dying or wound.dying
        if wound.dying and hero.endurance:
            events += change_endurance(hero, -hero.endurance, ChangeSource.COMBAT, ctx=ctx)
        events += recompute_conditions(hero, ctx=ctx)
    else:
        target.adversary.apply_wound()

    events.append(
        Event(
            kind=EventKind.WOUND_RECEIVED,
            actor=target.ref,
            payload={
                "severity": None if wound.severity is None else str(wound.severity),
                "injury_days": wound.injury_days,
                "dying": wound.dying,
                "slain": wound.slain,
            },
            rolls=() if wound.roll is None else (wound.roll,),
        )
    )
    hook_ctx = ctx.hook_context(Hook.ON_WOUND_RECEIVED, target.actor)
    ctx.consume(target.ref, ctx.bus(target.ref).collect(Hook.ON_WOUND_RECEIVED, hook_ctx), hook_ctx)
    return events


def _attack_event(outcome: AttackOutcome, *, hit: bool) -> Event:
    return Event(
        kind=EventKind.ATTACK_RESOLVED,
        actor=outcome.declaration.attacker,
        payload={
            "target": outcome.declaration.target,
            "weapon": outcome.declaration.form.name,
            "hit": hit,
            "endurance_loss": outcome.endurance_loss,
            "piercing_blow": outcome.piercing_blow,
            "knockback": outcome.knockback_taken,
            "special_damage": [s.option.value for s in outcome.special_damage],
        },
        rolls=(outcome.roll,),
    )
