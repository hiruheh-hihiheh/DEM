"""Artifact freshness manifest (scenario_manifest.json).

A step is considered fresh (and is skipped) when:

- its recorded parameters are unchanged, and
- every recorded input file still has the recorded size and mtime, and
- every recorded output file still exists with the recorded size and mtime.

This catches edited configs, regenerated/removed outputs, and replaced inputs
without pretending to be a full build system. ``--force`` bypasses the check.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import rel


def _stat(path: Path) -> dict[str, Any] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return {"path": str(path), "mtime_ns": st.st_mtime_ns, "size": st.st_size}


class Manifest:
    def __init__(self, path: Path, data: dict[str, Any]):
        self.path = path
        self.data = data

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        if not path.exists():
            return cls(path, {"version": 1, "steps": {}})
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("steps"), dict):
                raise ValueError("unexpected structure")
        except Exception:
            # A corrupt manifest is not fatal: treat everything as stale.
            print(f"warning: ignoring unreadable manifest {rel(path)}")
            data = {"version": 1, "steps": {}}
        return cls(path, data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
        self.data = {"version": 1, "steps": {}}

    @staticmethod
    def _matches(recorded: list[dict[str, Any]], current: list[dict[str, Any] | None]) -> bool:
        if len(recorded) != len(current):
            return False
        for want, got in zip(recorded, current):
            if got is None or got != want:
                return False
        return True

    def is_fresh(
        self,
        step_id: str,
        params: dict[str, Any],
        inputs: list[Path],
        outputs: list[Path],
    ) -> bool:
        entry = self.data["steps"].get(step_id)
        if not isinstance(entry, dict):
            return False
        if entry.get("params") != params:
            return False
        if not outputs:
            return False
        recorded_inputs = entry.get("inputs")
        recorded_outputs = entry.get("outputs")
        if not isinstance(recorded_inputs, list) or not isinstance(recorded_outputs, list):
            return False
        current_inputs = [_stat(p) for p in inputs]
        current_outputs = [_stat(p) for p in outputs]
        if any(c is None for c in current_outputs):
            return False
        if any(c is None for c in current_inputs):
            return False
        return self._matches(recorded_inputs, current_inputs) and self._matches(
            recorded_outputs, current_outputs
        )

    def record(
        self,
        step_id: str,
        params: dict[str, Any],
        inputs: list[Path],
        outputs: list[Path],
    ) -> None:
        self.data["steps"][step_id] = {
            "params": params,
            "inputs": [i for i in (_stat(p) for p in inputs) if i is not None],
            "outputs": [o for o in (_stat(p) for p in outputs) if o is not None],
            "recorded_at": _now(),
        }

    def remove(self, step_id: str) -> None:
        self.data["steps"].pop(step_id, None)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
