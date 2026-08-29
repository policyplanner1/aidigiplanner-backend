"""Pure-unit tests for postprocess.py's Pillow compositing -- ported from
the prototype's tests/test_postprocess.py. No DB, no HTTP client."""

import io

import pytest
from PIL import Image

from app.modules.creatives.brand import BrandProfileDTO
from app.modules.creatives.pipeline.postprocess import (
    compose_final_image,
    composite_logo,
    overlay_safezones,
    render_disclaimer_strip,
)

# Deliberately white, not brand navy: render_disclaimer_strip's fill color
# IS brand navy (10, 37, 64) -- a navy strip composited onto a navy
# background is indistinguishable by pixel color alone, so tests need a
# background that visibly contrasts with both the strip and the logo.
_BG_COLOR = "#FFFFFF"


def _png_bytes(size: tuple[int, int] = (1080, 1350), color: str = _BG_COLOR) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def blank_image() -> Image.Image:
    return Image.new("RGB", (1080, 1350), color=_BG_COLOR)


class TestCompositeLogo:
    def test_none_logo_bytes_returns_image_unchanged(self, blank_image: Image.Image) -> None:
        # Must degrade gracefully, not crash, when no logo has been
        # uploaded yet (brand.logo_storage_key is None).
        result = composite_logo(blank_image, None)
        assert result.size == blank_image.size

    def test_present_logo_bytes_is_composited(self, blank_image: Image.Image) -> None:
        logo_buf = io.BytesIO()
        Image.new("RGBA", (200, 200), (255, 165, 0, 255)).save(logo_buf, format="PNG")

        result = composite_logo(blank_image, logo_buf.getvalue())
        # Logo is placed at (margin, margin), not (0, 0) -- margin is
        # ~3% of width, so sample well inside the logo's actual bounds.
        margin = int(blank_image.width * 0.03)
        corner_pixel = result.convert("RGB").getpixel((margin + 20, margin + 20))
        assert corner_pixel != (255, 255, 255)

    def test_empty_logo_bytes_returns_image_unchanged(self, blank_image: Image.Image) -> None:
        result = composite_logo(blank_image, b"")
        assert result.size == blank_image.size


class TestRenderDisclaimerStrip:
    def test_output_is_taller_or_equal_and_same_width(self, blank_image: Image.Image) -> None:
        result = render_disclaimer_strip(
            blank_image, "Insurance is the subject matter of solicitation."
        )
        assert result.width == blank_image.width
        assert result.height == blank_image.height

    def test_bottom_strip_is_not_the_background_color(self, blank_image: Image.Image) -> None:
        result = render_disclaimer_strip(
            blank_image, "Insurance is the subject matter of solicitation."
        )
        bottom_pixel = result.convert("RGB").getpixel((5, result.height - 5))
        assert bottom_pixel != (255, 255, 255)

    def test_long_disclaimer_does_not_crash(self, blank_image: Image.Image) -> None:
        long_text = "T&C apply. " * 30
        result = render_disclaimer_strip(blank_image, long_text)
        assert result.height == blank_image.height


class TestOverlaySafezones:
    def test_returns_same_size(self, blank_image: Image.Image) -> None:
        result = overlay_safezones(blank_image)
        assert result.size == blank_image.size

    def test_top_region_is_tinted(self, blank_image: Image.Image) -> None:
        result = overlay_safezones(blank_image).convert("RGB")
        top_pixel = result.getpixel((5, 5))
        assert top_pixel != (255, 255, 255)


class TestComposeFinalImage:
    def test_returns_valid_png_bytes(self, brand: BrandProfileDTO) -> None:
        raw = _png_bytes()
        final = compose_final_image(
            raw, disclaimer="Insurance is the subject matter of solicitation."
        )
        img = Image.open(io.BytesIO(final))
        assert img.format == "PNG"

    def test_debug_safezone_flag_changes_output(self, brand: BrandProfileDTO) -> None:
        raw = _png_bytes()
        without = compose_final_image(
            raw,
            disclaimer="Insurance is the subject matter of solicitation.",
            debug_safezone=False,
        )
        with_safezone = compose_final_image(
            raw,
            disclaimer="Insurance is the subject matter of solicitation.",
            debug_safezone=True,
        )
        assert without != with_safezone

    def test_preserves_aspect_ratio_dimensions(self, brand: BrandProfileDTO) -> None:
        raw = _png_bytes(size=(1080, 1920))
        final = compose_final_image(
            raw, disclaimer="Insurance is the subject matter of solicitation."
        )
        img = Image.open(io.BytesIO(final))
        assert img.size == (1080, 1920)
