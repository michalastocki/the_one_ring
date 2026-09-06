"""`tor.content` — loading, validation, and the example pack (spec 05, 19.7).

Every test in the repository runs against `content/example/`. No test may depend on
licensed content, and none asserts a value from the real book — the mechanism is what is
under test, not a particular culture's Endurance bonus (19.9).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tor.content import ContentPack, Manifest, load_pack, load_packs, parse_ops
from tor.content.ops import OpKind, Who
from tor.content.schema import SCHEMA_DIR, schema_for
from tor.dice import FeatFace
from tor.effects.bus import EffectKind
from tor.effects.hooks import Hook
from tor.effects.library import EFFECT_FACTORIES, build_effect
from tor.errors import ContentError
from tor.model.abilities import COMBAT_PROFICIENCIES, SKILLS
from tor.model.conditions import DriveKind, StandardOfLiving
from tor.tables import FEAT_DOMAIN, SUCCESS_DOMAIN, DieKind, Sentinel

EXAMPLE = Path(__file__).resolve().parents[2] / "content" / "example"


@pytest.fixture(scope="module")
def pack() -> ContentPack:
    return load_pack(EXAMPLE)


@pytest.fixture
def scratch_pack(tmp_path: Path) -> Path:
    """A writable copy of the example pack, for testing failure modes."""
    target = tmp_path / "example"
    shutil.copytree(EXAMPLE, target)
    return target


def rewrite(pack_dir: Path, name: str, mutate) -> None:
    file = pack_dir / name
    data = json.loads(file.read_text())
    mutate(data)
    file.write_text(json.dumps(data))


class TestExamplePackCompleteness:
    """05.11 — the pack must exercise every schema branch."""

    def test_it_loads_and_validates(self, pack: ContentPack) -> None:
        assert pack.manifest.id == "example"

    def test_two_cultures_one_with_a_weakness_and_restrictions(self, pack: ContentPack) -> None:
        assert len(pack.cultures) == 2
        restricted = [c for c in pack.cultures.values() if c.allowed_weapons is not None]
        assert restricted and restricted[0].cultural_weakness
        assert restricted[0].forbidden_items

    def test_a_culture_may_be_unrestricted(self, pack: ContentPack) -> None:
        # allowed_weapons: null means no restriction, and must not be confused with [].
        assert any(c.allowed_weapons is None for c in pack.cultures.values())

    def test_one_calling_requires_a_subject(self, pack: ContentPack) -> None:
        assert any(c.requires_subject for c in pack.callings.values())

    def test_weapons_cover_every_branch(self, pack: ContentPack) -> None:
        weapons = list(pack.weapons.values())
        assert any(w.injury_one_handed is not None and w.injury_two_handed is None for w in weapons)
        assert any(w.two_handed_only for w in weapons)
        assert any(w.injury_one_handed and w.injury_two_handed for w in weapons)
        assert any(w.ranged for w in weapons)
        assert any(w.throwable for w in weapons)

    def test_the_unarmed_edge_case_is_present(self, pack: ContentPack) -> None:
        # 05.4: no Injury rating in either grip, and cannot cause a Piercing Blow. The
        # schema must permit the combination.
        unarmed = [
            w
            for w in pack.weapons.values()
            if w.injury_one_handed is None
            and w.injury_two_handed is None
            and not w.can_cause_piercing_blow
        ]
        assert len(unarmed) == 1

    def test_armour_covers_every_category_including_a_helm(self, pack: ContentPack) -> None:
        categories = {a.category for a in pack.armour.values()}
        assert {"leather", "mail", "headgear"} <= {str(c) for c in categories}

    def test_shields_with_and_without_a_living_standard_minimum(self, pack: ContentPack) -> None:
        minimums = {s.min_standard_of_living for s in pack.shields.values()}
        assert None in minimums
        assert any(m is not None for m in minimums)

    def test_all_six_reward_shapes(self, pack: ContentPack) -> None:
        assert len(pack.effects_of_kind("reward")) == 6

    def test_enchanted_rewards_span_craftsmanship_and_both_bane_states(
        self, pack: ContentPack
    ) -> None:
        enchanted = list(pack.effects_of_kind("enchanted_reward").values())
        assert len(enchanted) >= 4
        craftsmanship = {str(c) for e in enchanted for c in e.craftsmanship}
        assert craftsmanship == {"elven", "dwarven", "numenorean"}
        assert {e.requires_bane for e in enchanted} == {True, False}

    def test_an_enchanted_reward_declares_what_it_supersedes(self, pack: ContentPack) -> None:
        # 13.7.3 constraint 7: an item may not carry both a Reward and the Enchanted
        # Reward sharing its descriptor.
        superseding = [e for e in pack.effects_of_kind("enchanted_reward").values() if e.supersedes]
        assert superseding
        for effect in superseding:
            assert effect.supersedes in pack.effects

    def test_four_adversaries_covering_the_required_shapes(self, pack: ContentPack) -> None:
        adversaries = list(pack.adversaries.values())
        assert len(adversaries) == 4
        assert any(
            a.drive_kind is DriveKind.HATE and a.size == "human" and a.might == 1
            for a in adversaries
        )
        assert any(
            a.drive_kind is DriveKind.HATE and a.size == "large" and a.might == 2
            for a in adversaries
        )
        assert any(a.drive_kind is DriveKind.RESOLVE for a in adversaries)

    def test_a_non_shadow_adversary_may_roll_uninverted(self, pack: ContentPack) -> None:
        # 12.4: inversion is set from the creature's category, not universally, so a
        # Resolve-bearing foe who is no servant of the Shadow rolls normally.
        assert any(not a.icon_inverted for a in pack.adversaries.values())

    def test_a_virtue_exists_for_every_factory_it_uses(self, pack: ContentPack) -> None:
        used = {e.factory for e in pack.effects.values()}
        assert used <= set(EFFECT_FACTORIES)
        assert len(used) >= 8

    def test_every_effect_kind_is_represented(self, pack: ContentPack) -> None:
        # 19.7: a kind nothing uses is either dead or unimplemented.
        present = {e.kind for e in pack.effects.values()}
        expected = {
            EffectKind.CULTURAL_BLESSING,
            EffectKind.VIRTUE,
            EffectKind.CULTURAL_VIRTUE,
            EffectKind.REWARD,
            EffectKind.ENCHANTED_REWARD,
            EffectKind.FELL_ABILITY,
            EffectKind.FLAW,
            EffectKind.DISTINCTIVE_FEATURE,
            EffectKind.PATRON_BENEFIT,
        }
        assert {str(k) for k in expected} <= present

    def test_every_declared_effect_actually_builds(self, pack: ContentPack) -> None:
        # The strongest form of 05.1.1 check 1: not merely that a factory name resolves,
        # but that the declaration produces a working Effect.
        for definition in pack.effects.values():
            effect = build_effect(
                definition.id, definition.kind, definition.factory, definition.params
            )
            assert effect.id == definition.id

    def test_a_shadow_path_per_calling_with_four_steps(self, pack: ContentPack) -> None:
        for calling in pack.callings.values():
            path = pack.shadow_paths[calling.shadow_path]
            assert len(path.steps) == 4

    def test_the_living_standard_ladder_is_complete(self, pack: ContentPack) -> None:
        assert {t.tier for t in pack.standards_of_living} == set(StandardOfLiving)

    def test_names_are_supplied_per_culture(self, pack: ContentPack) -> None:
        for culture_id in pack.cultures:
            assert culture_id in pack.names


class TestTableCoverage:
    """19.7: every LookupTable covers its whole key domain."""

    def test_every_table_covers_its_domain(self, pack: ContentPack) -> None:
        # Construction already enforces this — a table with a gap cannot be built — so
        # this asserts the pack actually carries tables of both die kinds.
        assert pack.tables
        kinds = {t.die for t in pack.tables.values()}
        assert {DieKind.FEAT, DieKind.SUCCESS} <= {DieKind(k) for k in kinds}

    def test_a_feat_table_answers_for_every_face(self, pack: ContentPack) -> None:
        table = pack.table("wound_severity")
        for key in FEAT_DOMAIN:
            assert table.lookup(key) is not None

    def test_a_success_table_answers_for_every_face(self, pack: ContentPack) -> None:
        table = pack.table("event_target")
        for key in SUCCESS_DOMAIN:
            assert table.lookup(key) is not None

    def test_the_journey_event_ops_all_parse(self, pack: ContentPack) -> None:
        table = pack.table("journey_events")
        for key in FEAT_DOMAIN:
            row = table.lookup(key)
            for branch in ("on_success", "on_failure"):
                parse_ops(row.get(branch, []), entity_id="journey_events")


class TestOps:
    def test_parses_the_whole_vocabulary(self) -> None:
        ops = parse_ops(
            [
                {"op": "wound", "who": "target"},
                {"op": "shadow", "who": "company", "points": 1, "source": "dread"},
                {"op": "journey_days", "delta": -1},
                {"op": "suppress_fatigue"},
                {"op": "narrative", "tag": "favourable_encounter"},
            ]
        )
        assert [o.kind for o in ops] == [
            OpKind.WOUND,
            OpKind.SHADOW,
            OpKind.JOURNEY_DAYS,
            OpKind.SUPPRESS_FATIGUE,
            OpKind.NARRATIVE,
        ]
        assert ops[1].who is Who.COMPANY

    def test_who_defaults_to_the_target(self) -> None:
        assert parse_ops([{"op": "wound"}])[0].who is Who.TARGET

    def test_an_unknown_op_is_rejected(self) -> None:
        with pytest.raises(ContentError, match="unknown op"):
            parse_ops([{"op": "smite"}])

    def test_a_missing_op_field_is_rejected(self) -> None:
        with pytest.raises(ContentError, match="needs an 'op' field"):
            parse_ops([{"points": 1}])

    def test_an_unknown_who_is_rejected(self) -> None:
        with pytest.raises(ContentError, match="unknown 'who'"):
            parse_ops([{"op": "wound", "who": "the_horse"}])

    def test_a_field_the_op_does_not_take_is_rejected(self) -> None:
        # Catches a typo that would otherwise be silently ignored.
        with pytest.raises(ContentError, match="does not take"):
            parse_ops([{"op": "suppress_fatigue", "points": 3}])

    def test_the_error_points_at_the_offending_entry(self) -> None:
        with pytest.raises(ContentError) as excinfo:
            parse_ops([{"op": "wound"}, {"op": "nonsense"}], pointer="/rows/2", entity_id="t")
        assert excinfo.value.pointer == "/rows/2/1"


class TestLoadFailures:
    """05.1.1 — validation is eager and total; every failure names the offending node."""

    def test_a_missing_directory_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ContentError, match="not found"):
            load_pack(tmp_path / "nowhere")

    def test_a_stack_with_no_cultures_is_rejected(self, scratch_pack: Path) -> None:
        # A single pack may omit any file — a supplement carries only what it adds — but
        # the merged stack must still be able to build a hero.
        (scratch_pack / "cultures.json").unlink()
        with pytest.raises(ContentError, match="defines no cultures"):
            load_pack(scratch_pack)

    def test_malformed_json_is_rejected(self, scratch_pack: Path) -> None:
        (scratch_pack / "cultures.json").write_text("{not json")
        with pytest.raises(ContentError, match="not valid JSON"):
            load_pack(scratch_pack)

    def test_a_manifest_without_an_id_is_rejected(self, scratch_pack: Path) -> None:
        (scratch_pack / "pack.json").write_text('{"name": "x"}')
        with pytest.raises(ContentError, match="'id' is a required property"):
            load_pack(scratch_pack)

    def test_a_missing_derived_constant_is_rejected(self, scratch_pack: Path) -> None:
        # 05.2: these vary per culture across 18-22 / 6-10 / 10-14 and must never default.
        rewrite(
            scratch_pack,
            "cultures.json",
            lambda d: d["cultures"][0]["derived"].pop("parry_bonus"),
        )
        with pytest.raises(ContentError, match="'parry_bonus' is a required property") as excinfo:
            load_pack(scratch_pack)
        assert excinfo.value.pointer == "/cultures/0/derived"

    def test_a_reference_to_a_missing_effect_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack,
            "cultures.json",
            lambda d: d["cultures"][0]["cultural_blessing"].update({"effect": "no_such"}),
        )
        with pytest.raises(ContentError, match="does not exist"):
            load_pack(scratch_pack)

    def test_an_unknown_factory_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack, "virtues.json", lambda d: d["virtues"][0].update({"factory": "magic"})
        )
        with pytest.raises(ContentError, match="unknown factory"):
            load_pack(scratch_pack)

    def test_an_unknown_hook_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack,
            "virtues.json",
            lambda d: d["virtues"][0]["params"].update({"hook": "MODIFY_LUCK"}),
        )
        with pytest.raises(ContentError, match="unknown hook"):
            load_pack(scratch_pack)

    def test_an_unknown_ability_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack,
            "cultures.json",
            lambda d: d["cultures"][0]["skills"].update({"juggling": 2}),
        )
        with pytest.raises(ContentError, match="unknown ability"):
            load_pack(scratch_pack)

    def test_a_culture_missing_a_skill_is_rejected(self, scratch_pack: Path) -> None:
        # 05.1.1 check 3: exactly 18 skill ratings.
        rewrite(scratch_pack, "cultures.json", lambda d: d["cultures"][0]["skills"].pop("lore"))
        with pytest.raises(ContentError, match="all 18"):
            load_pack(scratch_pack)

    def test_a_culture_with_five_attribute_rows_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack,
            "cultures.json",
            lambda d: d["cultures"][0]["attribute_sets"].pop(),
        )
        with pytest.raises(ContentError, match="is too short") as excinfo:
            load_pack(scratch_pack)
        assert excinfo.value.pointer == "/cultures/0/attribute_sets"

    def test_six_attribute_rows_keyed_wrongly_are_rejected(self, scratch_pack: Path) -> None:
        # The schema counts the rows; only this check reads their 'roll' keys, so a
        # culture with six rows keyed 1,1,2,3,4,5 gets past the schema and is caught here.
        rewrite(
            scratch_pack,
            "cultures.json",
            lambda d: d["cultures"][0]["attribute_sets"][1].update({"roll": 1}),
        )
        with pytest.raises(ContentError, match="attribute_sets keyed 1-6"):
            load_pack(scratch_pack)

    def test_a_cultural_virtue_without_a_culture_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack,
            "cultural_virtues.json",
            lambda d: d["cultural_virtues"][0].pop("culture"),
        )
        with pytest.raises(ContentError, match="must declare a 'culture'"):
            load_pack(scratch_pack)

    def test_a_cultural_virtue_naming_an_unknown_culture_is_rejected(
        self, scratch_pack: Path
    ) -> None:
        rewrite(
            scratch_pack,
            "cultural_virtues.json",
            lambda d: d["cultural_virtues"][0].update({"culture": "no_such_folk"}),
        )
        with pytest.raises(ContentError, match="unknown culture"):
            load_pack(scratch_pack)

    def test_a_weapon_with_an_unknown_proficiency_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack, "weapons.json", lambda d: d["weapons"][0].update({"proficiency": "clubs"})
        )
        with pytest.raises(ContentError, match="unknown proficiency"):
            load_pack(scratch_pack)

    def test_an_adversary_listing_heavy_blow_is_rejected(self, scratch_pack: Path) -> None:
        # 05.7: every adversary always has Heavy Blow and the engine adds it implicitly.
        rewrite(
            scratch_pack,
            "adversaries.json",
            lambda d: d["adversaries"][0]["attacks"][0]["special_damage"].append("heavy_blow"),
        )
        with pytest.raises(ContentError, match="heavy_blow"):
            load_pack(scratch_pack)

    def test_an_adversary_without_an_attack_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack, "adversaries.json", lambda d: d["adversaries"][0].update({"attacks": []})
        )
        with pytest.raises(ContentError, match="should be non-empty") as excinfo:
            load_pack(scratch_pack)
        assert excinfo.value.pointer == "/adversaries/0/attacks"

    def test_a_shadow_path_with_three_steps_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(scratch_pack, "shadow_paths.json", lambda d: d["shadow_paths"][0]["steps"].pop())
        with pytest.raises(ContentError, match="is too short") as excinfo:
            load_pack(scratch_pack)
        assert excinfo.value.pointer == "/shadow_paths/0/steps"

    def test_a_calling_naming_an_unknown_shadow_path_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack,
            "callings.json",
            lambda d: d["callings"][0].update({"shadow_path": "no_such_path"}),
        )
        with pytest.raises(ContentError, match="unknown shadow path"):
            load_pack(scratch_pack)

    def test_a_missing_living_standard_tier_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(scratch_pack, "standards_of_living.json", lambda d: d["tiers"].pop())
        with pytest.raises(ContentError, match="missing"):
            load_pack(scratch_pack)

    def test_descending_thresholds_are_rejected(self, scratch_pack: Path) -> None:
        def scramble(data: dict) -> None:
            data["tiers"][2]["treasure_threshold"] = 500

        rewrite(scratch_pack, "standards_of_living.json", scramble)
        with pytest.raises(ContentError, match="must ascend"):
            load_pack(scratch_pack)

    def test_a_table_with_a_gap_is_rejected(self, scratch_pack: Path) -> None:
        # 05.1.1 check 4, enforced by LookupTable's eager validation.
        path = scratch_pack / "tables" / "wound_severity.json"
        data = json.loads(path.read_text())
        data["rows"] = [r for r in data["rows"] if r["match"] != "eye"]
        path.write_text(json.dumps(data))
        with pytest.raises(ContentError, match="does not cover"):
            load_pack(scratch_pack)

    def test_a_table_with_an_unknown_die_is_rejected(self, scratch_pack: Path) -> None:
        path = scratch_pack / "tables" / "wound_severity.json"
        data = json.loads(path.read_text())
        data["die"] = "d20"
        path.write_text(json.dumps(data))
        with pytest.raises(ContentError, match=r"not one of \['feat', 'success'\]") as excinfo:
            load_pack(scratch_pack)
        assert excinfo.value.pointer == "/die"

    def test_a_duplicate_effect_id_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack,
            "virtues.json",
            lambda d: d["virtues"].append(dict(d["virtues"][0])),
        )
        with pytest.raises(ContentError, match="duplicate effect id"):
            load_pack(scratch_pack)

    def test_an_entry_without_an_id_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(scratch_pack, "weapons.json", lambda d: d["weapons"][0].pop("id"))
        with pytest.raises(ContentError, match="'id' is a required property") as excinfo:
            load_pack(scratch_pack)
        assert excinfo.value.pointer == "/weapons/0"


class TestSchemaValidation:
    """05.1 — pack files are checked against the JSON Schemas in tor/content/schema/.

    The schema owns *shape*: required fields, types, enumerated values, arity, and — new
    with the schemas — rejecting a field the format does not define, which no hand-rolled
    guard ever caught. What a schema cannot express is 05.1.1's referential and semantic
    pass, which runs after.
    """

    def test_a_schema_governs_every_pack_file(self) -> None:
        # A file the loader reads but no schema governs would be validated by nothing.
        for name in (
            "pack.json",
            "cultures.json",
            "callings.json",
            "skills.json",
            "weapons.json",
            "armour.json",
            "shields.json",
            "shadow_paths.json",
            "adversaries.json",
            "patrons.json",
            "standards_of_living.json",
            "undertakings.json",
            "songs.json",
        ):
            assert schema_for(name) is not None, name
        assert schema_for("tables/") == "table.schema.json"
        assert schema_for("names/") == "names.schema.json"
        # The nine effect files share one shape, so the loader passes their schema
        # explicitly rather than deriving it from nine near-identical map entries.
        assert schema_for("virtues.json") is None

    def test_every_shipped_schema_is_itself_valid(self) -> None:
        for file in sorted(SCHEMA_DIR.glob("*.schema.json")):
            Draft202012Validator.check_schema(json.loads(file.read_text()))

    def test_an_undefined_field_is_rejected(self, scratch_pack: Path) -> None:
        # additionalProperties: false. A typo used to be silently ignored.
        rewrite(scratch_pack, "weapons.json", lambda d: d["weapons"][0].update({"dammage": 3}))
        with pytest.raises(ContentError, match="Additional properties are not allowed"):
            load_pack(scratch_pack)

    def test_an_id_that_is_not_snake_case_is_rejected(self, scratch_pack: Path) -> None:
        # 20.11: ids are snake_case ASCII and are the contract between engine and pack.
        rewrite(
            scratch_pack,
            "undertakings.json",
            lambda d: d["undertakings"][0].update({"id": "Gather Rumours"}),
        )
        with pytest.raises(ContentError, match="does not match"):
            load_pack(scratch_pack)

    def test_a_wrongly_typed_field_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(scratch_pack, "weapons.json", lambda d: d["weapons"][0].update({"damage": "lots"}))
        with pytest.raises(ContentError, match="is not of type 'integer'") as excinfo:
            load_pack(scratch_pack)
        assert excinfo.value.pointer == "/weapons/0/damage"

    def test_a_names_file_that_is_not_lists_of_strings_is_rejected(
        self, scratch_pack: Path
    ) -> None:
        path = scratch_pack / "names" / "example_folk.json"
        path.write_text(json.dumps({"male": [1, 2]}))
        with pytest.raises(ContentError, match="is not of type 'string'"):
            load_pack(scratch_pack)


class TestNewContentTypes:
    """The files 05.1 lists that had no reader, and the EffectKinds no file could carry."""

    def test_songs_load(self, pack: ContentPack) -> None:
        # 15.8: a song's kind selects the venture it suits.
        assert {song.kind for song in pack.songs.values()} == {
            "lay",
            "song_of_victory",
            "walking_song",
        }
        assert pack.song("example_lay").title

    def test_an_unknown_song_is_a_content_error(self, pack: ContentPack) -> None:
        with pytest.raises(ContentError, match="no such song"):
            pack.song("no_such_song")

    def test_a_song_of_an_unknown_kind_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(scratch_pack, "songs.json", lambda d: d["songs"][0].update({"kind": "dirge"}))
        with pytest.raises(ContentError, match="'dirge' is not one of"):
            load_pack(scratch_pack)

    def test_skill_display_names_override_but_do_not_invent(self, pack: ContentPack) -> None:
        # 05.1: skills.json is a display-name override only; the grid is engine-fixed.
        assert set(pack.skill_names) <= set(SKILLS)

    def test_renaming_something_that_is_not_a_skill_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(scratch_pack, "skills.json", lambda d: d["skills"].update({"swords": "Blades"}))
        with pytest.raises(ContentError, match="not one of the 18 Skills"):
            load_pack(scratch_pack)

    def test_every_effect_kind_has_a_file_a_pack_can_write_it_in(self, pack: ContentPack) -> None:
        # 19.7: every EffectKind is represented at least once. Four of them — item
        # blessings, curses, conditions and undertaking benefits — previously had no
        # source file at all, so a pack could not author them however it tried.
        assert {definition.kind for definition in pack.effects.values()} == {
            kind.value for kind in EffectKind
        }


class TestReplacementEffects:
    """05.1.1 check 5 — two effects that *replace* one hook value for one item type."""

    def test_the_example_pack_pairs_replacements_by_supersession(self, pack: ContentPack) -> None:
        # Keen and Superior Keen both replace PIERCING_THRESHOLD for a weapon; the
        # 'supersedes' link is what makes that legal (13.7.3 decides per *item*).
        assert pack.effect("superior_keen").supersedes == "keen"

    def test_two_unlinked_replacements_are_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack,
            "rewards.json",
            lambda d: d["rewards"].append(
                {
                    "id": "rival_keen",
                    "name": "Rival Keen",
                    "applies_to": ["weapon"],
                    "factory": "modify_piercing_threshold",
                    "params": {"threshold": 8},
                }
            ),
        )
        with pytest.raises(ContentError, match="both replace PIERCING_THRESHOLD"):
            load_pack(scratch_pack)

    def test_a_supersession_cycle_is_rejected(self, scratch_pack: Path) -> None:
        # keen already declares no supersedes; pointing it back at superior_keen closes
        # the loop, which would otherwise walk forever.
        rewrite(
            scratch_pack,
            "rewards.json",
            lambda d: next(r for r in d["rewards"] if r["id"] == "keen").update(
                {"supersedes": "superior_keen"}
            ),
        )
        with pytest.raises(ContentError, match="'supersedes' cycle"):
            load_pack(scratch_pack)

    def test_superseding_an_unknown_effect_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack,
            "enchanted_rewards.json",
            lambda d: d["enchanted_rewards"][2].update({"supersedes": "no_such_reward"}),
        )
        with pytest.raises(ContentError, match="supersedes unknown effect"):
            load_pack(scratch_pack)


class TestRollSentinel:
    """05.6 — "$roll" means the numeric die result itself."""

    def test_the_sentinel_is_a_marker_not_a_bare_string(self, pack: ContentPack) -> None:
        row = pack.table("endurance_loss").lookup(5)
        assert row["loss"] is Sentinel.ROLL

    def test_the_marker_still_round_trips_as_its_json_text(self) -> None:
        # It is a StrEnum, so a save file keeps reading "$roll".
        assert json.dumps(Sentinel.ROLL) == '"$roll"'

    def test_a_row_without_the_sentinel_is_untouched(self, pack: ContentPack) -> None:
        assert pack.table("endurance_loss").lookup(FeatFace.RUNE)["kind"] == "unscathed"


class TestMerging:
    """05.1 — a supplement may add freely but must declare an override."""

    def test_a_supplement_may_add_entries(self, pack: ContentPack, tmp_path: Path) -> None:
        supplement = ContentPack(
            manifest=Manifest(id="supp", name="Supplement"),
            weapons={"extra_blade": next(iter(pack.weapons.values()))},
        )
        merged = pack.merge(supplement)
        assert "extra_blade" in merged.weapons
        assert len(merged.weapons) == len(pack.weapons) + 1

    def test_an_undeclared_collision_is_rejected(self, pack: ContentPack) -> None:
        existing = next(iter(pack.weapons))
        supplement = ContentPack(
            manifest=Manifest(id="supp", name="Supplement"),
            weapons={existing: pack.weapons[existing]},
        )
        with pytest.raises(ContentError, match="must name it in its manifest"):
            pack.merge(supplement)

    def test_a_declared_override_is_permitted(self, pack: ContentPack) -> None:
        existing = next(iter(pack.weapons))
        supplement = ContentPack(
            manifest=Manifest(id="supp", name="Supplement", overrides=frozenset({existing})),
            weapons={existing: pack.weapons[existing]},
        )
        assert pack.merge(supplement).manifest.id == "supp"

    def test_load_packs_layers_a_supplement_over_a_base(self, tmp_path: Path) -> None:
        supplement = tmp_path / "supplement"
        supplement.mkdir()
        (supplement / "pack.json").write_text(
            json.dumps({"id": "supp", "name": "Example Supplement"})
        )
        # A supplement carries only what it adds; the base supplies the rest.
        (supplement / "weapons.json").write_text(
            json.dumps(
                {
                    "weapons": [
                        {
                            "id": "supplement_blade",
                            "name": "Supplement Blade",
                            "damage": 5,
                            "injury": {"one_handed": 18, "two_handed": None},
                            "load": 2,
                            "proficiency": "swords",
                        }
                    ]
                }
            )
        )
        merged = load_packs([EXAMPLE, supplement])
        assert "supplement_blade" in merged.weapons
        assert "example_blade" in merged.weapons  # the base survives
        assert merged.manifest.id == "supp"

    def test_load_packs_rejects_an_undeclared_collision_between_directories(
        self, scratch_pack: Path
    ) -> None:
        # scratch_pack is a copy of the example pack, so every id collides and it
        # declares no overrides. That is exactly the failure the rule exists to produce.
        with pytest.raises(ContentError, match="must name it in its manifest"):
            load_packs([EXAMPLE, scratch_pack])

    def test_load_packs_needs_at_least_one_path(self) -> None:
        with pytest.raises(ContentError, match="at least one"):
            load_packs([])


class TestLookups:
    def test_lookups_return_the_entity(self, pack: ContentPack) -> None:
        assert pack.culture("example_folk").name == "Example Folk"
        assert pack.calling("example_calling").name == "Example Calling"
        assert pack.weapon("example_blade").damage == 4
        assert pack.effect("hardiness").factory == "numeric_modifier"
        assert pack.adversary("example_minion").might == 1
        assert pack.table("wound_severity").die == "feat"

    def test_an_unknown_id_is_a_content_error(self, pack: ContentPack) -> None:
        with pytest.raises(ContentError, match="no such culture"):
            pack.culture("no_such_folk")

    def test_the_engine_ships_no_content(self, pack: ContentPack) -> None:
        # The point of the whole layer: the 18 Skills and 4 Proficiencies are engine
        # knowledge, everything else comes from the pack.
        assert set(pack.cultures) and set(pack.weapons)
        assert len(SKILLS) == 18 and len(COMBAT_PROFICIENCIES) == 4


class TestPerSectionGuards:
    """The parsing guards each section repeats, and the remaining one-off failures."""

    @pytest.mark.parametrize(
        ("filename", "section"),
        [
            ("cultures.json", "cultures"),
            ("callings.json", "callings"),
            ("armour.json", "armour"),
            ("shields.json", "shields"),
            ("virtues.json", "virtues"),
            ("adversaries.json", "adversaries"),
            ("patrons.json", "patrons"),
            ("shadow_paths.json", "shadow_paths"),
            ("undertakings.json", "undertakings"),
            ("standards_of_living.json", "tiers"),
        ],
    )
    def test_every_section_requires_an_id(
        self, scratch_pack: Path, filename: str, section: str
    ) -> None:
        rewrite(scratch_pack, filename, lambda d: d[section][0].pop("id"))
        with pytest.raises(ContentError, match="'id' is a required property") as excinfo:
            load_pack(scratch_pack)
        assert excinfo.value.pointer == f"/{section}/0"

    def test_a_file_holding_an_array_is_rejected(self, scratch_pack: Path) -> None:
        (scratch_pack / "weapons.json").write_text("[]")
        with pytest.raises(ContentError, match="must hold a JSON object"):
            load_pack(scratch_pack)

    def test_a_section_that_is_not_an_array_is_rejected(self, scratch_pack: Path) -> None:
        (scratch_pack / "weapons.json").write_text(json.dumps({"weapons": {"id": "x"}}))
        with pytest.raises(ContentError, match="is not of type 'array'"):
            load_pack(scratch_pack)

    def test_an_unknown_armour_category_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack, "armour.json", lambda d: d["armour"][0].update({"category": "chitin"})
        )
        with pytest.raises(ContentError, match="'chitin' is not one of"):
            load_pack(scratch_pack)

    def test_an_unknown_standard_of_living_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack,
            "cultures.json",
            lambda d: d["cultures"][0].update({"standard_of_living": "regal"}),
        )
        with pytest.raises(ContentError, match="'regal' is not one of"):
            load_pack(scratch_pack)

    def test_an_unknown_drive_kind_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack,
            "adversaries.json",
            lambda d: d["adversaries"][0]["drive"].update({"kind": "spite"}),
        )
        with pytest.raises(ContentError, match="'spite' is not one of"):
            load_pack(scratch_pack)

    def test_an_adversary_without_might_is_rejected(self, scratch_pack: Path) -> None:
        rewrite(
            scratch_pack, "adversaries.json", lambda d: d["adversaries"][0].update({"might": 0})
        )
        with pytest.raises(ContentError, match="less than the minimum of 1"):
            load_pack(scratch_pack)

    def test_a_culture_offering_too_few_favoured_choices_is_rejected(
        self, scratch_pack: Path
    ) -> None:
        rewrite(
            scratch_pack,
            "cultures.json",
            lambda d: d["cultures"][0].update({"favoured_skill_count": 5}),
        )
        with pytest.raises(ContentError, match="favoured skill choices"):
            load_pack(scratch_pack)

    def test_a_pack_with_no_living_standards_is_rejected(self, scratch_pack: Path) -> None:
        (scratch_pack / "standards_of_living.json").unlink()
        with pytest.raises(ContentError, match="defines no tiers"):
            load_pack(scratch_pack)

    def test_a_malformed_table_file_is_rejected(self, scratch_pack: Path) -> None:
        (scratch_pack / "tables" / "hoards.json").write_text("{oops")
        with pytest.raises(ContentError, match="not valid JSON"):
            load_pack(scratch_pack)

    def test_a_stack_with_no_callings_is_rejected(self, scratch_pack: Path) -> None:
        (scratch_pack / "callings.json").unlink()
        with pytest.raises(ContentError, match="defines no callings"):
            load_pack(scratch_pack)

    def test_a_pack_without_a_manifest_is_rejected(self, scratch_pack: Path) -> None:
        # pack.json is the one file every pack must carry.
        (scratch_pack / "pack.json").unlink()
        with pytest.raises(ContentError, match=r"missing pack\.json"):
            load_pack(scratch_pack)

    def test_load_packs_accepts_a_single_pack(self) -> None:
        assert load_packs([EXAMPLE]).manifest.id == "example"


class TestHookCoverage:
    """19.7: every hook in the registry should have an effect exercising it.

    A hook nothing uses is either dead or unimplemented; the spec asks the test to force
    the question rather than let it go unasked. Most hooks belong to subsystems the build
    order has not reached, so those are listed explicitly below and the list shrinks as
    each subsystem lands. What the test guarantees today is that no hook is quietly
    forgotten: an unreachable one must be *named* here.
    """

    #: Hooks whose consuming subsystem does not exist yet (build steps 7-17).
    #: A hook nothing uses is either dead or unimplemented, and this list forces the
    #: question. Entries come off as soon as an example-pack effect declares the hook —
    #: which can precede the subsystem that dispatches it, as it does for
    #: MODIFY_COUNCIL_ROLL (the Ill-omen curse) and MODIFY_FELLOWSHIP_RATING (the
    #: Strengthen the Fellowship undertaking benefit). Those two exist so the example pack
    #: covers every EffectKind (19.7); council and fellowship land at steps 13 and 15.
    AWAITING_SUBSYSTEM = frozenset(
        {
            # combat (08) — build step 11
            Hook.MODIFY_ATTACK_TN,
            Hook.MODIFY_TARGET_PROTECTION_ROLL,
            Hook.SPECIAL_DAMAGE_OPTIONS,
            Hook.ON_PIERCING_BLOW,
            Hook.ON_KILL,
            Hook.MODIFY_WOUND_SEVERITY_ROLL,
            Hook.ON_WOUND_RECEIVED,
            Hook.ON_ROUND_START,
            Hook.ON_ROUND_END,
            Hook.ON_COMBAT_END,
            Hook.STANCE_OPTIONS,
            Hook.OPENING_VOLLEY_COUNT,
            Hook.MODIFY_ENGAGEMENT,
            Hook.WOUND_INTERCEPT,
            # journey (10) — build step 12
            Hook.MODIFY_FATIGUE_GAIN,
            Hook.MODIFY_JOURNEY_EVENT_ROLL,
            Hook.JOURNEY_ROLE_LIMITS,
            Hook.ON_JOURNEY_END,
            # resources and shadow (07, 11) — build steps 7 and 10
            Hook.MODIFY_SHADOW_GAIN,
            Hook.MODIFY_SHADOW_TEST,
            Hook.MODIFY_SHADOW_FLOOR,
            Hook.MODIFY_SHORT_REST_RECOVERY,
            Hook.MODIFY_PROLONGED_REST_RECOVERY,
            Hook.SHORT_REST_COUNTS_AS_PROLONGED,
            Hook.MODIFY_HOPE_RECOVERY,
            # council (09) — build step 13
            Hook.MODIFY_AUDIENCE_ATTITUDE,
            # progression, fellowship and phase (15, 17) — build steps 15 and 16
            Hook.FREE_UNDERTAKINGS,
            Hook.ON_VALOUR_GAIN,
            Hook.ON_WISDOM_GAIN,
            Hook.ON_PHASE_START,
            Hook.ON_PHASE_END,
            Hook.MODIFY_MOUNT_VIGOUR,
            Hook.MODIFY_USEFUL_ITEM_LIMIT,
            # observers the session and Eye layers wire up — build steps 15 and 16
            Hook.MODIFY_ROLL_RESULT,
            Hook.ON_ROLL_RESOLVED,
        }
    )

    def test_every_hook_is_either_exercised_or_explicitly_pending(self, pack: ContentPack) -> None:
        exercised: set[Hook] = set()
        for definition in pack.effects.values():
            effect = build_effect(
                definition.id, definition.kind, definition.factory, definition.params
            )
            exercised |= set(effect.listeners)

        unaccounted = set(Hook) - exercised - self.AWAITING_SUBSYSTEM
        assert not unaccounted, (
            "these hooks are exercised by no effect in the example pack and are not "
            f"listed as awaiting a subsystem: {sorted(h.value for h in unaccounted)}"
        )

    def test_the_pending_list_holds_no_hook_that_is_already_exercised(
        self, pack: ContentPack
    ) -> None:
        # Keeps the list honest: as a subsystem lands, its entries must come off.
        exercised: set[Hook] = set()
        for definition in pack.effects.values():
            effect = build_effect(
                definition.id, definition.kind, definition.factory, definition.params
            )
            exercised |= set(effect.listeners)
        assert not (exercised & self.AWAITING_SUBSYSTEM)
