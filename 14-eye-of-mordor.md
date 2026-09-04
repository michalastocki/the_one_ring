# 14 — The Eye of Mordor

Module: `tor.rules.eye`.

An **optional** subsystem. The rules are explicitly suited to being introduced later in a
campaign, once the Company has a few years of game time behind it — they add complexity not
every group wants, and they exist to represent how the Enemy reacts to a *successful* group
of adventurers. Gate the whole module behind `Campaign.options.eye_of_mordor_enabled`,
defaulting to off, and make sure nothing else depends on it.

---

## 14.1 The two values

* **Eye Awareness** — how much attention the Enemy is paying to the Company. It ebbs and
  flows with their deeds and wandering, and is tracked from session to session within an
  Adventuring Phase.
* **Hunt threshold** — how much attention the Company can bear before being noticed. Set by
  the region they are in, modified by their precautions.

```python
@dataclass(slots=True)
class EyeState:
    awareness: int
    starting_awareness: int
    region: RegionType
    modifiers: list[HuntModifier]

    def threshold(self) -> int:
        return REGION_THRESHOLD[self.region] + sum(m.value for m in self.modifiers)

    @property
    def revealed(self) -> bool:
        return self.awareness >= self.threshold()
```

## 14.2 Starting Eye Awareness

Recalculated at the **beginning of each Adventuring Phase** from the Company's composition,
because the Enemy watches most closely those he hates or fears most.

```python
def starting_awareness(company: Company, heroes: Sequence[Hero]) -> int:
    base = max(CULTURE_AWARENESS[h.culture] for h in heroes)   # highest entry only
    base += sum(1 for h in heroes if h.valour >= 4)
    base += 2 * count_famous_items(heroes)
    return base
```

Three components:

1. **A base from the Company's composition.** Apply **only the highest applicable entry** —
   this is a maximum, not a sum. The scale runs from 0 for a Company of only the least
   conspicuous folk, up through 1 and 2 as more notable kindreds are present. The per-culture
   values are content data on the culture (`eye_awareness_weight`), not a hardcoded table.
2. **+1 for each hero with VALOUR 4 or more.**
3. **+2 for each Famous Weapon or Armour carried** by members of the Company — the spies of
   the Enemy are drawn to powerful weapons.

LM characters travelling with the Company are **not** normally counted for this purpose.

## 14.3 Increasing Eye Awareness

Note carefully: **most of these apply only outside of combat.** Pass `in_combat` in the
context and filter (`08.15`).

| Trigger | Increase |
|---|---|
| **Rolling eyes** — a player's die roll **outside of combat** produces an eye icon, whether the roll succeeded or failed | +1 |
| **Shadow gain** — a hero gains one or more Shadow points **outside of combat** | +1 per point |
| **Using magic** — a lesser effect | +1 |
| — a major spell | +2 |
| — a really powerful spell | +3 |

**Rolling eyes** is graded by the LM: under particularly dramatic or grave circumstances an
eye may raise Awareness by 2 or more; conversely, in a place the LM deems safe it may
provoke no increase at all. Expose as `eye_weight: int | None` on the trigger, defaulting
to 1, with `None` meaning suppressed.

**Using magic** covers blatant displays proportional to the effect. Illustrative gradations:
lesser effects such as illuminating a dark passage with a brief magical light, opening a
locked door, or playing tricks with voices; major spells such as lighting a fire in a storm,
barring a door against a powerful adversary, or setting a tree ablaze; powerful spells such
as calling down lightning on an advancing enemy, lending speed to a travelling company, or
making a river's waters rise in rage.

**Not every use of magic counts.** Tracing signs visible only to friendly eyes, or shaping
pipe-smoke and fireworks, provokes no increase — unless done in the wrong place or at the
wrong time.

```python
def register_magic(state: EyeState, level: MagicLevel, *, conspicuous: bool = True) -> int:
    if not conspicuous:
        return 0
    return MAGIC_AWARENESS[level]          # 1, 2, or 3
```

Wire the eye-result trigger as an `ON_ROLL_RESOLVED` observer (`04.3.1`) rather than
sprinkling calls through the subsystems:

```python
def eye_observer(result: RollResult, ctx: HookContext, state: EyeState) -> None:
    if ctx.extra.get("in_combat"):          return
    if not ctx.actor.is_hero:               return
    if result.kept_feat is not FeatFace.EYE: return
    state.awareness += ctx.extra.get("eye_weight", 1)
```

Similarly hook Shadow gain in `shadow.gain_shadow`, filtering on `in_combat`.

## 14.4 The Hunt threshold

Set by the region the heroes are currently in. If they travel from one region to another,
the threshold changes accordingly.

| Region traversed | Hunt threshold |
|---|---|
| Border Land | 18 |
| Wild Land | 16 |
| Dark Land | 14 |

Modified by circumstance:

| Modifier | Circumstance |
|---|---|
| **+4** | The Company is protected by the blessing of a Wizard or another powerful LM character |
| **+2** | Travelling under false names, treading seldom-trodden paths, or otherwise being discreet and unobtrusive |
| **−2** | The heroes have gained great renown in the area through some exceptional deed |
| **−4** | The Enemy is actively looking for them, or their mission or goal is known to him |

These stack. A Company that is both discreet and hunted sits at `+2 − 4 = −2`.

## 14.5 Revelation

```
awareness <  threshold  →  the Company is hidden
awareness >= threshold  →  the Company is revealed
```

While hidden, the adventurers appear to the Enemy as a group of stray wanderers whose
actions are of small concern.

When revealed, the LM introduces a **Revelation episode** — a dangerous event reflecting
the increased hostility of the game world toward the Company. Afterwards:

> As soon as the Revelation episode has been played out, the Company is **hidden again**,
> and Eye Awareness is **set back to its starting level** — not to zero.

Tallying then resumes, and may lead to another Revelation later in the same Adventuring
Phase.

```python
def check_revelation(state: EyeState) -> bool: ...

def resolve_revelation(state: EyeState) -> list[Event]:
    state.awareness = state.starting_awareness
    return [Event.revelation_resolved()]
```

## 14.6 Revelation episodes

The source of the hostility varies wildly: direct action by servants of the Enemy, an
accident born of another unfriendly power's ill-will, or simply a sinister stroke of bad
luck. There are many strange and dark powers in Middle-earth, and not all are in league
with the Dark Lord.

Three authoring constraints the engine should surface as guidance:

1. Whatever happens must **spring naturally from the ongoing flow of events**. No Orcs
   appearing from nowhere to attack heroes asleep in a safe house.
2. It should always **suggest strange forces at work** — a distinctive aura of something
   dark and foul, a malicious misfortune, odd behaviour in men or beasts, the faint trace
   of sorcery.
3. If the current situation offers nothing suitable, the Revelation should be **held off**
   until an appropriate moment, possibly into the following session. Support this with
   `EyeState.revelation_pending: bool`, which suppresses further checks until resolved.

Typical episodes: a hero losing the others during a journey or a flight; provisions found
unexpectedly spoilt; a decision proving to be the worst possible choice; someone the
Company was waiting for failing to appear at a crucial moment.

The book supplies ten worked examples, which the engine should carry as a content table
(`revelation_episodes.json`) using the declarative op language (`05.6`) where the effect is
mechanical. Their mechanical shapes:

| Shape | Op encoding |
|---|---|
| All Skill rolls concerning a chosen situation **lose `(1d)`** — for a journey leg, or the next encounter entirely | a scoped `Complication` |
| The goal of the **next Council is made harder** — a reasonable request becomes bold, a bold one outrageous | `{"op": "council_resistance_step", "steps": 1}` |
| A hero gains **3 Shadow (Greed)**, the challenge shaped by their Shadow Path | `{"op": "shadow", "who": "chosen", "points": 3, "source": "greed"}` |
| An ally becomes an enemy through coercion, betrayal, or sorcery | narrative |
| An unnatural weariness grips the Company: **all heroes are Weary** until the LM deems otherwise | `{"op": "condition", "who": "company", "condition": "weary", "value": true}` |
| A captured or pursued enemy escapes as if hidden by an unseen hand; or pursuers catch up unexpectedly | narrative |
| A threat the Company could have avoided is now waiting for them — a patrol that automatically spots them, a creature that sniffs them out at the worst moment | narrative |
| One enemy type faced in the next combat gains **Hatred (subject)** focused on a Heroic Culture present in the Company | `{"op": "grant_fell_ability", "ability": "hatred", "params": {"subject": "<culture>"}}` |
| An enemy in the next combat starts with an **additional pool of drive equal to a Success die roll**; the LM spends from this pool first whenever the creature would reduce its own | `{"op": "bonus_drive_pool", "dice": 1}` |
| One enemy type in the next combat gains the ability making Wounded targets roll severity **Ill-favoured** | `{"op": "grant_fell_ability", "ability": "deadly_wound"}` |

The "bonus drive pool" needs a distinct pool on the instance, spent before the normal one:

```python
@dataclass(slots=True)
class AdversaryInstance:
    drive: ResourcePool
    bonus_drive: int = 0        # spent before `drive`

    def spend_drive(self, n: int = 1) -> None:
        take = min(n, self.bonus_drive)
        self.bonus_drive -= take
        if n - take:
            self.drive.spend(n - take)
```

Note this interacts with the "Weary at zero drive" rule (`12.2`): a creature with bonus
drive remaining is not at zero.

The `council_resistance_step` op needs the council subsystem to accept a pending
adjustment. Because subsystems may not import one another (`01.1`), route it through
campaign state: `Campaign.pending_modifiers`, consumed by `council.start()`.

## 14.7 Resetting

> When an Adventuring Phase ends, the tally is **suspended for the length of the Fellowship
> Phase**. Eye Awareness is set again to its starting score at the beginning of the
> following Adventuring Phase.

Two distinct behaviours, both needed:

* **Suspended** during a Fellowship Phase — not zero, not accumulating. Eye triggers fired
  during a Fellowship Phase are ignored entirely. Guard with
  `if campaign.phase is not AdventuringPhase: return`.
* **Recalculated** at the start of the next Adventuring Phase via `starting_awareness()`,
  which picks up any change in composition, VALOUR, or Famous items acquired in between.

## 14.8 Tabletop affordance

The rules suggest tracking Awareness with physical tokens — around twenty suffice. Worth
mirroring in a UI: display Awareness as a filled counter against the current threshold, so
players see how close they are without the LM having to announce it.
