"""Hydro Twin scenario runner.

An orchestration layer on top of the existing project components:

- backend/app/services/terrain/   terrain pipeline (clip/condition/flow/domain/sph_terrain/mesh)
- examples/main/<Case>/           DualSPHysics case generation + diagnostics
- backend/app/services/sph/       SPHRunner (GenCase / DualSPHysics / PartVTK)

Scenario definitions live in scenarios/<name>.json. Nothing in this package
re-implements the processing algorithms; it only wires them together.
"""

__version__ = "1.0.0"
