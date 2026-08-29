from sqlalchemy import CHAR, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import CreatedAtMixin, UUIDPKMixin


class ContentComment(UUIDPKMixin, CreatedAtMixin, Base):
    """Phase 20's "Add Comment" -- a lightweight, append-only comment thread
    on one CreativeConcept. Also filed automatically by
    CreativeService.request_changes() alongside its `reason`, so reviewer
    feedback shows up in the same thread whether it came from the
    dedicated reject action or a follow-up comment."""

    __tablename__ = "content_comments"

    concept_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("creative_concepts.id"), nullable=False, index=True
    )
    author_id: Mapped[str] = mapped_column(CHAR(36), ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
