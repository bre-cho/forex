"""Add provider certification lifecycle fields.

Revision ID: 0025_provider_certification_lifecycle
Revises: 0024_provider_certification
Create Date: 2026-04-30
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0025_provider_certification_lifecycle"
down_revision = "0024_provider_certification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("provider_certifications", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("provider_certifications", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("provider_certifications", sa.Column("revoke_reason", sa.String(length=256), nullable=True))
    op.create_index("ix_provider_certifications_expires_at", "provider_certifications", ["expires_at"], unique=False)
    op.create_index("ix_provider_certifications_revoked_at", "provider_certifications", ["revoked_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_provider_certifications_revoked_at", table_name="provider_certifications")
    op.drop_index("ix_provider_certifications_expires_at", table_name="provider_certifications")
    op.drop_column("provider_certifications", "revoke_reason")
    op.drop_column("provider_certifications", "revoked_at")
    op.drop_column("provider_certifications", "expires_at")
