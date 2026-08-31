"""Deterministic fake providers used by tests and dry-run requests --
ported from the prototype's app/providers/mock.py.

No network calls, ever. MockLLMProvider speaks the same PASS/FAIL protocol
as the real compliance reviewer prompt, so it can drive the exact same
compliance gate as GeminiTextProvider.
"""

from __future__ import annotations

import io
import itertools
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from app.models.enums import CreativeFormat, CreativeQuality, VoiceoverMode
from app.modules.creatives.brand import BrandProfileDTO
from app.modules.creatives.domain import (
    REEL_LIKE_FORMATS,
    Angle,
    Brief,
    CarouselSlide,
    GeneratedConcept,
    OnImageText,
    ReelScene,
    ReelScript,
)
from app.modules.creatives.providers.base import (
    ImageGenerationResult,
    ImageProvider,
    LLMProvider,
    TextGenerationResult,
    VideoClipResult,
    VideoProvider,
)

_MOCK_TEXT_MODEL = "mock-text"
_MOCK_IMAGE_MODEL = "mock-image"
_MOCK_VIDEO_MODEL = "mock-video"


def _padded_hashtags(bank: list[str]) -> list[str]:
    """GeneratedConcept requires 8-15 hashtags, but a brand's hashtag_bank
    isn't guaranteed to have that many (only the real LLM is required to
    always produce enough, from its own prompt-driven output) -- pad with
    deterministic filler tags so the mock provider always returns a valid
    concept regardless of how sparse the brand profile's bank is."""
    tags = list(dict.fromkeys(bank))
    filler = itertools.count(1)
    while len(tags) < 8:
        tags.append(f"#Mock{next(filler)}")
    return tags[:10]


class MockLLMProvider(LLMProvider):
    def generate_concepts(
        self,
        brief: Brief,
        brand: BrandProfileDTO,
        count: int,
        prompt_version: str,
        *,
        feedback: dict[int, str] | None = None,
    ) -> list[GeneratedConcept]:
        product = brand.product_line(brief.product_line)
        angles = list(itertools.islice(itertools.cycle(Angle), count))
        concepts: list[GeneratedConcept] = []
        for i, angle in enumerate(angles):
            hook_source = product.hooks[i % len(product.hooks)] if product.hooks else brief.topic
            note = ""
            if feedback and i in feedback:
                note = f" [revised after: {feedback[i]}]"
            headline = f"{hook_source.title()}?"[:60]
            caption = (
                f"{headline}\n\n"
                f"Here's what {product.label.lower()} actually covers, in plain terms.{note}\n\n"
                f"{brand.compliance.mandatory_disclaimer}"
            )
            carousel_slides = None
            if brief.format is CreativeFormat.carousel:
                slide_count = brief.carousel_slides or 3
                carousel_slides = [
                    CarouselSlide(
                        visual_prompt=f"Slide {s + 1}: {hook_source}, step {s + 1} of the "
                        f"story, {brand.name} palette",
                        headline=f"Step {s + 1}: {hook_source.title()}"[:60],
                        subhead="",
                    )
                    for s in range(slide_count)
                ]
            reel = None
            if brief.format in REEL_LIKE_FORMATS:
                scene_count = 3
                per_scene = max(1, min(8, (brief.reel_duration_s or 8) // scene_count))
                reel = ReelScript(
                    scenes=[
                        ReelScene(
                            visual_prompt=f"Scene {s + 1}: {hook_source}, {brand.name} palette",
                            vo_line=""
                            if brief.voiceover == VoiceoverMode.silent_text
                            else f"Line {s + 1}",
                            duration_s=per_scene,
                            on_screen_text=f"Step {s + 1}",
                        )
                        for s in range(scene_count)
                    ]
                )
            concepts.append(
                GeneratedConcept(
                    angle=angle,
                    hook=headline,
                    caption=caption,
                    hashtags=_padded_hashtags(brand.hashtag_bank),
                    cta=brand.cta_bank[i % len(brand.cta_bank)] if brand.cta_bank else "Learn more",
                    on_image_text=OnImageText(headline=headline[:60], subhead=hook_source[:90]),
                    image_prompt=(
                        f"{brief.topic}, {hook_source}, palette "
                        f"{brand.visual_identity.palette}, {brand.visual_identity.style_keywords}"
                    ),
                    negative_prompt=", ".join(brand.visual_identity.avoid),
                    reel=reel,
                    carousel_slides=carousel_slides,
                    disclaimer_line=brand.compliance.mandatory_disclaimer,
                    compliance_notes=[],
                )
            )
        return concepts

    def generate_text(self, prompt: str, *, system: str = "") -> TextGenerationResult:
        return TextGenerationResult(text="PASS", model_id=_MOCK_TEXT_MODEL)


class MockImageProvider(ImageProvider):
    def generate_image(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        aspect_ratio: str,
        resolution: str,
        quality: CreativeQuality,
        reference_images: list[bytes] | None = None,
        previous_interaction_id: str | None = None,
    ) -> ImageGenerationResult:
        width_by_ratio = {"4:5": (1080, 1350), "9:16": (1080, 1920), "1:1": (1080, 1080)}
        w, h = width_by_ratio.get(aspect_ratio, (1080, 1350))
        img = Image.new("RGB", (w, h), color="#0A2540")
        draw = ImageDraw.Draw(img)
        label = prompt[:80].replace("\n", " ")
        draw.rectangle([40, 40, w - 40, 160], outline="#F5A623", width=4)
        draw.text((60, 70), f"MOCK IMAGE\n{label}", fill="#FFFFFF")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return ImageGenerationResult(
            image_bytes=buf.getvalue(),
            model_id=_MOCK_IMAGE_MODEL,
            interaction_id=f"mock-interaction-{abs(hash(prompt)) % 100000}",
            estimated_cost_inr=0.0,
        )


class MockVideoProvider(VideoProvider):
    backend_name = "mock"

    def generate_clip(
        self,
        *,
        scene: ReelScene,
        aspect_ratio: str,
        quality: CreativeQuality,
        voiceover: VoiceoverMode,
        first_frame_image: bytes | None = None,
        reference_images: list[bytes] | None = None,
    ) -> VideoClipResult:
        video_bytes = self._render_placeholder_clip(scene, aspect_ratio)
        return VideoClipResult(
            video_bytes=video_bytes,
            video_uri=None,
            model_id=_MOCK_VIDEO_MODEL,
            estimated_cost_inr=0.0,
            duration_s=float(scene.duration_s),
        )

    @staticmethod
    def _render_placeholder_clip(scene: ReelScene, aspect_ratio: str) -> bytes:
        """Uses local ffmpeg (a lavfi color source) to produce a real, tiny,
        playable MP4 -- purely local, no network -- so downstream ffmpeg
        stitching code can be exercised in tests. Falls back to a stub byte
        string if ffmpeg isn't on PATH."""
        if shutil.which("ffmpeg") is None:
            return b"MOCK_VIDEO_PLACEHOLDER:" + scene.visual_prompt.encode("utf-8")

        size = "1080x1920" if aspect_ratio == "9:16" else "1080x1080"
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "mock_clip.mp4"
            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x0A2540:s={size}:d={scene.duration_s}",
                "-pix_fmt",
                "yuv420p",
                str(out_path),
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=30)
                return out_path.read_bytes()
            except (subprocess.SubprocessError, OSError):
                return b"MOCK_VIDEO_PLACEHOLDER:" + scene.visual_prompt.encode("utf-8")
