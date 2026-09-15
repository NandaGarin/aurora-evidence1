"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("case_id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False, server_default="demo"),
    )
    op.create_table(
        "revisions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.String(length=36), sa.ForeignKey("cases.case_id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_revision", sa.Integer, nullable=False),
        sa.Column("claim_text", sa.Text, nullable=False),
        sa.Column("claim_text_sha256", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False, server_default="und"),
        sa.Column("image_asset_id", sa.String(length=72), nullable=True),
        sa.Column("as_of", sa.String(length=40), nullable=True),
        sa.Column("atom_set_id", sa.String(length=69), nullable=True),
        sa.Column("analysis_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "claim_revision", name="uq_case_revision"),
    )
    op.create_table(
        "media_assets",
        sa.Column("asset_id", sa.String(length=72), primary_key=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
        sa.Column("byte_size", sa.Integer, nullable=False),
        sa.Column("stored_path", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_media_assets_sha256", "media_assets", ["sha256"])
    op.create_table(
        "retrieval_runs",
        sa.Column("run_id", sa.String(length=36), primary_key=True),
        sa.Column("revision_id", sa.Integer, sa.ForeignKey("revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("retrieval_json", sa.JSON, nullable=False),
        sa.Column("run_inputs_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(length=36), primary_key=True),
        sa.Column("owner", sa.String(length=64), nullable=False, server_default="local"),
        sa.Column("endpoint", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("claim_revision", sa.Integer, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("request_json", sa.JSON, nullable=False),
        sa.Column("result_json", sa.JSON, nullable=True),
        sa.Column("error_json", sa.JSON, nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner", "endpoint", "idempotency_key", name="uq_idempotency"),
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("retrieval_runs")
    op.drop_index("ix_media_assets_sha256", table_name="media_assets")
    op.drop_table("media_assets")
    op.drop_table("revisions")
    op.drop_table("cases")
