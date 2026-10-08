"""Legacy VTK polydata reader for DualSPHysics PartVTK output.

PartVTK writes legacy ``.vtk`` polydata. On Windows it defaults to
``BINARY`` (big-endian) data, which is why naive ASCII parsers fail on
this repository's real outputs. The reader here handles both encodings
and the exact section layout produced by PartVTK:

    # vtk DataFile Version 3.0
    vtk output
    BINARY | ASCII
    DATASET POLYDATA
    POINTS <n> float
    VERTICES ...
    POINT_DATA <n>
    SCALARS <name> <type> [ncomp]
    LOOKUP_TABLE default
    FIELD FieldData <k>
    <name> <ncomp> <ntuples> <type>
    ...

Returns numpy arrays. Positions are float32 (N, 3); named point data
arrays keep their native width (``Idp`` is uint32, ``Type`` uint8, ...).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

_VTK_TYPES: dict[str, tuple[str, int]] = {
    "char": ("i1", 1),
    "unsigned_char": ("u1", 1),
    "short": ("i2", 2),
    "unsigned_short": ("u2", 2),
    "int": ("i4", 4),
    "unsigned_int": ("u4", 4),
    "long": ("i8", 8),
    "unsigned_long": ("u8", 8),
    "float": ("f4", 4),
    "double": ("f8", 8),
}


class VtkParseError(RuntimeError):
    """Raised when a file is not a readable legacy VTK polydata file."""


class _Cursor:
    """Byte cursor with line-oriented header reads and exact binary reads."""

    def __init__(self, raw: bytes):
        self.raw = raw
        self.pos = 0

    @property
    def eof(self) -> bool:
        return self.pos >= len(self.raw)

    def readline(self) -> str:
        end = self.raw.find(b"\n", self.pos)
        if end < 0:
            line = self.raw[self.pos:]
            self.pos = len(self.raw)
        else:
            line = self.raw[self.pos:end]
            self.pos = end + 1
        return line.decode("ascii", errors="replace").strip()

    def read_exact(self, n: int) -> bytes:
        if self.pos + n > len(self.raw):
            raise VtkParseError(
                f"unexpected end of file at byte {self.pos} (need {n} more)"
            )
        chunk = self.raw[self.pos:self.pos + n]
        self.pos += n
        # Binary blocks are followed by a single newline; consume it so the
        # next readline() lands on the next header.
        if self.pos < len(self.raw) and self.raw[self.pos:self.pos + 1] in (
            b"\n",
            b"\r",
        ):
            if self.raw[self.pos:self.pos + 1] == b"\r":
                self.pos += 1
            if self.pos < len(self.raw) and self.raw[self.pos:self.pos + 1] == b"\n":
                self.pos += 1
        return chunk

    def read_ascii_values(self, count: int) -> list[float]:
        values: list[float] = []
        while len(values) < count:
            if self.eof:
                raise VtkParseError("unexpected end of file in ASCII data")
            end = self.raw.find(b"\n", self.pos)
            if end < 0:
                end = len(self.raw)
            line = self.raw[self.pos:end]
            self.pos = end + 1
            for token in line.split():
                values.append(float(token))
        if len(values) != count:
            raise VtkParseError(
                f"expected {count} ASCII values, got {len(values)}"
            )
        return values


def _read_array(
    cursor: _Cursor,
    count: int,
    type_name: str,
    binary: bool,
) -> np.ndarray:
    if type_name not in _VTK_TYPES:
        raise VtkParseError(f"unsupported VTK data type: {type_name}")
    dtype, size = _VTK_TYPES[type_name]
    if binary:
        raw = cursor.read_exact(count * size)
        # VTK legacy binary is big-endian for multi-byte types.
        return np.frombuffer(raw, dtype=">" + dtype).astype(dtype, copy=True)
    values = cursor.read_ascii_values(count)
    return np.asarray(values, dtype=dtype)


def read_vtk_polydata(path: Path | str) -> dict[str, np.ndarray]:
    """Read a legacy VTK polydata file.

    Returns ``{"pos": (N,3) float32}`` plus every named point-data array
    (e.g. ``Vel``, ``Idp``, ``Press``, ``Rhop``, ``Type``).
    """
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise VtkParseError(f"cannot read {path}: {exc}") from exc

    if not raw.startswith(b"# vtk DataFile Version"):
        raise VtkParseError(f"{path.name}: not a legacy VTK file")

    cursor = _Cursor(raw)
    cursor.readline()  # version line
    cursor.readline()  # title
    encoding = cursor.readline().strip().upper()
    if encoding not in ("ASCII", "BINARY"):
        raise VtkParseError(f"{path.name}: unknown encoding {encoding!r}")
    binary = encoding == "BINARY"

    dataset = cursor.readline().strip().upper()
    if not dataset.startswith("DATASET POLYDATA"):
        raise VtkParseError(f"{path.name}: unsupported dataset {dataset!r}")

    arrays: dict[str, np.ndarray] = {}

    while not cursor.eof:
        header = cursor.readline()
        if not header:
            continue
        parts = header.split()
        keyword = parts[0].upper()

        if keyword == "POINTS":
            n = int(parts[1])
            arrays["pos"] = _read_array(cursor, n * 3, parts[2], binary).reshape(
                n, 3
            ).astype(np.float32, copy=False)

        elif keyword in ("VERTICES", "LINES", "POLYGONS", "TRIANGLE_STRIPS"):
            n_cells = int(parts[1])
            size = int(parts[2])
            _read_array(cursor, size, "int", binary)  # connectivity — unused

        elif keyword == "POINT_DATA":
            n_points = int(parts[1])
            arrays.setdefault("_n_points", np.asarray(n_points))

        elif keyword == "SCALARS":
            name = parts[1]
            type_name = parts[2]
            ncomp = int(parts[3]) if len(parts) > 3 else 1
            look = cursor.readline()
            if not look.upper().startswith("LOOKUP_TABLE"):
                raise VtkParseError(
                    f"{path.name}: expected LOOKUP_TABLE after SCALARS {name}"
                )
            data = _read_array(cursor, n_points * ncomp, type_name, binary)
            arrays[name] = data.reshape(n_points, ncomp) if ncomp > 1 else data

        elif keyword == "FIELD":
            # FIELD FieldData <k> followed by <k> array descriptors.
            for _ in range(int(parts[2])):
                desc = cursor.readline().split()
                if not desc:
                    raise VtkParseError(f"{path.name}: truncated FIELD block")
                name, ncomp, ntuples, type_name = (
                    desc[0],
                    int(desc[1]),
                    int(desc[2]),
                    desc[3],
                )
                data = _read_array(cursor, ncomp * ntuples, type_name, binary)
                arrays[name] = (
                    data.reshape(ntuples, ncomp) if ncomp > 1 else data
                )

        elif keyword in ("CELL_DATA", "METADATA"):
            # Not used by the fluid frames; stop rather than mis-parse.
            break

        else:
            # Unknown section — cannot safely skip without its layout.
            raise VtkParseError(
                f"{path.name}: unsupported VTK section {parts[0]!r}"
            )

    if "pos" not in arrays:
        raise VtkParseError(f"{path.name}: no POINTS section found")

    arrays.pop("_n_points", None)
    return arrays


def read_positions(path: Path | str) -> np.ndarray:
    """Positions only (fast path for header/count style checks)."""
    return read_vtk_polydata(path)["pos"]
