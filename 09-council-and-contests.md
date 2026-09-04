# 09 — The Resistance Contest, Councils, and Skill Endeavours

Modules: `tor.rules.contest` (shared leaf), `tor.rules.council`, `tor.rules.endeavour`.

Councils and Skill Endeavours are the same machine with different dressing. Implement the
machine once (`contest`), then two thin adapters. If `council.py` exceeds ~150 lines you
have duplicated the engine.

---

## 9.1 The shared machine

Both mechanics say: *accumulate a number of successful rolls matching or exceeding a
Resistance value, within a limited number of attempts.*

```python
class ResistanceLevel(IntEnum):
    LOW    = 3
    MEDIUM = 6
    HIGH   = 9
```

The two subsystems label these differently — a council's requests are graded reasonable /
bold / outrageous, an endeavour's goals simple / laborious / daunting — but the numbers are
identical. Provide display-name mappings in each adapter; keep one enum.

```python
@dataclass(slots=True)
class ResistanceContest:
    resistance: int
    attempts_allowed: int
    successes: int = 0
    attempts_used: int = 0
    attempt_log: list[ContestAttempt] = field(default_factory=list)
    botched_setup: bool = False          # council: a failed Introduction

    @property
    def met(self) -> bool:        return self.successes >= self.resistance
    @property
    def exhausted(self) -> bool:  return self.attempts_used >= self.attempts_allowed
    @property
    def finished(self) -> bool:   return self.met or self.exhausted

    def record(self, roll: RollResult) -> None:
        """One attempt. A success contributes 1 + one per Success icon."""
        self.attempts_used += 1
        self.successes += roll.magnitude(1)      # pattern P3; 0 if failed
        self.attempt_log.append(ContestAttempt(roll))

    def add_successes(self, n: int) -> None:
        """For the Skill Special Success that scores an extra success (02.3.6)."""
```

**Each rolled Success icon counts as an additional success.** This is the single most
consequential detail in both subsystems: a great success is worth 2 toward Resistance, an
extraordinary success 3 or more. `roll.magnitude(1)` encodes it.

```python
class ContestOutcome(StrEnum):
    SUCCESS          = "success"
    PARTIAL          = "partial"      # some successes, but ran out of attempts
    TOTAL_FAILURE    = "total_failure"
    DISASTER         = "disaster"

def evaluate(contest: ResistanceContest) -> ContestOutcome:
    if contest.met:                     return SUCCESS
    if contest.successes == 0:          return DISASTER
    if contest.botched_setup:           return DISASTER
    return PARTIAL
```

`TOTAL_FAILURE` vs `DISASTER` differs between the two adapters; see below.

## 9.2 Councils

A council is a **formal gathering with real stakes**, not every conversation. Ordinary
exchanges use plain Skill rolls. The engine cannot tell the difference — starting a council
is an explicit LM action.

### 9.2.1 Sequence

```
1. Set Resistance      (LM)
2. Introduction        (one spokesperson, one roll — sets the attempt budget)
3. Interaction         (repeated Skill rolls until met or exhausted)
4. End of Council      (evaluate)
```

### 9.2.2 Step 1 — Resistance

The LM grades the Company's stated goal against the motivations and expectations of the
folk being petitioned:

| Grade | Resistance | Character of the request |
|---|---|---|
| Reasonable | 3 | The audience loses nothing by helping, or the Company offers something of roughly equal worth. |
| Bold | 6 | The goal profits the Company more than it profits the audience. |
| Outrageous | 9 | The audience is asked to do something dangerous, or with little or no prospect of reward. |

Before this, the players should agree on what they want, how they intend to get it, and
what they are willing to give or sacrifice. Capture as `CouncilGoal(description, offer)` —
narrative, but worth recording for the log.

### 9.2.3 Step 2 — Introduction

The Company elects a **spokesperson**, who makes one Skill roll.

| Result | Attempt budget | Consequence |
|---|---|---|
| Success | `Resistance + 1 per Success icon` | — |
| Failure | `Resistance` | Sets `botched_setup = True`: if the council later fails, it fails as a **Disaster** |

Skills characteristically used, with their flavour of consequence:
* **AWE** — a powerful message in few words; can overturn a bad first reaction or set terms
  quickly. Downside: the spokesperson volunteers the lineage, deeds, and personal details
  of the Company's members for full effect.
* **COURTESY** — best when already on friendly terms; to unfriendly ears a courteous speaker
  may sound duplicitous. Allows politely declining to reveal much about the group.
* **RIDDLE** — extracts information while giving little away; a poor performance breeds
  mistrust.

The engine takes the Skill as an input and does not restrict it. Those consequences are
narrative and belong in the UI as guidance.

`MODIFY_COUNCIL_ATTEMPTS` can raise the budget — one Cultural Virtue adds +1 to the maximum
number of Skill rolls a hero may attempt in a council.

### 9.2.4 Step 3 — Interaction

Repeated Skill rolls, each consuming one attempt, until the contest finishes.

**Audience attitude** modifies every roll:

| Attitude | Modifier |
|---|---|
| Reluctant | `−1d` |
| Open | — (default) |
| Friendly | `+1d` |

Attitude is an LM input but is overridable by effects through `MODIFY_AUDIENCE_ATTITUDE` —
some Cultural Virtues make a specific folk, or all folk encountered, always Friendly.

**Effective roleplaying** is explicitly mechanised: if a player's delivered speech touches
topics relevant to the goal and important to the audience, the LM may grant `+1d` or even
`+2d`. Expose as `roleplay_bonus: int` (0–2) on each attempt input. The book is emphatic
that a good die roll and a clever decision deserve equal weight — surface this in the UI
rather than burying it.

Skills characteristically used: ENHEARTEN (needs a crowd or one person's full attention;
the objective must be obvious or the effect is weak even on a success), INSIGHT (evaluate
emotions, reveal unspoken purposes), PERSUADE (win minds; usable discreetly in any social
setting), RIDDLE (the formal riddle-game, or gathering tidbits while seeming
unconcerned), SONG (approval, and a genuine diplomatic device with the right song).

### 9.2.5 Step 4 — End of council

| Outcome | Condition | Meaning |
|---|---|---|
| **Success** | successes ≥ Resistance within the budget | The Company achieves its stated objective. |
| **Failure, or Success with Woe** | some successes but short of Resistance | The players **choose**: simply fail and be refused, or (with LM approval) achieve the goal at a price — much less than asked, or acquiring enemies among the audience. The price need not be immediately apparent. |
| **Disaster** | all attempts failed, **or** short of Resistance after a botched Introduction | The Company is now seen as a threat: they may be imprisoned, or attacked. |

The Failure/Success-with-Woe branch is a **player decision**, so `end_council` returns
`PARTIAL` with both options attached rather than resolving it:

```python
@dataclass(frozen=True, slots=True)
class CouncilResult:
    outcome: ContestOutcome
    successes: int
    resistance: int
    attempts_used: int
    woe_available: bool          # True only when outcome is PARTIAL
```

## 9.3 Skill Endeavours

A complex task better described as a series of smaller feats — searching a wide area for
clues, fortifying a steading before nightfall, following a warband's tracks, unpicking a
complicated riddle.

### 9.3.1 Sequence

```
1. Set Resistance
2. Set Time Limit
3. Execution
```

**Resistance** uses the same 3 / 6 / 9 ladder: simple (a lengthy but manageable effort),
laborious (difficult and time-consuming), daunting (hard and complicated).

**Time limit** — here the budget is set by available time rather than by a roll:

| Available time | Attempts |
|---|---|
| Short | Resistance |
| Enough | Resistance + 1 |
| Plenty | Resistance + 2 |

Whether an hour is "short" or "plenty" depends entirely on the task — an hour to search one
room is plenty; an hour to search a castle is short.

When no time limit applies at all, the engine runs the contest unbounded
(`attempts_allowed = None`) and instead reports elapsed effort. The LM adjudicates how
often rolls may be made — perhaps twice a day while tracking across country, perhaps once
an hour while scaling a cliff — which the engine records as `roll_interval` for the
timeline.

**Execution** — participants roll. Different Skills may be used toward the same goal, and
the same Skill may be used repeatedly if circumstances allow.

### 9.3.2 Completion and failure

Completed successfully when successes match or exceed Resistance. Failed when the Company
abandons the task or time runs out.

**Failed individual rolls within an endeavour** are graded by Risk level (`16.2`):
* **Simple failure** — normally just a delay; the endeavour continues.
* **Failure with Woe** — a negative consequence occurs (a short fall costing Endurance,
  inhaling smoke while fighting a fire). The endeavour continues.
* **Disaster** — the endeavour fails completely and **cannot be resumed**.

That last one terminates the contest immediately, regardless of remaining attempts.
`ResistanceContest` therefore needs an `abort(reason)` path distinct from exhaustion.

## 9.4 Opposed actions

Rare but specified: one PC acting in direct opposition to another.

* Everyone rolls simultaneously, using the same ability or different ones as circumstances
  suggest.
* **Each rolls against their own relevant Attribute TN.**
* If more than one contestant succeeds, the one with the **most Success icons** wins.
* If all fail, or the icon counts tie, either roll again or call it a draw.

```python
def resolve_opposed(entries: Sequence[OpposedEntry], rng: Randomness) -> OpposedResult:
    """Returns a winner, or None for a draw/all-fail."""
```

This lives in `contest.py` because it is a contest, but it shares no state with
`ResistanceContest`.
