"""Console reporting and per-run logging (run.log).

All reporter output is mirrored into the run log when one is open. Failures
are printed as compact blocks; full tracebacks only with --debug.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

RULE_WIDTH = 62

_ICONS = {"ok": "✓", "skip": "↷", "fail": "✗", "info": "→", "plan": "✓"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Reporter:
    def __init__(self, debug: bool = False):
        self.debug = debug
        self._log_file = None

    # -- run log -------------------------------------------------------------
    def open_log(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = path.open("w", encoding="utf-8")

    def close_log(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    # -- primitives ----------------------------------------------------------
    def line(self, text: str = "") -> None:
        print(text)
        if self._log_file is not None:
            self._log_file.write(text + "\n")
            self._log_file.flush()

    def rule(self, title: str = "") -> None:
        self.line("=" * RULE_WIDTH)
        if title:
            self.line(title)
            self.line("=" * RULE_WIDTH)

    def kv(self, key: str, value: str) -> None:
        width = max(18, len(key) + 2)
        self.line(f"{key:<{width}}{value}")

    def field(self, label: str, status: str, detail: str = "") -> None:
        """Validation line: 'DEM:  FOUND  path/to/dem.tif'."""
        status_col = max(11, len(status) + 1)
        line = f"{label:<24}{status:<{status_col}}{detail}"
        self.line(line.rstrip())

    def mark(self, status: str, text: str) -> None:
        self.line(f"{_ICONS.get(status, '→')} {text}")

    def note(self, text: str) -> None:
        self.line(f"  {text}")

    # -- structured blocks ----------------------------------------------------
    def banner(self, title: str) -> None:
        self.line()
        self.rule(title)
        self.line()

    def run_header(self, lines: list[str]) -> None:
        self.line()
        self.rule("HYDRO TWIN SCENARIO RUN")
        for line in lines:
            self.line(line)
        self.line("=" * RULE_WIDTH)

    def footer(self) -> None:
        # Log-file end marker only — the console summary already prints
        # "Finished:", so writing this via line() would duplicate it.
        if self._log_file is not None:
            self._log_file.write(f"finished: {_now()}\n")
            self._log_file.flush()

    def error_block(self, title: str, items: list[tuple[str, str]],
                    traceback_text: str | None = None) -> None:
        self.line()
        self.rule(title)
        for key, value in items:
            self.line(f"{key}: {value}")
        if traceback_text and self.debug:
            self.line()
            self.line(traceback_text.rstrip())
        self.rule()

    def exception(self, exc: BaseException, context: str = "") -> None:
        if self.debug:
            detail = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ).rstrip()
        else:
            detail = f"{type(exc).__name__}: {exc}"
        items: list[tuple[str, str]] = []
        if context:
            items.append(("Step", context))
        items.append(("Error", detail if not self.debug else str(exc)))
        self.error_block("STEP FAILED" if context else "ERROR", items,
                         traceback_text=detail if self.debug else None)


def utf8_console() -> None:
    """Keep status glyphs intact when stdout is piped or redirected."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
