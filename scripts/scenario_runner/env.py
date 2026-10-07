"""DualSPHysics / interpreter environment resolution and checks.

Resolution order for the DualSPHysics installation (per-run only — the Windows
system environment is never modified):

  1. --dualsphysics-root CLI flag        (explicit per-run override)
  2. DUALSPHYSICS_ROOT environment var   (CASE A: already configured)
  3. "dualsphysics_root" in the scenario (CASE B: scenario configuration)
  4. scenarios/local.json                (CASE B: machine-local, git-ignored)

If none is available the run stops with an actionable error (CASE C).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import LOCAL_CONFIG_PATH, REPO_ROOT, rel


class EnvError(Exception):
    """Raised when the run environment cannot be resolved/validated."""


@dataclass
class DualSPHysicsInfo:
    root: Path
    source: str  # human-readable origin of the path


def resolve_dualsphysics_root(
    cli_root: str | Path | None,
    scenario_root: Path | None,
) -> DualSPHysicsInfo:
    """Resolve the DualSPHysics installation directory, or explain why not."""
    candidates: list[tuple[str, str | Path | None]] = [
        ("--dualsphysics-root flag", cli_root),
        ("DUALSPHYSICS_ROOT environment variable", os.environ.get("DUALSPHYSICS_ROOT")),
        ("scenario config (dualsphysics_root)", scenario_root),
        (
            f"{rel(LOCAL_CONFIG_PATH)} (dualsphysics_root)",
            _local_root(),
        ),
    ]
    for source, value in candidates:
        if value:
            root = Path(str(value)).expanduser()
            if not root.is_absolute():
                root = (REPO_ROOT / root).resolve()
            return DualSPHysicsInfo(root=root, source=source)

    raise EnvError(
        "DUALSPHYSICS_ROOT not configured\n"
        "\n"
        "The DualSPHysics installation could not be located. Configure it in "
        "ONE of these ways (per-run only; nothing is written to Windows):\n"
        "\n"
        "  1. environment variable for this shell:\n"
        '       set DUALSPHYSICS_ROOT=C:\\path\\to\\DualSPHysics_v5.4\n'
        "  2. pass it for a single run:\n"
        '       python scripts/run_scenario.py <scenario> '
        '--dualsphysics-root "C:\\path\\to\\DualSPHysics_v5.4"\n'
        "  3. machine-local file scenarios/local.json (git-ignored) — copy "
        "scenarios/local.example.json:\n"
        '       { "dualsphysics_root": "C:/path/to/DualSPHysics_v5.4" }\n'
        "  4. a shared setting in the scenario file itself:\n"
        '       "dualsphysics_root": "C:/path/to/DualSPHysics_v5.4"\n'
        "\n"
        "The installation must contain bin/windows/GenCase_win64.exe, "
        "DualSPHysics5.4_win64.exe and PartVTK_win64.exe."
    )


def _local_root() -> str | Path | None:
    if not LOCAL_CONFIG_PATH.exists():
        return None
    try:
        import json

        data = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
        value = data.get("dualsphysics_root")
        return value if isinstance(value, str) and value else None
    except Exception:
        return None


# The binary names mirror backend/app/services/sph/runner.py exactly — the
# runner validates the same executables SPHRunner will execute.
REQUIRED_BINARIES: tuple[tuple[str, str], ...] = (
    ("GenCase", "GenCase_win64.exe"),
    ("DualSPHysics solver", "DualSPHysics5.4_win64.exe"),
    ("PartVTK", "PartVTK_win64.exe"),
)


def binary_paths(root: Path) -> list[tuple[str, Path]]:
    bin_dir = Path(root) / "bin" / "windows"
    return [(label, bin_dir / filename) for label, filename in REQUIRED_BINARIES]


def check_modules(names: tuple[str, ...]) -> list[tuple[str, bool]]:
    results: list[tuple[str, bool]] = []
    for name in names:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        results.append((name, found))
    return results


def python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
