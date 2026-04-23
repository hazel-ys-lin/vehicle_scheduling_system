"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vehicles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("battery_level", sa.Float(), nullable=False, server_default="80.0"),
        sa.UniqueConstraint("name", name="uq_vehicles_name"),
        sa.CheckConstraint(
            "battery_level >= 0 AND battery_level <= 100", name="ck_vehicle_battery"
        ),
    )

    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "vehicle_id",
            sa.Integer(),
            sa.ForeignKey("vehicles.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_services_vehicle_id", "services", ["vehicle_id"])

    op.create_table(
        "service_stops",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "service_id",
            sa.Integer(),
            sa.ForeignKey("services.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=10), nullable=False),
        sa.Column("arrival_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("departure_time", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("service_id", "sequence", name="uq_service_stop_seq"),
    )
    op.create_index("ix_service_stops_service_id", "service_stops", ["service_id"])

    op.create_table(
        "block_configs",
        sa.Column("block_id", sa.String(length=10), primary_key=True),
        sa.Column("traversal_seconds", sa.Integer(), nullable=False, server_default="60"),
    )


def downgrade() -> None:
    op.drop_table("block_configs")
    op.drop_index("ix_service_stops_service_id", table_name="service_stops")
    op.drop_table("service_stops")
    op.drop_index("ix_services_vehicle_id", table_name="services")
    op.drop_table("services")
    op.drop_table("vehicles")
