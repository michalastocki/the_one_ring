# 16 — Risk, Consequences of Failure, and Sources of Injury

Module: `tor.rules.injury` (a shared leaf), plus the Risk model used by `contest` and by
any Skill roll.

---

## 16.1 When to roll at all

Guidance the engine should surface, because it shapes whether a subsystem is invoked. A
roll is called for only if the action might fail, **and** if the action or its goal falls
into one of three cases:

| Case | Roll if… | Do not roll if… |
|---|---|---|
| **Danger** | the action is dangerous | the hero risks nothing by failing |
| **Knowledge** | the action seeks information not immediately available | the knowledge is not secret or hidden |
| **Manipulation** | the action aims to influence uncooperative LM characters | what is asked matches those characters' own motives |

If the description leaves no doubt about the outcome, the action simply succeeds.

Expose `should_roll(intent) -> RollAdvice` as an advisory helper for a UI; never make it a
gate the engine enforces.

## 16.2 Risk levels

To give Skill rolls an extra layer of consequence, the LM may set a **Risk level**. This is
an LM input, chosen before the roll.

```python
class RiskLevel(StrEnum):
    STANDARD  = "standard"
    HAZARDOUS = "hazardous"
    FOOLISH   = "foolish"
```

| Risk | A failed roll results in… | Character of the action |
|---|---|---|
| **Standard** | a **Simple Failure**, or (at the LM's discretion) **Success with Woe** | performed under limited pressure; the hero has real control over the circumstances |
| **Hazardous** | **Failure with Woe** | something daring, accepting the risk of serious negative consequences |
| **Foolish** | **Disaster!** | grievous negative consequences are near-certain on a failure |

**The LM must warn the players** when setting a level above Standard, describing what makes
the action particularly hazardous, so the players can see the potential consequences before
committing. Model this as a required acknowledgement:

```python
@dataclass(frozen=True, slots=True)
class RiskDeclaration:
    level: RiskLevel
    warning: str                  # required when level is not STANDARD
    acknowledged: bool            # the players saw the warning
```

`resolve_risky_roll` raises `RuleViolation` if a non-Standard level arrives without a
warning and acknowledgement. The engine cannot judge fairness, but it can refuse to let the
warning step be skipped silently.

### 16.2.1 The four consequence shapes

**Simple Failure.** The action does not achieve its objective. Nothing more.

**Success with Woe.** Offered as a *choice* at Standard risk: the hero succeeds, but at the
cost of an unexpected inconvenience that devalues the performance or carries a negative
side effect. Characteristic examples: a hero clears a chasm but drops a weapon or shield; a
sleeping dragon is not roused but shifts to block the exit; the intricate lock opens but the
key is stuck fast.

Because it is a choice, `resolve_risky_roll` returns both branches and the caller commits:

```python
@dataclass(frozen=True, slots=True)
class RiskOutcome:
    roll: RollResult
    shape: FailureShape           # SIMPLE | WOE_OFFERED | FAILURE_WITH_WOE | DISASTER
    woe_available: bool
```

**Failure with Woe.** The hero fails, **and** a serious negative event adds a new concern
needing attention. Characteristic examples: escaping prey wails and risks attracting
something more dangerous; a guard spots the hiding hero and runs to raise an alarm; the
ruffians the hero meant to intimidate draw their blades instead.

**Disaster!** The attempt not only fails but causes a grievous negative event, and **the
negative effects cannot be prevented** — unlike Failure with Woe, there is no mitigation.
Characteristic examples: a hero climbing a tree to spy on an Orc camp falls into the middle
of them; a purse lifted from a troll's pocket sounds a warning and the hero is caught.

## 16.3 Risk inside Skill Endeavours

The same three levels grade the consequence of each **individual failed roll** during a
Skill Endeavour (`09.3.2`):

| Shape | Effect on the endeavour |
|---|---|
| Simple failure | Normally only a **delay**. It may take longer, but resolution continues. |
| Failure with Woe | A negative consequence occurs — a short fall costing Endurance, smoke inhaled while fighting a fire. Resolution continues. |
| Disaster! | The endeavour **fails completely and cannot be resumed**. |

Wire the third to `ResistanceContest.abort()`.

## 16.4 Sources of injury

Not all peril comes from visible adversaries. Drowning in a frigid lake, breathing noxious
fumes from an ancient tomb, a raging fire, a long fall — any can kill an adventurer.

Heroes often take damage from a source of injury as a result of **failing a roll** — an
ATHLETICS roll while climbing or swimming. Other times it depends purely on circumstance —
exposure to frigid winds without proper garments.

### 16.4.1 Endurance loss levels

Harm outside combat is graded in three levels, and the level sets the **Feat die policy**
for the loss roll — pattern P10 again:

| Loss level | Feat die policy |
|---|---|
| Moderate | **Favoured** |
| Severe | normal |
| Grievous | **Ill-favoured** |

Then read the Feat result on the Endurance Loss table:

| Feat result | Hero is… | Effect |
|---|---|---|
| the eye | **Knocked out** | reduced to zero Endurance |
| 1–10 | **Bruised** | loses Endurance equal to the numeric result |
| the rune | **Unscathed** | unharmed |

```python
def roll_endurance_loss(level: LossLevel, rng: Randomness) -> EnduranceLossOutcome:
    policy = {LossLevel.MODERATE: FeatDicePolicy.FAVOURED,
              LossLevel.SEVERE:   FeatDicePolicy.NORMAL,
              LossLevel.GRIEVOUS: FeatDicePolicy.ILL_FAVOURED}[level]
    result = resolve(RollRequest(ability=None, rating=0, target_number=None,
                                 **policy_sources(policy)), rng)
    return ENDURANCE_LOSS_TABLE.lookup(result.kept_feat)
```

Note the Favoured/Ill-favoured direction: a **Moderate** source is Favoured, meaning it
tends toward the rune and thus toward being unscathed. Getting this backwards makes minor
hazards lethal.

### 16.4.2 Bridging Risk to damage

The two systems connect explicitly:

| Roll outcome | May cause |
|---|---|
| A failure, or a Success with Woe | a **moderate** loss of Endurance |
| A Failure with Woe | a **severe** loss of Endurance |
| A Disaster | a **grievous** loss of Endurance |

```python
LOSS_FOR_SHAPE: Mapping[FailureShape, LossLevel]
```

This mapping is the whole reason both live in one module.

### 16.4.3 The sources table

Each source inflicts damage differently, and the grade depends on intensity and exposure.
Content data (`sources_of_injury.json`); the structural fields the engine needs are the
**cadence** (how often the roll repeats) and the **zero-Endurance consequence**, which
differ per source:

| Source | Cadence | At zero Endurance the hero is… |
|---|---|---|
| Extreme cold | roll each **half hour** | **Dying** |
| Falling | once | **Wounded** |
| Fire | roll each **round** | also **Wounded** |
| Suffocation | roll each **round** | **Dying** |
| Poison | roll each **day** | **Dying** |

Illustrative gradations, from moderate through severe to grievous: chilling winds / deep
snow / frigid waters; a short fall of up to about ten feet or a soft landing / a long fall
of up to about thirty feet or a hard landing / a deadly fall from a great height or onto a
dangerous surface; a torch flame or campfire / a brazier or burning house / a funeral pyre
or dragon-fire; choking fumes / drowning / strangulation; food poisoning / a snake-bite or
orc-poison / spider-poison.

```json
{"sources": [{
  "id": "example_hazard",
  "cadence": "round",
  "zero_endurance": "wounded",
  "grades": {"moderate": "...", "severe": "...", "grievous": "..."}
}]}
```

`zero_endurance` ∈ `wounded` | `dying` | `unconscious`. The default at zero Endurance is
unconsciousness (`07.2`); these sources override it, and the override is the mechanically
significant part.

### 16.4.4 Fatal injuries

> If heroes suffer an incident that should in all likelihood prove fatal, they **die
> instantly** when Wounded or when they gain the Dying condition.

Characteristic circumstances: falling from an extreme height onto rocky ground, being
trapped under a burning structure, drowning in freezing water.

This is an LM declaration, not a computed property:

```python
@dataclass(frozen=True, slots=True)
class InjuryContext:
    source: InjurySourceId
    level: LossLevel
    fatal: bool = False        # LM declares the circumstance plainly lethal
```

When `fatal` is set, `apply_injury` converts a Wound or a Dying result directly to death.
There is no HEALING window.

### 16.4.5 Poison

Poison has its own sub-rules, distinct from the general cadence:

* A poisoned hero **cannot rest**.
* They roll for the corresponding Endurance loss **at the end of each day**.
* **If the roll produces a rune**, the hero takes no damage **and is no longer poisoned**.
  This is the natural recovery path and follows from the table's "Unscathed" row, but the
  cure is specific to poison — do not generalise it.
* A successful **HEALING** roll made at the **start of a day** also removes the poison. That
  roll **loses `(1d)` if the poison is Severe** and **loses `(2d)` if Grievous**.

```python
def daily_poison_check(hero: Hero, poison: PoisonState, rng) -> PoisonOutcome:
    outcome = roll_endurance_loss(poison.level, rng)
    if outcome.kind is LossKind.UNSCATHED:
        return PoisonOutcome(cured=True, loss=0)
    ...
```

At least one adversary ability applies poison as a rider on a Wound; it plugs in here.

## 16.5 Consequences of failure — general guidance

Worth exposing to an LM-facing UI, since the engine cannot supply it:

Failing normally means the hero did not obtain what they wanted. But circumstances vary —
most situations are tranquil enough that a failure is no disaster, while some are adverse
enough that failure means something bad has happened. The Risk level is the tool for saying
which.

A hero given a second chance must always cope with the consequences of having failed the
first time (`02.3.9`).
