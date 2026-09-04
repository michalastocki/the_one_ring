# 17 — Session, Campaign, and Persistence

Module: `tor.session`.

The imperative shell. Owns the clock, the phase state machine, the event log, and the save
file. Everything below it is pure.

---

## 17.1 The phase state machine

```
Campaign
 └── AdventuringPhase        (2–3 sessions typical)
      └── Session            (~3 hours typical)
           └── Scene         (Combat | Council | Journey | Free | Endeavour)
 └── FellowshipPhase
      └── (Yule every ~3rd Fellowship Phase)
```

```python
class PhaseKind(StrEnum):
    ADVENTURING = "adventuring"
    FELLOWSHIP  = "fellowship"

@dataclass(slots=True)
class Campaign:
    year: int
    season: Season
    day: int                              # day within the campaign calendar
    phase: PhaseKind
    phase_index: int
    fellowship_phases_since_yule: int
    heroes: dict[HeroId, Hero]
    company: Company
    eye: EyeState | None                  # None when the subsystem is disabled
    treasure_index: TreasureIndex
    options: CampaignOptions
    pending_modifiers: list[PendingModifier]
    log: EventLog
```

`pending_modifiers` is the mailbox that lets one subsystem affect another without importing
it (`01.1`) — the Revelation episode that stiffens the next Council (`14.6`) writes here;
`council.start()` reads and consumes.

### 17.1.1 Phase transitions and their side effects

Each boundary fires a fixed list of actions. Implement as one function per boundary, each
returning events; never scatter these across subsystems.

**Session start** — nothing mechanical. The LM sets when, where, what, and why. If it is the
first session of an Adventuring Phase these frame an opening scene; otherwise the opening
scene resumes from where the last session broke off.

**Session end:**
* Award **3 Skill points and 3 Adventure points** to each hero who attended (or the
  per-hour equivalent under `AdvancementOptions`).
* **Refresh the Fellowship pool to its maximum.** This is a *session* boundary, not a phase
  boundary — a distinct and easily missed detail.
* Reset per-session usage budgets.

**Adventuring Phase start:**
* Recalculate **starting Eye Awareness** (`14.2`) and set current Awareness to it.
* Reset all Company songs to unspent (`15.8`).
* Reset per-phase usage budgets.

**Adventuring Phase end:**
* **Retire any hero whose Shadow matches their maximum Hope** — a bout of madness must
  occur during the phase, so ending it in that state means the hero has left the Company
  (`11.6`).
* Suspend Eye Awareness tallying (`14.7`).

**Fellowship Phase start:**
* Determine whether it is Yule (`fellowship_phases_since_yule >= 2`, or an explicit LM
  declaration — the "roughly every third" cadence is guidance, not a hard rule).

**Fellowship Phase end:**
* Expire modifiers scoped to the phase — the Strengthen Fellowship bonus, the Ponder
  Storied and Figured Maps journey bonus.
* If Yule: `advance_year()` (`15.7`).
* Reset per-phase usage budgets.

```python
class PhaseController:
    def start_session(self, attendees: Sequence[HeroId]) -> list[Event]: ...
    def end_session(self) -> list[Event]: ...
    def begin_adventuring_phase(self) -> list[Event]: ...
    def end_adventuring_phase(self) -> list[Event]: ...
    def begin_fellowship_phase(self, *, yule: bool | None = None) -> list[Event]: ...
    def end_fellowship_phase(self) -> list[Event]: ...
```

## 17.2 Scenes

```python
@dataclass(slots=True)
class Scene:
    kind: SceneKind                  # COMBAT | COUNCIL | JOURNEY | ENDEAVOUR | FREE
    environment: Environment
    state: Any | None                # CombatState | ResistanceContest | Journey | None
    attempt_ledger: AttemptLedger
```

Only one scene is active at a time. The scene owns the `Environment` that effects predicate
on (`04.4`), and the `AttemptLedger` that governs repeating rolls (`02.3.9`), which is
discarded when the scene closes.

Scene transitions reset `UsageScope.SCENE` budgets and, for combat, `UsageScope.COMBAT`.

## 17.3 The clock

Several mechanics need real elapsed time, so the campaign carries a day counter:

| Consumer | Need |
|---|---|
| Severe injuries | days until the injury mends (`07.7`) |
| Dying heroes | a ~1 hour window for a HEALING roll (`08.9`) |
| First Aid | a failed HEALING roll cannot be retried for at least a day |
| Poison | a roll at the end of each day (`16.4.5`) |
| Extreme cold / fire / suffocation | rolls per half hour or per round |
| Journeys | length in days (`10.7`) |
| Fatigue shedding | one point per prolonged rest in a sheltered refuge |
| Yule | ages every hero one year |

```python
@dataclass(slots=True)
class Clock:
    day: int
    minutes: int = 0                 # within the day, for sub-day tracking

    def advance(self, *, days: int = 0, hours: int = 0, minutes: int = 0) -> list[Timer]:
        """Returns any timers that fired."""

@dataclass(slots=True)
class Timer:
    kind: TimerKind                  # INJURY_MEND | DYING | FIRST_AID_COOLDOWN | POISON
    subject: HeroId
    fires_at: tuple[int, int]        # (day, minutes)
```

Timers are the mechanism by which a Dying hero left untended actually dies — the engine
must not rely on someone remembering to check. Every `advance()` returns fired timers and
the caller must resolve them before continuing.

Combat rounds are reckoned at a **maximum of 30 seconds** each; a short rest at
**at least one hour**.

## 17.4 The event log

Every state change emits an event. The log is the source of truth for replay, undo, and
the session transcript.

```python
@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    timestamp: tuple[int, int]        # campaign (day, minutes)
    kind: EventKind
    actor: HeroId | AdversaryInstanceId | None
    payload: Mapping[str, Any]
    rolls: tuple[RollResult, ...] = ()
    gm_only: bool = False
```

`gm_only` marks information the players must not see: an item's secret lifting condition
(`13.8`), dormant Famous item qualities (`13.7.4`), the Treasure Index contents, a council's
Resistance before it is revealed, an adversary's remaining drive. The persistence layer and
any UI must honour it.

```python
class EventLog:
    def append(self, event: Event) -> None: ...
    def replay(self, into: Campaign, until: int | None = None) -> Campaign: ...
    def redact(self, *, for_players: bool) -> Iterator[Event]: ...
```

**Replay guarantee.** Given the same content packs, the same seed, and the same sequence of
*decisions*, replaying the log must reproduce the campaign state byte-for-byte. This is why
`04.2.1` demands deterministic effect ordering and why `resolve()` takes an injected
`Randomness`. Test it (`19.6`).

To make replay work, log **decisions** as well as outcomes: which set of Attributes was
picked, which stance was chosen, how icons were spent, what Resistance the LM set, whether
Success with Woe was accepted. An outcome-only log cannot be replayed.

## 17.5 Persistence

```python
def save(campaign: Campaign, path: Path) -> None: ...
def load(path: Path, packs: Sequence[Path]) -> Campaign: ...
```

Format: JSON, one file, with a `schema_version` at the root. Requirements:

1. **Content is referenced, not embedded.** The save records pack ids and versions; the
   packs are loaded separately. A save that references an unavailable pack fails loudly at
   load with a `ContentError` naming what is missing.
2. **Derived values are not stored.** Max Endurance, Parry, Load, Standard of Living, Weary
   and Miserable flags are all recomputed on load. Storing them invites drift. Store only:
   current Endurance, current Hope, Shadow, Shadow Scars, Fatigue, injury days, Treasure,
   ratings, and the effect list with its `params`.
3. **The event log is included** and is what makes the save auditable. Offer
   `save(compact=True)` which truncates the log to the current phase for size.
4. **GM-only fields are segregated** into a `gm` sub-object so a players' copy can be
   produced by dropping one key.
5. **Migration.** Ship `migrations/` with one function per schema version bump. `load()`
   applies them in order. Never silently accept an unknown version.

```python
MIGRATIONS: dict[int, Callable[[dict], dict]]

def migrate(raw: dict) -> dict:
    while raw["schema_version"] < CURRENT_SCHEMA_VERSION:
        raw = MIGRATIONS[raw["schema_version"]](raw)
    return raw
```

## 17.6 Campaign options

```python
@dataclass(frozen=True, slots=True)
class CampaignOptions:
    short_campaign_tns: bool = False       # derive Attribute TNs from 18 instead of 20
    eye_of_mordor_enabled: bool = False    # the Eye subsystem is explicitly optional
    advancement: AdvancementOptions = AdvancementOptions()
    adversary_icon_inversion: bool = True  # swap Feat icons for servants of the Shadow
    warn_on_misdeed: bool = True           # prompt before a Misdeed is committed
```

Every one of these corresponds to an option the rules state outright. Do not add
house-rule toggles beyond them without a matching spec entry — an options bag is where
engines go to rot.

## 17.7 Concurrency and reentrancy

The engine is **single-threaded and synchronous**. No subsystem may call back into the
session layer. If a rule needs a player or LM decision mid-resolution — spending Hope,
choosing knockback, accepting Success with Woe, allocating icons — the resolution function
**returns a request for that decision** rather than blocking:

```python
@dataclass(frozen=True, slots=True)
class DecisionRequired:
    kind: DecisionKind
    prompt: str
    options: tuple[DecisionOption, ...]
    context: Mapping[str, Any]

Resolution = Outcome | DecisionRequired
```

The caller answers and re-enters. This keeps the pure core free of I/O and makes every
decision point a loggable, replayable event. It is more work than a callback and it is what
makes replay possible at all.
