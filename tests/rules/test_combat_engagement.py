"""`tor.rules.combat.engagement` — stance entry and the engagement limits (spec 08.2.3-4).

Nothing here rolls a die, so no `ScriptedRandomness` appears.
"""

from __future__ import annotations

import pytest
from conftest import BRUTE, MINION, build_adversary, build_hero

from tor.effects.bus import EffectSource
from tor.errors import RuleViolation
from tor.model.adversary import AdversarySize
from tor.rules.combat.engagement import (
    FOES_PER_HERO,
    HEROES_PER_FOE,
    EngagementPlan,
    engagement_limit,
    may_engage,
    may_take_rearward,
    resolve_engagement,
    unengage,
    unengaged_heroes,
)
from tor.rules.combat.state import Stance


def company(fight, heroes: int, foes: int, *, template: str = MINION, stance=Stance.OPEN):
    f = fight()
    for n in range(heroes):
        f.hero(build_hero(f"h{n}"), stance=stance)
    for n in range(foes):
        f.foe(build_adversary(f.pack, template, f"f{n}"))
    return f


class TestRearwardRequirements:
    def test_the_mandatory_vector_of_too_many_enemies(self, fight) -> None:
        # 19.4: 4 heroes, 9 enemies -> Rearward refused (more than twice the Company).
        f = company(fight, heroes=4, foes=9)
        assert not may_take_rearward(f.state, f.state.active_heroes()[0].ref)

    def test_the_mandatory_vector_of_too_few_companions(self, fight) -> None:
        # 19.4: 4 heroes, 6 enemies, 1 already in Rearward, 2 in close combat -> refused,
        # because two heroes in Rearward need four others in close combat.
        f = company(fight, heroes=4, foes=6)
        heroes = f.state.active_heroes()
        heroes[0].stance = Stance.REARWARD
        heroes[1].stance = Stance.FORWARD
        heroes[2].stance = Stance.FORWARD
        heroes[3].stance = Stance.OPEN
        assert not may_take_rearward(f.state, heroes[3].ref)

    def test_exactly_twice_the_company_is_still_allowed(self, fight) -> None:
        # "more than twice" — the boundary is inclusive on the Company's side.
        f = company(fight, heroes=3, foes=6)
        heroes = f.state.active_heroes()
        heroes[1].stance = Stance.FORWARD
        heroes[2].stance = Stance.FORWARD
        assert may_take_rearward(f.state, heroes[0].ref)

    def test_two_companions_in_close_combat_are_enough_for_one(self, fight) -> None:
        f = company(fight, heroes=3, foes=2)
        heroes = f.state.active_heroes()
        heroes[1].stance = Stance.DEFENSIVE
        heroes[2].stance = Stance.OPEN
        assert may_take_rearward(f.state, heroes[0].ref)

    def test_one_companion_is_not(self, fight) -> None:
        f = company(fight, heroes=2, foes=2)
        heroes = f.state.active_heroes()
        heroes[1].stance = Stance.FORWARD
        assert not may_take_rearward(f.state, heroes[0].ref)

    def test_the_loremaster_may_waive_both(self, fight) -> None:
        # 08.2.3: a narrow ledge, a mountain path, or a Company that heavily outnumbers.
        f = company(fight, heroes=1, foes=9)
        assert may_take_rearward(f.state, f.state.active_heroes()[0].ref, override=True)

    def test_a_hero_already_in_rearward_is_counted_once(self, fight) -> None:
        # Re-asking for a hero who is already there must not count them twice.
        f = company(fight, heroes=3, foes=2)
        heroes = f.state.active_heroes()
        heroes[0].stance = Stance.REARWARD
        heroes[1].stance = Stance.FORWARD
        heroes[2].stance = Stance.FORWARD
        assert may_take_rearward(f.state, heroes[0].ref)


class TestEngagementLimits:
    def test_the_table_of_8_2_4(self) -> None:
        assert FOES_PER_HERO == {AdversarySize.HUMAN: 3, AdversarySize.LARGE: 2}
        assert HEROES_PER_FOE == {AdversarySize.HUMAN: 3, AdversarySize.LARGE: 6}

    def test_a_fourth_human_sized_foe_cannot_engage_one_hero(self, fight) -> None:
        # 19.4's mandatory vector.
        f = company(fight, heroes=1, foes=4)
        hero = f.state.active_heroes()[0]
        foes = f.state.active_adversaries()
        resolve_engagement(
            f.state,
            EngagementPlan(pairs=tuple((foe.ref, hero.ref) for foe in foes[:3])),
            ctx=f.ctx,
        )
        assert len(hero.engaged_with) == 3
        assert not may_engage(f.state, foes[3].ref, hero.ref, ctx=f.ctx)

    def test_a_seventh_hero_cannot_engage_a_large_creature(self, fight) -> None:
        # 19.4's mandatory vector: six may surround a troll, a seventh may not.
        f = company(fight, heroes=7, foes=1, template=BRUTE)
        troll = f.state.active_adversaries()[0]
        heroes = f.state.active_heroes()
        resolve_engagement(
            f.state,
            EngagementPlan(pairs=tuple((h.ref, troll.ref) for h in heroes[:6])),
            ctx=f.ctx,
        )
        assert len(troll.engaged_with) == 6
        assert not may_engage(f.state, heroes[6].ref, troll.ref, ctx=f.ctx)

    def test_only_two_large_creatures_may_reach_one_hero(self, fight) -> None:
        # The table is not symmetric: six heroes may surround a troll, but two trolls are
        # all that can reach one hero.
        f = company(fight, heroes=1, foes=3, template=BRUTE)
        hero = f.state.active_heroes()[0]
        trolls = f.state.active_adversaries()
        resolve_engagement(
            f.state,
            EngagementPlan(pairs=tuple((t.ref, hero.ref) for t in trolls[:2])),
            ctx=f.ctx,
        )
        assert not may_engage(f.state, trolls[2].ref, hero.ref, ctx=f.ctx)

    def test_an_effect_may_adjust_the_limit(self, fight) -> None:
        f = company(fight, heroes=1, foes=5)
        hero = f.state.active_heroes()[0]
        foes = f.state.active_adversaries()
        f.register(hero.ref, "long_reach", EffectSource.acquired("virtue"))
        assert engagement_limit(hero, foes[0], ctx=f.ctx) == 4

    def test_the_limit_is_never_below_one(self, fight) -> None:
        # A limit of zero would mean nobody could ever close, which no effect describes.
        f = company(fight, heroes=1, foes=1)
        hero = f.state.active_heroes()[0]
        foe = f.state.active_adversaries()[0]
        from tor.effects.bus import Effect, EffectKind
        from tor.effects.hooks import Hook, NumericContribution
        from tor.model.ids import EffectId

        f.buses[hero.ref].register(
            Effect(
                id=EffectId("hemmed_in"),
                kind=EffectKind.CURSE,
                listeners={
                    Hook.MODIFY_ENGAGEMENT: lambda _c: NumericContribution(
                        source=EffectId("hemmed_in"), delta=-9
                    )
                },
            ),
            EffectSource.item("cursed_ring"),
        )
        assert engagement_limit(hero, foe, ctx=f.ctx) == 1


class TestMayEngage:
    def test_a_rearward_hero_cannot_be_engaged(self, fight) -> None:
        # 08.2.4: the protection Rearward buys.
        f = company(fight, heroes=1, foes=1, stance=Stance.REARWARD)
        hero = f.state.active_heroes()[0]
        foe = f.state.active_adversaries()[0]
        assert not may_engage(f.state, foe.ref, hero.ref, ctx=f.ctx)

    def test_two_of_a_side_never_engage_each_other(self, fight) -> None:
        f = company(fight, heroes=2, foes=2)
        heroes = f.state.active_heroes()
        foes = f.state.active_adversaries()
        assert not may_engage(f.state, heroes[0].ref, heroes[1].ref, ctx=f.ctx)
        assert not may_engage(f.state, foes[0].ref, foes[1].ref, ctx=f.ctx)

    def test_an_inactive_combatant_engages_nobody(self, fight) -> None:
        f = company(fight, heroes=1, foes=1)
        hero = f.state.active_heroes()[0]
        foe = f.state.active_adversaries()[0]
        foe.adversary.apply_wound()
        assert not may_engage(f.state, hero.ref, foe.ref, ctx=f.ctx)

    def test_an_existing_pairing_is_always_legal(self, fight) -> None:
        # Engagement persists; re-asking about a pair already in place must not refuse it
        # just because the limit is now full.
        f = company(fight, heroes=1, foes=3)
        hero = f.state.active_heroes()[0]
        foes = f.state.active_adversaries()
        resolve_engagement(
            f.state, EngagementPlan(pairs=tuple((x.ref, hero.ref) for x in foes)), ctx=f.ctx
        )
        assert may_engage(f.state, foes[0].ref, hero.ref, ctx=f.ctx)


class TestResolveEngagement:
    def test_pairings_are_mutual(self, fight) -> None:
        f = company(fight, heroes=1, foes=1)
        hero = f.state.active_heroes()[0]
        foe = f.state.active_adversaries()[0]
        resolve_engagement(f.state, EngagementPlan(pairs=((hero.ref, foe.ref),)), ctx=f.ctx)
        assert hero.engaged_with == {foe.ref}
        assert foe.engaged_with == {hero.ref}

    def test_existing_pairings_survive_a_later_plan(self, fight) -> None:
        # 08.2.4: engagement persists across rounds; re-run it only for the unengaged.
        f = company(fight, heroes=2, foes=2)
        a, b = f.state.active_heroes()
        x, y = f.state.active_adversaries()
        resolve_engagement(f.state, EngagementPlan(pairs=((a.ref, x.ref),)), ctx=f.ctx)
        resolve_engagement(f.state, EngagementPlan(pairs=((b.ref, y.ref),)), ctx=f.ctx)
        assert a.engaged_with == {x.ref}
        assert b.engaged_with == {y.ref}

    def test_repeating_a_pair_is_a_no_op(self, fight) -> None:
        f = company(fight, heroes=1, foes=1)
        hero = f.state.active_heroes()[0]
        foe = f.state.active_adversaries()[0]
        plan = EngagementPlan(pairs=((hero.ref, foe.ref), (hero.ref, foe.ref)))
        resolve_engagement(f.state, plan, ctx=f.ctx)
        assert hero.engaged_with == {foe.ref}

    def test_a_plan_that_breaches_the_limits_applies_nothing(self, fight) -> None:
        # Validated in full before any of it lands, so a bad plan leaves no half-engagement.
        f = company(fight, heroes=1, foes=4)
        hero = f.state.active_heroes()[0]
        foes = f.state.active_adversaries()
        with pytest.raises(RuleViolation, match="cannot engage"):
            resolve_engagement(
                f.state,
                EngagementPlan(pairs=tuple((foe.ref, hero.ref) for foe in foes)),
                ctx=f.ctx,
            )
        assert hero.engaged_with == set()

    def test_a_rearward_hero_in_a_plan_is_refused(self, fight) -> None:
        f = company(fight, heroes=1, foes=1, stance=Stance.REARWARD)
        hero = f.state.active_heroes()[0]
        foe = f.state.active_adversaries()[0]
        with pytest.raises(RuleViolation, match="cannot engage"):
            resolve_engagement(f.state, EngagementPlan(pairs=((foe.ref, hero.ref),)), ctx=f.ctx)

    def test_a_foe_may_stand_back_and_shoot_a_rearward_hero(self, fight) -> None:
        # 08.2.4: a foe standing back may target *any* hero, Rearward included.
        f = company(fight, heroes=2, foes=1)
        archer, shielded = f.state.active_heroes()
        archer.stance = Stance.REARWARD
        shielded.stance = Stance.FORWARD
        foe = f.state.active_adversaries()[0]
        resolve_engagement(
            f.state,
            EngagementPlan(stood_back=(foe.ref,), ranged_targets={foe.ref: archer.ref}),
            ctx=f.ctx,
        )
        assert foe.stood_back
        assert foe.attacking == archer.ref
        assert archer.engaged_with == set()

    def test_a_hero_cannot_stand_back(self, fight) -> None:
        f = company(fight, heroes=1, foes=1)
        hero = f.state.active_heroes()[0]
        with pytest.raises(RuleViolation, match="Rearward stance"):
            resolve_engagement(f.state, EngagementPlan(stood_back=(hero.ref,)), ctx=f.ctx)


class TestUnengaging:
    def test_a_fallen_foe_releases_the_hero_it_held(self, fight) -> None:
        # 08.2.4's "unengaged mid-round": the hero may then pick another to attack.
        f = company(fight, heroes=1, foes=2)
        hero = f.state.active_heroes()[0]
        a, b = f.state.active_adversaries()
        resolve_engagement(
            f.state, EngagementPlan(pairs=((a.ref, hero.ref), (b.ref, hero.ref))), ctx=f.ctx
        )
        unengage(f.state, a.ref)
        assert hero.engaged_with == {b.ref}
        assert a.engaged_with == set()

    def test_unengaged_heroes_are_the_ones_the_loremaster_assigns_first(self, fight) -> None:
        f = company(fight, heroes=3, foes=1)
        heroes = f.state.active_heroes()
        heroes[2].stance = Stance.REARWARD
        foe = f.state.active_adversaries()[0]
        resolve_engagement(f.state, EngagementPlan(pairs=((heroes[0].ref, foe.ref),)), ctx=f.ctx)
        assert [c.ref for c in unengaged_heroes(f.state)] == [heroes[1].ref]
