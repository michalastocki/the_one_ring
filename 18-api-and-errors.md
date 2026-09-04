# 18 — Public API, CLI, and Errors

Modules: `tor.api`, `tor.cli`.

---

## 18.1 Error model

Three families, and nothing else. Every raise must be one of these.

```python
class TorError(Exception):
    """Base."""

class ContentError(TorError):
    """A pack is malformed or references a missing id.
    Raised only at load time — never during play."""
    file: Path
    pointer: str          # JSON pointer to the offending node
    entity_id: str | None

class RuleViolation(TorError):
    """An action is illegal by the rules of the game."""
    rule_reference: str   # names the mechanic, e.g. "rearward_stance_requirements"
    detail: str
    suggestion: str | None = None

class StateError(TorError):
    """The engine was driven into an impossible state. Indicates a caller bug."""
```

**Never raise for a legal but unlucky outcome.** Failing a roll, losing a council, dying —
all are results, not errors. If you find yourself catching an exception to handle a game
outcome, the design is wrong.

`RuleViolation.rule_reference` should name the mechanic in a way that maps to this spec, so
a UI can link to the relevant section and a player can see *why* the engine refused.
`suggestion` carries the remedy where there is an obvious one ("two other heroes must be
in a close combat stance").

**Collect, don't short-circuit, where several things are wrong at once.** Validating a
Famous item (`13.7.3`) or a set of undertakings (`15.6`) should report every breach, not
just the first:

```python
class RuleViolationGroup(RuleViolation):
    violations: tuple[RuleViolation, ...]
```

## 18.2 Warnings

Some situations are legal but probably unintended: an age outside a culture's typical range,
a Combat Proficiency no allowed weapon uses, a Calling re-marking an already-Favoured Skill,
a journey longer than 20 hexes that should be split into legs.

```python
@dataclass(frozen=True, slots=True)
class Warning_:
    code: str
    message: str
```

Return warnings alongside results; never raise them, never log-and-forget them. A UI that
suppresses them is failing the player.

## 18.3 The facade

`tor.api` is a thin, stable surface over the subsystems, for embedding in a UI or bot. It
adds no rules.

```python
class TorEngine:
    def __init__(self, packs: Sequence[Path], *, seed: int | None = None,
                 options: CampaignOptions | None = None) -> None: ...

    # --- campaign ---
    def new_campaign(self, year: int, season: Season) -> Campaign: ...
    def load(self, path: Path) -> Campaign: ...
    def save(self, path: Path, *, compact: bool = False) -> None: ...

    # --- creation ---
    def begin_hero(self) -> HeroDraft: ...
    def advance_draft(self, draft: HeroDraft, choice: Any) -> tuple[HeroDraft, list[Warning_]]: ...
    def finish_hero(self, draft: HeroDraft) -> Hero: ...
    def form_company(self, heroes: Sequence[Hero], choice: CompanyChoice) -> Company: ...

    # --- rolling ---
    def roll(self, hero: HeroId, ability: AbilityId, *,
             tn: int | None = None, spend_hope: bool = False,
             support: SupportInput | None = None,
             bonus: int = 0, penalty: int = 0,
             risk: RiskDeclaration | None = None) -> Resolution: ...

    # --- scenes ---
    def begin_combat(self, setup: CombatSetup) -> CombatState: ...
    def combat_round(self, inputs: RoundInputs) -> Resolution: ...
    def begin_council(self, setup: CouncilSetup) -> ResistanceContest: ...
    def council_attempt(self, attempt: CouncilAttempt) -> Resolution: ...
    def begin_journey(self, setup: JourneySetup) -> Journey: ...
    def journey_step(self) -> Resolution: ...
    def begin_endeavour(self, setup: EndeavourSetup) -> ResistanceContest: ...

    # --- other subsystems ---
    def gain_shadow(self, hero: HeroId, points: int, source: ShadowSource,
                    *, test: bool = True) -> Resolution: ...
    def find_hoard(self, grade: HoardGrade) -> HoardOutcome: ...
    def register_magic(self, level: MagicLevel, *, conspicuous: bool = True) -> int: ...

    # --- phases ---
    @property
    def phases(self) -> PhaseController: ...
    def fellowship_phase(self, setup: FellowshipSetup) -> FellowshipOutcome: ...
```

Every method returning `Resolution` may return a `DecisionRequired` instead of an outcome
(`17.7`). The caller answers with:

```python
    def decide(self, decision_id: str, answer: Any) -> Resolution: ...
```

## 18.4 CLI

`tor.cli` is a reference client — a way to exercise the engine end to end and a working
example for anyone embedding it. It is not the product.

```
tor content validate <pack>...          # validate packs, print every error and warning
tor content list <pack> <kind>          # enumerate cultures, virtues, adversaries, ...

tor hero new --pack <p> [--interactive] # walk the creation pipeline
tor hero show <save> <hero>             # render a character sheet
tor hero advance <save> <hero> ...      # spend XP during a Fellowship Phase

tor roll <save> <hero> <ability> [--tn N] [--hope] [--favoured] [--bonus N] [--penalty N]
tor combat ...                          # interactive combat driver
tor council ...
tor journey ...

tor campaign new|save|load|status
tor campaign phase start|end
tor log show <save> [--player]          # transcript, honouring gm_only
tor log replay <save> [--until N]       # verify the replay guarantee
```

Requirements:

* `--seed` on any command that rolls, for reproducibility.
* `--json` on every command, emitting the result object verbatim, so the CLI is scriptable
  and testable.
* Rolls print their full derivation: dice shown, policy, why it was Favoured, which
  modifiers applied and from which effect. A player must be able to audit any number the
  engine produces. This is the single most valuable thing the CLI does.

Example of the required roll output detail:

```
Hunting (rating 2) vs STRENGTH TN 13          FAVOURED
  favoured by: culture_blessing:example_blessing
  dice: Feat [3, 8] → kept 8 | Success [4, 5]
  modifiers: +1d (hope), -1d (hard terrain) → 2 Success dice
  total: 8 + 4 + 5 = 17  ≥ 13   SUCCESS (0 icons)
```

## 18.5 Rendering a character sheet

The engine must be able to produce every value on the printed sheet, since that is the
practical test of completeness. `api.sheet(hero) -> Sheet` returns a structure covering:

Heroic Culture, Cultural Blessing, Calling, Shadow Path, name, age, Standard of Living,
Treasure; the three Attributes with their ratings and TNs; Endurance (max and current),
Hope (max and current), Parry with the shield bonus shown separately; all 18 Skills with
ratings and Favoured marks; the 4 Combat Proficiencies; Distinctive Features; Flaws;
Rewards and Virtues; VALOUR and WISDOM; Adventure points, Skill points, Fellowship score;
Load, Shadow, Shadow Scars, Fatigue; the three condition boxes and the Injury count; war
gear with Damage, Injury, Load and notes; armour and helm Protection; shield Parry;
travelling gear and useful items.

If any of those cannot be produced from engine state, something in the model is missing.
Use this as an acceptance checklist.

## 18.6 Versioning

Semantic versioning on the package. The API surface in `18.3`, the save schema (`17.5`),
and the content schemas (`05`) are the three public contracts. Breaking any of them is a
major bump; each needs its own version number and its own migration path.
