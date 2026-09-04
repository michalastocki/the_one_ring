"""`tor.model` — domain entities, invariants I1-I10, and derived stats (spec 03)."""

from __future__ import annotations

import pytest

from tor.errors import RuleViolation, RuleViolationGroup
from tor.model.abilities import (
    BRAWLING,
    COMBAT_PROFICIENCIES,
    SKILLS,
    VALOUR,
    WISDOM,
    SkillGroup,
    ability_attribute,
    is_combat_proficiency,
    is_skill,
    skills_in_group,
)
from tor.model.adversary import Adversary, AdversaryInstance, AdversarySize, TakenOutReason
from tor.model.attributes import Attribute, AttributeSet
from tor.model.company import Company, Song, SongKind
from tor.model.conditions import (
    Condition,
    ConditionSet,
    DriveKind,
    ResourcePool,
    StandardOfLiving,
    TreasureHoldings,
    standard_of_living_for,
)
from tor.model.derive import DerivedStat, Modifier, PhaseBoundary
from tor.model.gear import (
    ArmourCategory,
    ArmourInstance,
    ArmourType,
    Gear,
    ShieldInstance,
    Upgrades,
    WeaponInstance,
    WeaponType,
)
from tor.model.hero import Heir, Hero, RewardInstance
from tor.model.ids import (
    AbilityId,
    AdversaryId,
    AdversaryInstanceId,
    CallingId,
    CultureId,
    EffectId,
    HeroId,
    ItemId,
)

# The Standard of Living ladder of 05.9, as content would supply it.
LADDER: list[tuple[StandardOfLiving, int | None]] = [
    (StandardOfLiving.POOR, None),
    (StandardOfLiving.FRUGAL, 0),
    (StandardOfLiving.COMMON, 30),
    (StandardOfLiving.PROSPEROUS, 90),
    (StandardOfLiving.RICH, 180),
    (StandardOfLiving.VERY_RICH, 300),
]


def make_hero(**overrides: object) -> Hero:
    defaults: dict[str, object] = {
        "id": HeroId("h1"),
        "name": "Testfolk Wanderer",
        "culture": CultureId("example_folk"),
        "calling": CallingId("example_calling"),
        "age": 30,
        "attributes": AttributeSet(strength=5, heart=6, wits=3),
        "skills": dict.fromkeys(SKILLS, 1),
        "proficiencies": dict.fromkeys(COMBAT_PROFICIENCIES, 0),
        "max_endurance": DerivedStat(base=25),
        "max_hope": DerivedStat(base=14),
        "parry": DerivedStat(base=15),
        "endurance": 25,
        "hope": 14,
    }
    return Hero(**(defaults | overrides))  # type: ignore[arg-type]


class TestAttributes:
    def test_score_reads_each_attribute(self) -> None:
        attrs = AttributeSet(strength=4, heart=5, wits=6)
        assert attrs.score(Attribute.STRENGTH) == 4
        assert attrs.score(Attribute.HEART) == 5
        assert attrs.score(Attribute.WITS) == 6

    def test_a_score_below_one_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            AttributeSet(strength=0, heart=5, wits=5)

    def test_with_bonus_returns_a_copy(self) -> None:
        # The cultural blessing granting +1 to a chosen Attribute (06.2).
        base = AttributeSet(strength=4, heart=5, wits=6)
        raised = base.with_bonus(Attribute.WITS, 1)
        assert raised.wits == 7
        assert base.wits == 6


class TestSkillGrid:
    def test_there_are_exactly_eighteen_skills(self) -> None:
        assert len(SKILLS) == 18

    def test_every_group_holds_three_skills_one_per_attribute(self) -> None:
        for group in SkillGroup:
            trio = skills_in_group(group)
            assert len(trio) == 3
            assert {SKILLS[s].attribute for s in trio} == set(Attribute)

    def test_group_order_is_strength_heart_wits(self) -> None:
        # 20.1: the second Blessings die selects 1-2 STRENGTH, 3-4 HEART, 5-6 WITS.
        assert skills_in_group(SkillGroup.VOCATION) == (
            AbilityId("craft"),
            AbilityId("battle"),
            AbilityId("lore"),
        )

    @pytest.mark.parametrize("proficiency", COMBAT_PROFICIENCIES)
    def test_every_combat_proficiency_rolls_against_strength(self, proficiency: AbilityId) -> None:
        assert ability_attribute(proficiency) is Attribute.STRENGTH
        assert is_combat_proficiency(proficiency)
        assert not is_skill(proficiency)

    def test_brawling_rolls_against_strength_but_is_no_proficiency(self) -> None:
        assert ability_attribute(BRAWLING) is Attribute.STRENGTH
        assert not is_combat_proficiency(BRAWLING)

    def test_rank_abilities_use_heart_and_wits(self) -> None:
        assert ability_attribute(VALOUR) is Attribute.HEART
        assert ability_attribute(WISDOM) is Attribute.WITS

    def test_an_unknown_ability_is_an_error(self) -> None:
        with pytest.raises(KeyError, match="unknown ability"):
            ability_attribute(AbilityId("juggling"))


class TestDerivedStat:
    def test_sums_deltas_over_the_base(self) -> None:
        stat = DerivedStat(
            base=20,
            modifiers=(
                Modifier(EffectId("hardiness"), delta=2),
                Modifier(EffectId("hardiness#2"), delta=2),
            ),
        )
        assert stat.value == 24

    def test_a_replacement_overrides_the_base(self) -> None:
        # Mithril replaces an item's Load outright rather than adjusting it (04.2.2).
        stat = DerivedStat(base=9, modifiers=(Modifier(EffectId("mithril"), replacement=2),))
        assert stat.value == 2

    def test_deltas_apply_on_top_of_a_replacement(self) -> None:
        stat = DerivedStat(
            base=9,
            modifiers=(
                Modifier(EffectId("mithril"), replacement=4),
                Modifier(EffectId("cunning_make"), delta=-2),
            ),
        )
        assert stat.value == 2

    def test_expiring_drops_only_the_matching_boundary(self) -> None:
        stat = DerivedStat(
            base=4,
            modifiers=(
                Modifier(EffectId("patron"), delta=1),
                Modifier(
                    EffectId("strengthen_fellowship"),
                    delta=1,
                    expires_at=PhaseBoundary.NEXT_FELLOWSHIP_PHASE,
                ),
            ),
        )
        assert stat.value == 6
        assert stat.expire(PhaseBoundary.NEXT_FELLOWSHIP_PHASE).value == 5

    def test_recomputing_is_idempotent(self) -> None:
        stat = DerivedStat(base=10, modifiers=(Modifier(EffectId("x"), delta=3),))
        assert stat.value == stat.value == 13

    def test_explain_shows_the_derivation(self) -> None:
        stat = DerivedStat(base=12, modifiers=(Modifier(EffectId("nimbleness"), delta=1),))
        assert stat.explain() == "12 +1 (nimbleness) = 13"


class TestResourcePool:
    def test_spending_reduces_the_pool(self) -> None:
        pool = ResourcePool(current=3, maximum=5)
        pool.spend(2)
        assert pool.current == 1

    def test_overspending_is_a_rule_violation(self) -> None:
        with pytest.raises(RuleViolation, match="only 1 left"):
            ResourcePool(current=1, maximum=5).spend(2)

    def test_a_depleted_pool_reports_itself(self) -> None:
        assert ResourcePool(current=0, maximum=5).depleted

    def test_restore_clamps_at_the_maximum_and_reports_the_gain(self) -> None:
        pool = ResourcePool(current=4, maximum=5)
        assert pool.restore(3) == 1
        assert pool.current == 5

    def test_raising_the_maximum_raises_the_current_value(self) -> None:
        # 03.4.2: a Virtue that raises a maximum raises the current value with it.
        pool = ResourcePool(current=8, maximum=10)
        pool.set_maximum(12)
        assert pool.current == 10

    def test_raising_the_maximum_without_the_current_value(self) -> None:
        pool = ResourcePool(current=8, maximum=10)
        pool.set_maximum(12, raise_current=False)
        assert pool.current == 8

    def test_lowering_the_maximum_clamps_the_current_value(self) -> None:
        pool = ResourcePool(current=9, maximum=10)
        pool.set_maximum(6)
        assert pool.current == 6

    def test_a_current_value_outside_the_range_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="outside the pool range"):
            ResourcePool(current=11, maximum=10)


class TestStandardOfLiving:
    @pytest.mark.parametrize(
        ("treasure", "expected"),
        [
            (0, StandardOfLiving.FRUGAL),
            (29, StandardOfLiving.FRUGAL),
            (30, StandardOfLiving.COMMON),
            (89, StandardOfLiving.COMMON),
            (90, StandardOfLiving.PROSPEROUS),
            (300, StandardOfLiving.VERY_RICH),
        ],
    )
    def test_the_boundary_is_inclusive(self, treasure: int, expected: StandardOfLiving) -> None:
        # 19.4: Treasure 89 is Common, 90 is Prosperous. The comparison is `>=`.
        assert standard_of_living_for(treasure, LADDER) is expected

    def test_derives_from_total_holdings_not_carried(self) -> None:
        # 19.4: cache 60 of 90 and the tier holds, because Standard of Living follows the
        # total. Load is what follows `carried` — conflating them is the classic bug.
        hero = make_hero(treasure=TreasureHoldings(carried=30, cached=60))
        assert hero.treasure.total == 90
        assert hero.standard_of_living(LADDER) is StandardOfLiving.PROSPEROUS

    def test_negative_holdings_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            TreasureHoldings(carried=-1)


class TestHeroRatings:
    def test_brawling_is_the_highest_proficiency(self) -> None:
        hero = make_hero(
            proficiencies={
                AbilityId("axes"): 1,
                AbilityId("bows"): 3,
                AbilityId("spears"): 2,
                AbilityId("swords"): 0,
            }
        )
        assert hero.rating(BRAWLING) == 3

    def test_an_unknown_ability_is_an_error(self) -> None:
        with pytest.raises(KeyError, match="no rating for"):
            make_hero().rating(AbilityId("juggling"))

    def test_a_combat_proficiency_cannot_be_favoured(self) -> None:
        # Invariant I7 — one of the three ways Proficiencies differ from Skills.
        with pytest.raises(RuleViolation, match="cannot be Favoured"):
            make_hero().set_favoured(AbilityId("swords"))

    def test_a_skill_can_be_favoured_and_unfavoured(self) -> None:
        hero = make_hero()
        hero.set_favoured(AbilityId("hunting"))
        assert hero.is_favoured(AbilityId("hunting"))
        hero.set_favoured(AbilityId("hunting"), favoured=False)
        assert not hero.is_favoured(AbilityId("hunting"))


class TestHeroInvariants:
    def test_a_well_formed_hero_validates(self) -> None:
        make_hero().validate()

    def test_shadow_may_not_exceed_maximum_hope(self) -> None:
        # I5 — the ceiling that makes the "Shadow reaches maximum Hope" trigger reachable
        # but not exceedable.
        with pytest.raises(RuleViolationGroup, match="maximum Hope"):
            make_hero(shadow=15).validate()

    def test_shadow_equal_to_maximum_hope_is_legal(self) -> None:
        make_hero(shadow=14).validate()

    def test_scars_may_not_exceed_shadow(self) -> None:
        with pytest.raises(RuleViolationGroup, match="Shadow Scars exceed"):
            make_hero(shadow=2, shadow_scars=3).validate()

    def test_flaw_count_must_match_the_shadow_path_step(self) -> None:
        with pytest.raises(RuleViolationGroup, match="Shadow Path step"):
            make_hero(shadow_path_step=2, flaws=[EffectId("a")]).validate()

    def test_a_rating_above_six_is_rejected(self) -> None:
        with pytest.raises(RuleViolationGroup, match=r"outside 0\.\.6"):
            make_hero(skills={**dict.fromkeys(SKILLS, 1), AbilityId("lore"): 7}).validate()

    def test_valour_below_one_is_rejected(self) -> None:
        with pytest.raises(RuleViolationGroup, match="valour"):
            make_hero(valour=0).validate()

    def test_endurance_above_the_maximum_is_rejected(self) -> None:
        with pytest.raises(RuleViolationGroup, match="Endurance"):
            make_hero(endurance=99).validate()

    def test_every_breach_is_reported_not_just_the_first(self) -> None:
        with pytest.raises(RuleViolationGroup) as excinfo:
            make_hero(valour=0, wisdom=9, endurance=99, hope=99).validate()
        assert len(excinfo.value.violations) >= 4


class TestConditions:
    def test_as_set_reports_the_raised_flags(self) -> None:
        conditions = ConditionSet(weary=True, wounded=True)
        assert conditions.as_set() == {Condition.WEARY, Condition.WOUNDED}

    def test_has_reads_one_flag(self) -> None:
        assert ConditionSet(miserable=True).has(Condition.MISERABLE)


class TestGear:
    def test_a_dropped_item_is_not_carried(self) -> None:
        gear = Gear(
            weapons=[WeaponInstance(type_id=ItemId("blade"))],
            helm=ArmourInstance(type_id=ItemId("helm"), dropped=True),
        )
        assert len(gear.carried()) == 1

    def test_a_reward_bearing_item_is_plot_immune(self) -> None:
        weapon = WeaponInstance(
            type_id=ItemId("blade"), upgrades=Upgrades(rewards=[EffectId("grievous")])
        )
        assert weapon.plot_immune

    def test_a_reward_bearing_shield_cannot_be_smashed(self) -> None:
        # 12.5 / 15.4.1: Break Shield must check this before smashing.
        plain = ShieldInstance(type_id=ItemId("shield"))
        warded = ShieldInstance(
            type_id=ItemId("shield"), upgrades=Upgrades(rewards=[EffectId("reinforced")])
        )
        assert not plain.unsmashable
        assert warded.unsmashable

    def test_a_weapon_selects_its_injury_rating_by_grip(self) -> None:
        weapon = WeaponType(
            id=ItemId("long_sword"),
            name="Example Long Sword",
            damage=5,
            injury_one_handed=16,
            injury_two_handed=18,
            load=3,
            proficiency=AbilityId("swords"),
        )
        assert weapon.injury(two_handed=False) == 16
        assert weapon.injury(two_handed=True) == 18

    def test_unarmed_has_no_injury_and_cannot_pierce(self) -> None:
        # The schema must permit this combination (05.4).
        unarmed = WeaponType(
            id=ItemId("unarmed"),
            name="Unarmed",
            damage=1,
            injury_one_handed=None,
            injury_two_handed=None,
            load=0,
            proficiency="brawling",
            can_cause_piercing_blow=False,
        )
        assert unarmed.injury(two_handed=False) is None
        assert not unarmed.can_cause_piercing_blow

    def test_upgrade_count_sums_rewards_and_enchantments(self) -> None:
        upgrades = Upgrades(rewards=[EffectId("keen")], enchanted=[EffectId("superior_keen")])
        assert upgrades.count == 2

    def test_helm_and_armour_are_separate_slots(self) -> None:
        armour = ArmourType(
            id=ItemId("mail"),
            name="Example Mail",
            protection=3,
            load=9,
            category=ArmourCategory.MAIL,
        )
        helm = ArmourType(
            id=ItemId("helm"),
            name="Example Helm",
            protection=1,
            load=4,
            category=ArmourCategory.HEADGEAR,
        )
        # The PROTECTION roll uses their sum (08.7).
        assert armour.protection + helm.protection == 4


class TestCompany:
    def test_fellowship_refreshes_to_the_rating(self) -> None:
        company = Company(heroes=[HeroId("a"), HeroId("b")], fellowship_rating=DerivedStat(base=2))
        company.refresh_fellowship()
        assert company.fellowship.current == 2

    def test_a_patron_bonus_raises_the_rating(self) -> None:
        company = Company(
            heroes=[HeroId("a"), HeroId("b")],
            fellowship_rating=DerivedStat(
                base=2, modifiers=(Modifier(EffectId("patron"), delta=1),)
            ),
        )
        company.refresh_fellowship()
        assert company.fellowship.current == 3

    def test_focus_is_directed_and_non_reciprocal(self) -> None:
        company = Company(heroes=[HeroId("a"), HeroId("b"), HeroId("c")])
        company.set_focus(HeroId("a"), HeroId("b"))
        company.set_focus(HeroId("c"), HeroId("b"))
        assert company.heroes_focused_on(HeroId("b")) == [HeroId("a"), HeroId("c")]
        assert company.heroes_focused_on(HeroId("a")) == []

    def test_a_second_focus_needs_the_raised_limit(self) -> None:
        company = Company(heroes=[HeroId("a"), HeroId("b"), HeroId("c")])
        company.set_focus(HeroId("a"), HeroId("b"))
        with pytest.raises(RuleViolation, match="limit 1"):
            company.set_focus(HeroId("a"), HeroId("c"))
        company.set_focus(HeroId("a"), HeroId("c"), limit=2)
        assert len(company.fellowship_focus[HeroId("a")]) == 2

    def test_a_hero_may_not_focus_on_themselves(self) -> None:
        company = Company(heroes=[HeroId("a")])
        with pytest.raises(RuleViolation, match="another hero"):
            company.set_focus(HeroId("a"), HeroId("a"))

    def test_setting_the_same_focus_twice_is_a_no_op(self) -> None:
        company = Company(heroes=[HeroId("a"), HeroId("b")])
        company.set_focus(HeroId("a"), HeroId("b"))
        company.set_focus(HeroId("a"), HeroId("b"))
        assert company.fellowship_focus[HeroId("a")] == [HeroId("b")]

    def test_songs_reset_at_the_start_of_an_adventuring_phase(self) -> None:
        company = Company(songs=[Song(title="A Lay", kind=SongKind.LAY, spent=True)])
        company.reset_songs()
        assert not company.songs[0].spent


def make_adversary(**overrides: object) -> Adversary:
    defaults: dict[str, object] = {
        "id": AdversaryId("example_foe"),
        "name": "Example Foe",
        "attribute_level": 4,
        "endurance": 16,
        "might": 1,
        "drive_kind": DriveKind.HATE,
        "drive": 4,
        "parry": 2,
        "armour": 3,
    }
    return Adversary(**(defaults | overrides))  # type: ignore[arg-type]


class TestAdversary:
    def test_hate_and_resolve_are_one_pool_with_a_discriminator(self) -> None:
        minion = make_adversary(drive_kind=DriveKind.HATE, drive=4)
        man = make_adversary(drive_kind=DriveKind.RESOLVE, drive=3)
        assert (minion.hate, minion.resolve) == (4, None)
        assert (man.hate, man.resolve) == (None, 3)

    def test_only_a_resolve_bearing_foe_prompts_a_misdeed_check(self) -> None:
        # 12.2: fighting minions of the Enemy can hardly call the heroes' integrity into
        # question; killing a Resolve-bearing foe always must be weighed.
        assert not make_adversary(drive_kind=DriveKind.HATE).misdeed_check_required
        assert make_adversary(drive_kind=DriveKind.RESOLVE).misdeed_check_required

    def test_a_might_two_creature_survives_its_first_wound(self) -> None:
        instance = AdversaryInstance.of(make_adversary(might=2), AdversaryInstanceId("orc1"))
        assert not instance.apply_wound()
        assert instance.apply_wound()
        assert instance.taken_out is TakenOutReason.SLAIN

    def test_a_might_one_creature_dies_on_its_first_wound(self) -> None:
        instance = AdversaryInstance.of(make_adversary(might=1), AdversaryInstanceId("orc1"))
        assert instance.apply_wound()

    def test_zero_endurance_takes_a_creature_out_without_making_it_weary(self) -> None:
        # 12.3: adversaries never become Weary from Endurance loss — only from empty drive.
        instance = AdversaryInstance.of(make_adversary(endurance=5), AdversaryInstanceId("o"))
        instance.take_endurance_loss(9)
        assert instance.endurance == 0
        assert instance.taken_out is TakenOutReason.ENDURANCE
        assert not instance.weary_this_round

    def test_weariness_is_decided_at_the_start_of_a_round(self) -> None:
        # 12.2: spending the last point mid-round does not make the creature Weary until
        # the next round begins.
        instance = AdversaryInstance.of(make_adversary(drive=1), AdversaryInstanceId("o"))
        instance.begin_round()
        assert not instance.weary_this_round
        instance.spend_drive(1)
        assert not instance.weary_this_round
        instance.begin_round()
        assert instance.weary_this_round

    def test_bonus_drive_is_spent_first(self) -> None:
        # 14.6: a Revelation episode's extra pool is drawn on before the creature's own.
        instance = AdversaryInstance.of(make_adversary(drive=2), AdversaryInstanceId("o"))
        instance.bonus_drive = 2
        instance.spend_drive(3)
        assert instance.bonus_drive == 0
        assert instance.drive.current == 1

    def test_a_creature_holding_bonus_drive_is_not_at_zero(self) -> None:
        instance = AdversaryInstance.of(make_adversary(drive=1), AdversaryInstanceId("o"))
        instance.spend_drive(1)
        instance.bonus_drive = 1
        instance.begin_round()
        assert not instance.weary_this_round

    def test_overspending_drive_is_a_rule_violation(self) -> None:
        instance = AdversaryInstance.of(make_adversary(drive=1), AdversaryInstanceId("o"))
        with pytest.raises(RuleViolation, match="only 1 left"):
            instance.spend_drive(2)

    def test_instances_never_share_state(self) -> None:
        template = make_adversary(drive=4)
        a = AdversaryInstance.of(template, AdversaryInstanceId("orc1"))
        b = AdversaryInstance.of(template, AdversaryInstanceId("orc2"))
        a.spend_drive(2)
        assert b.drive.current == 4

    def test_size_defaults_to_human(self) -> None:
        assert make_adversary().size is AdversarySize.HUMAN


class TestGuardsAndAccessors:
    """The narrow guard clauses and accessors the broader tests do not reach."""

    def test_a_skill_reports_its_governing_attribute(self) -> None:
        assert ability_attribute(AbilityId("lore")) is Attribute.WITS
        assert ability_attribute(AbilityId("athletics")) is Attribute.STRENGTH

    def test_wits_is_readable_from_an_attribute_set(self) -> None:
        assert AttributeSet(strength=3, heart=3, wits=7).score(Attribute.WITS) == 7

    def test_hero_reads_a_skill_rating(self) -> None:
        hero = make_hero(skills={**dict.fromkeys(SKILLS, 1), AbilityId("lore"): 3})
        assert hero.rating(AbilityId("lore")) == 3

    def test_hero_exposes_the_attribute_an_ability_rolls_against(self) -> None:
        assert make_hero().attribute_for(AbilityId("battle")) is Attribute.HEART

    def test_permanent_flaw_count_excludes_temporary_flaws(self) -> None:
        # 11.7: a Flaw inflicted by the Curse of Weakness must not count toward succumbing.
        hero = make_hero(
            flaws=[EffectId("a"), EffectId("b")],
            temporary_flaws=[EffectId("cursed")],
            shadow_path_step=2,
        )
        assert hero.permanent_flaw_count == 2

    def test_an_heir_is_ready_at_a_reserve_of_ten(self) -> None:
        assert not Heir(name="Young Testfolk", reserve=9).ready
        assert Heir(name="Young Testfolk", reserve=10).ready

    def test_favouring_a_non_skill_is_caught_by_validate(self) -> None:
        # set_favoured refuses it, so reaching the invariant needs a direct mutation —
        # which is exactly the drift validate() exists to catch.
        hero = make_hero()
        hero.favoured_skills.add(AbilityId("swords"))
        with pytest.raises(RuleViolationGroup, match="only Skills may be Favoured"):
            hero.validate()

    def test_worn_armour_counts_as_carried(self) -> None:
        gear = Gear(
            armour=ArmourInstance(type_id=ItemId("mail")),
            helm=ArmourInstance(type_id=ItemId("helm")),
            shield=ShieldInstance(type_id=ItemId("shield")),
        )
        assert len(gear.carried()) == 3

    def test_with_modifiers_appends_without_mutating(self) -> None:
        stat = DerivedStat(base=10)
        raised = stat.with_modifiers([Modifier(EffectId("hardiness"), delta=2)])
        assert raised.value == 12
        assert stat.value == 10

    def test_explain_reports_a_replacement(self) -> None:
        stat = DerivedStat(base=9, modifiers=(Modifier(EffectId("mithril"), replacement=2),))
        assert stat.explain() == "2 (replaced by mithril) = 2"

    @pytest.mark.parametrize(
        ("call", "message"),
        [
            (lambda: ResourcePool(current=0, maximum=-1), "maximum cannot be negative"),
            (lambda: ResourcePool(current=1, maximum=3).spend(-1), "negative amount"),
            (lambda: ResourcePool(current=1, maximum=3).restore(-1), "negative amount"),
            (lambda: ResourcePool(current=1, maximum=3).set_maximum(-1), "cannot be negative"),
        ],
    )
    def test_resource_pool_rejects_nonsense(self, call, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            call()

    def test_spending_negative_drive_is_rejected(self) -> None:
        instance = AdversaryInstance.of(make_adversary(), AdversaryInstanceId("o"))
        with pytest.raises(ValueError, match="negative amount"):
            instance.spend_drive(-1)


class TestRemainingInvariants:
    def test_hero_reads_a_proficiency_rating(self) -> None:
        hero = make_hero(
            proficiencies={**dict.fromkeys(COMBAT_PROFICIENCIES, 0), AbilityId("axes"): 2}
        )
        assert hero.rating(AbilityId("axes")) == 2

    def test_a_shadow_path_step_above_four_is_rejected(self) -> None:
        # I8: there are exactly four steps on a Shadow Path (11.7).
        with pytest.raises(RuleViolationGroup, match=r"outside 0\.\.4"):
            make_hero(shadow_path_step=5, flaws=[EffectId(f"f{i}") for i in range(5)]).validate()

    def test_the_same_reward_twice_on_one_item_is_rejected(self) -> None:
        # I10: all upgrades can be applied only once to the same piece of gear (15.4.1).
        hero = make_hero(
            rewards=[
                RewardInstance(reward=EffectId("grievous"), item="blade"),
                RewardInstance(reward=EffectId("grievous"), item="blade"),
            ]
        )
        with pytest.raises(RuleViolationGroup, match="applied twice"):
            hero.validate()

    def test_the_same_reward_on_two_items_is_fine(self) -> None:
        make_hero(
            rewards=[
                RewardInstance(reward=EffectId("grievous"), item="blade"),
                RewardInstance(reward=EffectId("grievous"), item="axe"),
            ]
        ).validate()

    def test_negative_fatigue_is_rejected(self) -> None:
        with pytest.raises(RuleViolationGroup, match="Fatigue cannot be negative"):
            make_hero(fatigue=-1).validate()
