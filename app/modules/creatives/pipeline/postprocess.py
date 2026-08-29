"""Pillow post-processing shared by every generated image: logo composite,
disclaimer strip, and an optional Instagram safe-zone debug overlay --
ported unchanged from the prototype's app/pipeline/postprocess.py.

Runs identically on mock and real provider output, so a dry-run preview
looks like the real thing except for image content quality. All outputs
also carry a SynthID watermark from the model itself -- this module only
adds our own brand/compliance layer on top.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

_DISCLAIMER_FONT_SIZE_FRACTION = 0.022
_DISCLAIMER_PADDING_FRACTION = 0.012
_LOGO_MAX_WIDTH_FRACTION = 0.18
_LOGO_MARGIN_FRACTION = 0.03

# Rough Instagram feed/reel UI chrome guidance, as a fraction of image height
# reserved at top/bottom -- approximate, for a debug safe-zone visualization
# only, not a precise spec.
_SAFEZONE_TOP_FRACTION = 0.09
_SAFEZONE_BOTTOM_FRACTION = 0.20


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def composite_logo(image: Image.Image, logo_bytes: bytes | None) -> Image.Image:
    if not logo_bytes:
        return image
    image = image.convert("RGBA")
    logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
    max_w = int(image.width * _LOGO_MAX_WIDTH_FRACTION)
    if logo.width > max_w > 0:
        ratio = max_w / logo.width
        logo = logo.resize((max_w, max(1, int(logo.height * ratio))))
    margin = int(image.width * _LOGO_MARGIN_FRACTION)
    image.alpha_composite(logo, dest=(margin, margin))
    return image


def render_disclaimer_strip(image: Image.Image, disclaimer: str) -> Image.Image:
    image = image.convert("RGBA")
    font_size = max(14, int(image.height * _DISCLAIMER_FONT_SIZE_FRACTION))
    padding = max(8, int(image.height * _DISCLAIMER_PADDING_FRACTION))
    font = _load_font(font_size)

    measurer = ImageDraw.Draw(image)
    bbox = measurer.textbbox((0, 0), disclaimer, font=font)
    text_h = int(bbox[3] - bbox[1])
    strip_h = text_h + padding * 2

    strip = Image.new("RGBA", (image.width, strip_h), (10, 37, 64, 235))
    ImageDraw.Draw(strip).text(
        (padding, padding - int(bbox[1])), disclaimer, font=font, fill=(255, 255, 255, 255)
    )

    canvas = Image.new("RGBA", image.size, (0, 0, 0, 0))
    canvas.alpha_composite(image)
    canvas.alpha_composite(strip, dest=(0, image.height - strip_h))
    return canvas


def overlay_safezones(image: Image.Image) -> Image.Image:
    image = image.convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    top_h = int(image.height * _SAFEZONE_TOP_FRACTION)
    bottom_h = int(image.height * _SAFEZONE_BOTTOM_FRACTION)
    draw.rectangle([0, 0, image.width, top_h], fill=(255, 0, 0, 70))
    draw.rectangle([0, image.height - bottom_h, image.width, image.height], fill=(255, 0, 0, 70))
    return Image.alpha_composite(image, overlay)


def compose_final_image(
    raw_image_bytes: bytes,
    *,
    disclaimer: str,
    logo_bytes: bytes | None = None,
    debug_safezone: bool = False,
) -> bytes:
    image: Image.Image = Image.open(io.BytesIO(raw_image_bytes))
    image = composite_logo(image, logo_bytes)
    image = render_disclaimer_strip(image, disclaimer)
    if debug_safezone:
        image = overlay_safezones(image)
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()
