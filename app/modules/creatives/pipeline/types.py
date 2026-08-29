"""Transient in-memory result of one rendered asset, before persistence.

The prototype's pipeline wrote straight to disk (Asset.path) as each image/
clip was produced. Here, pipeline/render_*.py functions stay synchronous
(matching the sync provider calls they wrap) and just return bytes in
memory; the async worker task persists them via StorageService and creates
the CreativeAsset DB row afterward, back on the event loop -- keeping the
storage write off the worker thread the sync pipeline runs in.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import CreativeAssetKind

VIDEO_MIME_TYPE = "video/mp4"


@dataclass(slots=True)
class RenderedAsset:
    kind: CreativeAssetKind
    # Position in the original (not filtered) ideation.concepts list --
    # matches CreativeConcept.concept_index, so the worker can map this
    # back to the right persisted concept row.
    concept_index: int
    label: str
    model_id: str
    mime_type: str
    data: bytes
    estimated_cost_inr: float = 0.0
    actual_cost_inr: float | None = None
