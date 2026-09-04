# 10 — Journey

Module: `tor.rules.journey`.

Journeys are resolved on a **hex path**, not a distance in miles. The engine models the
path as an ordered list of hexes with terrain and region annotations; the actual map is
content or campaign data.

---

## 10.1 State

```python
class TerrainKind(StrEnum):
    EASY = "easy"          # plains, open country
    HARD = "hard"          # hills, woods, marshes, and similar
    ROAD = "road"          # a road runs through this hex

@dataclass(frozen=True, slots=True)
class Hex:
    index: int                       # 0-based position along the path
    terrain: TerrainKind
    region: RegionType               # BORDER | WILD | DARK
    has_road: bool = False
    perilous_area: PerilousArea | None = None
    landmark: str | None = None

@dataclass(frozen=True, slots=True)
class PerilousArea:
    id: str
    name: str
    peril: int                       # number of Events that must be faced
    event_table: str | None = None   # optional area-specific event table

@dataclass(slots=True)
class Journey:
    origin: str
    destination: str
    path: tuple[Hex, ...]            # excludes the starting hex
    season: Season
    roles: dict[HeroId, set[JourneyRole]]
    mounts: dict[HeroId, Mount]
    position: int = 0                # hexes travelled
    events_resolved: list[JourneyEventRecord] = field(default_factory=list)
    day_adjustments: int = 0         # from Mishap / Short Cut
    forced_march: bool = False
    mounted: bool = False
    finished: bool = False
```

**Path length excludes the starting hex.** Off-by-one here corrupts every marching test.

The path drawn on the log need not follow the shape the players traced on the map — the
only requirement is that it contain the **same number of hexes**. That is a table-play
convenience; the engine only ever sees the hex list.

**Long journeys** of more than 20 hexes should be split into legs, each treated as a
separate journey. Enforce with a warning at journey construction, not an error.

## 10.2 Roles

Four roles. Every journey must cover all four.

| Role | Function | Challenged Skill |
|---|---|---|
| Guide | route, rest, and supply decisions | TRAVEL (Marching Tests) |
| Hunter | finding food in the wild | HUNTING |
| Look-out | keeping watch | AWARENESS |
| Scout | making camp, opening new trails | EXPLORE |

Rules:
* **Exactly one Guide.** Never more.
* Any number of Hunters, Look-outs, and Scouts.
* With fewer than four heroes, some must cover multiple roles.
* With more than four, several may share a role.

`JOURNEY_ROLE_LIMITS` lets an effect relax this — one Cultural Virtue always permits its
holder to cover more than one role.

```python
def validate_roles(assignment: Mapping[HeroId, set[JourneyRole]], bus: EffectBus) -> None:
    """Raises RuleViolation on: no Guide, multiple Guides, an uncovered role, or a hero
    holding multiple roles without permission."""
```

## 10.3 Sequence

```
1. Set Journey Path
2. Make Marching Tests   (repeat until the destination is reached)
3. End the Journey
```

### 10.3.1 Marching Test

The **Guide** rolls **TRAVEL**. The result determines how far the Company travels before
the next event.

```python
def marching_distance(roll: RollResult, season: Season) -> int:
    if roll.succeeded:
        return 3 + roll.icons                     # pattern P3 with base 3
    return 2 if season in (Season.SPRING, Season.SUMMER) else 1
```

A success carries the Company **3 hexes plus 1 per Success icon**. A failure carries them
only **2 hexes in Spring or Summer, 1 hex in Autumn or Winter**.

Count that many hexes forward along the path from the current position; the hex reached is
where the event occurs.

### 10.3.2 Ending the journey

**The journey ends when the marching distance matches or exceeds the number of hexes
remaining** between the Company's position and the destination. When that happens no event
occurs — the Company simply arrives.

```python
def step(journey: Journey, roll: RollResult) -> JourneyStep:
    distance  = marching_distance(roll, journey.season)
    remaining = len(journey.path) - journey.position
    if distance >= remaining:
        journey.position = len(journey.path)
        journey.finished = True
        return JourneyStep(arrived=True)
    journey.position += distance
    return JourneyStep(arrived=False, event_hex=journey.path[journey.position - 1])
```

A journey may also end because an unexpected occurrence draws the Company into a different
activity for a significant time. That is an LM ruling, exposed as
`journey.interrupt(reason)`.

### 10.3.3 Perilous areas

Some map areas are not hexed at all: thick woodland, steep passes, treacherous marshes.
Each has a **Peril rating**.

When a marching roll would carry the Company into or across a Perilous Area:

1. The Company **stops** in the area as soon as it enters.
2. Before leaving, it must face a number of Events equal to the **Peril rating**. All
   normal event rules apply.
3. Marching Tests then resume from the **first hex along the path outside the area's
   boundary**.

An area may supply its own event table; if so, use it instead of the standard table for
those events. This is why `LookupTable` selection must be a parameter to event resolution
and not a module constant.

## 10.4 Event resolution

```
1. Select Targets
2. Determine Event
3. Resolve the Event
```

### 10.4.1 Select targets

Roll one **Success die**:

| Success die | Target role | Skill challenged |
|---|---|---|
| 1–2 | Scouts | EXPLORE |
| 3–4 | Look-outs | AWARENESS |
| 5–6 | Hunters | HUNTING |

Note the Guide is never a target of this table — the Guide's contribution is the Marching
Test itself.

### 10.4.2 Determine the event

Roll a **Feat die**, with the policy set by the region the event occurs in:

| Region | Feat die policy |
|---|---|
| Border Land | Favoured |
| Wild Land | normal |
| Dark Land | Ill-favoured |

Pattern P10 — reuse `FeatDicePolicy`; do not write a bespoke branch.

Modifiers to this roll:
* The *Ponder Storied and Figured Maps* undertaking applies a **+1 shift to the Feat die
  result** until the next Fellowship Phase. Implement with `LookupTable.lookup(shift=+1)`
  (`02.2`), which leaves icon faces untouched.
* One Cultural Virtue makes events targeting its holder resolve **as if in a Border
  Land** — i.e. Favoured — regardless of the actual region.
* A Patron benefit does the same for a defined geographic area.

All flow through `MODIFY_JOURNEY_EVENT_ROLL`.

### 10.4.3 The events table

Look up the Feat result. Each row gives an event, a Skill-roll consequence, and a Fatigue
cost paid by **everyone in the Company**.

| Feat result | Event | Consequence of the Skill roll | Fatigue |
|---|---|---|---|
| the eye | Terrible Misfortune | On failure, the target is **Wounded** | 3 |
| 1 | Despair | On failure, **everyone** in the Company gains 1 Shadow (Dread) | 2 |
| 2–3 | Ill Choices | On failure, the target gains 1 Shadow (Dread) | 2 |
| 4–7 | Mishap | On failure, add 1 day to the journey and the target gains 1 extra Fatigue | 2 |
| 8–9 | Short Cut | On **success**, reduce the journey by 1 day | 1 |
| 10 | Chance-meeting | On **success**, no Fatigue is gained and the LM improvises an encounter favouring the Company | 1 |
| the rune | Joyful Sight | On **success**, everyone in the Company regains 1 Hope | — |

Two structural points:

* Rows differ in whether the consequence triggers on **failure** or on **success**. The
  declarative `on_failure` / `on_success` op lists (`05.6`) handle this; do not write a
  per-row branch.
* The Fatigue cost is paid regardless of the roll, **except** where an op explicitly
  suppresses it (Chance-meeting on a success).

### 10.4.4 Resolving the event

One hero among those holding the targeted role makes the Skill roll. **Up to one other
hero covering the same role may support** it (`02.3.3`).

Situational modifiers on that roll:
* Event in a **hard terrain** hex ⇒ `−1d`.
* Event **along a road** ⇒ `+1d`.

These are terrain properties of the specific event hex, not of the journey.

Consequences naming "the target" apply to the hero who made the roll — and to the
supporting hero as well, where relevant.

## 10.5 Fatigue

Fatigue is a deeper weariness accrued while travelling that manifests fully once the
journey ends.

* Each Fatigue point **raises the hero's total Load by 1** (`07.4`), making Weariness more
  likely.
* **Fatigue cannot be shed while the journey lasts.**
* It passes through `MODIFY_FATIGUE_GAIN` — one Cultural Virtue reduces each event's
  Fatigue by 1; another prevents Fatigue entirely while wearing light or no armour and
  carrying no shield.

## 10.6 Ending a journey

In order:

1. **Mount reduction.** Each hero with a mount reduces their Fatigue by the mount's
   **Vigour** rating.
2. **TRAVEL roll.** Each hero may further reduce Fatigue by **1, plus 1 per Success icon**
   (pattern P3).
3. **Residual Fatigue** is recorded on the character sheet and is shed at **1 point per
   prolonged rest taken in a sheltered, safe refuge** — explicitly not while on the road.

```python
def end_journey(journey: Journey, heroes: Sequence[Hero], rng) -> JourneyEndOutcome:
```

## 10.7 Journey length in days

Computed only if the group wants to know:

```python
def journey_days(journey: Journey) -> int:
    base = sum(1 for h in journey.path if h.terrain is TerrainKind.HARD)
    days = len(journey.path) + base          # 1 day per hex, +1 more for each hard hex
    if journey.mounted:
        days = ceil(days / 2)
    if journey.forced_march:
        days = ceil(len(journey.path) / 2) + base   # one day per two hexes
    return days + journey.day_adjustments
```

* **Base**: one day per hex, plus **one additional day for each hex of hard terrain**.
* **Mounted**: if the *entire* Company travels on horseback, **halve the total, rounding
  up**. The LM should note that riding is normally only possible along roads and good
  paths, and some regions — heavy woodland — do not allow it at all.
* **Forced march**: count **one day per two hexes** instead of one per hex, but **each hero
  gains 1 additional Fatigue per day of forced march**.
* `day_adjustments` accumulates the ±1 day results from Mishap and Short Cut events.

Mounted and forced-march are not additive in an obvious way; treat forced march as
replacing the base rate and apply the mounted halving afterwards only if both are declared.
Flag the combination for LM adjudication rather than guessing.

## 10.8 When not to use these rules

Worth exposing in the UI: the journey rules are not meant for every trip. They should
rarely be used for the journey home, when the Company is heading for a safe haven where
they can rest without concern, or when travelling without any time pressure. Play out the
return only when it makes a good closing act, or when arriving was only half the mission.

The engine offers `journey.narrate_only()` which records origin, destination, and elapsed
days without any rolls.
