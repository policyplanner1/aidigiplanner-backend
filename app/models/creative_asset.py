from datetime import datetime

from sqlalchemy import CHAR, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TIMESTAMP_TYPE, SoftDeleteMixin, TimestampMixin, UUIDPKMixin
from app.db.types import enum_column_type
from app.models.enums import CreativeAssetKind


class CreativeAsset(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "creative_assets"

    concept_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("creative_concepts.id"), nullable=False, index=True
    )
    kind: Mapped[CreativeAssetKind] = mapped_column(
        enum_column_type(CreativeAssetKind), nullable=False
    )
    # Key handed to StorageService, never a raw filesystem path -- so
    # swapping LocalDiskStorage for S3Storage later needs no schema change.
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    estimated_cost_inr: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    actual_cost_inr: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(TIMESTAMP_TYPE, nullable=False)
