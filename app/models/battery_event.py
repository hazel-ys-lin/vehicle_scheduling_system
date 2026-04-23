"""Append-only battery ledger.

Each event records a delta (+charge / -consume / ±adjust) at a timestamp.
Current battery for a vehicle is `sum(delta where occurred_at <= now)`.
Departure battery for a service is `sum(delta where occurred_at < service_start)`.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BatteryEventType(StrEnum):
    BASELINE = "BASELINE"
    SERVICE_CONSUME = "SERVICE_CONSUME"
    YARD_CHARGE = "YARD_CHARGE"
    MANUAL_ADJUST = "MANUAL_ADJUST"


class BatteryEvent(Base):
    __tablename__ = "battery_events"
    __table_args__ = (
        Index("ix_batt_vehicle_time", "vehicle_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("services.id", ondelete="CASCADE"), nullable=True
    )
    event_type: Mapped[BatteryEventType] = mapped_column(
        Enum(BatteryEventType, name="battery_event_type"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delta: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
