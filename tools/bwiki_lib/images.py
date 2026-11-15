from __future__ import annotations

import hashlib
import os
import struct
import tempfile
from pathlib import Path

from .client import ALLOWED_HOSTS, WikiClient, validate_url

ALLOWED_MIME = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024


class ImageValidationError(ValueError):
    pass


def inspect_image(data: bytes) -> dict[str, object]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 33 or data[12:16] != b"IHDR":
            raise ImageValidationError("truncated PNG")
        width, height, bit_depth, color_type = struct.unpack(">IIBB", data[16:26])
        if width <= 0 or height <= 0:
            raise ImageValidationError("invalid PNG dimensions")
        return {
            "format": "PNG",
            "extension": "png",
            "mime": "image/png",
            "width": width,
            "height": height,
            "png_alpha": color_type in {4, 6} or b"tRNS" in data,
            "png_bit_depth": bit_depth,
            "png_color_type": color_type,
        }
    if data.startswith(b"\xff\xd8"):
        width, height = _jpeg_size(data)
        return {"format": "JPEG", "extension": "jpg", "mime": "image/jpeg", "width": width, "height": height, "png_alpha": None}
    if len(data) >= 30 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        width, height, alpha = _webp_size(data)
        return {"format": "WebP", "extension": "webp", "mime": "image/webp", "width": width, "height": height, "png_alpha": None, "webp_alpha": alpha}
    raise ImageValidationError("unsupported image magic")


def _jpeg_size(data: bytes) -> tuple[int, int]:
    offset = 2
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            break
        length = struct.unpack(">H", data[offset : offset + 2])[0]
        if length < 2 or offset + length > len(data):
            break
        if marker in sof and length >= 7:
            height, width = struct.unpack(">HH", data[offset + 3 : offset + 7])
            return width, height
        offset += length
    raise ImageValidationError("JPEG dimensions not found")


def _webp_size(data: bytes) -> tuple[int, int, bool]:
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        flags = data[20]
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height, bool(flags & 0x10)
    if chunk == b"VP8 " and len(data) >= 30:
        start = data.find(b"\x9d\x01\x2a", 20)
        if start >= 0 and start + 7 <= len(data):
            width, height = struct.unpack("<HH", data[start + 3 : start + 7])
            return width & 0x3FFF, height & 0x3FFF, False
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, True
    raise ImageValidationError("WebP dimensions not found")


def safe_component(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value).strip("._")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("unsafe path component")
    return cleaned[:80]


def download_sample(
    client: WikiClient,
    *,
    sample_id: str,
    url: str,
    expected_mime: str | None,
    downloads_dir: Path,
    total_used: int,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> dict:
    validate_url(url, ALLOWED_HOSTS)
    safe_id = safe_component(sample_id)
    sample_dir = Path(downloads_dir) / safe_id
    sample_dir.mkdir(parents=True, exist_ok=False)
    result = client.fetch(url, response_name=f"sample-{safe_id}", max_bytes=max_file_bytes, record_body=False)
    if len(result.data) > max_file_bytes:
        raise ImageValidationError(f"sample exceeds {max_file_bytes} bytes")
    if total_used + len(result.data) > max_total_bytes:
        raise ImageValidationError(f"samples exceed {max_total_bytes} total bytes")
    details = inspect_image(result.data)
    if expected_mime and expected_mime not in ALLOWED_MIME:
        raise ImageValidationError(f"metadata MIME is forbidden: {expected_mime}")
    if result.content_type not in ALLOWED_MIME:
        raise ImageValidationError(f"HTTP MIME is forbidden: {result.content_type}")
    if details["mime"] != result.content_type or (expected_mime and details["mime"] != expected_mime):
        raise ImageValidationError("image MIME and magic do not agree")
    filename = f"image.{details['extension']}"
    destination = sample_dir / filename
    fd, part_name = tempfile.mkstemp(prefix="image.", suffix=".part", dir=sample_dir)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(result.data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(part_name, destination)
    except BaseException:
        try:
            os.unlink(part_name)
        except FileNotFoundError:
            pass
        raise
    return {
        "sample_id": sample_id,
        "url": result.final_url,
        "content_type": result.content_type,
        "bytes": len(result.data),
        "sha256": hashlib.sha256(result.data).hexdigest(),
        "path": f"downloads/{safe_id}/{filename}",
        **details,
    }
