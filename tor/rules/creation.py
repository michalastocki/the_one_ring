"""Character creation and Company formation (``06``).

A **staged pipeline over an immutable draft**. Each stage validates its own inputs and
returns a new draft; the hero is materialised only at the end. That shape is what lets a UI
walk forward and backward freely and lets a test assert on any intermediate stage.

Re-running a stage **invalidates every later stage**, whose fields are cleared. The cascade
is mandatory, not a convenience: changing culture after buying Previous Experience must not
leave behind a Combat Proficiency the new culture forbids.

Three things ``06`` asks for cannot be done where it puts them, and each is resolved here
in the same direction the rest of the engine already takes.

**Effects are registered at materialisation, not "immediately".** ``06.2`` says the Cultural
Blessing is registered the moment a culture is chosen. There is nothing to register it on: a
draft is frozen, an ``EffectBus`` cannot live on a ``Hero`` (``tor.rules.context`` explains
why), and a bus mutated during a pipeline that may be re-walked backwards would accumulate
the effects of abandoned choices. :func:`build_hero` therefore takes the bus and registers
everything once, in stage order, which is the same order and the same result.

**Attribute TNs are not frozen.** ``06.3`` says to compute and freeze the three Target
Numbers alongside the maxima. They are a derived stat passing through
``MODIFY_ATTRIBUTE_TN`` (``02.3.8``), and ``04.7`` names storing a computed derived stat as
an anti-pattern, so ``tor.rolls.attribute_tn`` derives them per roll and ``Hero`` has no
field for them. The maxima and Parry *are* stored, as ``DerivedStat``, because that is what
``03.4`` gives the hero.

**The draft splits what the Calling grants from what the culture granted.** ``06.1`` gives
the draft one ``favoured`` set and one ``features`` tuple, but stage 5 adds to both — so
re-running stage 5 alone could not withdraw its own contribution without also discarding the
culture's. ``calling_favoured`` and ``calling_feature`` are separate fields for that reason,
and :func:`build_hero` unions them.

**Deferred, with owners.** Registering the Patron's benefit belongs to the Fellowship
subsystem (``15``), which owns Patrons in play; the Company's starting Eye Awareness is
recalculated at the beginning of each Adventuring Phase from the Company's composition, so
it belongs to ``14.2``; ``MODIFY_USEFUL_ITEM_LIMIT`` and ``MODIFY_MOUNT_VIGOUR`` fire where
those limits are enforced in play; and moving an heirloom out of the retiring hero's hands —
activating a Famous item's first quality, revealing a Wondrous Artefact's Blessings — needs
the Famous-item model of ``13.7``. :func:`heirlooms_allowed` implements the one part of
``06.12`` that is arithmetic rather than treasure.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import MISSING, dataclass, field, fields, replace
from enum import IntEnum
from typing import Any, Literal, TypeVar

from tor.content.entities import Culture, EffectDefinition
from tor.content.pack import ContentPack
from tor.dice import Randomness
from tor.effects.bus import EffectBus, EffectKind, EffectSource
from tor.effects.hooks import Hook, HookContext
from tor.errors import RuleViolation, StateError, Warning_
from tor.model.abilities import COMBAT_PROFICIENCIES, SKILLS, is_combat_proficiency
from tor.model.attributes import Attribute, AttributeSet
from tor.model.company import Company
from tor.model.conditions import StandardOfLiving, TreasureHoldings
from tor.model.derive import DerivedStat
from tor.model.gear import (
    ArmourCategory,
    ArmourInstance,
    Gear,
    Mount,
    ShieldInstance,
    UsefulItem,
    WeaponInstance,
)
from tor.model.hero import Heir, Hero, RewardInstance
from tor.model.ids import (
    AbilityId,
    CallingId,
    CombatantId,
    CultureId,
    EffectId,
    HeroId,
    ItemId,
    PatronId,
)
from tor.rules.context import RulesContext
from tor.rules.resources import recompute_conditions
from tor.rules.shadow import register_shadow_conditions

__all__ = [
    "STARTING_RANK",
    "AttributeChoice",
    "CallingChoice",
    "CompanyChoice",
    "CreationStage",
    "ExperienceChoice",
    "FeatureChoice",
    "GearChoice",
    "HeroDraft",
    "IdentityChoice",
    "RewardAndVirtueChoice",
    "RewardChoice",
    "VirtueChoice",
    "WeaponSelection",
    "begin_heir",
    "begin_hero",
    "build_hero",
    "choose_attributes",
    "choose_calling",
    "choose_culture",
    "choose_features",
    "choose_gear",
    "choose_identity",
    "choose_reward_and_virtue",
    "choose_skills",
    "form_company",
    "heirlooms_allowed",
    "spend_previous_experience",
]

T = TypeVar("T")

#: ``06.8``: VALOUR and WISDOM both start here, which is also invariant I2's floor.
STARTING_RANK = 1
#: ``06.12`` deviation 4: a reserve of 15 passes one heirloom, 20 passes two.
_HEIRLOOM_THRESHOLDS = (15, 20)
#: ``06.12`` deviation 3: the *Raise an Heir* undertaking accumulates 10 to 20 points.
MIN_HEIR_RESERVE = 10
MAX_HEIR_RESERVE = 20


class CreationStage(IntEnum):
    """The stage a draft is *awaiting*, so ``COMPLETE`` means nothing is left to choose."""

    CULTURE = 1
    ATTRIBUTES = 2
    SKILLS = 3
    FEATURES = 4
    CALLING = 5
    PREVIOUS_EXPERIENCE = 6
    GEAR = 7
    REWARD_AND_VIRTUE = 8
    IDENTITY = 9
    COMPLETE = 10


# -- stage inputs --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttributeChoice:
    """``06.3``. Both paths land on the same six-row table; ``roll`` is not a distribution.

    ``bonus_attribute`` is required exactly when the culture grants a free +1, because that
    blessing must apply *before* the derived stats read STRENGTH, HEART and WITS.
    """

    method: Literal["pick", "roll"] = "pick"
    #: 1..6, required when ``method == "pick"`` and ignored when rolling.
    set_index: int | None = None
    bonus_attribute: Attribute | None = None


@dataclass(frozen=True, slots=True)
class FeatureChoice:
    """One Distinctive Feature, with the creature type the one Calling feature names."""

    feature: EffectId
    subject: str | None = None


@dataclass(frozen=True, slots=True)
class SkillChoice:
    """``06.4``. ``proficiencies`` answers the culture's grants **in the order given**."""

    favoured: tuple[AbilityId, ...] = ()
    proficiencies: tuple[AbilityId, ...] = ()


@dataclass(frozen=True, slots=True)
class CallingChoice:
    """``06.6``. ``subject`` fills the one Calling feature that names a creature type."""

    calling: CallingId
    favoured: tuple[AbilityId, ...] = ()
    subject: str | None = None


@dataclass(frozen=True, slots=True)
class ExperienceChoice:
    """``06.6``. ``targets`` are **rating targets**, not point totals.

    ``06.1`` names the draft field ``previous_experience_spend`` and ``06.6`` prices a
    purchase with ``cost_to_raise(current, target, ladder)``. A points-per-ability mapping
    could not say *which* levels were bought, so the target rating is what is recorded and
    the cost is derived from it.
    """

    targets: Mapping[AbilityId, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WeaponSelection:
    """One starting weapon. ``06.7``: a weapon usable in either grip defaults to one-handed."""

    item: ItemId
    two_handed: bool = False


@dataclass(frozen=True, slots=True)
class GearChoice:
    """``06.7``. The four war-gear slots, plus the two stage-7 lists that carry no Load.

    ``travelling_gear`` is explicitly *not* Load-rated and need not be itemised, so it is
    free text with no mechanical weight.
    """

    weapons: tuple[WeaponSelection, ...] = ()
    armour: ItemId | None = None
    helm: ItemId | None = None
    shield: ItemId | None = None
    useful_items: tuple[UsefulItem, ...] = ()
    travelling_gear: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RewardChoice:
    """``06.8``. A Reward is always bound to a specific owned item, never held loose."""

    reward: EffectId
    item: ItemId
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VirtueChoice:
    """``06.8``. ``params`` carries the choice a Virtue requires — which Skills, which
    Attribute — captured now rather than asked for at every dispatch."""

    virtue: EffectId
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RewardAndVirtueChoice:
    """Stage 8 takes both at once, because ``06.8`` grants exactly one of each."""

    reward: RewardChoice
    virtue: VirtueChoice


@dataclass(frozen=True, slots=True)
class IdentityChoice:
    """``06.9``. An age outside the culture's range is a warning, never an error."""

    name: str
    age: int


@dataclass(frozen=True, slots=True)
class CompanyChoice:
    """``06.11``. The Fellowship Focus may be deferred, so ``focus`` may be left empty."""

    patron: PatronId | None = None
    safe_haven: str = ""
    focus: Mapping[HeroId, HeroId] = field(default_factory=dict)
    #: Mount quality follows each hero's tier; only the name is a choice.
    mount_names: Mapping[HeroId, str] = field(default_factory=dict)


# -- the draft -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HeroDraft:
    """``06.1``. Frozen: every stage returns a new draft rather than editing this one.

    The three heir fields and ``stage`` belong to no stage and therefore survive every
    cascade — an heir does not stop being an heir because the player went back to reconsider
    their culture.
    """

    stage: CreationStage = CreationStage.CULTURE
    culture: CultureId | None = None
    attributes: AttributeSet | None = None
    skills: Mapping[AbilityId, int] = field(default_factory=dict)
    favoured: frozenset[AbilityId] = frozenset()
    proficiencies: Mapping[AbilityId, int] = field(default_factory=dict)
    features: tuple[FeatureChoice, ...] = ()
    calling: CallingId | None = None
    #: Kept apart from ``favoured`` so re-running stage 5 withdraws only the Calling's grant.
    calling_favoured: frozenset[AbilityId] = frozenset()
    calling_feature: FeatureChoice | None = None
    previous_experience_spend: Mapping[AbilityId, int] = field(default_factory=dict)
    gear: GearChoice | None = None
    useful_items: tuple[UsefulItem, ...] = ()
    travelling_gear: tuple[str, ...] = ()
    starting_reward: RewardChoice | None = None
    starting_virtue: VirtueChoice | None = None
    name: str | None = None
    age: int | None = None
    #: Heirs only (``06.12``). A free additional Favoured Skill on top of every other grant.
    heritage_favoured: AbilityId | None = None
    inherited_standard_of_living: StandardOfLiving | None = None
    #: Heirs only: spent instead of the 10-point budget. ``06.1`` gives the draft no field
    #: for it, but ``06.12`` makes it the budget, so it has to be reachable at stage 6.
    heir_reserve: int | None = None


#: Which stage owns which draft fields. Re-running a stage clears everything from it
#: onward, which is what makes ``06.1``'s cascade a rule rather than a caller's discipline.
_STAGE_FIELDS: Mapping[CreationStage, tuple[str, ...]] = {
    CreationStage.CULTURE: ("culture",),
    CreationStage.ATTRIBUTES: ("attributes",),
    CreationStage.SKILLS: ("skills", "favoured", "proficiencies"),
    CreationStage.FEATURES: ("features",),
    CreationStage.CALLING: ("calling", "calling_favoured", "calling_feature"),
    CreationStage.PREVIOUS_EXPERIENCE: ("previous_experience_spend",),
    CreationStage.GEAR: ("gear", "useful_items", "travelling_gear"),
    CreationStage.REWARD_AND_VIRTUE: ("starting_reward", "starting_virtue"),
    CreationStage.IDENTITY: ("name", "age"),
}

_DRAFT_FIELDS = {f.name: f for f in fields(HeroDraft)}


def _blank(name: str) -> Any:
    """The field's own declared default, so the cascade cannot drift from the dataclass."""
    declared = _DRAFT_FIELDS[name]
    if declared.default_factory is not MISSING:
        return declared.default_factory()
    return declared.default


def _advance(draft: HeroDraft, stage: CreationStage, **updates: Any) -> HeroDraft:
    """Apply one stage's result, clearing every later stage (``06.1``)."""
    if draft.stage < stage:
        raise StateError(
            f"cannot run the {stage.name} stage from {draft.stage.name}; creation stages run "
            "in order, and a stage may only be re-run once it has been reached"
        )
    cleared = {
        name for later in CreationStage if later >= stage for name in _STAGE_FIELDS.get(later, ())
    }
    fresh = {name: _blank(name) for name in cleared}
    return replace(draft, **{**fresh, **updates}, stage=CreationStage(stage + 1))


def begin_hero() -> HeroDraft:
    """An empty draft awaiting a culture."""
    return HeroDraft()


def begin_heir(heir: Heir, retiring: Hero) -> HeroDraft:
    """A draft carrying ``06.12``'s four deviations from an ordinary hero.

    The reserve is the Previous Experience budget, the retiring hero's Standard of Living
    replaces the culture's, and one of the retiring hero's Favoured Skills passes down as a
    free additional Favoured Skill. Moving the heirlooms themselves is ``13.7``'s work; what
    is checked here is that the reserve permits as many as the heir is carrying.
    """
    if not heir.ready:
        raise RuleViolation(
            f"an heir needs a reserve of at least {MIN_HEIR_RESERVE}, not {heir.reserve}",
            rule_reference="heir_reserve",
            suggestion="continue the Raise an Heir undertaking",
        )
    if heir.reserve > MAX_HEIR_RESERVE:
        raise RuleViolation(
            f"an heir's reserve tops out at {MAX_HEIR_RESERVE}, not {heir.reserve}",
            rule_reference="heir_reserve",
        )
    allowed = heirlooms_allowed(heir.reserve)
    if len(heir.heirlooms) > allowed:
        raise RuleViolation(
            f"a reserve of {heir.reserve} passes {allowed} heirloom(s), not {len(heir.heirlooms)}",
            rule_reference="heirloom_allowance",
        )
    if heir.heritage_skill is not None and heir.heritage_skill not in retiring.favoured_skills:
        raise RuleViolation(
            f"{heir.heritage_skill!r} is not one of {retiring.id}'s Favoured Skills, so it "
            "cannot pass down as the family heritage",
            rule_reference="heritage_skill",
        )
    return HeroDraft(
        heritage_favoured=heir.heritage_skill,
        inherited_standard_of_living=retiring.starting_standard_of_living,
        heir_reserve=heir.reserve,
    )


def heirlooms_allowed(reserve: int) -> int:
    """How many heirlooms a reserve passes on (``06.12`` deviation 4)."""
    return sum(1 for threshold in _HEIRLOOM_THRESHOLDS if reserve >= threshold)


# -- stage 1: culture ----------------------------------------------------------------


def choose_culture(
    draft: HeroDraft, culture_id: CultureId, pack: ContentPack
) -> tuple[HeroDraft, list[Warning_]]:
    """``06.2``. The Cultural Blessing and Weakness are registered by :func:`build_hero`."""
    pack.culture(culture_id)
    return _advance(draft, CreationStage.CULTURE, culture=culture_id), []


# -- stage 2: attributes -------------------------------------------------------------


def choose_attributes(
    draft: HeroDraft,
    choice: AttributeChoice,
    pack: ContentPack,
    *,
    rng: Randomness | None = None,
) -> tuple[HeroDraft, list[Warning_]]:
    """``06.3``. Pick a row or roll one Success die for it; both read the same table."""
    culture = _culture(draft, pack)
    if choice.method == "roll":
        if rng is None:
            raise StateError("rolling for an Attribute set needs a Randomness to roll with")
        index = rng.success().face
    else:
        if choice.set_index is None:
            raise RuleViolation(
                "picking an Attribute set needs a set_index",
                rule_reference="attribute_set_choice",
                suggestion=f"1..{len(culture.attribute_sets)}, or roll for it instead",
            )
        index = choice.set_index

    row = next((r for r in culture.attribute_sets if r.roll == index), None)
    if row is None:
        raise RuleViolation(
            f"culture {culture.id!r} has no Attribute set {index}",
            rule_reference="attribute_set_choice",
        )
    attributes = AttributeSet(strength=row.strength, heart=row.heart, wits=row.wits)
    attributes = _apply_attribute_bonus(culture, attributes, choice.bonus_attribute)
    return _advance(draft, CreationStage.ATTRIBUTES, attributes=attributes), []


def _apply_attribute_bonus(
    culture: Culture, attributes: AttributeSet, chosen: Attribute | None
) -> AttributeSet:
    """``06.2``'s ``attribute_bonus``, applied before anything derives from a score."""
    bonus = culture.attribute_bonus
    if not bonus:
        if chosen is not None:
            raise RuleViolation(
                f"culture {culture.id!r} grants no free Attribute bonus",
                rule_reference="culture_attribute_bonus",
            )
        return attributes
    if chosen is None:
        raise RuleViolation(
            f"culture {culture.id!r} grants a free Attribute bonus and needs one chosen",
            rule_reference="culture_attribute_bonus",
        )
    allowed = bonus.get("choose", "any")
    if allowed != "any" and chosen.value not in allowed:
        raise RuleViolation(
            f"culture {culture.id!r} may raise only {sorted(allowed)}, not {chosen.value!r}",
            rule_reference="culture_attribute_bonus",
        )
    return attributes.with_bonus(chosen, int(bonus.get("count", 1)))


# -- stage 3: skills and combat proficiencies ----------------------------------------


def choose_skills(
    draft: HeroDraft, choice: SkillChoice, pack: ContentPack
) -> tuple[HeroDraft, list[Warning_]]:
    """``06.4``. The culture's 18 Skill ratings verbatim, then the player's two choices.

    That the culture supplies all 18 and names only known abilities is a *content*
    guarantee, checked when the pack loads (``05.1.1`` checks 2 and 3); re-checking it here
    would be a second implementation of a rule that has one. What is checked here is the
    player's picks against what the culture offers.
    """
    culture = _culture(draft, pack)
    warnings: list[Warning_] = []

    if len(choice.favoured) != culture.favoured_skill_count:
        raise RuleViolation(
            f"culture {culture.id!r} marks {culture.favoured_skill_count} Skill(s) Favoured, "
            f"not {len(choice.favoured)}",
            rule_reference="favoured_skill_count",
        )
    for ability in choice.favoured:
        if ability not in culture.favoured_skill_choices:
            raise RuleViolation(
                f"{ability!r} is not one of culture {culture.id!r}'s Favoured Skill choices",
                rule_reference="favoured_skill_choice",
            )
        _reject_favoured_proficiency(ability)

    proficiencies = _resolve_proficiency_grants(culture, choice.proficiencies, warnings)
    warnings.extend(_weapon_restriction_warnings(culture, proficiencies, pack))

    return (
        _advance(
            draft,
            CreationStage.SKILLS,
            skills=dict(culture.skills),
            favoured=frozenset(choice.favoured),
            proficiencies=proficiencies,
        ),
        warnings,
    )


def _reject_favoured_proficiency(ability: AbilityId) -> None:
    """Invariant I7, enforced before the hero exists rather than at :meth:`Hero.validate`."""
    if is_combat_proficiency(ability):
        raise RuleViolation(
            f"{ability!r} is a Combat Proficiency and cannot be Favoured",
            rule_reference="combat_proficiency_not_favourable",
        )


def _resolve_proficiency_grants(
    culture: Culture, picks: Sequence[AbilityId], warnings: list[Warning_]
) -> dict[AbilityId, int]:
    """Answer each grant in order; ratings do not stack, the higher applies (``06.4``)."""
    if len(picks) != len(culture.combat_proficiencies):
        raise RuleViolation(
            f"culture {culture.id!r} makes {len(culture.combat_proficiencies)} Combat "
            f"Proficiency grant(s), and each needs one pick; got {len(picks)}",
            rule_reference="proficiency_grant_count",
        )
    ratings: dict[AbilityId, int] = dict.fromkeys(COMBAT_PROFICIENCIES, 0)
    for grant, pick in zip(culture.combat_proficiencies, picks, strict=True):
        if pick not in COMBAT_PROFICIENCIES:
            raise RuleViolation(
                f"{pick!r} is not a Combat Proficiency",
                rule_reference="proficiency_grant_choice",
            )
        if not isinstance(grant.choose, str) and pick not in grant.choose:
            raise RuleViolation(
                f"this grant offers {sorted(grant.choose)}, not {pick!r}",
                rule_reference="proficiency_grant_choice",
            )
        if ratings[pick]:
            warnings.append(
                Warning_(
                    code="proficiency_grant_overlaps",
                    message=(
                        f"{pick!r} was already granted rating {ratings[pick]}; the ratings do "
                        f"not stack, so the higher of {ratings[pick]} and {grant.rating} applies"
                    ),
                )
            )
        ratings[pick] = max(ratings[pick], grant.rating)
    return ratings


def _weapon_restriction_warnings(
    culture: Culture, proficiencies: Mapping[AbilityId, int], pack: ContentPack
) -> list[Warning_]:
    """``06.4``: a proficiency no allowed weapon uses is legal, but probably unintended."""
    if culture.allowed_weapons is None:
        return []
    usable = {
        pack.weapon(item).proficiency
        for item in culture.allowed_weapons
        if item not in culture.forbidden_items
    }
    return [
        Warning_(
            code="proficiency_without_weapon",
            message=(
                f"culture {culture.id!r} allows no weapon using {ability!r}, so its rating "
                f"of {rating} will be hard to spend"
            ),
        )
        for ability, rating in sorted(proficiencies.items())
        if rating and ability not in usable
    ]


# -- stage 4: distinctive features ---------------------------------------------------


def choose_features(
    draft: HeroDraft, features: Sequence[FeatureChoice], pack: ContentPack
) -> tuple[HeroDraft, list[Warning_]]:
    """``06.5``. Exactly ``distinctive_feature_count`` from the culture's own list."""
    culture = _culture(draft, pack)
    if len(features) != culture.distinctive_feature_count:
        raise RuleViolation(
            f"culture {culture.id!r} grants {culture.distinctive_feature_count} Distinctive "
            f"Feature(s), not {len(features)}",
            rule_reference="distinctive_feature_count",
        )
    chosen: set[EffectId] = set()
    for feature in features:
        if feature.feature not in culture.distinctive_feature_choices:
            raise RuleViolation(
                f"{feature.feature!r} is not one of culture {culture.id!r}'s Distinctive "
                "Feature choices",
                rule_reference="distinctive_feature_choice",
            )
        if feature.feature in chosen:
            raise RuleViolation(
                f"{feature.feature!r} is chosen twice",
                rule_reference="distinctive_feature_choice",
            )
        chosen.add(feature.feature)
        _check_subject(pack.effect(feature.feature), feature.subject)
    return _advance(draft, CreationStage.FEATURES, features=tuple(features)), []


def _check_subject(definition: EffectDefinition, subject: str | None) -> None:
    """The one Calling feature that must name a creature type at acquisition (``05.3``)."""
    required = "subject" in definition.requires_choice
    if required and not subject:
        raise RuleViolation(
            f"{definition.id!r} must name its subject when it is taken",
            rule_reference="feature_requires_subject",
        )
    if subject and not required:
        raise RuleViolation(
            f"{definition.id!r} names no subject",
            rule_reference="feature_requires_subject",
        )


# -- stage 5: calling ----------------------------------------------------------------


def choose_calling(
    draft: HeroDraft, choice: CallingChoice, pack: ContentPack
) -> tuple[HeroDraft, list[Warning_]]:
    """``06.6``. Two Favoured Skills from the Calling's three, plus one more Feature.

    A Skill the culture already Favoured may be chosen again; it does not double, so the
    player is warned rather than refused — ``18.2``'s own example of a legal but probably
    unintended choice.
    """
    calling = pack.calling(choice.calling)
    warnings: list[Warning_] = []

    if len(choice.favoured) != calling.favoured_skill_count:
        raise RuleViolation(
            f"calling {calling.id!r} marks {calling.favoured_skill_count} Skill(s) Favoured, "
            f"not {len(choice.favoured)}",
            rule_reference="favoured_skill_count",
        )
    if len(set(choice.favoured)) != len(choice.favoured):
        raise RuleViolation(
            f"calling {calling.id!r} cannot mark the same Skill Favoured twice",
            rule_reference="favoured_skill_choice",
        )
    for ability in choice.favoured:
        if ability not in calling.favoured_skill_choices:
            raise RuleViolation(
                f"{ability!r} is not one of calling {calling.id!r}'s Favoured Skill choices",
                rule_reference="favoured_skill_choice",
            )
        _reject_favoured_proficiency(ability)
        if ability in draft.favoured:
            warnings.append(
                Warning_(
                    code="favoured_skill_already_favoured",
                    message=(
                        f"{ability!r} is already Favoured from the culture; marking it again "
                        "does not double it, and the Calling's other choices are still open"
                    ),
                )
            )

    feature = FeatureChoice(feature=calling.distinctive_feature, subject=choice.subject)
    _check_subject(pack.effect(calling.distinctive_feature), choice.subject)

    return (
        _advance(
            draft,
            CreationStage.CALLING,
            calling=choice.calling,
            calling_favoured=frozenset(choice.favoured),
            calling_feature=feature,
        ),
        warnings,
    )


# -- stage 6: previous experience ----------------------------------------------------


def spend_previous_experience(
    draft: HeroDraft, choice: ExperienceChoice, pack: ContentPack
) -> tuple[HeroDraft, list[Warning_]]:
    """``06.6``. A budget of 10 — or the heir's reserve — spent down ``05.10``'s ladders.

    Ratings may be bought from 0, several levels in one ability are legal and each level is
    paid for individually, and **unspent points are lost, not carried**: nothing records a
    remainder, which is why leaving one is a warning rather than silence.
    """
    if draft.stage < CreationStage.PREVIOUS_EXPERIENCE:
        raise StateError("Previous Experience is spent after Skills and Combat Proficiencies")
    budget = _previous_experience_budget(draft, pack)
    skill_ladder = pack.cost_ladder("previous_experience", "skills")
    proficiency_ladder = pack.cost_ladder("previous_experience", "proficiencies")

    spent = 0
    for ability, target in sorted(choice.targets.items()):
        if ability in draft.skills:
            current, ladder = draft.skills[ability], skill_ladder
        elif ability in draft.proficiencies:
            current, ladder = draft.proficiencies[ability], proficiency_ladder
        else:
            raise RuleViolation(
                f"{ability!r} is neither one of the 18 Skills nor a Combat Proficiency",
                rule_reference="previous_experience_ability",
            )
        spent += ladder.cost_to_raise(current, target)

    if spent > budget:
        raise RuleViolation(
            f"Previous Experience costs {spent} but the budget is {budget}",
            rule_reference="previous_experience_budget",
        )
    warnings = (
        [
            Warning_(
                code="previous_experience_unspent",
                message=(
                    f"{budget - spent} of {budget} Previous Experience point(s) are unspent; "
                    "they are lost at creation, not carried into play"
                ),
            )
        ]
        if spent < budget
        else []
    )
    return (
        _advance(
            draft,
            CreationStage.PREVIOUS_EXPERIENCE,
            previous_experience_spend=dict(choice.targets),
        ),
        warnings,
    )


def _previous_experience_budget(draft: HeroDraft, pack: ContentPack) -> int:
    """The heir's accumulated reserve, or the pack's flat budget (``06.6``, ``06.12``)."""
    if draft.heir_reserve is not None:
        return draft.heir_reserve
    costs = pack.experience_costs()
    return int(costs["previous_experience"]["budget"])


# -- stage 7: gear -------------------------------------------------------------------


def choose_gear(
    draft: HeroDraft, choice: GearChoice, pack: ContentPack
) -> tuple[HeroDraft, list[Warning_]]:
    """``06.7``. One weapon **per Combat Proficiency** with a rating, not per point."""
    culture = _culture(draft, pack)
    tier = _starting_tier(draft, culture)
    # A Combat Proficiency bought from 0 at stage 6 earns a weapon like any other, so the
    # ratings read here are the post-Previous-Experience ones, not stage 3's.
    proficiencies = _ratings(draft.proficiencies, draft.previous_experience_spend)
    _check_weapons(choice.weapons, proficiencies, culture, pack)
    _check_armour(choice, culture, tier, pack)
    _check_useful_items(choice.useful_items, pack, tier)
    return (
        _advance(
            draft,
            CreationStage.GEAR,
            gear=choice,
            useful_items=tuple(choice.useful_items),
            travelling_gear=tuple(choice.travelling_gear),
        ),
        [],
    )


def _check_weapons(
    weapons: Sequence[WeaponSelection],
    proficiencies: Mapping[AbilityId, int],
    culture: Culture,
    pack: ContentPack,
) -> None:
    claimed: set[AbilityId] = set()
    for selection in weapons:
        weapon = pack.weapon(selection.item)
        _check_weapon_allowed(selection.item, culture)
        if selection.two_handed and weapon.injury_two_handed is None:
            raise RuleViolation(
                f"{selection.item!r} cannot be wielded two-handed",
                rule_reference="weapon_grip",
            )
        if not selection.two_handed and weapon.two_handed_only:
            raise RuleViolation(
                f"{selection.item!r} is two-handed only",
                rule_reference="weapon_grip",
            )
        # Brawling is an attack *mode*, not a Combat Proficiency (``03.3``), so a weapon
        # resolved at it claims no slot and needs no rating.
        proficiency = AbilityId(str(weapon.proficiency))
        if proficiency not in COMBAT_PROFICIENCIES:
            continue
        if not proficiencies.get(proficiency, 0):
            raise RuleViolation(
                f"{selection.item!r} needs a rating of 1 or more in {proficiency!r}",
                rule_reference="weapon_per_proficiency",
            )
        if proficiency in claimed:
            raise RuleViolation(
                f"{proficiency!r} already has a starting weapon; ``06.7`` grants one per "
                "Combat Proficiency, not one per point",
                rule_reference="weapon_per_proficiency",
            )
        claimed.add(proficiency)


def _check_armour(
    choice: GearChoice, culture: Culture, tier: StandardOfLiving, pack: ContentPack
) -> None:
    for item, expect_headgear in ((choice.armour, False), (choice.helm, True)):
        if item is None:
            continue
        armour = pack.armour_type(item)
        _check_not_forbidden(item, culture)
        is_headgear = armour.category is ArmourCategory.HEADGEAR
        if is_headgear is not expect_headgear:
            slot = "helm" if expect_headgear else "body armour"
            raise RuleViolation(
                f"{item!r} is {armour.category.value} and cannot be worn as {slot}",
                rule_reference="armour_slot",
            )
        _check_min_living(item, armour.min_standard_of_living, tier)
    if choice.shield is not None:
        shield = pack.shield_type(choice.shield)
        _check_not_forbidden(choice.shield, culture)
        _check_min_living(choice.shield, shield.min_standard_of_living, tier)


def _check_not_forbidden(item: ItemId, culture: Culture) -> None:
    """``forbidden_items`` names individual items and applies to every slot (``03.5.1``)."""
    if item in culture.forbidden_items:
        raise RuleViolation(
            f"culture {culture.id!r} forbids {item!r}",
            rule_reference="culture_forbidden_item",
        )


def _check_weapon_allowed(item: ItemId, culture: Culture) -> None:
    """``allowed_weapons`` is a **weapons-only** allow-list (``03.5.1``).

    Applying it to armour and shields would confine a restricted culture to no armour at
    all, because a weapon list never names any.
    """
    _check_not_forbidden(item, culture)
    if culture.allowed_weapons is not None and item not in culture.allowed_weapons:
        raise RuleViolation(
            f"culture {culture.id!r} restricts its heroes to {sorted(culture.allowed_weapons)}",
            rule_reference="culture_allowed_weapons",
        )


def _check_min_living(
    item: ItemId, minimum: StandardOfLiving | None, tier: StandardOfLiving
) -> None:
    if minimum is not None and tier < minimum:
        raise RuleViolation(
            f"{item!r} requires a Standard of Living of at least {minimum.name.lower()}, "
            f"and this hero starts {tier.name.lower()}",
            rule_reference="item_min_standard_of_living",
        )


def _check_useful_items(
    items: Sequence[UsefulItem], pack: ContentPack, tier: StandardOfLiving
) -> None:
    limit = pack.living_tier(tier).useful_items
    if len(items) > limit:
        raise RuleViolation(
            f"a {tier.name.lower()} hero carries {limit} Useful Item(s), not {len(items)}",
            rule_reference="useful_item_limit",
        )
    for item in items:
        if item.skill not in SKILLS:
            raise RuleViolation(
                f"a Useful Item names one of the 18 Skills, not {item.skill!r}",
                rule_reference="useful_item_skill",
            )


# -- stage 8: starting Reward and Virtue ---------------------------------------------


def choose_reward_and_virtue(
    draft: HeroDraft, choice: RewardAndVirtueChoice, pack: ContentPack
) -> tuple[HeroDraft, list[Warning_]]:
    """``06.8``. One Reward bound to an owned item, and one standard Virtue."""
    if draft.gear is None:
        raise StateError("a starting Reward binds to an owned item, so gear is chosen first")
    _check_reward(choice.reward, draft.gear, pack)
    _check_virtue(choice.virtue, pack)
    return (
        _advance(
            draft,
            CreationStage.REWARD_AND_VIRTUE,
            starting_reward=choice.reward,
            starting_virtue=choice.virtue,
        ),
        [],
    )


def _check_reward(choice: RewardChoice, gear: GearChoice, pack: ContentPack) -> None:
    definition = pack.effect(choice.reward)
    if definition.kind != EffectKind.REWARD:
        raise RuleViolation(
            f"{choice.reward!r} is a {definition.kind}, not a Reward",
            rule_reference="starting_reward_kind",
        )
    slots = _slots(gear)
    if choice.item not in slots:
        raise RuleViolation(
            f"{choice.item!r} is not among this hero's starting gear",
            rule_reference="reward_binds_to_owned_item",
        )
    if definition.applies_to and slots[choice.item] not in definition.applies_to:
        raise RuleViolation(
            f"{choice.reward!r} applies to {sorted(definition.applies_to)}, and "
            f"{choice.item!r} is a {slots[choice.item]}",
            rule_reference="reward_applies_to",
        )
    _check_required_choices(definition, choice.params)


def _slots(gear: GearChoice) -> dict[ItemId, str]:
    """Every owned item and the kind a Reward's ``applies_to`` names it by."""
    owned: dict[ItemId, str] = {selection.item: "weapon" for selection in gear.weapons}
    for item, kind in ((gear.armour, "armour"), (gear.helm, "helm"), (gear.shield, "shield")):
        if item is not None:
            owned[item] = kind
    return owned


def _check_virtue(choice: VirtueChoice, pack: ContentPack) -> None:
    definition = pack.effect(choice.virtue)
    if definition.kind == EffectKind.CULTURAL_VIRTUE:
        raise RuleViolation(
            f"{choice.virtue!r} is a Cultural Virtue, which requires WISDOM 2 and so is not "
            "available at creation",
            rule_reference="cultural_virtue_at_creation",
        )
    if definition.kind != EffectKind.VIRTUE:
        raise RuleViolation(
            f"{choice.virtue!r} is a {definition.kind}, not a Virtue",
            rule_reference="starting_virtue_kind",
        )
    if definition.min_wisdom > STARTING_RANK:
        raise RuleViolation(
            f"{choice.virtue!r} requires WISDOM {definition.min_wisdom}, and a new hero has "
            f"{STARTING_RANK}",
            rule_reference="virtue_min_wisdom",
        )
    _check_required_choices(definition, choice.params)


def _check_required_choices(definition: EffectDefinition, params: Mapping[str, Any]) -> None:
    """``requires_choice`` announces what the player must name; this is where it is read.

    A count of 1 accepts a bare value as well as a one-element sequence, because
    ``{"attribute": 1}`` reads naturally as a single Attribute rather than a list of one.
    """
    for key, count in definition.requires_choice.items():
        if key not in params:
            raise RuleViolation(
                f"{definition.id!r} requires a {key!r} choice",
                rule_reference="effect_requires_choice",
            )
        supplied = _as_tuple(params[key])
        if len(supplied) != int(count):
            raise RuleViolation(
                f"{definition.id!r} requires {count} {key!r} choice(s), got {len(supplied)}",
                rule_reference="effect_requires_choice",
            )


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if isinstance(value, str) or not isinstance(value, Iterable):
        return (value,)
    return tuple(value)


# -- stage 9: identity ---------------------------------------------------------------


def choose_identity(
    draft: HeroDraft, choice: IdentityChoice, pack: ContentPack
) -> tuple[HeroDraft, list[Warning_]]:
    """``06.9``. The rulebook frames the adventuring ages as typical, not binding."""
    culture = _culture(draft, pack)
    if not choice.name.strip():
        raise RuleViolation("a hero needs a name", rule_reference="hero_name")
    warnings: list[Warning_] = []
    low = culture.adventuring_age.get("min")
    high = culture.adventuring_age.get("typical_retirement")
    if (low is not None and choice.age < low) or (high is not None and choice.age > high):
        warnings.append(
            Warning_(
                code="age_outside_range",
                message=(
                    f"culture {culture.id!r} typically adventures between {low} and {high}, "
                    f"and this hero is {choice.age}"
                ),
            )
        )
    return (
        _advance(draft, CreationStage.IDENTITY, name=choice.name, age=choice.age),
        warnings,
    )


# -- materialisation -----------------------------------------------------------------


def build_hero(draft: HeroDraft, pack: ContentPack, *, bus: EffectBus, hero_id: HeroId) -> Hero:
    """``06.10``. Requires a ``COMPLETE`` draft; runs the full invariant check of ``03.4.1``.

    Two keyword-only parameters ``06.10`` does not name. ``bus`` is the hero's own
    ``EffectBus``, which cannot live on the ``Hero`` (``tor.rules.context``) and which this
    function fills in stage order — the Cultural Blessing and Weakness, the Distinctive
    Features, the Calling's Feature, the Virtue, and the Reward on the item it is bound to.
    ``hero_id`` is a session-level identity rather than a creation choice: two heroes may
    share a name, and ``20.11`` requires an id to be stable forever, so it is not derived
    from one.

    Because the Virtue is registered before the maxima are read, ``03.4.2``'s rule that a
    Virtue raising a maximum raises the current value falls out of Endurance and Hope
    starting *at* their maxima rather than needing a second code path.

    A hero who begins Weary is reported, not hidden (``06.7``): the flag is set by
    ``resources.recompute_conditions`` like any other, and a heavily armoured hero from a
    low-Endurance culture can legitimately trip it.
    """
    if draft.stage is not CreationStage.COMPLETE:
        raise StateError(
            f"this draft is still awaiting its {draft.stage.name} stage and cannot be built"
        )
    culture = _culture(draft, pack)
    calling = pack.calling(_require(draft.calling, "a calling"))
    attributes = _require(draft.attributes, "an Attribute set")
    tier = _starting_tier(draft, culture)

    hero = Hero(
        id=hero_id,
        name=_require(draft.name, "a name"),
        culture=culture.id,
        calling=calling.id,
        age=_require(draft.age, "an age"),
        attributes=attributes,
        skills=_ratings(draft.skills, draft.previous_experience_spend),
        proficiencies=_ratings(draft.proficiencies, draft.previous_experience_spend),
        favoured_skills=_favoured(draft),
        valour=STARTING_RANK,
        wisdom=STARTING_RANK,
        distinctive_features=[f.feature for f in _all_features(draft)],
        # The Calling's Shadow Path id. ``Hero.shadow_path`` types it ``EffectId``, which
        # is looser than it is: the Path is a named sequence of four Flaw effects (``11.7``),
        # not an effect itself.
        shadow_path=EffectId(calling.shadow_path),
        gear=_build_gear(draft, pack),
        useful_items=list(draft.useful_items),
        treasure=TreasureHoldings(cached=_starting_treasure(pack, tier)),
        starting_standard_of_living=tier,
    )

    _register_effects(hero, draft, culture, pack, bus)

    derived = culture.derived
    hero.max_endurance = _derived(
        bus, hero, Hook.MODIFY_MAX_ENDURANCE, attributes.strength + derived.endurance_bonus
    )
    hero.max_hope = _derived(bus, hero, Hook.MODIFY_MAX_HOPE, attributes.heart + derived.hope_bonus)
    hero.parry = _derived(bus, hero, Hook.MODIFY_PARRY, attributes.wits + derived.parry_bonus)
    hero.endurance = hero.max_endurance.value
    hero.hope = hero.max_hope.value
    hero.shadow = 0

    recompute_conditions(hero, ctx=RulesContext.single(hero.id, bus=bus, gear=pack))
    hero.validate()
    return hero


def _derived(bus: EffectBus, hero: Hero, hook: Hook, base: int) -> DerivedStat:
    """One maximum or Parry, with the modifiers a registered effect contributes (P18).

    A derived-stat hook never consumes a usage budget (``19.5``), which is why this reaches
    ``apply_numeric`` directly rather than through ``RulesContext.consume``. Creation
    belongs to no scene, so the neutral :class:`HookContext` is the whole story.
    """
    return bus.apply_numeric(hook, HookContext(hook=hook, actor=hero), base)


def _ratings(base: Mapping[AbilityId, int], spend: Mapping[AbilityId, int]) -> dict[AbilityId, int]:
    """Stage-3 ratings with the Previous Experience targets applied over them."""
    ratings = dict(base)
    for ability, target in spend.items():
        if ability in ratings:
            ratings[ability] = target
    return ratings


def _favoured(draft: HeroDraft) -> set[AbilityId]:
    """Culture, Calling and — for an heir — the family heritage, unioned (``06.12``)."""
    favoured = set(draft.favoured) | set(draft.calling_favoured)
    if draft.heritage_favoured is not None:
        favoured.add(draft.heritage_favoured)
    return favoured


def _all_features(draft: HeroDraft) -> tuple[FeatureChoice, ...]:
    extra = () if draft.calling_feature is None else (draft.calling_feature,)
    return (*draft.features, *extra)


def _starting_treasure(pack: ContentPack, tier: StandardOfLiving) -> int:
    """``03.7``: each tier maps to a starting Treasure rating — its own threshold.

    The lowest tier's threshold is ``null``, meaning accumulating Treasure never reaches it;
    a hero starting there starts with nothing.

    .. note::
       It is placed **cached**, not carried. ``07.4`` charges one point of Load per point of
       Treasure *carried*, so a Common hero who began with all 30 points on their back would
       be Weary before leaving home, and every hero above Frugal with them. ``06.7`` treats a
       hero who starts Weary as a notable case — "a heavily armoured hero from a
       low-Endurance culture can legitimately start Weary" — not as the universal outcome,
       which it would be if starting Treasure were carried. Standard of Living reads
       :attr:`TreasureHoldings.total` and so is unaffected either way (``03.7``); the hero
       keeps the wealth and sheds the burden, which is exactly what ``07.4`` says caching is
       for. A player who wants it on their back moves it with ``resources.move_treasure``.
    """
    return pack.living_tier(tier).treasure_threshold or 0


def _build_gear(draft: HeroDraft, pack: ContentPack) -> Gear:
    choice = _require(draft.gear, "a gear choice")
    gear = Gear(
        weapons=[
            WeaponInstance(type_id=selection.item, two_handed=_grip(selection, pack))
            for selection in choice.weapons
        ]
    )
    if choice.armour is not None:
        gear.armour = ArmourInstance(type_id=choice.armour)
    if choice.helm is not None:
        gear.helm = ArmourInstance(type_id=choice.helm)
    if choice.shield is not None:
        gear.shield = ShieldInstance(type_id=choice.shield)
    return gear


def _grip(selection: WeaponSelection, pack: ContentPack) -> bool:
    """``06.7``: either-grip weapons default to one-handed; grip is changeable in play."""
    return selection.two_handed or pack.weapon(selection.item).two_handed_only


def _register_effects(
    hero: Hero, draft: HeroDraft, culture: Culture, pack: ContentPack, bus: EffectBus
) -> None:
    """Everything creation grants, in stage order (``06.2``, ``06.5``, ``06.6``, ``06.8``)."""
    # 11.2 asks for this at hero construction, permanently, so that no code path can
    # forget to attach it: being Ill-favoured at maximum Shadow is a predicate on an
    # always-on effect rather than a threshold the rules have to notice being crossed.
    register_shadow_conditions(bus)

    source = EffectSource.culture(culture.id)
    bus.register(pack.instantiate(culture.cultural_blessing), source)
    if culture.cultural_weakness is not None:
        bus.register(pack.instantiate(culture.cultural_weakness), source)

    for feature in _all_features(draft):
        params = {} if feature.subject is None else {"subject": feature.subject}
        bus.register(
            pack.instantiate(feature.feature, params),
            EffectSource.acquired("distinctive_feature"),
        )

    virtue = _require(draft.starting_virtue, "a starting Virtue")
    bus.register(pack.instantiate(virtue.virtue, virtue.params), EffectSource.acquired("virtue"))
    hero.virtues.append(EffectId(virtue.virtue))

    reward = _require(draft.starting_reward, "a starting Reward")
    bus.register(
        pack.instantiate(reward.reward, reward.params), EffectSource.item(str(reward.item))
    )
    hero.rewards.append(RewardInstance(reward=reward.reward, item=str(reward.item)))
    _record_upgrade(hero, reward)


def _record_upgrade(hero: Hero, reward: RewardChoice) -> None:
    """The Reward also lands on the item instance, which is what makes it plot-immune."""
    for item in hero.gear.carried():
        if item.type_id == reward.item:
            item.upgrades.rewards.append(reward.reward)
            return
    raise StateError(  # pragma: no cover - stage 8 refuses a Reward on an unowned item
        f"{reward.item!r} is not among this hero's gear"
    )


# -- Company formation ---------------------------------------------------------------


def form_company(
    heroes: Sequence[Hero],
    choice: CompanyChoice,
    pack: ContentPack,
    *,
    buses: Mapping[HeroId, EffectBus],
) -> tuple[Company, list[Warning_]]:
    """``06.11``. Run once per Company, separately from hero creation.

    ``buses`` is the keyword-only addition: the Fellowship rating is a derived stat like any
    other (``03.6``, pattern P18), and the Cultural Blessings and Virtues that raise it
    contribute through ``MODIFY_FELLOWSHIP_RATING`` on the bus of the hero who carries them.
    Collecting once per hero is what makes "one culture adds +1 per member of that culture
    present" fall out of the pack rather than out of a special case here.

    Mounts derive from each hero's tier and are recorded here rather than on the hero,
    because that is where ``03.5`` puts them.
    """
    if not heroes:
        raise RuleViolation("a Company needs at least one hero", rule_reference="company_size")

    company = Company(heroes=[hero.id for hero in heroes])

    if choice.patron is not None:
        company.patrons.append(PatronId(pack.patron(choice.patron).id))
    if choice.safe_haven:
        company.safe_havens.append(choice.safe_haven)

    company.fellowship_rating = _fellowship_rating(heroes, choice, pack, buses)
    company.refresh_fellowship()
    company.mounts.update(_mounts(heroes, choice, pack))

    warnings: list[Warning_] = []
    for hero_id, focus in sorted(choice.focus.items()):
        company.set_focus(hero_id, focus)
    if len(choice.focus) < len(heroes):
        warnings.append(
            Warning_(
                code="fellowship_focus_deferred",
                message=(
                    f"{len(heroes) - len(choice.focus)} hero/heroes have named no Fellowship "
                    "Focus; it may be set at any later point"
                ),
            )
        )
    return company, warnings


def _fellowship_rating(
    heroes: Sequence[Hero],
    choice: CompanyChoice,
    pack: ContentPack,
    buses: Mapping[HeroId, EffectBus],
) -> DerivedStat:
    base = len(heroes)
    if choice.patron is not None:
        base += pack.patron(choice.patron).fellowship_bonus
    covered: dict[CombatantId, EffectBus] = {hero.id: buses[hero.id] for hero in heroes}
    ctx = RulesContext(gear=pack, buses=covered)
    stat = DerivedStat(base=base)
    for hero in heroes:
        hook_ctx = ctx.hook_context(Hook.MODIFY_FELLOWSHIP_RATING, hero)
        contributed = ctx.bus(hero.id).apply_numeric(Hook.MODIFY_FELLOWSHIP_RATING, hook_ctx, 0)
        stat = stat.with_modifiers(contributed.modifiers)
    return stat


def _mounts(
    heroes: Sequence[Hero], choice: CompanyChoice, pack: ContentPack
) -> dict[HeroId, Mount]:
    """``06.7``: quality follows the hero's tier, and the two lowest tiers afford none."""
    ladder = pack.living_ladder()
    mounts: dict[HeroId, Mount] = {}
    for hero in heroes:
        vigour = pack.living_tier(hero.standard_of_living(ladder)).mount_vigour
        if vigour is None:
            continue
        mounts[hero.id] = Mount(
            name=choice.mount_names.get(hero.id, f"{hero.name}'s mount"), vigour=vigour
        )
    return mounts


# -- shared draft readers ------------------------------------------------------------


def _culture(draft: HeroDraft, pack: ContentPack) -> Culture:
    if draft.culture is None:
        raise StateError("this stage needs a culture, which is chosen first")
    return pack.culture(draft.culture)


def _require(value: T | None, what: str) -> T:
    """Narrow one of the draft's optional fields at materialisation.

    Every one of them is filled by the stage that owns it, so a ``COMPLETE`` draft always
    carries all of them. These guards exist to say so to the type checker rather than to
    handle a case that can arise — hence a ``StateError``, and hence no test for the raise.
    """
    if value is None:  # pragma: no cover - a COMPLETE draft has passed every stage
        raise StateError(f"a complete draft always carries {what}")
    return value


def _starting_tier(draft: HeroDraft, culture: Culture) -> StandardOfLiving:
    """The culture's tier, or the retiring hero's for an heir (``06.12`` deviation 2)."""
    if draft.inherited_standard_of_living is not None:
        return draft.inherited_standard_of_living
    return culture.standard_of_living
