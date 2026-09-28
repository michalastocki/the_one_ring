"""`tor.rules.creation` — the staged pipeline and Company formation (spec 06).

Unlike the rules tests around it, these load `content/example/` rather than stubbing three
dicts: creation is the one subsystem whose whole job is turning a pack into a hero, and a
hand-built culture would test the test rather than the code. 19.9 still applies — every
value asserted here comes from the invented example pack, never from the licensed book.

Only stage 2 touches randomness, and only on the `roll` path, so `ScriptedRandomness`
appears in exactly one class.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest

from tor.dice import ScriptedRandomness
from tor.effects.bus import EffectBus, EffectSource
from tor.effects.hooks import Hook, HookContext
from tor.errors import ContentError, RuleViolation, StateError
from tor.model.abilities import SKILLS
from tor.model.attributes import Attribute
from tor.model.conditions import StandardOfLiving
from tor.model.gear import UsefulItem
from tor.model.hero import Heir, Hero
from tor.model.ids import (
    AbilityId,
    CallingId,
    CultureId,
    EffectId,
    HeroId,
    ItemId,
    PatronId,
)
from tor.rules.context import RulesContext
from tor.rules.creation import (
    AttributeChoice,
    CallingChoice,
    CompanyChoice,
    CreationStage,
    ExperienceChoice,
    FeatureChoice,
    GearChoice,
    HeroDraft,
    IdentityChoice,
    RewardAndVirtueChoice,
    RewardChoice,
    SkillChoice,
    VirtueChoice,
    WeaponSelection,
    begin_heir,
    begin_hero,
    build_hero,
    choose_attributes,
    choose_calling,
    choose_culture,
    choose_features,
    choose_gear,
    choose_identity,
    choose_reward_and_virtue,
    choose_skills,
    form_company,
    heirlooms_allowed,
    spend_previous_experience,
)
from tor.rules.resources import recompute_load

EXAMPLE_PACK = Path(__file__).resolve().parents[2] / "content" / "example"

FOLK = CultureId("example_folk")
SECOND_FOLK = CultureId("second_folk")
CALLING = CallingId("example_calling")
SECOND_CALLING = CallingId("second_calling")

BLADE = ItemId("example_blade")
BOW = ItemId("example_bow")
SPEAR = ItemId("example_spear")
GREAT_AXE = ItemId("example_great_axe")
UNARMED = ItemId("unarmed")
MAIL = ItemId("example_mail")
LEATHER = ItemId("example_leather")
HELM = ItemId("example_helm")
BUCKLER = ItemId("example_buckler")
GREAT_SHIELD = ItemId("example_great_shield")

SWORDS = AbilityId("swords")
BOWS = AbilityId("bows")
SPEARS = AbilityId("spears")
AXES = AbilityId("axes")
HUNTING = AbilityId("hunting")
BATTLE = AbilityId("battle")
ENHEARTEN = AbilityId("enhearten")
STEALTH = AbilityId("stealth")
RIDDLE = AbilityId("riddle")
SCAN = AbilityId("scan")


@pytest.fixture(scope="module")
def pack():
    from tor.content.loader import load_pack

    return load_pack(EXAMPLE_PACK)


# -- a full walk, reused by everything downstream ------------------------------------


def walk_to(stage: CreationStage, pack, **overrides) -> HeroDraft:
    """Advance a fresh draft up to (but not through) ``stage``.

    Every default here is a legal choice for `example_folk`; a test that cares about one
    stage overrides just that stage's input and inherits the rest.
    """
    draft = overrides.get("start", begin_hero())
    steps: Sequence[tuple[CreationStage, object]] = (
        (CreationStage.CULTURE, overrides.get("culture", FOLK)),
        (
            CreationStage.ATTRIBUTES,
            overrides.get("attributes", AttributeChoice(set_index=4)),
        ),
        (
            CreationStage.SKILLS,
            overrides.get("skills", SkillChoice(favoured=(HUNTING,), proficiencies=(SWORDS, BOWS))),
        ),
        (
            CreationStage.FEATURES,
            overrides.get(
                "features", [FeatureChoice(EffectId("bold")), FeatureChoice(EffectId("eager"))]
            ),
        ),
        (
            CreationStage.CALLING,
            overrides.get("calling", CallingChoice(CALLING, favoured=(BATTLE, ENHEARTEN))),
        ),
        (
            CreationStage.PREVIOUS_EXPERIENCE,
            overrides.get("experience", ExperienceChoice(targets={HUNTING: 4, SCAN: 2})),
        ),
        (CreationStage.GEAR, overrides.get("gear", default_gear())),
        (
            CreationStage.REWARD_AND_VIRTUE,
            overrides.get(
                "reward_and_virtue",
                RewardAndVirtueChoice(
                    reward=RewardChoice(EffectId("cunning_make"), MAIL),
                    virtue=VirtueChoice(EffectId("hardiness")),
                ),
            ),
        ),
        (
            CreationStage.IDENTITY,
            overrides.get("identity", IdentityChoice(name="Testy", age=30)),
        ),
    )
    runners = {
        CreationStage.CULTURE: choose_culture,
        CreationStage.ATTRIBUTES: choose_attributes,
        CreationStage.SKILLS: choose_skills,
        CreationStage.FEATURES: choose_features,
        CreationStage.CALLING: choose_calling,
        CreationStage.PREVIOUS_EXPERIENCE: spend_previous_experience,
        CreationStage.GEAR: choose_gear,
        CreationStage.REWARD_AND_VIRTUE: choose_reward_and_virtue,
        CreationStage.IDENTITY: choose_identity,
    }
    for step, choice in steps:
        if step >= stage:
            break
        draft, _ = runners[step](draft, choice, pack)
    return draft


def default_gear() -> GearChoice:
    return GearChoice(
        weapons=(WeaponSelection(BLADE), WeaponSelection(BOW, two_handed=True)),
        armour=MAIL,
        helm=HELM,
        shield=BUCKLER,
    )


def complete(pack, **overrides) -> HeroDraft:
    return walk_to(CreationStage.COMPLETE, pack, **overrides)


@contextmanager
def restored(entity: object, **overrides: object) -> Iterator[None]:
    """Temporarily rewrite a frozen pack entity in place.

    19.9 forbids fixtures that reproduce the licensed book's tables, and the example pack
    is deliberately small, so the handful of shapes 05/06 allow but the pack does not
    happen to use are produced by narrowing one of its own entries rather than by inventing
    a rival culture. The original is put back whatever the test does.
    """
    before = {name: getattr(entity, name) for name in overrides}
    for name, value in overrides.items():
        object.__setattr__(entity, name, value)
    try:
        yield
    finally:
        for name, value in before.items():
            object.__setattr__(entity, name, value)


#: Exactly 20 points down 05.10's Skill ladder — an heir's largest possible reserve.
HEIR_SPEND = {HUNTING: 4, SCAN: 3, STEALTH: 3, AbilityId("awareness"): 1}


def second_folk(**overrides) -> dict[str, object]:
    """A legal walk for the restricted, Frugal, bonus-granting second culture."""
    return {
        "culture": SECOND_FOLK,
        "attributes": AttributeChoice(set_index=1, bonus_attribute=Attribute.HEART),
        "skills": SkillChoice(favoured=(STEALTH,), proficiencies=(SPEARS, BOWS)),
        "features": [FeatureChoice(EffectId("cunning")), FeatureChoice(EffectId("merry"))],
        "calling": CallingChoice(SECOND_CALLING, favoured=(RIDDLE, SCAN), subject="orcs"),
        "experience": ExperienceChoice(targets={STEALTH: 4}),
        "gear": GearChoice(weapons=(WeaponSelection(SPEAR),), armour=LEATHER),
        "reward_and_virtue": RewardAndVirtueChoice(
            reward=RewardChoice(EffectId("close_fitting"), LEATHER),
            virtue=VirtueChoice(EffectId("confidence")),
        ),
        **overrides,
    }


def built(pack, hero_id: str = "h1", **overrides) -> tuple[Hero, EffectBus]:
    bus = EffectBus()
    hero = build_hero(complete(pack, **overrides), pack, bus=bus, hero_id=HeroId(hero_id))
    return hero, bus


# -- the pipeline itself -------------------------------------------------------------


class TestPipelineShape:
    def test_a_fresh_draft_awaits_a_culture(self) -> None:
        assert begin_hero().stage is CreationStage.CULTURE

    def test_each_stage_advances_by_one(self, pack) -> None:
        draft = begin_hero()
        for expected in list(CreationStage)[1:]:
            draft = walk_to(expected, pack)
            assert draft.stage is expected

    def test_a_stage_cannot_be_skipped(self, pack) -> None:
        # 01.6: driving the pipeline out of order is a caller bug, not an illegal move.
        with pytest.raises(StateError, match="creation stages run in order"):
            choose_calling(begin_hero(), CallingChoice(CALLING, favoured=(BATTLE, ENHEARTEN)), pack)

    def test_a_stage_that_reads_the_draft_says_what_is_missing(self, pack) -> None:
        with pytest.raises(StateError, match="needs a culture"):
            choose_features(begin_hero(), [], pack)

    def test_the_draft_is_frozen(self, pack) -> None:
        draft = walk_to(CreationStage.ATTRIBUTES, pack)
        with pytest.raises(AttributeError):
            draft.culture = SECOND_FOLK  # type: ignore[misc]


class TestCascade:
    """06.1: re-running a stage invalidates every later stage."""

    def test_rerunning_culture_clears_everything_after_it(self, pack) -> None:
        draft = complete(pack)
        again, _ = choose_culture(draft, SECOND_FOLK, pack)

        assert again.stage is CreationStage.ATTRIBUTES
        assert again.culture == SECOND_FOLK
        assert again.attributes is None
        assert again.skills == {}
        assert again.favoured == frozenset()
        assert again.calling is None
        assert again.gear is None
        assert again.name is None

    def test_rerunning_a_middle_stage_keeps_what_came_before(self, pack) -> None:
        draft = complete(pack)
        again, _ = choose_features(
            draft, [FeatureChoice(EffectId("fair")), FeatureChoice(EffectId("wilful"))], pack
        )

        assert again.culture == FOLK
        assert again.attributes is not None
        assert again.favoured == frozenset({HUNTING})
        assert [f.feature for f in again.features] == ["fair", "wilful"]
        assert again.calling is None

    def test_rerunning_the_calling_withdraws_only_its_own_favoured_skills(self, pack) -> None:
        # The reason calling_favoured is a field of its own: a shared `favoured` set could
        # not be un-merged, and re-running stage 5 would either keep the old Calling's
        # grant or discard the culture's along with it.
        draft = complete(pack)
        assert draft.calling_favoured == frozenset({BATTLE, ENHEARTEN})

        again, _ = choose_calling(
            draft, CallingChoice(CALLING, favoured=(BATTLE, AbilityId("persuade"))), pack
        )
        assert again.favoured == frozenset({HUNTING})
        assert again.calling_favoured == frozenset({BATTLE, AbilityId("persuade")})

    def test_a_stage_may_be_re_run_in_place(self, pack) -> None:
        draft = walk_to(CreationStage.SKILLS, pack)
        first, _ = choose_attributes(draft, AttributeChoice(set_index=1), pack)
        second, _ = choose_attributes(first, AttributeChoice(set_index=6), pack)
        assert second.attributes is not None
        assert (second.attributes.strength, second.attributes.wits) == (6, 2)


# -- stage 1 -------------------------------------------------------------------------


class TestCulture:
    def test_an_unknown_culture_is_a_content_error(self, pack) -> None:
        with pytest.raises(ContentError, match="no such culture"):
            choose_culture(begin_hero(), CultureId("nowhere_folk"), pack)


# -- stage 2 -------------------------------------------------------------------------


class TestAttributes:
    def test_picking_a_set_reads_the_row(self, pack) -> None:
        draft = walk_to(CreationStage.ATTRIBUTES, pack)
        draft, warnings = choose_attributes(draft, AttributeChoice(set_index=1), pack)
        assert draft.attributes is not None
        assert (draft.attributes.strength, draft.attributes.heart, draft.attributes.wits) == (
            5,
            7,
            2,
        )
        assert warnings == []

    def test_rolling_consumes_exactly_one_success_die(self, pack) -> None:
        # 06.3: the roll selects a row from the same six-row table; it is not an
        # alternative distribution.
        rng = ScriptedRandomness(successes=[1])
        draft = walk_to(CreationStage.ATTRIBUTES, pack)
        draft, _ = choose_attributes(draft, AttributeChoice(method="roll"), pack, rng=rng)
        assert rng.exhausted
        assert draft.attributes is not None
        assert draft.attributes.strength == 5

    def test_rolling_without_a_randomness_is_a_caller_bug(self, pack) -> None:
        draft = walk_to(CreationStage.ATTRIBUTES, pack)
        with pytest.raises(StateError, match="needs a Randomness"):
            choose_attributes(draft, AttributeChoice(method="roll"), pack)

    def test_picking_without_an_index_is_refused(self, pack) -> None:
        draft = walk_to(CreationStage.ATTRIBUTES, pack)
        with pytest.raises(RuleViolation, match="needs a set_index"):
            choose_attributes(draft, AttributeChoice(), pack)

    def test_an_index_outside_the_table_is_refused(self, pack) -> None:
        draft = walk_to(CreationStage.ATTRIBUTES, pack)
        with pytest.raises(RuleViolation, match="no Attribute set 9"):
            choose_attributes(draft, AttributeChoice(set_index=9), pack)

    def test_a_free_bonus_applies_before_anything_derives_from_the_score(self, pack) -> None:
        # 06.2: which is why it is a stage-2 choice and not a runtime hook.
        draft = walk_to(CreationStage.ATTRIBUTES, pack, **second_folk())
        draft, _ = choose_attributes(
            draft,
            AttributeChoice(set_index=1, bonus_attribute=Attribute.STRENGTH),
            pack,
        )
        assert draft.attributes is not None
        assert draft.attributes.strength == 4  # the row's 3, plus the blessing

    def test_a_culture_granting_a_bonus_requires_one_to_be_chosen(self, pack) -> None:
        draft = walk_to(CreationStage.ATTRIBUTES, pack, **second_folk())
        with pytest.raises(RuleViolation, match="needs one chosen"):
            choose_attributes(draft, AttributeChoice(set_index=1), pack)

    def test_a_bonus_restricted_to_named_attributes_refuses_the_others(self, pack) -> None:
        # 06.2 models the grant as {"count": 1, "choose": "any"} *or* a list. The example
        # pack only exercises "any", so the narrowed form is built here.
        draft = walk_to(CreationStage.ATTRIBUTES, pack, **second_folk())
        culture = pack.culture(SECOND_FOLK)
        narrowed = {"count": 1, "choose": ["strength"]}
        with (
            restored(culture, attribute_bonus=narrowed),
            pytest.raises(RuleViolation, match="may raise only"),
        ):
            choose_attributes(
                draft, AttributeChoice(set_index=1, bonus_attribute=Attribute.WITS), pack
            )
        with restored(culture, attribute_bonus=narrowed):
            ok, _ = choose_attributes(
                draft,
                AttributeChoice(set_index=1, bonus_attribute=Attribute.STRENGTH),
                pack,
            )
        assert ok.attributes is not None
        assert ok.attributes.strength == 4

    def test_a_culture_without_a_bonus_refuses_one(self, pack) -> None:
        draft = walk_to(CreationStage.ATTRIBUTES, pack)
        with pytest.raises(RuleViolation, match="grants no free Attribute bonus"):
            choose_attributes(
                draft, AttributeChoice(set_index=1, bonus_attribute=Attribute.WITS), pack
            )


# -- stage 3 -------------------------------------------------------------------------


class TestSkills:
    def test_the_cultures_eighteen_ratings_are_copied_verbatim(self, pack) -> None:
        draft = walk_to(CreationStage.FEATURES, pack)
        assert draft.skills == dict(pack.culture(FOLK).skills)
        assert set(draft.skills) == set(SKILLS)

    def test_favoured_must_come_from_the_cultures_own_list(self, pack) -> None:
        draft = walk_to(CreationStage.SKILLS, pack)
        with pytest.raises(RuleViolation, match="not one of culture"):
            choose_skills(
                draft, SkillChoice(favoured=(STEALTH,), proficiencies=(SWORDS, BOWS)), pack
            )

    def test_the_favoured_count_is_the_cultures(self, pack) -> None:
        draft = walk_to(CreationStage.SKILLS, pack)
        with pytest.raises(RuleViolation, match="marks 1 Skill"):
            choose_skills(
                draft, SkillChoice(favoured=(HUNTING, BATTLE), proficiencies=(SWORDS, BOWS)), pack
            )

    def test_a_combat_proficiency_can_never_be_favoured(self, pack) -> None:
        # Invariant I7, caught before the hero exists rather than at Hero.validate().
        draft = walk_to(CreationStage.SKILLS, pack)
        # 05.1.1 check 2 lets a culture name any known ability here, Proficiencies
        # included, so this is a shape the loader admits and the stage must refuse.
        with (
            restored(pack.culture(FOLK), favoured_skill_choices=(SWORDS,)),
            pytest.raises(RuleViolation, match="cannot be Favoured"),
        ):
            choose_skills(
                draft, SkillChoice(favoured=(SWORDS,), proficiencies=(SWORDS, BOWS)), pack
            )

    def test_every_grant_needs_one_pick(self, pack) -> None:
        draft = walk_to(CreationStage.SKILLS, pack)
        with pytest.raises(RuleViolation, match="makes 2 Combat Proficiency"):
            choose_skills(draft, SkillChoice(favoured=(HUNTING,), proficiencies=(SWORDS,)), pack)

    def test_a_pick_outside_a_restricted_grant_is_refused(self, pack) -> None:
        draft = walk_to(CreationStage.SKILLS, pack)
        with pytest.raises(RuleViolation, match="this grant offers"):
            choose_skills(
                draft, SkillChoice(favoured=(HUNTING,), proficiencies=(SPEARS, BOWS)), pack
            )

    def test_an_any_grant_takes_any_of_the_four(self, pack) -> None:
        draft = walk_to(CreationStage.SKILLS, pack)
        draft, warnings = choose_skills(
            draft, SkillChoice(favoured=(HUNTING,), proficiencies=(SWORDS, AXES)), pack
        )
        assert draft.proficiencies == {AXES: 1, BOWS: 0, SPEARS: 0, SWORDS: 2}
        assert warnings == []

    def test_a_non_proficiency_pick_is_refused(self, pack) -> None:
        draft = walk_to(CreationStage.SKILLS, pack)
        with pytest.raises(RuleViolation, match="not a Combat Proficiency"):
            choose_skills(
                draft, SkillChoice(favoured=(HUNTING,), proficiencies=(SWORDS, HUNTING)), pack
            )

    def test_overlapping_grants_do_not_stack_and_warn(self, pack) -> None:
        # 06.4: an "any" choice may legally name a proficiency already granted — the
        # higher rating applies, and the player is told rather than refused.
        draft = walk_to(CreationStage.SKILLS, pack)
        draft, warnings = choose_skills(
            draft, SkillChoice(favoured=(HUNTING,), proficiencies=(SWORDS, SWORDS)), pack
        )
        assert draft.proficiencies[SWORDS] == 2
        assert [w.code for w in warnings] == ["proficiency_grant_overlaps"]

    def test_a_proficiency_no_allowed_weapon_uses_warns(self, pack) -> None:
        # second_folk allows blade, spear, bow and unarmed — nothing using axes.
        draft = walk_to(CreationStage.SKILLS, pack, **second_folk())
        draft, warnings = choose_skills(
            draft, SkillChoice(favoured=(STEALTH,), proficiencies=(SPEARS, AXES)), pack
        )
        assert [w.code for w in warnings] == ["proficiency_without_weapon"]
        assert draft.proficiencies[AXES] == 1

    def test_an_unrestricted_culture_never_warns_about_weapons(self, pack) -> None:
        draft = walk_to(CreationStage.SKILLS, pack)
        _, warnings = choose_skills(
            draft, SkillChoice(favoured=(HUNTING,), proficiencies=(SWORDS, AXES)), pack
        )
        assert warnings == []


# -- stage 4 -------------------------------------------------------------------------


class TestFeatures:
    def test_exactly_the_cultures_count(self, pack) -> None:
        draft = walk_to(CreationStage.FEATURES, pack)
        with pytest.raises(RuleViolation, match="grants 2 Distinctive Feature"):
            choose_features(draft, [FeatureChoice(EffectId("bold"))], pack)

    def test_features_come_from_the_cultures_list(self, pack) -> None:
        draft = walk_to(CreationStage.FEATURES, pack)
        with pytest.raises(RuleViolation, match="not one of culture"):
            choose_features(
                draft, [FeatureChoice(EffectId("bold")), FeatureChoice(EffectId("cunning"))], pack
            )

    def test_the_same_feature_cannot_be_taken_twice(self, pack) -> None:
        draft = walk_to(CreationStage.FEATURES, pack)
        with pytest.raises(RuleViolation, match="chosen twice"):
            choose_features(
                draft, [FeatureChoice(EffectId("bold")), FeatureChoice(EffectId("bold"))], pack
            )

    def test_a_feature_that_names_no_subject_refuses_one(self, pack) -> None:
        draft = walk_to(CreationStage.FEATURES, pack)
        with pytest.raises(RuleViolation, match="names no subject"):
            choose_features(
                draft,
                [FeatureChoice(EffectId("bold"), subject="orcs"), FeatureChoice(EffectId("eager"))],
                pack,
            )


# -- stage 5 -------------------------------------------------------------------------


class TestCalling:
    def test_two_favoured_skills_from_the_callings_three(self, pack) -> None:
        draft = walk_to(CreationStage.CALLING, pack)
        draft, warnings = choose_calling(
            draft, CallingChoice(CALLING, favoured=(BATTLE, ENHEARTEN)), pack
        )
        assert draft.calling_favoured == frozenset({BATTLE, ENHEARTEN})
        assert draft.calling_feature == FeatureChoice(EffectId("steadfast"))
        assert warnings == []

    def test_the_count_is_the_callings(self, pack) -> None:
        draft = walk_to(CreationStage.CALLING, pack)
        with pytest.raises(RuleViolation, match="marks 2 Skill"):
            choose_calling(draft, CallingChoice(CALLING, favoured=(BATTLE,)), pack)

    def test_the_same_skill_cannot_be_named_twice(self, pack) -> None:
        draft = walk_to(CreationStage.CALLING, pack)
        with pytest.raises(RuleViolation, match="Favoured twice"):
            choose_calling(draft, CallingChoice(CALLING, favoured=(BATTLE, BATTLE)), pack)

    def test_a_skill_outside_the_callings_list_is_refused(self, pack) -> None:
        draft = walk_to(CreationStage.CALLING, pack)
        with pytest.raises(RuleViolation, match="not one of calling"):
            choose_calling(draft, CallingChoice(CALLING, favoured=(BATTLE, STEALTH)), pack)

    def test_re_favouring_a_cultural_skill_warns_rather_than_refuses(self, pack) -> None:
        # 06.6: it does not double, and the player should reconsider before it is wasted.
        draft = walk_to(
            CreationStage.CALLING,
            pack,
            skills=SkillChoice(favoured=(BATTLE,), proficiencies=(SWORDS, BOWS)),
        )
        draft, warnings = choose_calling(
            draft, CallingChoice(CALLING, favoured=(BATTLE, ENHEARTEN)), pack
        )
        assert [w.code for w in warnings] == ["favoured_skill_already_favoured"]
        assert draft.calling_favoured == frozenset({BATTLE, ENHEARTEN})

    def test_a_feature_requiring_a_subject_must_be_given_one(self, pack) -> None:
        draft = walk_to(CreationStage.CALLING, pack)
        with pytest.raises(RuleViolation, match="must name its subject"):
            choose_calling(draft, CallingChoice(SECOND_CALLING, favoured=(RIDDLE, SCAN)), pack)

    def test_a_subject_is_recorded_on_the_feature(self, pack) -> None:
        draft = walk_to(CreationStage.CALLING, pack)
        draft, _ = choose_calling(
            draft, CallingChoice(SECOND_CALLING, favoured=(RIDDLE, SCAN), subject="orcs"), pack
        )
        assert draft.calling_feature == FeatureChoice(EffectId("enemy_lore"), subject="orcs")


# -- stage 6 -------------------------------------------------------------------------


class TestPreviousExperience:
    def test_the_mandatory_ladder_vector(self, pack) -> None:
        # 19.4: raising one Skill from rating 1 to rating 4 costs exactly the full budget.
        ladder = pack.cost_ladder("previous_experience", "skills")
        assert ladder.cost_to_raise(1, 4) == 10
        assert int(pack.experience_costs()["previous_experience"]["budget"]) == 10

    def test_a_target_within_budget_is_recorded(self, pack) -> None:
        draft = walk_to(CreationStage.PREVIOUS_EXPERIENCE, pack)
        draft, warnings = spend_previous_experience(
            draft, ExperienceChoice(targets={HUNTING: 4, SCAN: 2}), pack
        )
        assert draft.previous_experience_spend == {HUNTING: 4, SCAN: 2}
        assert warnings == []

    def test_ratings_may_be_bought_from_zero(self, pack) -> None:
        draft = walk_to(CreationStage.PREVIOUS_EXPERIENCE, pack)
        draft, _ = spend_previous_experience(
            draft, ExperienceChoice(targets={AbilityId("awareness"): 3}), pack
        )
        assert draft.previous_experience_spend[AbilityId("awareness")] == 3

    def test_overspending_is_refused(self, pack) -> None:
        draft = walk_to(CreationStage.PREVIOUS_EXPERIENCE, pack)
        with pytest.raises(RuleViolation, match="costs 12 but the budget is 10"):
            spend_previous_experience(draft, ExperienceChoice(targets={HUNTING: 4, BOWS: 2}), pack)

    def test_unspent_points_are_lost_and_said_so(self, pack) -> None:
        draft = walk_to(CreationStage.PREVIOUS_EXPERIENCE, pack)
        _, warnings = spend_previous_experience(draft, ExperienceChoice(), pack)
        assert [w.code for w in warnings] == ["previous_experience_unspent"]

    def test_the_skill_ladder_tops_out_at_four(self, pack) -> None:
        draft = walk_to(CreationStage.PREVIOUS_EXPERIENCE, pack)
        with pytest.raises(RuleViolation, match=r"prices ratings 1\.\.4"):
            spend_previous_experience(draft, ExperienceChoice(targets={HUNTING: 5}), pack)

    def test_the_proficiency_ladder_tops_out_at_three(self, pack) -> None:
        draft = walk_to(CreationStage.PREVIOUS_EXPERIENCE, pack)
        with pytest.raises(RuleViolation, match=r"prices ratings 1\.\.3"):
            spend_previous_experience(draft, ExperienceChoice(targets={BOWS: 4}), pack)

    def test_the_two_ladders_are_not_conflated(self, pack) -> None:
        # 05.10 opens by warning against exactly this. Raising a Proficiency from 0 to 2
        # costs 2 + 4; the Skill ladder would have charged 1 + 2.
        proficiencies = pack.cost_ladder("previous_experience", "proficiencies")
        skills = pack.cost_ladder("previous_experience", "skills")
        assert proficiencies.cost_to_raise(0, 2) == 6
        assert skills.cost_to_raise(0, 2) == 3

    def test_an_unknown_ability_is_refused(self, pack) -> None:
        draft = walk_to(CreationStage.PREVIOUS_EXPERIENCE, pack)
        with pytest.raises(RuleViolation, match="neither one of the 18 Skills"):
            spend_previous_experience(
                draft, ExperienceChoice(targets={AbilityId("brawling"): 2}), pack
            )

    def test_spending_before_the_skills_stage_is_a_caller_bug(self, pack) -> None:
        with pytest.raises(StateError, match="after Skills"):
            spend_previous_experience(begin_hero(), ExperienceChoice(), pack)


# -- stage 7 -------------------------------------------------------------------------


class TestGear:
    def test_a_legal_kit_is_accepted(self, pack) -> None:
        draft = walk_to(CreationStage.GEAR, pack)
        draft, warnings = choose_gear(draft, default_gear(), pack)
        assert draft.gear is not None
        assert warnings == []

    def test_one_weapon_per_proficiency_not_per_point(self, pack) -> None:
        draft = walk_to(CreationStage.GEAR, pack)
        with pytest.raises(RuleViolation, match="already has a starting weapon"):
            choose_gear(
                draft,
                GearChoice(weapons=(WeaponSelection(BLADE), WeaponSelection(BLADE)), armour=MAIL),
                pack,
            )

    def test_a_weapon_needs_a_rating_in_its_proficiency(self, pack) -> None:
        draft = walk_to(CreationStage.GEAR, pack)
        with pytest.raises(RuleViolation, match="needs a rating of 1 or more"):
            choose_gear(draft, GearChoice(weapons=(WeaponSelection(SPEAR),)), pack)

    def test_a_proficiency_bought_at_stage_six_earns_a_weapon(self, pack) -> None:
        # The ratings stage 7 reads are the post-Previous-Experience ones.
        draft = walk_to(CreationStage.GEAR, pack, experience=ExperienceChoice(targets={SPEARS: 1}))
        draft, _ = choose_gear(draft, GearChoice(weapons=(WeaponSelection(SPEAR),)), pack)
        assert draft.gear is not None

    def test_brawling_claims_no_proficiency_slot(self, pack) -> None:
        # 03.3: Brawling is an attack mode, not a Combat Proficiency, so "one weapon per
        # Combat Proficiency" does not govern it.
        draft = walk_to(CreationStage.GEAR, pack, **second_folk())
        draft, _ = choose_gear(
            draft,
            GearChoice(weapons=(WeaponSelection(SPEAR), WeaponSelection(UNARMED))),
            pack,
        )
        assert draft.gear is not None
        assert len(draft.gear.weapons) == 2

    def test_a_two_handed_only_weapon_must_be_wielded_two_handed(self, pack) -> None:
        draft = walk_to(CreationStage.GEAR, pack)
        with pytest.raises(RuleViolation, match="two-handed only"):
            choose_gear(draft, GearChoice(weapons=(WeaponSelection(BOW),)), pack)

    def test_a_one_handed_weapon_cannot_be_wielded_two_handed(self, pack) -> None:
        draft = walk_to(CreationStage.GEAR, pack)
        with pytest.raises(RuleViolation, match="cannot be wielded two-handed"):
            choose_gear(draft, GearChoice(weapons=(WeaponSelection(BLADE, two_handed=True),)), pack)

    def test_a_helm_cannot_be_worn_as_body_armour(self, pack) -> None:
        draft = walk_to(CreationStage.GEAR, pack)
        with pytest.raises(RuleViolation, match="cannot be worn as body armour"):
            choose_gear(draft, GearChoice(armour=HELM), pack)

    def test_body_armour_cannot_be_worn_as_a_helm(self, pack) -> None:
        draft = walk_to(CreationStage.GEAR, pack)
        with pytest.raises(RuleViolation, match="cannot be worn as helm"):
            choose_gear(draft, GearChoice(helm=MAIL), pack)

    def test_a_standard_of_living_minimum_is_enforced(self, pack) -> None:
        # example_mail needs Common; second_folk starts Frugal.
        draft = walk_to(CreationStage.GEAR, pack, **second_folk())
        with pytest.raises(RuleViolation, match="requires a Standard of Living of at least common"):
            choose_gear(draft, GearChoice(armour=MAIL), pack)

    def test_a_shield_minimum_is_enforced(self, pack) -> None:
        draft = walk_to(CreationStage.GEAR, pack)
        with pytest.raises(RuleViolation, match="at least prosperous"):
            choose_gear(draft, GearChoice(shield=GREAT_SHIELD), pack)

    def test_a_culture_may_forbid_an_individual_item(self, pack) -> None:
        draft = walk_to(CreationStage.GEAR, pack, **second_folk())
        with pytest.raises(RuleViolation, match="forbids"):
            choose_gear(draft, GearChoice(shield=GREAT_SHIELD), pack)

    def test_the_weapon_allow_list_is_weapons_only(self, pack) -> None:
        # 03.5.1: applying allowed_weapons to armour would leave a restricted culture with
        # no armour at all, because a weapon list never names any.
        draft = walk_to(CreationStage.GEAR, pack, **second_folk())
        draft, _ = choose_gear(
            draft,
            GearChoice(weapons=(WeaponSelection(SPEAR),), armour=LEATHER, helm=HELM),
            pack,
        )
        assert draft.gear is not None
        assert draft.gear.armour == LEATHER

    def test_a_weapon_outside_the_allow_list_is_refused(self, pack) -> None:
        draft = walk_to(
            CreationStage.GEAR,
            pack,
            **second_folk(skills=SkillChoice(favoured=(STEALTH,), proficiencies=(SPEARS, AXES))),
        )
        with pytest.raises(RuleViolation, match="restricts its heroes to"):
            choose_gear(
                draft, GearChoice(weapons=(WeaponSelection(GREAT_AXE, two_handed=True),)), pack
            )

    def test_useful_items_are_capped_by_the_tier(self, pack) -> None:
        draft = walk_to(CreationStage.GEAR, pack)
        items = tuple(UsefulItem(f"item {n}", AbilityId("craft")) for n in range(3))
        with pytest.raises(RuleViolation, match="carries 2 Useful Item"):
            choose_gear(draft, GearChoice(useful_items=items), pack)

    def test_a_useful_item_names_one_of_the_eighteen_skills(self, pack) -> None:
        draft = walk_to(CreationStage.GEAR, pack)
        with pytest.raises(RuleViolation, match="names one of the 18 Skills"):
            choose_gear(draft, GearChoice(useful_items=(UsefulItem("a blade", SWORDS),)), pack)

    def test_travelling_gear_is_free_text_and_carries_no_load(self, pack) -> None:
        draft = walk_to(CreationStage.GEAR, pack)
        draft, _ = choose_gear(
            draft, GearChoice(travelling_gear=("a bedroll", "a cooking pot")), pack
        )
        assert draft.travelling_gear == ("a bedroll", "a cooking pot")


# -- stage 8 -------------------------------------------------------------------------


class TestRewardAndVirtue:
    def test_a_legal_pair_is_accepted(self, pack) -> None:
        draft = walk_to(CreationStage.REWARD_AND_VIRTUE, pack)
        draft, warnings = choose_reward_and_virtue(
            draft,
            RewardAndVirtueChoice(
                reward=RewardChoice(EffectId("close_fitting"), MAIL),
                virtue=VirtueChoice(EffectId("confidence")),
            ),
            pack,
        )
        assert draft.starting_reward is not None
        assert draft.starting_virtue is not None
        assert warnings == []

    def test_a_reward_binds_only_to_an_owned_item(self, pack) -> None:
        draft = walk_to(CreationStage.REWARD_AND_VIRTUE, pack)
        with pytest.raises(RuleViolation, match="not among this hero's starting gear"):
            choose_reward_and_virtue(
                draft,
                RewardAndVirtueChoice(
                    reward=RewardChoice(EffectId("cunning_make"), SPEAR),
                    virtue=VirtueChoice(EffectId("hardiness")),
                ),
                pack,
            )

    def test_a_reward_must_satisfy_its_applies_to(self, pack) -> None:
        draft = walk_to(CreationStage.REWARD_AND_VIRTUE, pack)
        with pytest.raises(RuleViolation, match="applies to"):
            choose_reward_and_virtue(
                draft,
                RewardAndVirtueChoice(
                    reward=RewardChoice(EffectId("grievous"), MAIL),
                    virtue=VirtueChoice(EffectId("hardiness")),
                ),
                pack,
            )

    def test_a_non_reward_effect_is_refused(self, pack) -> None:
        draft = walk_to(CreationStage.REWARD_AND_VIRTUE, pack)
        with pytest.raises(RuleViolation, match="not a Reward"):
            choose_reward_and_virtue(
                draft,
                RewardAndVirtueChoice(
                    reward=RewardChoice(EffectId("hardiness"), MAIL),
                    virtue=VirtueChoice(EffectId("hardiness")),
                ),
                pack,
            )

    def test_a_cultural_virtue_is_not_available_at_creation(self, pack) -> None:
        # 06.8: they require WISDOM 2, and a new hero has 1.
        draft = walk_to(CreationStage.REWARD_AND_VIRTUE, pack)
        with pytest.raises(RuleViolation, match="Cultural Virtue"):
            choose_reward_and_virtue(
                draft,
                RewardAndVirtueChoice(
                    reward=RewardChoice(EffectId("cunning_make"), MAIL),
                    virtue=VirtueChoice(EffectId("example_cv_hope")),
                ),
                pack,
            )

    def test_a_virtue_gated_on_wisdom_is_refused_at_creation(self, pack) -> None:
        # Distinct from the Cultural Virtue rule: 05.4 lets an ordinary Virtue carry a
        # min_wisdom of its own, and a new hero has WISDOM 1.
        draft = walk_to(CreationStage.REWARD_AND_VIRTUE, pack)
        with (
            restored(pack.effect("confidence"), min_wisdom=3),
            pytest.raises(RuleViolation, match="requires WISDOM 3"),
        ):
            choose_reward_and_virtue(
                draft,
                RewardAndVirtueChoice(
                    reward=RewardChoice(EffectId("cunning_make"), MAIL),
                    virtue=VirtueChoice(EffectId("confidence")),
                ),
                pack,
            )

    def test_a_non_virtue_effect_is_refused(self, pack) -> None:
        draft = walk_to(CreationStage.REWARD_AND_VIRTUE, pack)
        with pytest.raises(RuleViolation, match="not a Virtue"):
            choose_reward_and_virtue(
                draft,
                RewardAndVirtueChoice(
                    reward=RewardChoice(EffectId("cunning_make"), MAIL),
                    virtue=VirtueChoice(EffectId("bold")),
                ),
                pack,
            )

    def test_a_virtue_requiring_a_choice_must_be_given_one(self, pack) -> None:
        draft = walk_to(CreationStage.REWARD_AND_VIRTUE, pack)
        with pytest.raises(RuleViolation, match="requires a 'skills' choice"):
            choose_reward_and_virtue(
                draft,
                RewardAndVirtueChoice(
                    reward=RewardChoice(EffectId("cunning_make"), MAIL),
                    virtue=VirtueChoice(EffectId("mastery")),
                ),
                pack,
            )

    def test_the_choice_must_have_the_right_count(self, pack) -> None:
        draft = walk_to(CreationStage.REWARD_AND_VIRTUE, pack)
        with pytest.raises(RuleViolation, match="requires 2 'skills' choice"):
            choose_reward_and_virtue(
                draft,
                RewardAndVirtueChoice(
                    reward=RewardChoice(EffectId("cunning_make"), MAIL),
                    virtue=VirtueChoice(EffectId("mastery"), params={"skills": [STEALTH]}),
                ),
                pack,
            )

    def test_a_single_choice_accepts_a_bare_value(self, pack) -> None:
        draft = walk_to(CreationStage.REWARD_AND_VIRTUE, pack)
        draft, _ = choose_reward_and_virtue(
            draft,
            RewardAndVirtueChoice(
                reward=RewardChoice(EffectId("cunning_make"), MAIL),
                virtue=VirtueChoice(EffectId("prowess"), params={"attribute": "wits"}),
            ),
            pack,
        )
        assert draft.starting_virtue is not None
        assert draft.starting_virtue.params == {"attribute": "wits"}

    def test_choosing_before_gear_is_a_caller_bug(self, pack) -> None:
        draft = walk_to(CreationStage.GEAR, pack)
        with pytest.raises(StateError, match="gear is chosen first"):
            choose_reward_and_virtue(
                draft,
                RewardAndVirtueChoice(
                    reward=RewardChoice(EffectId("cunning_make"), MAIL),
                    virtue=VirtueChoice(EffectId("hardiness")),
                ),
                pack,
            )


# -- stage 9 -------------------------------------------------------------------------


class TestIdentity:
    def test_a_name_and_an_age_complete_the_draft(self, pack) -> None:
        draft = walk_to(CreationStage.IDENTITY, pack)
        draft, warnings = choose_identity(draft, IdentityChoice(name="Testy", age=30), pack)
        assert draft.stage is CreationStage.COMPLETE
        assert (draft.name, draft.age) == ("Testy", 30)
        assert warnings == []

    def test_an_empty_name_is_refused(self, pack) -> None:
        draft = walk_to(CreationStage.IDENTITY, pack)
        with pytest.raises(RuleViolation, match="needs a name"):
            choose_identity(draft, IdentityChoice(name="   ", age=30), pack)

    @pytest.mark.parametrize("age", [17, 46])
    def test_an_age_outside_the_range_warns_but_is_legal(self, pack, age: int) -> None:
        # 06.9: the rulebook frames those bounds as typical, not binding.
        draft = walk_to(CreationStage.IDENTITY, pack)
        draft, warnings = choose_identity(draft, IdentityChoice(name="Testy", age=age), pack)
        assert draft.stage is CreationStage.COMPLETE
        assert [w.code for w in warnings] == ["age_outside_range"]


# -- materialisation -----------------------------------------------------------------


class TestBuildHero:
    def test_an_incomplete_draft_cannot_be_built(self, pack) -> None:
        with pytest.raises(StateError, match="still awaiting its IDENTITY stage"):
            build_hero(
                walk_to(CreationStage.IDENTITY, pack),
                pack,
                bus=EffectBus(),
                hero_id=HeroId("h1"),
            )

    def test_the_post_conditions_of_6_10(self, pack) -> None:
        hero, _ = built(pack)
        assert hero.endurance == hero.max_endurance.value
        assert hero.hope == hero.max_hope.value
        assert hero.shadow == 0
        assert hero.valour == hero.wisdom == 1
        hero.validate()

    def test_the_maxima_and_parry_come_from_the_cultures_own_constants(self, pack) -> None:
        # 03.4.2. The engine must never assume a value for any of the three.
        hero, _ = built(
            pack,
            reward_and_virtue=RewardAndVirtueChoice(
                reward=RewardChoice(EffectId("cunning_make"), MAIL),
                virtue=VirtueChoice(EffectId("nimbleness")),
            ),
        )
        derived = pack.culture(FOLK).derived
        assert hero.max_endurance.base == hero.attributes.strength + derived.endurance_bonus
        assert hero.max_hope.base == hero.attributes.heart + derived.hope_bonus
        assert hero.parry.base == hero.attributes.wits + derived.parry_bonus

    def test_a_virtue_raising_a_maximum_raises_the_current_value(self, pack) -> None:
        # 03.4.2. It falls out of registering the Virtue before the maxima are read.
        plain, _ = built(
            pack,
            reward_and_virtue=RewardAndVirtueChoice(
                reward=RewardChoice(EffectId("cunning_make"), MAIL),
                virtue=VirtueChoice(EffectId("nimbleness")),
            ),
        )
        hardy, _ = built(pack)
        assert hardy.max_endurance.value == plain.max_endurance.value + 2
        assert hardy.endurance == hardy.max_endurance.value

    def test_previous_experience_is_applied_to_the_ratings(self, pack) -> None:
        hero, _ = built(pack)
        assert hero.skills[HUNTING] == 4
        assert hero.skills[SCAN] == 2

    def test_favoured_skills_union_the_culture_and_the_calling(self, pack) -> None:
        hero, _ = built(pack)
        assert hero.favoured_skills == {HUNTING, BATTLE, ENHEARTEN}

    def test_the_calling_supplies_a_shadow_path_and_a_third_feature(self, pack) -> None:
        hero, _ = built(pack)
        assert hero.shadow_path == "example_path"
        assert hero.distinctive_features == ["bold", "eager", "steadfast"]

    def test_every_granted_effect_reaches_the_bus_in_stage_order(self, pack) -> None:
        hero, bus = built(pack)
        assert [str(e.id) for e in bus.effects()] == [
            "example_blessing",
            "bold",
            "eager",
            "steadfast",
            "hardiness",
            "cunning_make",
        ]
        assert hero.virtues == ["hardiness"]

    def test_a_cultural_weakness_is_registered_alongside_the_blessing(self, pack) -> None:
        hero, bus = built(pack, **second_folk())
        registered = {str(e.id) for e in bus.effects()}
        assert {"second_blessing", "example_weakness"} <= registered
        assert hero.culture == SECOND_FOLK

    def test_a_players_choice_reaches_the_effect_it_parameterises(self, pack) -> None:
        _, bus = built(
            pack,
            reward_and_virtue=RewardAndVirtueChoice(
                reward=RewardChoice(EffectId("cunning_make"), MAIL),
                virtue=VirtueChoice(EffectId("mastery"), params={"skills": [STEALTH, RIDDLE]}),
            ),
        )
        mastery = next(e for e in bus.effects() if e.id == "mastery")
        assert mastery.params["skills"] == [STEALTH, RIDDLE]

    def test_a_calling_features_subject_reaches_its_effect(self, pack) -> None:
        _, bus = built(
            pack,
            calling=CallingChoice(SECOND_CALLING, favoured=(RIDDLE, SCAN), subject="orcs"),
        )
        lore = next(e for e in bus.effects() if e.id == "enemy_lore")
        assert lore.params["subject"] == "orcs"

    def test_the_reward_lands_on_the_item_and_makes_it_plot_immune(self, pack) -> None:
        hero, _ = built(pack)
        assert hero.gear.armour is not None
        assert hero.gear.armour.upgrades.rewards == ["cunning_make"]
        assert hero.gear.armour.plot_immune
        assert [(r.reward, r.item) for r in hero.rewards] == [("cunning_make", MAIL)]

    def test_an_item_reward_lightens_only_its_own_item(self, pack) -> None:
        # Cunning Make is -2 Load on the mail alone. Without per-item scoping it would
        # lighten the bow, the helm and the buckler too.
        hero, bus = built(pack)
        ctx = RulesContext.single(hero.id, bus=bus, gear=pack)
        bare = (
            pack.weapon(BLADE).load
            + pack.weapon(BOW).load
            + pack.armour_type(MAIL).load
            + pack.armour_type(HELM).load
            + pack.shield_type(BUCKLER).load
        )
        assert recompute_load(hero, ctx=ctx) == bare - 2

    def test_a_hero_may_go_without_armour_entirely(self, pack) -> None:
        # 06.7 makes helm and shield optional in so many words; body armour is optional
        # too, and the Reward then has to bind to a weapon.
        hero, _ = built(
            pack,
            gear=GearChoice(weapons=(WeaponSelection(BLADE),)),
            reward_and_virtue=RewardAndVirtueChoice(
                reward=RewardChoice(EffectId("grievous"), BLADE),
                virtue=VirtueChoice(EffectId("hardiness")),
            ),
        )
        assert (hero.gear.armour, hero.gear.helm, hero.gear.shield) == (None, None, None)
        assert hero.gear.weapons[0].upgrades.rewards == ["grievous"]

    def test_gear_becomes_instances_with_the_chosen_grip(self, pack) -> None:
        hero, _ = built(pack)
        grips = {w.type_id: w.two_handed for w in hero.gear.weapons}
        assert grips == {BLADE: False, BOW: True}
        assert hero.gear.helm is not None
        assert hero.gear.shield is not None

    def test_starting_treasure_is_the_tiers_threshold_and_is_cached(self, pack) -> None:
        # 07.4: Load counts carried Treasure only, so a Common hero who began with all 30
        # points on their back would be Weary before leaving home.
        hero, _ = built(pack)
        assert hero.treasure.cached == 30
        assert hero.treasure.carried == 0
        assert hero.standard_of_living(pack.living_ladder()) is StandardOfLiving.COMMON
        assert not hero.conditions.weary

    def test_the_lowest_tier_starts_with_nothing(self, pack) -> None:
        hero, _ = built(pack, **second_folk())
        assert hero.starting_standard_of_living is StandardOfLiving.FRUGAL
        assert hero.treasure.total == 0

    def test_a_hero_who_starts_weary_is_reported_not_hidden(self, pack) -> None:
        # 06.7. Fatigue is the accessible lever here: Load rises one-for-one with it.
        hero, bus = built(pack)
        hero.fatigue = hero.max_endurance.value
        from tor.rules.resources import recompute_conditions

        recompute_conditions(hero, ctx=RulesContext.single(hero.id, bus=bus, gear=pack))
        assert hero.conditions.weary

    def test_useful_items_and_travelling_gear_carry_over(self, pack) -> None:
        hero, _ = built(
            pack,
            gear=GearChoice(
                weapons=(WeaponSelection(BLADE),),
                armour=MAIL,
                useful_items=(UsefulItem("a whetstone", AbilityId("craft")),),
                travelling_gear=("a bedroll",),
            ),
        )
        assert [i.description for i in hero.useful_items] == ["a whetstone"]


# -- Company formation ---------------------------------------------------------------


class TestFormCompany:
    def test_the_fellowship_rating_counts_the_heroes_and_the_patron(self, pack) -> None:
        a, bus_a = built(pack, "a")
        b, bus_b = built(pack, "b")
        company, _ = form_company(
            [a, b],
            CompanyChoice(patron=PatronId("second_patron"), safe_haven="Home"),
            pack,
            buses={a.id: bus_a, b.id: bus_b},
        )
        assert company.fellowship_rating.value == 4  # 2 heroes + the patron's +2
        assert company.fellowship.current == company.fellowship.maximum == 4
        assert company.patrons == ["second_patron"]
        assert company.safe_havens == ["Home"]

    def test_an_effect_on_a_heros_bus_raises_the_rating(self, pack) -> None:
        # 03.6: certain Cultural Blessings and Virtues add to it, and they live on the bus
        # of the hero who carries them.
        a, bus_a = built(pack, "a")
        bus_a.register(
            pack.instantiate("example_strengthen_fellowship"), EffectSource.acquired("undertaking")
        )
        company, _ = form_company([a], CompanyChoice(), pack, buses={a.id: bus_a})
        assert company.fellowship_rating.value == 2

    def test_mounts_derive_from_each_heros_tier(self, pack) -> None:
        hero, bus = built(pack)
        company, _ = form_company(
            [hero], CompanyChoice(mount_names={hero.id: "Swift"}), pack, buses={hero.id: bus}
        )
        assert company.mounts[hero.id].name == "Swift"
        assert company.mounts[hero.id].vigour == 1

    def test_the_two_lowest_tiers_afford_no_mount(self, pack) -> None:
        hero, bus = built(pack, **second_folk())
        company, _ = form_company([hero], CompanyChoice(), pack, buses={hero.id: bus})
        assert company.mounts == {}

    def test_an_unnamed_mount_takes_its_owners_name(self, pack) -> None:
        hero, bus = built(pack)
        company, _ = form_company([hero], CompanyChoice(), pack, buses={hero.id: bus})
        assert company.mounts[hero.id].name == "Testy's mount"

    def test_a_focus_may_be_named_now(self, pack) -> None:
        a, bus_a = built(pack, "a")
        b, bus_b = built(pack, "b")
        company, warnings = form_company(
            [a, b],
            CompanyChoice(focus={a.id: b.id, b.id: a.id}),
            pack,
            buses={a.id: bus_a, b.id: bus_b},
        )
        assert company.heroes_focused_on(b.id) == [a.id]
        assert warnings == []

    def test_a_deferred_focus_warns_rather_than_refuses(self, pack) -> None:
        # 06.11 step 4: the engine accepts None and allows setting it at any later point.
        hero, bus = built(pack)
        _, warnings = form_company([hero], CompanyChoice(), pack, buses={hero.id: bus})
        assert [w.code for w in warnings] == ["fellowship_focus_deferred"]

    def test_a_company_needs_at_least_one_hero(self, pack) -> None:
        with pytest.raises(RuleViolation, match="at least one hero"):
            form_company([], CompanyChoice(), pack, buses={})


# -- heirs ---------------------------------------------------------------------------


class TestHeirs:
    def test_the_heirloom_allowance(self, pack) -> None:
        # 06.12 deviation 4: 15 passes one, 20 passes two.
        assert [heirlooms_allowed(n) for n in (10, 14, 15, 19, 20)] == [0, 0, 1, 1, 2]

    def test_an_heir_is_not_ready_below_ten(self, pack) -> None:
        retiring, _ = built(pack)
        with pytest.raises(RuleViolation, match="reserve of at least 10"):
            begin_heir(Heir(name="Younger", reserve=9), retiring)

    def test_a_reserve_beyond_twenty_is_refused(self, pack) -> None:
        retiring, _ = built(pack)
        with pytest.raises(RuleViolation, match="tops out at 20"):
            begin_heir(Heir(name="Younger", reserve=21), retiring)

    def test_more_heirlooms_than_the_reserve_passes_is_refused(self, pack) -> None:
        retiring, _ = built(pack)
        with pytest.raises(RuleViolation, match="passes 1 heirloom"):
            begin_heir(Heir(name="Younger", reserve=15, heirlooms=["a", "b"]), retiring)

    def test_the_heritage_skill_must_be_one_the_retiring_hero_favoured(self, pack) -> None:
        retiring, _ = built(pack)
        with pytest.raises(RuleViolation, match="cannot pass down as the family heritage"):
            begin_heir(Heir(name="Younger", reserve=10, heritage_skill=STEALTH), retiring)

    def test_an_heir_starts_from_the_retiring_heros_standard_of_living(self, pack) -> None:
        retiring, _ = built(pack)
        draft = begin_heir(Heir(name="Younger", reserve=20, heritage_skill=HUNTING), retiring)
        assert draft.inherited_standard_of_living is StandardOfLiving.COMMON
        assert draft.heritage_favoured == HUNTING
        assert draft.heir_reserve == 20
        assert draft.stage is CreationStage.CULTURE

    def test_the_reserve_replaces_the_ten_point_budget(self, pack) -> None:
        # Twice what an ordinary hero gets: hunting 2->4 is 8, scan 1->3 is 5, stealth
        # 0->3 is 6 and awareness 0->1 is 1 — 20, which the flat budget could not buy.
        retiring, _ = built(pack)
        heir = begin_heir(Heir(name="Younger", reserve=20, heritage_skill=HUNTING), retiring)
        draft = walk_to(CreationStage.PREVIOUS_EXPERIENCE, pack, start=heir)
        draft, warnings = spend_previous_experience(
            draft, ExperienceChoice(targets=HEIR_SPEND), pack
        )
        assert warnings == []
        assert draft.previous_experience_spend == HEIR_SPEND

    def test_the_ladder_ceilings_still_apply_to_an_heir(self, pack) -> None:
        # 06.12 deviation 3: Skills may not exceed 4, Proficiencies 3 — the same ceilings.
        retiring, _ = built(pack)
        heir = begin_heir(Heir(name="Younger", reserve=20, heritage_skill=HUNTING), retiring)
        draft = walk_to(CreationStage.PREVIOUS_EXPERIENCE, pack, start=heir)
        with pytest.raises(RuleViolation, match=r"prices ratings 1\.\.4"):
            spend_previous_experience(draft, ExperienceChoice(targets={HUNTING: 5}), pack)

    def test_the_heritage_skill_is_favoured_on_top_of_every_other_grant(self, pack) -> None:
        # 06.12 deviation 1: free, and on top of the culture's one and the Calling's two.
        retiring, _ = built(pack)
        retiring.favoured_skills.add(STEALTH)
        heir = begin_heir(Heir(name="Younger", reserve=20, heritage_skill=STEALTH), retiring)
        hero, _ = built(pack, "heir", start=heir, experience=ExperienceChoice(targets=HEIR_SPEND))
        assert hero.favoured_skills == {HUNTING, BATTLE, ENHEARTEN, STEALTH}

    def test_an_heir_inherits_the_retiring_heros_tier_not_the_cultures(self, pack) -> None:
        # 06.12 deviation 2. Both happen to be Common here, so the test moves the
        # retiring hero up a tier first and asserts the heir follows them rather than
        # example_folk's own standard_of_living.
        retiring, _ = built(pack)
        retiring.starting_standard_of_living = StandardOfLiving.PROSPEROUS
        heir = begin_heir(Heir(name="Younger", reserve=20, heritage_skill=HUNTING), retiring)
        hero, _ = built(pack, "heir", start=heir, experience=ExperienceChoice(targets=HEIR_SPEND))

        assert pack.culture(FOLK).standard_of_living is StandardOfLiving.COMMON
        assert hero.starting_standard_of_living is StandardOfLiving.PROSPEROUS
        assert hero.treasure.cached == 90


# -- derived-stat scoping ------------------------------------------------------------


class TestItemScoping:
    """The bus rule creation is the first caller to depend on."""

    def test_an_item_effect_is_silent_for_another_item(self, pack) -> None:
        bus = EffectBus()
        bus.register(pack.instantiate("cunning_make"), EffectSource.item("mail"))
        for ref, expected in (("mail", -2), ("bow", 0)):
            ctx = HookContext(hook=Hook.MODIFY_ITEM_LOAD, extra={"item_ref": ref})
            assert bus.apply_numeric(Hook.MODIFY_ITEM_LOAD, ctx, 0).value == expected

    def test_an_item_effect_still_fires_when_no_item_is_named(self, pack) -> None:
        # Silencing it would be the worse bug: a Reward that raises max Endurance is not
        # about one item.
        bus = EffectBus()
        bus.register(pack.instantiate("hardiness"), EffectSource.item("mail"))
        ctx = HookContext(hook=Hook.MODIFY_MAX_ENDURANCE)
        assert bus.apply_numeric(Hook.MODIFY_MAX_ENDURANCE, ctx, 0).value == 2

    def test_an_instance_refs_by_name_when_it_has_one(self, pack) -> None:
        from tor.model.gear import WeaponInstance

        assert WeaponInstance(type_id=BLADE).ref == BLADE
        assert WeaponInstance(type_id=BLADE, name="Sting").ref == "Sting"
