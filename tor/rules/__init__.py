"""The rules subsystems (L4).

One module per mechanic, per ``01.1``. No subsystem imports another: cross-subsystem needs
go through ``tor.effects`` and ``tor.model``, or through an event the session layer routes.

``tor.rules.resources``, ``tor.rules.shadow``, ``tor.rules.injury`` and
``tor.rules.contest`` are the exception the same section names — **shared leaves** that
every subsystem may depend on, and that may themselves depend on nothing else at L4.

Import the submodules directly; this package re-exports nothing, so
``import tor.rules.journey`` never drags in combat.
"""
