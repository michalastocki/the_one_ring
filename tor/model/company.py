"""The Company aggregate and its shared Fellowship pool (``03.6``, ``15.8``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from tor.errors import RuleViolation
from tor.model.conditions import ResourcePool
from tor.model.derive import DerivedStat
from tor.model.gear import Mount
from tor.model.ids import HeroId, PatronId

__all__ = ["Company", "Song", "SongKind"]


class SongKind(StrEnum):
    """Each song suits one kind of heroic venture (``15.8``)."""

    LAY = "lay"
    SONG_OF_VICTORY = "song_of_victory"
    WALKING_SONG = "walking_song"


@dataclass(slots=True)
class Song:
    title: str
    kind: SongKind
    #: Marked whether the SONG roll succeeded or not, and reset at the start of each
    #: Adventuring Phase.
    spent: bool = False


@dataclass(slots=True)
class Company:
    """The adventuring company.

    Fellowship is a **shared pool**: every spend decrements one counter that all heroes
    read. Never copy the value onto a ``Hero``.
    """

    heroes: list[HeroId] = field(default_factory=list)
    patrons: list[PatronId] = field(default_factory=list)
    safe_havens: list[str] = field(default_factory=list)
    visited_locations: list[str] = field(default_factory=list)
    #: Base is the number of player-heroes; modifiers come from the Patron, Cultural
    #: Blessings and Virtues, and the expiring *Strengthen Fellowship* bonus.
    fellowship_rating: DerivedStat = field(default_factory=lambda: DerivedStat(base=0))
    fellowship: ResourcePool = field(default_factory=lambda: ResourcePool(0, 0))
    #: Directed and non-reciprocal: A may choose B while B chooses C, and two heroes may
    #: both choose B. Capped at one per hero, raised to two by one Cultural Virtue.
    fellowship_focus: dict[HeroId, list[HeroId]] = field(default_factory=dict)
    mounts: dict[HeroId, Mount] = field(default_factory=dict)
    songs: list[Song] = field(default_factory=list)
    eye_awareness: int = 0

    def sync_fellowship_maximum(self) -> None:
        """Re-derive the pool's ceiling from the rating."""
        self.fellowship.set_maximum(self.fellowship_rating.value, raise_current=False)

    def refresh_fellowship(self) -> None:
        """Refill the pool.

        This happens at the end of each **gaming session**, not at a phase boundary — a
        distinct and easily missed detail (``03.6``, ``17.1.1``).
        """
        self.sync_fellowship_maximum()
        self.fellowship.current = self.fellowship.maximum

    def set_focus(self, hero: HeroId, focus: HeroId, *, limit: int = 1) -> None:
        current = self.fellowship_focus.setdefault(hero, [])
        if focus == hero:
            raise RuleViolation(
                "a hero's Fellowship Focus must be another hero",
                rule_reference="fellowship_focus",
            )
        if focus in current:
            return
        if len(current) >= limit:
            raise RuleViolation(
                f"{hero} already has {len(current)} Fellowship Focus "
                f"{'entries' if limit > 1 else 'entry'} (limit {limit})",
                rule_reference="fellowship_focus_limit",
                suggestion="a Cultural Virtue raises the limit to two",
            )
        current.append(focus)

    def heroes_focused_on(self, hero: HeroId) -> list[HeroId]:
        """Everyone who named ``hero`` as their Focus.

        They each gain 1 unresistible Shadow when that hero is Wounded, suffers a bout of
        madness, or is otherwise seriously harmed (``11.3``).
        """
        return [holder for holder, targets in self.fellowship_focus.items() if hero in targets]

    def reset_songs(self) -> None:
        """Every song becomes available again at the start of an Adventuring Phase."""
        for song in self.songs:
            song.spent = False
