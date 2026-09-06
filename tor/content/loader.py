"""Loading and validating a content pack (``05.1``, ``05.1.1``).

Validation is **eager and total**. A malformed pack must never produce a runtime failure
mid-session, so every check runs at load time and every failure names the file, a JSON
pointer to the offending node, and the entity id.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tor.content.entities import (
    Adversary,
    AttributeRow,
    Calling,
    Culture,
    CultureDerived,
    EffectDefinition,
    Manifest,
    Patron,
    ProficiencyGrant,
    ShadowPath,
    SongTemplate,
    StandardOfLivingTier,
    Undertaking,
)
from tor.content.pack import ContentPack
from tor.content.schema import schema_for, validate_document
from tor.effects.hooks import Hook
from tor.effects.library import (
    EFFECT_FACTORIES,
    REPLACEMENT_FACTORIES,
    build_effect,
)
from tor.errors import ContentError
from tor.model.abilities import COMBAT_PROFICIENCIES, SKILLS, VALOUR, WISDOM, is_skill
from tor.model.conditions import DriveKind, StandardOfLiving
from tor.model.gear import ArmourCategory, ArmourType, Craftsmanship, ShieldType, WeaponType
from tor.model.ids import AbilityId, AdversaryId, CallingId, CultureId, EffectId, ItemId
from tor.tables import LookupTable, rows_from_json

__all__ = ["load_pack", "load_packs"]

#: Every ability id a content pack may name (``05.1.1`` check 2).
VALID_ABILITIES: frozenset[AbilityId] = frozenset({*SKILLS, *COMBAT_PROFICIENCIES, VALOUR, WISDOM})

#: Files that hold declarative effects. All land in one namespace: ids are globally
#: unique, and the engine cares about an effect's ``kind``, not which file it came from.
_EFFECT_FILES: Mapping[str, str] = {
    "virtues.json": "virtue",
    "cultural_virtues.json": "cultural_virtue",
    "rewards.json": "reward",
    "enchanted_rewards.json": "enchanted_reward",
    "distinctive_features.json": "distinctive_feature",
    "flaws.json": "flaw",
    "fell_abilities.json": "fell_ability",
    "cultural_blessings.json": "cultural_blessing",
    "patron_benefits.json": "patron_benefit",
    # The four kinds 04.1 names that had no file to be written in. Item blessings and
    # curses are central to 13; conditions are how a state like being poisoned reaches
    # the rest hooks (16.4.5) without one subsystem importing another.
    "item_blessings.json": "item_blessing",
    "curses.json": "curse",
    "conditions.json": "condition",
    "undertaking_benefits.json": "undertaking_benefit",
}


def load_pack(path: Path, *, strict: bool = True) -> ContentPack:
    """Load one pack directory and validate it."""
    if not path.is_dir():
        raise ContentError(f"content pack directory not found: {path}", file=path)

    manifest = _manifest(path)
    pack = ContentPack(
        manifest=manifest,
        cultures=_cultures(path),
        callings=_callings(path),
        weapons=_weapons(path),
        armour=_armour(path),
        shields=_shields(path),
        effects=_effects(path),
        shadow_paths=_shadow_paths(path),
        adversaries=_adversaries(path),
        patrons=_patrons(path),
        standards_of_living=_standards_of_living(path),
        undertakings=_undertakings(path),
        tables=_tables(path),
        names=_names(path),
        songs=_songs(path),
        skill_names=_skill_names(path),
    )
    if strict:
        validate(pack, path)
    return pack


def load_packs(paths: Sequence[Path], *, strict: bool = True) -> ContentPack:
    """Load several packs in order, layering each over the last."""
    if not paths:
        raise ContentError("at least one content pack is required")
    merged = load_pack(paths[0], strict=False)
    for extra in paths[1:]:
        merged = merged.merge(load_pack(extra, strict=False))
    if strict:
        validate(merged, paths[-1])
    return merged


# ----------------------------------------------------------------------------------
# Reading files
# ----------------------------------------------------------------------------------


def _read(
    path: Path, name: str, *, required: bool = True, schema: str | None = None
) -> dict[str, Any]:
    file = path / name
    if not file.exists():
        if required:
            raise ContentError(f"content pack is missing {name}", file=file)
        return {}
    loaded = _decode(file, name)
    if (schema_name := schema or schema_for(name)) is not None:
        validate_document(loaded, schema_name=schema_name, file=file)
    return loaded


def _decode(file: Path, name: str) -> dict[str, Any]:
    """Read one JSON object, failing loudly on a syntax error or a bare array."""
    try:
        loaded = json.loads(file.read_text())
    except json.JSONDecodeError as exc:
        raise ContentError(f"{name} is not valid JSON: {exc.msg}", file=file) from exc
    if not isinstance(loaded, dict):
        raise ContentError(f"{name} must hold a JSON object", file=file)
    return loaded


def _rows(data: Mapping[str, Any], key: str, _file: Path) -> list[dict[str, Any]]:
    """One section's rows. The schema has already guaranteed the shape."""
    rows: list[dict[str, Any]] = data.get(key, [])
    return rows


def _manifest(path: Path) -> Manifest:
    data = _read(path, "pack.json")
    return Manifest(
        id=str(data["id"]),
        name=str(data["name"]),
        version=str(data.get("version", "0")),
        overrides=frozenset(data.get("overrides", ())),
    )


def _cultures(path: Path) -> dict[str, Culture]:
    file = path / "cultures.json"
    data = _read(path, "cultures.json", required=False)
    out: dict[str, Culture] = {}
    for row in _rows(data, "cultures", file):
        cid = str(row["id"])
        derived = row["derived"]
        blessing = row.get("cultural_blessing", {})
        out[cid] = Culture(
            id=CultureId(cid),
            name=str(row.get("name", cid)),
            cultural_blessing=EffectId(str(blessing.get("effect", ""))),
            cultural_weakness=_opt_effect((row.get("cultural_weakness") or {}).get("effect")),
            standard_of_living=_sol(row["standard_of_living"]),
            derived=CultureDerived(
                endurance_bonus=int(derived["endurance_bonus"]),
                hope_bonus=int(derived["hope_bonus"]),
                parry_bonus=int(derived["parry_bonus"]),
            ),
            attribute_sets=tuple(
                AttributeRow(
                    roll=int(a["roll"]),
                    strength=int(a["strength"]),
                    heart=int(a["heart"]),
                    wits=int(a["wits"]),
                )
                for a in row.get("attribute_sets", ())
            ),
            skills={AbilityId(k): int(v) for k, v in row.get("skills", {}).items()},
            favoured_skill_choices=tuple(
                AbilityId(s) for s in row.get("favoured_skill_choices", ())
            ),
            favoured_skill_count=int(row.get("favoured_skill_count", 1)),
            combat_proficiencies=tuple(
                ProficiencyGrant(
                    rating=int(g["rating"]),
                    choose=(
                        g["choose"]
                        if isinstance(g["choose"], str)
                        else tuple(AbilityId(c) for c in g["choose"])
                    ),
                )
                for g in row.get("combat_proficiencies", ())
            ),
            distinctive_feature_choices=tuple(
                EffectId(f) for f in row.get("distinctive_feature_choices", ())
            ),
            distinctive_feature_count=int(row.get("distinctive_feature_count", 2)),
            allowed_weapons=(
                None
                if row.get("allowed_weapons") is None
                else tuple(ItemId(w) for w in row["allowed_weapons"])
            ),
            forbidden_items=tuple(ItemId(i) for i in row.get("forbidden_items", ())),
            attribute_bonus=row.get("attribute_bonus"),
            languages=tuple(row.get("languages", ())),
            typical_names={k: tuple(v) for k, v in (row.get("typical_names") or {}).items()},
            adventuring_age=row.get("adventuring_age", {}),
            eye_awareness_weight=int(row.get("eye_awareness_weight", 0)),
        )
    return out


def _opt_effect(value: Any) -> EffectId | None:
    return None if value is None else EffectId(str(value))


def _opt_culture(value: Any) -> CultureId | None:
    return None if value is None else CultureId(str(value))


def _sol(value: Any) -> StandardOfLiving:
    return StandardOfLiving[str(value).upper()]


def _callings(path: Path) -> dict[str, Calling]:
    file = path / "callings.json"
    data = _read(path, "callings.json", required=False)
    out: dict[str, Calling] = {}
    for row in _rows(data, "callings", file):
        cid = str(row["id"])
        feature = row.get("distinctive_feature", {})
        out[cid] = Calling(
            id=CallingId(cid),
            name=str(row.get("name", cid)),
            favoured_skill_choices=tuple(
                AbilityId(s) for s in row.get("favoured_skill_choices", ())
            ),
            favoured_skill_count=int(row.get("favoured_skill_count", 2)),
            distinctive_feature=EffectId(str(feature.get("effect", ""))),
            requires_subject=bool(feature.get("requires_subject", False)),
            shadow_path=str(row.get("shadow_path", "")),
            free_undertaking=str(row.get("free_undertaking", "")),
        )
    return out


def _weapons(path: Path) -> dict[str, WeaponType]:
    file = path / "weapons.json"
    data = _read(path, "weapons.json", required=False)
    out: dict[str, WeaponType] = {}
    for row in _rows(data, "weapons", file):
        wid = str(row["id"])
        injury = row.get("injury", {})
        out[wid] = WeaponType(
            id=ItemId(wid),
            name=str(row.get("name", wid)),
            damage=int(row.get("damage", 0)),
            injury_one_handed=injury.get("one_handed"),
            injury_two_handed=injury.get("two_handed"),
            load=int(row.get("load", 0)),
            proficiency=row.get("proficiency", "brawling"),
            ranged=bool(row.get("ranged", False)),
            throwable=bool(row.get("throwable", False)),
            two_handed_only=bool(row.get("two_handed_only", False)),
            can_cause_piercing_blow=bool(row.get("can_cause_piercing_blow", True)),
        )
    return out


def _armour(path: Path) -> dict[str, ArmourType]:
    file = path / "armour.json"
    data = _read(path, "armour.json", required=False)
    out: dict[str, ArmourType] = {}
    for key in ("armour", "helms"):
        for row in _rows(data, key, file):
            aid = str(row["id"])
            minimum = row.get("min_standard_of_living")
            out[aid] = ArmourType(
                id=ItemId(aid),
                name=str(row.get("name", aid)),
                protection=int(row.get("protection", 0)),
                load=int(row.get("load", 0)),
                category=ArmourCategory(row["category"]),
                min_standard_of_living=None if minimum is None else _sol(minimum),
                removable_in_combat=bool(row.get("removable_in_combat", True)),
            )
    return out


def _shields(path: Path) -> dict[str, ShieldType]:
    file = path / "shields.json"
    data = _read(path, "shields.json", required=False)
    out: dict[str, ShieldType] = {}
    for row in _rows(data, "shields", file):
        sid = str(row["id"])
        minimum = row.get("min_standard_of_living")
        out[sid] = ShieldType(
            id=ItemId(sid),
            name=str(row.get("name", sid)),
            parry_bonus=int(row.get("parry_bonus", 0)),
            load=int(row.get("load", 0)),
            min_standard_of_living=None if minimum is None else _sol(minimum),
        )
    return out


def _effects(path: Path) -> dict[str, EffectDefinition]:
    out: dict[str, EffectDefinition] = {}
    for filename, default_kind in _EFFECT_FILES.items():
        file = path / filename
        data = _read(path, filename, required=False, schema="effects.schema.json")
        if not data:
            continue
        section = next(iter(data)) if data else ""
        for index, row in enumerate(_rows(data, section, file)):
            at = f"/{section}/{index}"
            eid = str(row["id"])
            if eid in out:
                raise ContentError(
                    f"duplicate effect id {eid!r} in {filename}",
                    file=file,
                    pointer=at,
                    entity_id=eid,
                )
            out[eid] = EffectDefinition(
                id=EffectId(eid),
                name=str(row.get("name", eid)),
                factory=str(row.get("factory", "")),
                kind=str(row.get("kind", default_kind)),
                params=row.get("params", {}),
                repeatable=bool(row.get("repeatable", False)),
                applies_to=tuple(row.get("applies_to", ())),
                culture=_opt_culture(row.get("culture")),
                min_wisdom=int(row.get("min_wisdom", 0)),
                craftsmanship=tuple(Craftsmanship(c) for c in row.get("craftsmanship", ())),
                item_types=tuple(row.get("item_types", ())),
                requires_bane=bool(row.get("requires_bane", False)),
                supersedes=_opt_effect(row.get("supersedes")),
                requires_choice=row.get("requires_choice", {}),
                deprecated=bool(row.get("deprecated", False)),
            )
    return out


def _shadow_paths(path: Path) -> dict[str, ShadowPath]:
    file = path / "shadow_paths.json"
    data = _read(path, "shadow_paths.json", required=False)
    out: dict[str, ShadowPath] = {}
    for row in _rows(data, "shadow_paths", file):
        pid = str(row["id"])
        out[pid] = ShadowPath(
            id=pid,
            calling=CallingId(str(row.get("calling", ""))),
            steps=tuple(EffectId(f) for f in row.get("steps", ())),
        )
    return out


def _adversaries(path: Path) -> dict[str, Adversary]:
    file = path / "adversaries.json"
    data = _read(path, "adversaries.json", required=False)
    out: dict[str, Adversary] = {}
    for row in _rows(data, "adversaries", file):
        aid = str(row["id"])
        drive = row.get("drive", {})
        out[aid] = Adversary(
            id=AdversaryId(aid),
            name=str(row.get("name", aid)),
            attribute_level=int(row.get("attribute_level", 0)),
            endurance=int(row.get("endurance", 0)),
            might=int(row.get("might", 1)),
            drive_kind=DriveKind(drive["kind"]),
            drive=int(drive.get("value", 0)),
            parry=int(row.get("parry", 0)),
            armour=int(row.get("armour", 0)),
            attacks=tuple(row.get("attacks", ())),
            size=str(row.get("size", "human")),
            creature_types=tuple(row.get("creature_types", ())),
            always_available_special_damage=tuple(row.get("always_available_special_damage", ())),
            fell_abilities=tuple(EffectId(a) for a in row.get("fell_abilities", ())),
            distinctive_features=tuple(EffectId(f) for f in row.get("distinctive_features", ())),
            icon_inverted=bool(row.get("icon_inverted", True)),
        )
    return out


def _patrons(path: Path) -> dict[str, Patron]:
    file = path / "patrons.json"
    data = _read(path, "patrons.json", required=False)
    out: dict[str, Patron] = {}
    for row in _rows(data, "patrons", file):
        pid = str(row["id"])
        out[pid] = Patron(
            id=pid,
            name=str(row.get("name", pid)),
            fellowship_bonus=int(row.get("fellowship_bonus", 0)),
            benefit=EffectId(str((row.get("benefit") or {}).get("effect", ""))),
            favoured_callings=tuple(CallingId(c) for c in row.get("favoured_callings", ())),
            agenda=str(row.get("agenda", "")),
        )
    return out


def _standards_of_living(path: Path) -> tuple[StandardOfLivingTier, ...]:
    file = path / "standards_of_living.json"
    data = _read(path, "standards_of_living.json", required=False)
    tiers: list[StandardOfLivingTier] = []
    for index, row in enumerate(_rows(data, "tiers", file)):
        tid = str(row.get("id", ""))
        if not tid:
            raise ContentError("a tier needs an 'id'", file=file, pointer=f"/tiers/{index}")
        tiers.append(
            StandardOfLivingTier(
                tier=_sol(tid),
                treasure_threshold=row.get("treasure_threshold"),
                useful_items=int(row.get("useful_items", 0)),
                mount_vigour=row.get("mount_vigour"),
            )
        )
    return tuple(tiers)


def _undertakings(path: Path) -> dict[str, Undertaking]:
    file = path / "undertakings.json"
    data = _read(path, "undertakings.json", required=False)
    out: dict[str, Undertaking] = {}
    for row in _rows(data, "undertakings", file):
        uid = str(row["id"])
        out[uid] = Undertaking(
            id=uid,
            name=str(row.get("name", uid)),
            yule_only=bool(row.get("yule_only", False)),
            description=str(row.get("description", "")),
        )
    return out


def _tables(path: Path) -> dict[str, LookupTable[Any]]:
    directory = path / "tables"
    out: dict[str, LookupTable[Any]] = {}
    if not directory.is_dir():
        return out
    for file in sorted(directory.glob("*.json")):
        data = _decode(file, file.name)
        validate_document(data, schema_name="table.schema.json", file=file)
        tid = str(data.get("id", file.stem))
        die = str(data["die"])
        # LookupTable validates total coverage eagerly, so a gap fails here (05.1.1 #4).
        out[tid] = LookupTable(
            id=tid, die=die, rows=rows_from_json(data.get("rows", []), table_id=tid)
        )
    return out


def _songs(path: Path) -> dict[str, SongTemplate]:
    """``songs.json`` (``15.8``) — the stock of songs a Company may draw on."""
    data = _read(path, "songs.json", required=False)
    out: dict[str, SongTemplate] = {}
    for row in _rows(data, "songs", path / "songs.json"):
        sid = str(row["id"])
        out[sid] = SongTemplate(id=sid, title=str(row["title"]), kind=str(row["kind"]))
    return out


def _skill_names(path: Path) -> dict[str, str]:
    """``skills.json`` (``05.1``) — display-name overrides, never new Skills.

    The grid is engine knowledge (``tor.model.abilities.SKILLS``); a pack that wants to
    call ``scan`` something else may, but a pack that names an ability the engine does not
    know is a :class:`ContentError` — caught by ``_check_abilities``.
    """
    data = _read(path, "skills.json", required=False)
    names = data.get("skills", {})
    return {str(key): str(value) for key, value in names.items()}


def _names(path: Path) -> dict[str, dict[str, tuple[str, ...]]]:
    directory = path / "names"
    out: dict[str, dict[str, tuple[str, ...]]] = {}
    if not directory.is_dir():
        return out
    for file in sorted(directory.glob("*.json")):
        data = _decode(file, file.name)
        validate_document(data, schema_name="names.schema.json", file=file)
        out[file.stem] = {key: tuple(value) for key, value in data.items()}
    return out


# ----------------------------------------------------------------------------------
# Validation (05.1.1) — eager, total, and reporting the offending node
# ----------------------------------------------------------------------------------


def validate(pack: ContentPack, path: Path) -> None:
    """The referential and semantic checks of ``05.1.1``.

    Run on the **merged** result, not on each pack in isolation: a supplement carries only
    what it adds, so completeness is a property of the whole stack.
    """
    _check_not_empty(pack, path)
    _check_effects_resolve(pack, path)
    _check_abilities(pack, path)
    _check_culture_completeness(pack, path)
    _check_cultural_virtues(pack, path)
    _check_replacement_effects(pack, path)
    _check_weapon_proficiencies(pack, path)
    _check_adversary_drive(pack, path)
    _check_standards_of_living(pack, path)
    _check_shadow_paths(pack, path)


def _check_not_empty(pack: ContentPack, path: Path) -> None:
    """A playable stack needs at least one culture and one calling."""
    for section in ("cultures", "callings"):
        if not getattr(pack, section):
            raise ContentError(
                f"the content stack defines no {section}; a hero cannot be built without them",
                file=path,
            )


def _check_effects_resolve(pack: ContentPack, path: Path) -> None:
    """Check 1: every referenced effect exists and names a factory the engine knows."""
    for effect in pack.effects.values():
        if effect.factory not in EFFECT_FACTORIES:
            raise ContentError(
                f"effect {effect.id!r} names unknown factory {effect.factory!r}; known: "
                f"{sorted(EFFECT_FACTORIES)}",
                file=path,
                entity_id=effect.id,
            )
        hook = effect.params.get("hook")
        if hook is not None and hook not in Hook.__members__:
            raise ContentError(
                f"effect {effect.id!r} names unknown hook {hook!r}",
                file=path,
                entity_id=effect.id,
            )

    def require(effect_id: str | None, *, owner: str, what: str) -> None:
        if effect_id and effect_id not in pack.effects:
            raise ContentError(
                f"{what} {effect_id!r} referenced by {owner!r} does not exist",
                file=path,
                entity_id=owner,
            )

    for culture in pack.cultures.values():
        require(culture.cultural_blessing, owner=culture.id, what="cultural blessing")
        require(culture.cultural_weakness, owner=culture.id, what="cultural weakness")
        for feature in culture.distinctive_feature_choices:
            require(feature, owner=culture.id, what="distinctive feature")
    for calling in pack.callings.values():
        require(calling.distinctive_feature, owner=calling.id, what="distinctive feature")
    for adversary in pack.adversaries.values():
        for ability in adversary.fell_abilities:
            require(ability, owner=adversary.id, what="fell ability")
        for feature in adversary.distinctive_features:
            require(feature, owner=adversary.id, what="distinctive feature")
    for patron in pack.patrons.values():
        require(patron.benefit, owner=patron.id, what="patron benefit")
    for shadow_path in pack.shadow_paths.values():
        for flaw in shadow_path.steps:
            require(flaw, owner=shadow_path.id, what="flaw")


def _check_abilities(pack: ContentPack, path: Path) -> None:
    """Check 2: every ability id is one the engine knows (``20.1``-``20.3``)."""

    def require(abilities: Sequence[str], *, owner: str) -> None:
        for ability in abilities:
            if AbilityId(ability) not in VALID_ABILITIES:
                raise ContentError(
                    f"{owner!r} names unknown ability {ability!r}",
                    file=path,
                    entity_id=owner,
                )

    for culture in pack.cultures.values():
        require(list(culture.skills), owner=culture.id)
        require(culture.favoured_skill_choices, owner=culture.id)
        for grant in culture.combat_proficiencies:
            if not isinstance(grant.choose, str):
                require(grant.choose, owner=culture.id)
    for calling in pack.callings.values():
        require(calling.favoured_skill_choices, owner=calling.id)
    # skills.json renames; it must not invent. Only a Skill may be renamed, not a Combat
    # Proficiency or a rank — those are not in the 18-skill grid a pack may relabel.
    for ability in pack.skill_names:
        if not is_skill(AbilityId(ability)):
            raise ContentError(
                f"skills.json renames {ability!r}, which is not one of the 18 Skills",
                file=path / "skills.json",
                entity_id=ability,
            )


def _check_culture_completeness(pack: ContentPack, path: Path) -> None:
    """Check 3: exactly 18 skill ratings, and a full set of Attribute rows."""
    for culture in pack.cultures.values():
        if missing := set(SKILLS) - set(culture.skills):
            raise ContentError(
                f"culture {culture.id!r} is missing ratings for {sorted(missing)}; all 18 "
                "Skills must be present",
                file=path,
                entity_id=culture.id,
            )
        rolls = sorted(row.roll for row in culture.attribute_sets)
        if rolls != list(range(1, 7)):
            raise ContentError(
                f"culture {culture.id!r} needs exactly six attribute_sets keyed 1-6, got {rolls}",
                file=path,
                entity_id=culture.id,
            )
        if len(culture.favoured_skill_choices) < culture.favoured_skill_count:
            raise ContentError(
                f"culture {culture.id!r} offers {len(culture.favoured_skill_choices)} "
                f"favoured skill choices but requires {culture.favoured_skill_count}",
                file=path,
                entity_id=culture.id,
            )


def _check_cultural_virtues(pack: ContentPack, path: Path) -> None:
    """Check 7: a Cultural Virtue declares a culture, and that culture exists."""
    for effect in pack.effects.values():
        if effect.kind != "cultural_virtue":
            continue
        if not effect.culture:
            raise ContentError(
                f"cultural virtue {effect.id!r} must declare a 'culture'",
                file=path,
                entity_id=effect.id,
            )
        if effect.culture not in pack.cultures:
            raise ContentError(
                f"cultural virtue {effect.id!r} names unknown culture {effect.culture!r}",
                file=path,
                entity_id=effect.id,
            )


def _supersession_root(effect_id: str, pack: ContentPack, path: Path) -> str:
    """Follow ``supersedes`` to the head of the chain, refusing a cycle."""
    root = effect_id
    for _ in range(len(pack.effects) + 1):
        definition = pack.effects.get(root)
        if definition is None or not definition.supersedes:
            return root
        if definition.supersedes not in pack.effects:
            raise ContentError(
                f"effect {definition.id!r} supersedes unknown effect {definition.supersedes!r}",
                file=path,
                entity_id=definition.id,
            )
        root = definition.supersedes
    raise ContentError(
        f"effect {effect_id!r} is part of a 'supersedes' cycle", file=path, entity_id=effect_id
    )


def _check_replacement_effects(pack: ContentPack, path: Path) -> None:
    """Check 5: two effects that *replace* a hook value cannot share one item type.

    A replacing effect overwrites rather than adds, so two of them on one hook for one item
    type has no defined answer. The legitimate case — Superior Keen standing in for Keen —
    is exactly what ``supersedes`` declares, so effects on one supersession chain are one
    slot and are allowed to coexist in a pack. Which of them applies to a given *item* is
    ``13.7.3``'s business, not the loader's.
    """
    claimed: dict[tuple[Hook, str], tuple[str, str]] = {}
    for definition in sorted(pack.effects.values(), key=lambda d: d.id):
        if definition.factory not in REPLACEMENT_FACTORIES or definition.deprecated:
            continue
        effect = build_effect(
            EffectId(definition.id), definition.kind, definition.factory, definition.params
        )
        root = _supersession_root(definition.id, pack, path)
        item_types = definition.applies_to or definition.item_types or ("*",)
        for hook in effect.listeners:
            for item_type in item_types:
                key = (hook, str(item_type))
                if (previous := claimed.get(key)) is not None and previous[1] != root:
                    raise ContentError(
                        f"effects {previous[0]!r} and {definition.id!r} both replace "
                        f"{hook.value} for item type {item_type!r}; one must declare "
                        "'supersedes' naming the other",
                        file=path,
                        entity_id=definition.id,
                    )
                claimed.setdefault(key, (definition.id, root))


def _check_weapon_proficiencies(pack: ContentPack, path: Path) -> None:
    """Check 8: a weapon's proficiency exists, or is the literal ``brawling``."""
    allowed = {*COMBAT_PROFICIENCIES, "brawling"}
    for weapon in pack.weapons.values():
        if weapon.proficiency not in allowed:
            raise ContentError(
                f"weapon {weapon.id!r} names unknown proficiency {weapon.proficiency!r}",
                file=path,
                entity_id=str(weapon.id),
            )


def _check_adversary_drive(pack: ContentPack, path: Path) -> None:
    """Check 9: exactly one of hate / resolve, which the DriveKind discriminator gives."""
    for adversary in pack.adversaries.values():
        for attack in adversary.attacks:
            if "heavy_blow" in attack.get("special_damage", ()):
                raise ContentError(
                    f"adversary {adversary.id!r} lists heavy_blow explicitly; every "
                    "adversary always has it and the engine adds it implicitly",
                    file=path,
                    entity_id=adversary.id,
                )


def _check_standards_of_living(pack: ContentPack, path: Path) -> None:
    """Check 6: the tier ladder is complete and its thresholds ascend.

    This is the whole of check 6. "Every ``min_standard_of_living`` names a defined tier"
    needs no separate pass: the readers resolve every such field through :func:`_sol_of`,
    which rejects a name that is not a :class:`StandardOfLiving`, and the completeness
    requirement below means every member of that enum is a declared tier.
    """
    if not pack.standards_of_living:
        raise ContentError("standards_of_living.json defines no tiers", file=path)
    seen = {tier.tier for tier in pack.standards_of_living}
    if missing := set(StandardOfLiving) - seen:
        raise ContentError(
            f"standards_of_living is missing {sorted(t.name for t in missing)}", file=path
        )
    thresholds = [
        tier.treasure_threshold
        for tier in sorted(pack.standards_of_living, key=lambda t: t.tier)
        if tier.treasure_threshold is not None
    ]
    if thresholds != sorted(thresholds):
        raise ContentError(
            f"standard of living thresholds must ascend with the tier, got {thresholds}",
            file=path,
        )


def _check_shadow_paths(pack: ContentPack, path: Path) -> None:
    """Each Calling's Shadow Path exists and holds exactly four ordered Flaws (``11.7``)."""
    for calling in pack.callings.values():
        if calling.shadow_path and calling.shadow_path not in pack.shadow_paths:
            raise ContentError(
                f"calling {calling.id!r} names unknown shadow path {calling.shadow_path!r}",
                file=path,
                entity_id=calling.id,
            )
