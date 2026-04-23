from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (Index("ix_services_vehicle_id", "vehicle_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False)
    departure_battery: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    vehicle: Mapped["Vehicle"] = relationship(back_populates="services")  # noqa: F821
    stops: Mapped[list["ServiceStop"]] = relationship(
        back_populates="service", cascade="all, delete-orphan", order_by="ServiceStop.sequence"
    )


class ServiceStop(Base):
    """One entry per node in the service path (blocks + platforms + yard).

    Arrival/departure times are set only for platforms and the yard;
    block nodes have null times (traversal time comes from BlockConfig).
    """

    __tablename__ = "service_stops"
    __table_args__ = (
        UniqueConstraint("service_id", "sequence", name="uq_service_stop_seq"),
        Index("ix_service_stops_service_id", "service_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[str] = mapped_column(String(10), nullable=False)
    arrival_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    departure_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    service: Mapped["Service"] = relationship(back_populates="stops")
