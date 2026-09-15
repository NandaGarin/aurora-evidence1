"""ORM entities: cases, revisions, media assets, retrieval runs, jobs.

Design notes (contract-aligned):
- A Case is stable (case_id UUID). A Revision captures a specific
  (claim_text, image) snapshot; claim_revision increments when either changes.
- MediaAsset identity is asset_id = ``asset_`` + sha256(original bytes); the URI
  is transport-only and not part of identity.
- RetrievalRun stores the produced Retrieval section (as JSON) plus provenance.
- Job persists async work with lease/timeout/recovery and idempotency keys.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow


class Case(Base):
    __tablename__ = "cases"

    case_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    mode: Mapped[str] = mapped_column(String(8), default="demo")

    revisions: Mapped[list["Revision"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class Revision(Base):
    __tablename__ = "revisions"
    __table_args__ = (UniqueConstraint("case_id", "claim_revision", name="uq_case_revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id", ondelete="CASCADE"))
    claim_revision: Mapped[int] = mapped_column(Integer)
    claim_text: Mapped[str] = mapped_column(Text)
    claim_text_sha256: Mapped[str] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(16), default="und")
    image_asset_id: Mapped[str | None] = mapped_column(String(72), nullable=True)
    as_of: Mapped[str | None] = mapped_column(String(40), nullable=True)
    atom_set_id: Mapped[str | None] = mapped_column(String(69), nullable=True)
    analysis_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[Case] = relationship(back_populates="revisions")
    runs: Mapped[list["RetrievalRun"]] = relationship(
        back_populates="revision", cascade="all, delete-orphan"
    )


class MediaAsset(Base):
    __tablename__ = "media_assets"

    asset_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    media_type: Mapped[str] = mapped_column(String(64))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    byte_size: Mapped[int] = mapped_column(Integer)
    stored_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RetrievalRun(Base):
    __tablename__ = "retrieval_runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("revisions.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16))  # completed/partial/failed
    retrieval_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    run_inputs_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    revision: Mapped[Revision] = relationship(back_populates="runs")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("owner", "endpoint", "idempotency_key", name="uq_idempotency"),
    )

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), default="local")
    endpoint: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    payload_hash: Mapped[str] = mapped_column(String(64))  # JCS hash of request payload
    case_id: Mapped[str] = mapped_column(String(36))
    claim_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued/running/succeeded/partial/failed
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
