"""Decode and normalize public images before they leave quarantine."""
from io import BytesIO

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError


SAFE_FORMATS = {"JPEG", "PNG", "WEBP"}


def sanitize_image(stream, *, max_dimension: int, max_pixels: int) -> tuple[BytesIO, str, str]:
    """Return metadata-free, orientation-corrected image bytes, MIME and suffix."""
    stream.seek(0)
    try:
        with Image.open(stream) as source:
            if source.format not in SAFE_FORMATS:
                raise HTTPException(status_code=415, detail="Unsupported image format. Use JPG, PNG or WebP.")
            if source.width <= 0 or source.height <= 0 or source.width * source.height > max_pixels:
                raise HTTPException(status_code=413, detail="Image dimensions are too large to process safely.")
            source.load()
            image = ImageOps.exif_transpose(source)
            image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            has_alpha = image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info)
            output = BytesIO()
            if has_alpha:
                image.convert("RGBA").save(output, format="WEBP", quality=88, method=6)
                mime, suffix = "image/webp", "webp"
            else:
                image.convert("RGB").save(output, format="JPEG", quality=88, optimize=True, progressive=True)
                mime, suffix = "image/jpeg", "jpg"
            output.seek(0)
            return output, mime, suffix
    except HTTPException:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=415, detail="Image content is corrupted or could not be decoded.") from exc
