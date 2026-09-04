# 11 — The Shadow

Module: `tor.rules.shadow`. A **shared leaf** — combat, journey, council, treasure, and the
Eye subsystem all call into it.

---

## 11.1 Public surface

```python
def gain_shadow(hero: Hero, points: int, source: ShadowSource,
                *, test: ShadowTestInput | None, rng: Randomness) -> ShadowGainOutcome: ...
def harden_will(hero: Hero) -> list[Event]: ...
def bout_of_madness(hero: Hero, description: str) -> MadnessOutcome: ...
def remove_shadow(hero: Hero, points: int, reason: RemovalReason) -> list[Event]: ...
def heal_scar(hero: Hero) -> list[Event]: ...
```

```python
class ShadowSource(StrEnum):
    DREAD    = "dread"      # resisted with VALOUR
    SORCERY  = "sorcery"    # resisted with WISDOM
    GREED    = "greed"      # resisted with WISDOM
    MISDEED  = "misdeed"    # CANNOT be resisted
    FOCUS    = "focus"      # harm to your Fellowship Focus — CANNOT be resisted
    OTHER    = "other"
```

The resisting ability is a **property of the source**, not a parameter. Encode the mapping
once here; no caller supplies it.

## 11.2 The Shadow score and its consequences

* Shadow accumulates during the Adventuring Phase and is shed by Shadow Tests, by
  hardening the will, and during Fellowship Phases.
* **Shadow can never exceed maximum Hope.** Points gained beyond that ceiling are simply
  discarded, not banked (invariant I5).

Three escalating consequences:

| Trigger | Consequence |
|---|---|
| `shadow >= current_hope` | **Miserable** — an eye on the Feat die means automatic failure regardless of total |
| `shadow == max_hope` | **Ill-favoured on every roll** |
| Shadow reaches max Hope with four Flaws already acquired | The hero is **taken out of play** |

The second is implemented as an always-on effect registered when the threshold is crossed
and unregistered when it is not, contributing an ill-favoured source to every
`RollRequest`:

```python
OVERBURDENED = Effect(
    id=EffectId("overburdened_by_shadow"),
    kind=EffectKind.CONDITION,
    listeners={Hook.MODIFY_ROLL_REQUEST: add_ill_favoured_source},
    predicate=lambda ctx: ctx.actor.shadow == ctx.actor.max_hope,
)
```

Register it permanently at hero construction and let the predicate do the work — that way
no code path can forget to attach it.

## 11.3 Shadow Tests

A Shadow Test reduces or cancels the points gained from a resistible source.

```python
def apply_shadow_test(points: int, roll: RollResult) -> int:
    """Returns the points actually gained."""
    return max(0, points - roll.magnitude(1))
```

**A passed test reduces the gain by 1, plus 1 per Success icon** — pattern P3 again. A
failed test reduces nothing.

The test is a normal roll of VALOUR (against HEART TN) or WISDOM (against WITS TN),
modified by the source that provoked it. It passes through `MODIFY_SHADOW_TEST`, which is
how these work: a Cultural Blessing making VALOUR rolls Favoured; a Cultural Blessing
making WISDOM rolls Favoured and adding `+1d` against Greed specifically; Cultural Virtues
adding `+1d` against Sorcery or against Dread; a Cultural Virtue making Dread tests
Favoured with a further `+1d` against evil spirits and ghosts; a Patron benefit making a
Shadow Test Favoured for a Fellowship point.

**Two sources permit no test at all**:
* **Misdeeds** — no Shadow Test of any kind may reduce them.
* **Harm to your Fellowship Focus** — the 1 point gained when your Focus is Wounded,
  suffers a bout of madness, or is otherwise seriously harmed cannot be prevented.

Enforce in `gain_shadow`: if `source in NON_RESISTIBLE` and a test is supplied, raise
`RuleViolation` rather than silently ignoring it. Silent ignoring hides caller bugs.

## 11.4 Sources of Shadow

### 11.4.1 Dread — resisted with VALOUR

Gained from witnessing what strikes fear or sows doubt. A content table grades the amount:

| Severity of the source | Points |
|---|---|
| A natural but unexpected tragedy, or a very grievous occurrence | 1 |
| A gruesome killing, a dreadful experience, the work of Orcs | 2 |
| A harrowing experience; physical and spiritual torment | 3 |
| Experiencing the power of the Enemy directly | 4 |

Adversary Fell Abilities are the other main source, typically inflicting 2 or 3 points on
everyone in sight, often with a rider: those who **fail** the resulting Shadow Test are
daunted and **cannot spend Hope for the rest of the fight**. One undead ability instead
knocks failing (or already Miserable) targets unconscious, rousable only with a SONG roll,
otherwise waking after an hour.

Model the rider as part of the ability's declarative definition (`on_fail` in
`shadow_on_hit`), not as a shadow-subsystem special case.

### 11.4.2 Greed — resisted with WISDOM

Gained from taking possession of precious or powerful objects that have lain tainted in
the dark. The main mechanical source is magical treasure found on an eye result (`13.4`):
1, 2, or 3 points depending on what was found.

### 11.4.3 Sorcery — resisted with WISDOM

Gained from falling victim to the dark magic of the Enemy and his minions. The amount
depends on the spell's power, and often carries additional consequences beyond the
Shadow itself.

### 11.4.4 Misdeeds — **not resistible**

Gained from acts that are wrong or nefarious in nature, regardless of the goal they serve.
A content table grades them:

| Action | Points |
|---|---|
| Violent threats and malicious lies; heedless cruelty | 1 |
| Manipulating others; abusing authority; deliberate cruelty | 2 |
| Theft or plunder; oathbreaking or cowardice; treachery | 3 |
| Torment or torture; killing or crippling a surrendered foe or harmless folk | 4 |
| Murder; willingly acting in the service of the Enemy | 4 **plus 1 Shadow Scar** |

Three rules the engine must encode:

1. **Attempting** a despicable act is a Misdeed whether or not it succeeds.
2. A Misdeed can be committed **unknowingly** — attacking someone believed guilty who turns
   out innocent. In that case Shadow is **not** gained automatically; it is gained only if
   the hero fails to attempt reparations once the mistake comes to light. If the reaction is
   contrition and an earnest attempt to put things right, a **WISDOM Shadow Test is
   allowed** — the one exception to the no-test rule, so model it as
   `ShadowSource.MISDEED` with an explicit `reparation_allowed=True` flag.
3. The LM should normally **warn** players before they commit a Misdeed. Expose
   `preview_misdeed(action) -> int` so a UI can surface the cost first.

Killing an adversary that has a **Resolve** score (rather than Hate) should always be
evaluated as a possible Misdeed — see `12.2`.

## 11.5 Hardening the will

A hero whose accumulated Shadow **does not yet match** their maximum Hope may choose to
**remove all current Shadow, replacing it with a single Shadow Scar**.

```python
def harden_will(hero: Hero) -> list[Event]:
    if hero.shadow >= hero.max_hope:
        raise RuleViolation("harden_will requires Shadow below maximum Hope")
    hero.shadow = 1
    hero.shadow_scars += 1
```

A **Shadow Scar** is a permanent Shadow point. It counts as a normal Shadow point for every
purpose — including the Miserable threshold — and can be removed only during a **Yule**
Fellowship Phase via the *Heal Scars* undertaking, which costs 5 Adventure points and
removes exactly one scar (`15.6`).

Note the resulting state: Shadow becomes exactly the scar count, which is at least 1. It
does not become 0.

## 11.6 Bouts of madness

A hero whose Shadow **reaches maximum Hope** is Ill-favoured on everything, and there is
exactly one way out: a bout of madness.

The player describes how the character loses control for a short time and does something
they will later regret. The description should draw on the source of the last Shadow gain,
the character's Shadow Path, or one of their Flaws. Characteristic shapes: **betrayal**
(failing to keep their word or perform a duty, and the Company suffers); **fear** (fleeing
any danger, thinking only of self-preservation); **lust** (an irresistible desire for
something not theirs, taken secretly or openly); **rage** (brooding on real or imagined
wrongs until reacting aggressively). If none fits, a standard bout is an aggression of any
kind, verbal or physical, on the most likely available target — often the character's own
Fellowship Focus.

Mechanically:

```python
def bout_of_madness(hero: Hero, description: str) -> MadnessOutcome:
    hero.shadow = hero.shadow_scars      # remove all *current* Shadow; scars persist
    hero.shadow_path_step += 1
    flaw = shadow_path_flaw(hero.shadow_path, hero.shadow_path_step)
    hero.flaws.append(flaw)
    # the hero is no longer Ill-favoured; recompute conditions
```

The hero recovers control quickly afterward, mind cleared of the tangle of fear and doubt
they had fallen into.

**Timing constraint:** a bout of madness must take place **during the current Adventuring
Phase**. A hero who ends an Adventuring Phase with Shadow matching maximum Hope is
considered to have left the Company and is **retired from play**. Enforce this at the
phase-end boundary in `tor.session`.

**Fellowship Focus interaction:** when a hero suffers a bout of madness, everyone who has
that hero as their Fellowship Focus gains 1 Shadow, unresistible (`3.6`).

## 11.7 Shadow Paths and Flaws

Each **Calling** assigns a Shadow Path. Each path has **four steps**, and each step grants a
different **Flaw** — a Distinctive Feature with a negative connotation. The paths and their
flaw sequences are content data (`shadow_paths.json`):

```json
{"shadow_paths": [
  {"id": "example_path", "calling": "example_calling",
   "steps": ["flaw_a", "flaw_b", "flaw_c", "flaw_d"]}
]}
```

There are six paths, one per Calling, each with four ordered flaws — 24 in total.

**Using a Flaw.** If a hero uses a Skill that a Flaw could plausibly affect, the roll is
made **Ill-favoured**. This is an LM judgement, exposed as an input; the engine supplies
the mechanism:

```json
{"id": "example_flaw", "kind": "flaw",
 "factory": "ill_favour_when_applicable"}
```

The effect adds an ill-favoured source when `ctx.extra["flaw_applies"]` is set by the LM.
It is not automatic, because plausibility cannot be computed.

Note: some Flaws can also be inflicted **temporarily** by the *Curse of Weakness* on a
cursed item (`13.8`), which gives the bearer the worst Flaw on their own Shadow Path. Such
a temporary Flaw **does not count toward succumbing** — it must be tagged
`temporary=True` and excluded from the four-Flaw count.

## 11.8 Succumbing to the Shadow

A hero who has developed all four Flaws of their path is at the edge:

> The next time their Shadow score reaches maximum Hope, they do **not** become
> Ill-favoured — they are **taken out of play**.

```python
def check_succumb(hero: Hero) -> bool:
    permanent_flaws = [f for f in hero.flaws if not f.temporary]
    return len(permanent_flaws) >= 4 and hero.shadow >= hero.max_hope
```

The narrative fate differs by kind of folk — Men, Hobbits, and Dwarves succumb to madness,
usually ending in death; Elves are overpowered by the burden and seek to leave
Middle-earth for the Uttermost West. Both are narrative; the mechanical result is the
same. Record `departure_kind` on the retirement event so the UI can narrate correctly, and
let the LM and player work out the details together.

## 11.9 Removing Shadow in a Fellowship Phase

See `15.5` for the full procedure. Two things belong here:

* The amount removed (1–3 points, depending on how noteworthy the Company's deeds were)
  is an **LM judgement**.
* `MODIFY_SHADOW_REMOVAL_CAP` can restrict it — one culture's weakness caps removal at a
  single point per Fellowship Phase. Apply the cap **after** the LM's figure, not before.
