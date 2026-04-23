"""event-sourced battery: battery_events + services.departure_battery

Revision ID: 0002_battery_events
Revises: 0001_initial
Create Date: 2026-04-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_battery_events"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BATT_EVT_ENUM = sa.Enum(
    "BASELINE",
    "SERVICE_CONSUME",
    "YARD_CHARGE",
    "MANUAL_ADJUST",
    name="battery_event_type",
)


def upgrade() -> None:
    op.create_table(
        "battery_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "vehicle_id",
            sa.Integer(),
            sa.ForeignKey("vehicles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "service_id",
            sa.Integer(),
            sa.ForeignKey("services.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("event_type", BATT_EVT_ENUM, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delta", sa.Numeric(5, 2), nullable=False),
    )
    op.create_index("ix_batt_vehicle_time", "battery_events", ["vehicle_id", "occurred_at"])

    op.add_column(
        "services",
        sa.Column("departure_battery", sa.Float(), nullable=True),
    )

    # Backfill: one BASELINE event per vehicle (carries existing battery_level)
    op.execute(
        """
        INSERT INTO battery_events (vehicle_id, service_id, event_type, occurred_at, delta)
        SELECT id, NULL, 'BASELINE', NOW(), battery_level
        FROM vehicles
        """
    )

    # Backfill: seed departure_battery from vehicle baseline so existing rows satisfy NOT NULL.
    # Correct event-sourced value is recomputed on next write.
    op.execute(
        """
        UPDATE services s
        SET departure_battery = v.battery_level
        FROM vehicles v
        WHERE s.vehicle_id = v.id
        """
    )

    op.alter_column("services", "departure_battery", nullable=False)

    op.drop_constraint("ck_vehicle_battery", "vehicles", type_="check")
    op.drop_column("vehicles", "battery_level")


def downgrade() -> None:
    op.add_column(
        "vehicles",
        sa.Column(
            "battery_level",
            sa.Float(),
            nullable=False,
            server_default="80.0",
        ),
    )
    op.create_check_constraint(
        "ck_vehicle_battery",
        "vehicles",
        "battery_level >= 0 AND battery_level <= 100",
    )

    # Restore vehicle.battery_level from latest BASELINE/MANUAL_ADJUST cumulative sum
    op.execute(
        """
        UPDATE vehicles v
        SET battery_level = COALESCE((
            SELECT SUM(delta)::float
            FROM battery_events e
            WHERE e.vehicle_id = v.id
        ), 80.0)
        """
    )

    op.drop_column("services", "departure_battery")
    op.drop_index("ix_batt_vehicle_time", table_name="battery_events")
    op.drop_table("battery_events")
    BATT_EVT_ENUM.drop(op.get_bind(), checkfirst=True)
