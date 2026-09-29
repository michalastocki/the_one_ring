"""Shared builders for the combat tests (spec 08, 12).

A fight needs a hero with real gear, an adversary instance with a real stat block, and a
bus for each — enough setup that repeating it per test would bury the rule being asserted.
Everything here comes from `content/example/`, which is invented: 19.9 forbids a fixture
that reproduces the licensed book's tables, and nothing below asserts a value from it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from tor.content.pack import ContentPack
from tor.effects.bus import EffectBus
from tor.model.abilities import COMBAT_PROFICIENCIES, SKILLS
from tor.model.adversary import AdversaryInstance
from tor.model.attributes import AttributeSet
from tor.model.conditions import ConditionSet
from tor.model.derive import DerivedStat
from tor.model.gear import ArmourInstance, Gear, ShieldInstance, WeaponInstance
from tor.model.hero import Hero
from tor.model.ids import (
    AbilityId,
    AdversaryInstanceId,
    CallingId,
    CultureId,
    EffectId,
    HeroId,
    ItemId,
)
from tor.rules.combat.state import CombatState, Stance
from tor.rules.context import RulesContext

EXAMPLE = Path(__file__).resolve().parents[2] / "content" / "example"

BLADE = ItemId("example_blade")
BOW = ItemId("example_bow")
SPEAR = ItemId("example_spear")
GREAT_AXE = ItemId("example_great_axe")
UNARMED = ItemId("unarmed")
MAIL = ItemId("example_mail")
LEATHER = ItemId("example_leather")
HELM = ItemId("example_helm")
BUCKLER = ItemId("example_buckler")

MINION = "example_minion"
BRUTE = "example_brute"
RUFFIAN = "example_ruffian"
LURKER = "example_lurker"


@pytest.fixture(scope="session")
def pack() -> ContentPack:
    from tor.content.loader import load_pack

    return load_pack(EXAMPLE)


def build_hero(
    hero_id: str = "hero",
    *,
    strength: int = 5,
    heart: int = 5,
    wits: int = 4,
    endurance: int = 24,
    max_endurance: int = 24,
    hope: int = 12,
    parry: int = 12,
    weapons: Sequence[WeaponInstance] = (),
    armour: ItemId | None = MAIL,
    helm: ItemId | None = None,
    shield: ItemId | None = None,
    proficiencies: dict[AbilityId, int] | None = None,
) -> Hero:
    """A hero with just enough sheet to fight. Nothing here is a value from the book."""
    gear = Gear(weapons=list(weapons) or [WeaponInstance(type_id=BLADE)])
    if armour is not None:
        gear.armour = ArmourInstance(type_id=armour)
    if helm is not None:
        gear.helm = ArmourInstance(type_id=helm)
    if shield is not None:
        gear.shield = ShieldInstance(type_id=shield)
    hero = Hero(
        id=HeroId(hero_id),
        name=hero_id.title(),
        culture=CultureId("example_folk"),
        calling=CallingId("example_calling"),
        age=30,
        attributes=AttributeSet(strength=strength, heart=heart, wits=wits),
        skills=dict.fromkeys(SKILLS, 2),
        proficiencies=proficiencies or dict.fromkeys(COMBAT_PROFICIENCIES, 3),
        max_endurance=DerivedStat(base=max_endurance),
        max_hope=DerivedStat(base=12),
        parry=DerivedStat(base=parry),
        endurance=endurance,
        hope=hope,
        conditions=ConditionSet(),
        shadow_path=EffectId("example_path"),
        gear=gear,
    )
    return hero


def build_adversary(
    pack: ContentPack, template: str = MINION, instance_id: str = "foe1"
) -> AdversaryInstance:
    return AdversaryInstance.of(pack.stat_block(template), AdversaryInstanceId(instance_id))


class Fight:
    """A set-up fight: the state, a bus per combatant, and the context over both."""

    def __init__(self, pack: ContentPack) -> None:
        self.pack = pack
        self.state = CombatState()
        self.buses: dict[object, EffectBus] = {}

    def hero(self, hero: Hero, *, stance: Stance | None = Stance.OPEN) -> Hero:
        self.state.add_hero(hero, stance=stance)
        self.buses[hero.id] = EffectBus()
        return hero

    def foe(self, instance: AdversaryInstance) -> AdversaryInstance:
        self.state.add_adversary(instance)
        self.buses[instance.instance_id] = EffectBus()
        return instance

    def register(self, ref: object, effect_id: str, source) -> None:
        self.buses[ref].register(self.pack.instantiate(effect_id), source)

    @property
    def ctx(self) -> RulesContext:
        return RulesContext(gear=self.pack, buses=dict(self.buses))  # type: ignore[arg-type]


@pytest.fixture
def fight(pack: ContentPack) -> Callable[[], Fight]:
    def make() -> Fight:
        return Fight(pack)

    return make
