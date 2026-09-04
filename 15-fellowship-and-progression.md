# 15 — Fellowship Phases and Progression

Modules: `tor.rules.fellowship`, `tor.rules.progression`.

---

## 15.1 Where a Fellowship Phase sits

A Fellowship Phase marks the conclusion of each Adventuring Phase. Ideally it takes place
at the end of a gaming session; otherwise it opens a new session and precedes the next
Adventuring Phase.

Structure:

```
1. Set duration
2. Choose destination
3. Perform updates      (training, growth, spiritual recovery)
4. Choose undertakings
```

## 15.2 Duration and destination

**Duration.** A Fellowship Phase should last a **minimum of one week, up to an entire
season**, to give the heroes a real break from adventuring. The longest is normally taken
during the mid-winter festivities — **Yule**.

**Destination.** The players choose where to spend it, normally at a safe haven, as a
group — unless the year's end festivities are near. They may select **any location the
Company has visited so far**; the journey there happens behind the scenes unless the players
want to play it out. The choice should stay within reasonable distance of the recent
adventuring area, allowing for the phase's length.

```python
@dataclass(slots=True)
class FellowshipPhase:
    duration: timedelta
    is_yule: bool
    destination: str
    undertakings: list[UndertakingChoice]
```

Constraint the engine checks: `duration >= 1 week`; destination is in
`company.visited_locations`.

## 15.3 Experience points

Two currencies, earned separately and spent on different things.

| | Skill points | Adventure points |
|---|---|---|
| Earned per gaming session attended | **3** | **3** |
| Earned additionally at **Yule** | a number equal to the hero's **WITS** rating | — |
| Spent on | Skill ranks | Combat Proficiency ranks, VALOUR ranks, WISDOM ranks |

Points are **earned during the Adventuring Phase and spent during the Fellowship Phase**.
Unspent points carry over — a player may save toward a more expensive upgrade later.

**Optional finer granularity.** If a group wants a more granular rate, heroes may earn
**1 Skill point and 1 Adventure point per hour of gaming** instead. The average session is
reckoned at about three hours, which is where the flat 3/3 comes from. Groups that pack more
into an hour may raise the rate to 1.5 points per hour or beyond. Expose as:

```python
@dataclass(frozen=True, slots=True)
class AdvancementOptions:
    mode: Literal["per_session", "per_hour"] = "per_session"
    per_session: int = 3
    per_hour: float = 1.0
```

Worked example of the Yule bonus: a hero who attended 3 sessions in the last Adventuring
Phase has 9 Skill points; at Yule they gain a further amount equal to their WITS.

## 15.4 Updates — training and growth

**Training** spends Skill points on Skill ranks using the `advancement.ability` ladder
(`05.10`): 4 / 8 / 12 / 20 / 26 / 30 for ranks 1 through 6.

> **During a single Fellowship Phase a player may buy a maximum of one rank in each Skill.**

**Growth** spends Adventure points on Combat Proficiencies (same `ability` ladder) and on
VALOUR or WISDOM ranks (the `rank` ladder: 8 / 12 / 20 / 26 / 30 for ranks 2 through 6).

> **During a single Fellowship Phase: a maximum of one rank in each Combat Proficiency, and
> a rank in either VALOUR or WISDOM but not both.**

```python
def buy_rank(hero: Hero, ability: AbilityId, phase: FellowshipPhase,
             pack: ContentPack) -> list[Event]:
    """Raises RuleViolation for: insufficient points; a second rank in the same ability
    this phase; both VALOUR and WISDOM in the same phase; a Combat Proficiency bought with
    Skill points; a Skill bought with Adventure points."""
```

Track per-phase purchases in `FellowshipPhase.purchases: dict[AbilityId, int]` so the
one-rank caps are enforceable and the phase can be replayed.

**On gaining a new rank:**

* **VALOUR** → the hero gains a new **Reward**. They may instead choose to **activate one
  dormant quality of a Famous Weapon or Armour** they own (`13.7.4`) — a substitution, not
  an addition.
* **WISDOM** → the hero gains a new **Virtue**. From WISDOM 2 onward this may be a
  **Cultural Virtue** of their own culture, in place of a standard Virtue.

Both fire through `ON_VALOUR_GAIN` / `ON_WISDOM_GAIN`.

> If a new rank is attained, **the scores of all related abilities and features must be
> updated accordingly** — Virtues that raise maxima raise current values too (`03.4.2`),
> and Rewards recompute the affected item's derived stats.

### 15.4.1 Rewards

Rewards are upgrades that raise the effectiveness of war gear. Each affects a **single
characteristic** of one item. **All upgrades can be applied only once to the same piece of
gear** (invariant I10).

The six standard Rewards, by the shape the engine must support:

| Applies to | Effect |
|---|---|
| Armour or helm | **+2 to the result** of a PROTECTION roll made while wearing it |
| Armour, helm, or shield | **Reduce the item's Load by 2**, to a minimum of 0 |
| Weapon | **+2 to the Injury rating**. A weapon usable in either grip gets the bonus to **both** its Injury ratings. |
| Weapon | **+1 to the Damage rating** |
| Weapon | Attack rolls score a **Piercing Blow on a 9** as well |
| Shield | **+1 to the shield's Parry bonus** |

Whether a Reward represents a newly discovered property of an item or an entirely new piece
of gear is up to the player — they are encouraged to weave it into a narrative of how the
gift was received or the equipment improved.

**Plot immunity.** Ordinary war gear lost or broken in play can be replaced or fixed at no
cost at the next settlement, or at another appropriate narrative moment — at most a small
favour is asked, such as a task, a song, or a tale, especially among another folk. But:

> Items upgraded with one or more Rewards enjoy a degree of **plot immunity**: they can
> never be lost, broken, or otherwise taken from a hero. A shield enhanced by a Reward
> cannot be smashed.

The engine enforces this in two places: `Break Shield` special damage (`12.5`) checks for
Rewards, and the item-loss path refuses to remove a Reward-bearing item.

> For the same reason such items **cannot be handed to other heroes**, not even on
> character death — prized items are usually buried with their owners. A Reward is a token
> of a hero's renown and cannot be transferred.

The **heirloom** mechanism (`06.12`) is the only exception. `transfer_item()` must check
`is_heirloom_transfer` and reject otherwise.

### 15.4.2 Virtues

Standard Virtues may each be **acquired multiple times**, should a player wish. Their
shapes:

| Effect |
|---|
| Raise maximum **Hope** by 2 |
| Raise maximum **Endurance** by 2 |
| Raise **Parry** by 1 |
| Lower one **Attribute TN** by 1 |
| Choose two Skills and make them **Favoured** |
| On Special Damage, add **+1 to STRENGTH on a Heavy Blow** and **+1 to the Feat die numeric result on a Pierce** |

**Cultural Virtues** are exclusive to members of a culture, require **WISDOM 2 or higher**,
and are taken **in place of** a standard Virtue. Each culture has six. They are not
repeatable. Validate `hero.culture == virtue.culture and hero.wisdom >= virtue.min_wisdom`.

## 15.5 Updates — spiritual recovery

Two automatic effects, in this order:

**1. Hope.** Every hero recovers Hope equal to their **HEART** score. At **Yule** they
recover **all** of it. One culture's blessing modifies this to **half HEART, rounding up**
— routed through `MODIFY_HOPE_RECOVERY` (`07.3`).

**2. Shadow.** If the Adventuring Phase produced what can be considered a positive outcome
in the fight against the Shadow, every member of the Company removes Shadow points. The LM
weighs the Company's actions against the broader context:

| Assessment of their deeds | Points removed |
|---|---|
| At least marginally interfered with the return of the Shadow | 1 |
| Actively hindered or damaged the Enemy | up to 2 |
| Feats that would draw the attention of the Dark Lord himself, or of one of his major servants | up to 3 |

This is an LM judgement — an input, never computed. Then apply
`MODIFY_SHADOW_REMOVAL_CAP` (`11.9`): one culture's weakness caps removal at a single point
per Fellowship Phase, applied **after** the LM's figure.

Shadow Scars are **not** removed here — only by the *Heal Scars* undertaking.

## 15.6 Undertakings

Time-consuming endeavours possible only during a lengthy stop.

**How many:**

* **Ordinary Fellowship Phase** — the Company as a group chooses **one** undertaking.
* **Yule Fellowship Phase** — **each player** chooses one.
* **Additionally, in every phase**, the Company is entitled to **one extra undertaking**
  chosen from those listed as **free** for the Callings represented among the heroes.

So: an ordinary phase yields at most **two** undertakings; Yule yields **number of heroes
plus one**.

**Players must always select different undertakings**, unless the undertaking is marked as
a Yule activity — those may be chosen by any number of heroes.

```python
def validate_undertakings(choices: Sequence[UndertakingChoice], phase: FellowshipPhase,
                          company: Company, pack: ContentPack) -> None:
    """Enforces: the count for the phase type; distinctness for non-Yule-marked
    undertakings; Yule-only undertakings not chosen outside Yule; the free undertaking
    matching a Calling present in the Company."""
```

The undertakings, with their mechanics:

| Undertaking | Effect | Yule only | Free for |
|---|---|---|---|
| **Gather Rumours** | Receive a rumour from the LM — a story about a person, place, or coming event that the Company can explore, prevent, or aspire to; or something specific the hero asked about. | no | one Calling |
| **Heal Scars** | Spend **5 Adventure points**, remove **1 Shadow Scar**. | **yes** | — |
| **Meet Patron** | Meet one of the Company's friends and allies, when spending the phase where that individual can be found and they are available. Usually the hero asks for assistance, possibly accepting a task in return. Also the route by which an item's Blessings, name, or history are revealed. | no | one Calling |
| **Ponder Storied and Figured Maps** | Until the next Fellowship Phase, apply **+1 to all Feat die rolls determining the nature of Journey Events** (`10.4.2`). | no | one Calling |
| **Raise an Heir** | Spend up to **5 Treasure and an equal number of Adventure points**; each Adventure point raises the heir's Previous Experience reserve by 1. Name the heir on first use. | **yes** | — |
| **Recount a Story** | Replace one **Distinctive Feature** with a new trait — a quality displayed in the narrated episode. May be chosen from the standard list or invented. | **yes** | — |
| **Strengthen Fellowship** | Raise the Company's **Fellowship rating by +1 until the next Fellowship Phase**. | no | one Calling |
| **Study Magical Items** | Learn everything about the qualities of all **Marvellous Artefacts and Wondrous Items** in the Company's possession. | no | one Calling |
| **Write a Song** | Compose a song — a **Lay**, a **Song of Victory**, or a **Walking-song** — added to the Company's song list. | no | one Calling |
| **Visit the Treasury** | Gift a Reward-bearing item to your folk; activate an equal number of dormant qualities on a Famous item (`13.7.4`). | no | — |

Six Callings, six free undertakings — the mapping is content data on the Calling
(`05.3`), not a hardcoded table.

**Change Useful Items.** Independent of the undertaking count: during **each** Fellowship
Phase players are free to change their selection of Useful Items, always respecting the
maximum allowed by their current Standard of Living.

`Raise an Heir` may be chosen repeatedly across phases, adding to the reserve up to a
maximum of **20**. The heir is ready at **10** (`06.12`).

## 15.7 Yule

Approximately once every three Fellowship Phases winter comes and the year ends. The
Company normally spends the whole cold season as a single prolonged Fellowship Phase, and
usually disbands temporarily as each hero returns home — three months suffice to reach any
but the most remote homeland.

Mechanical consequences of Yule, collected:

* **Every hero ages one year.**
* Each hero earns **bonus Skill points equal to their WITS rating**.
* Hope is recovered **in full** rather than by HEART.
* **Each player** chooses an undertaking, rather than one for the Company.
* Four undertakings become available that are otherwise not.
* The LM should update the Company on changes in the world — news tailored to their
  circumstances and whereabouts, which seeds the next Adventuring Phase.

```python
def advance_year(campaign: Campaign) -> list[Event]:
    for hero in campaign.heroes:
        hero.age += 1
        hero.skill_points += hero.attributes.wits
    campaign.year += 1
```

## 15.8 Songs

Songs written during a Fellowship Phase go on the Company's list and are sung during an
Adventuring Phase.

```python
@dataclass(slots=True)
class Song:
    title: str
    kind: SongKind          # LAY | SONG_OF_VICTORY | WALKING_SONG
    spent: bool = False

class SongKind(StrEnum):
    LAY             = "lay"              # Councils
    SONG_OF_VICTORY = "song_of_victory"  # Combat
    WALKING_SONG    = "walking_song"     # Journeys
```

**Singing a song.** When the Company is involved in a Council, Combat, or Journey, the
heroes may sing to rouse themselves.

1. Choose a song from the Company's list **appropriate to the current venture**.
2. Make a **SONG roll**.
3. On a success, the heroes **ignore the effects of being Weary for the length of the
   heroic venture**.

Rules:
* Each song may be used **once per Adventuring Phase**. Mark it off the list.
* **A song is marked off whether it was used successfully or not.**
* Singing in combat is a **secondary action** (`08.3`).

```python
def sing(company: Company, song: Song, venture: SceneKind, singer: Hero,
         rng: Randomness) -> SongOutcome:
    if SONG_VENTURE[song.kind] is not venture:
        raise RuleViolation("this song does not suit the current venture")
    if song.spent:
        raise RuleViolation("this song has already been sung this Adventuring Phase")
    song.spent = True                     # before checking the roll — failure still spends
    ...
```

The "ignore Weary" effect applies to **everyone succeeding in the roll**; implement as a
scene-scoped `FlagContribution` suppressing the Weary treatment inside `rolls.resolve()`
(specifically, outlined Success dice count normally again). It does not clear the Weary
condition — Load is unchanged and the flag reappears when the venture ends.

Reset `spent` on all songs at the start of each Adventuring Phase.

## 15.9 Adventuring career and retirement

Guidance the engine should surface rather than enforce:

* Heroes are expected to rise to excellence in about **10 years of game time** — ranks of
  5+ in VALOUR and WISDOM, and comparable levels in a Combat Proficiency.
* It is rare for heroes of any culture to go adventuring for more than **two decades**.
* Cultures list a typical retirement age (`05.2`).

A hero leaves play by death, by succumbing to the Shadow (`11.8`), or by voluntary
retirement. In every case, an heir who has been prepared continues the line; a player
whose hero leaves without a ready heir creates a new, unrelated hero by the normal rules.

```python
def retire(hero: Hero, campaign: Campaign, reason: RetirementReason) -> RetirementOutcome:
    """Returns the heir draft seed if hero.heir is ready, else None."""
```
