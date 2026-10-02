from __future__ import annotations

import io
import re
import uuid
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from minioj.config import settings

AVATAR_LIMIT_BYTES = 1024 * 1024
AVATAR_MAX_PIXELS = 4_194_304
AVATAR_KEY = re.compile(r"^[0-9a-f]{32}\.png$")
FORMATS = {
    ".png": ("PNG", "image/png"),
    ".jpg": ("JPEG", "image/jpeg"),
    ".jpeg": ("JPEG", "image/jpeg"),
    ".webp": ("WEBP", "image/webp"),
}


def avatar_path(key: str) -> Path:
    if not AVATAR_KEY.fullmatch(key):
        raise ValueError("Invalid avatar key")
    directory = settings.data_dir / "avatars"
    if directory.is_symlink():
        raise ValueError("Unsafe avatar directory")
    path = directory / key
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise ValueError("Unsafe avatar path")
    return path


def save_avatar(filename: str, content_type: str | None, data: bytes) -> str:
    if len(data) > AVATAR_LIMIT_BYTES:
        raise ValueError("Avatar must not exceed 1 MiB")
    if (
        not filename
        or filename != Path(filename).name
        or "\\" in filename
        or ".." in filename
    ):
        raise ValueError("Invalid avatar filename")
    expected = FORMATS.get(Path(filename).suffix.lower())
    if expected is None or content_type != expected[1]:
        raise ValueError("Use a PNG, JPEG or WebP image")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as original:
                if (
                    original.format != expected[0]
                    or original.width * original.height > AVATAR_MAX_PIXELS
                ):
                    raise ValueError(
                        "Invalid image type or dimensions (maximum 4 megapixels)"
                    )
                original.verify()
            with Image.open(io.BytesIO(data)) as original:
                image = ImageOps.exif_transpose(original).convert("RGBA")
                image.thumbnail((256, 256))
                output = io.BytesIO()
                image.save(output, format="PNG")
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("Invalid or unsafe image") from exc
    key = uuid.uuid4().hex + ".png"
    target = avatar_path(key)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(output.getvalue())
    return key
