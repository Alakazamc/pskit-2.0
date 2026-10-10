"""Bounded avatar image decoding and normalization."""

import warnings
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_AVATAR_BYTES = 4 * 1024 * 1024


def normalize_avatar(content: bytes) -> bytes:
    """Validate a raster image and strip metadata by encoding a 256 px square WebP."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                if source.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("Unsupported avatar format")
                if source.width * source.height > 16_000_000:
                    raise ValueError("Avatar dimensions are too large")
                source.load()
                oriented = ImageOps.exif_transpose(source)
                square = ImageOps.fit(oriented.convert("RGBA"), (256, 256), Image.Resampling.LANCZOS)
                output = BytesIO()
                square.save(output, format="WEBP", quality=85)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError("Invalid avatar image") from exc
