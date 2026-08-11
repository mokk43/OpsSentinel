import struct
import zlib
from pathlib import Path

import pytest

from monitor.screenshot import ImageBudgetError, check_image_budget


def _write_png(path: Path, width: int, height: int) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    # minimal empty IDAT
    raw = b""
    for _ in range(height):
        raw += b"\x00" + b"\x00\x00\x00" * width
    idat = zlib.compress(raw)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    path.write_bytes(data)


def test_image_budget_ok(tmp_path: Path):
    path = tmp_path / "ok.png"
    _write_png(path, 10, 10)
    size, w, h = check_image_budget(path, max_bytes=10_000_000, max_side_px=1000)
    assert size > 0
    assert w == 10 and h == 10


def test_image_budget_side(tmp_path: Path):
    path = tmp_path / "big.png"
    _write_png(path, 100, 50)
    with pytest.raises(ImageBudgetError):
        check_image_budget(path, max_bytes=10_000_000, max_side_px=80)
