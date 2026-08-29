"""Provider ABCs -- ported unchanged from the prototype's
app/providers/base.py.

Real Gemini implementations (gemini_text.py now; gemini_image.py/
gemini_video.py in Sub-phases E/F) and MockLLMProvider (providers/mock.py)
both implement these interfaces. Pipeline code (pipeline/*) depends only on
these ABCs, never on a concrete provider class.

All methods are synchronous, matching the underlying google-genai SDK calls
they wrap -- callers running inside the async arq worker offload a whole
pipeline call via asyncio.to_thread(...) rather than each provider call
being made async individually (see worker.py).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models.enums import CreativeQuality, VoiceoverMode
from app.modules.creatives.brand import BrandProfileDTO
from app.modules.creatives.domain import Brief, GeneratedConcept, ReelScene


@dataclass(slots=True)
class TextGenerationResult:
    text: str
    model_id: str
    estimated_cost_inr: float = 0.0


@dataclass(slots=True)
class ImageGenerationResult:
    image_bytes: bytes
    model_id: str
    mime_type: str = "image/png"
    interaction_id: str | None = None
    estimated_cost_inr: float = 0.0


@dataclass(slots=True)
class VideoClipResult:
    video_bytes: bytes | None
    video_uri: str | None
    model_id: str
    estimated_cost_inr: float = 0.0
    duration_s: float = 0.0


class LLMProvider(ABC):
    """Text generation: concept ideation plus a cheap freeform pass used by
    the compliance gate's second-opinion reviewer."""

    @abstractmethod
    def generate_concepts(
        self,
        brief: Brief,
        brand: BrandProfileDTO,
        count: int,
        prompt_version: str,
        *,
        feedback: dict[int, str] | None = None,
    ) -> list[GeneratedConcept]:
        """Returns `count` distinct-angle concepts. `feedback` maps a concept
        index to a violation description, for the single retry-with-feedback
        pass the compliance gate performs."""

    @abstractmethod
    def generate_text(self, prompt: str, *, system: str = "") -> TextGenerationResult:
        """Freeform completion, used for the compliance reviewer pass."""


class ImageProvider(ABC):
    """Image generation/editing (Sub-phase E). Carousel slide chaining is
    expressed via `previous_interaction_id` (stateful multi-turn editing),
    matching the real Interactions API -- not independent calls per slide."""

    @abstractmethod
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
    ) -> ImageGenerationResult: ...


class VideoProvider(ABC):
    """Video generation (Sub-phase F). Two real backends exist (omni, veo)
    with different call shapes and polling mechanics. `backend_name` is
    used to look up the right row of the cost table."""

    backend_name: str

    @abstractmethod
    def generate_clip(
        self,
        *,
        scene: ReelScene,
        aspect_ratio: str,
        quality: CreativeQuality,
        voiceover: VoiceoverMode,
        first_frame_image: bytes | None = None,
        reference_images: list[bytes] | None = None,
    ) -> VideoClipResult: ...


@dataclass(slots=True)
class ProviderBundle:
    """Everything the orchestrator needs for one job. Bundled so swapping
    mock <-> real providers is a single construction site."""

    llm: LLMProvider
    image: ImageProvider
    video: VideoProvider
