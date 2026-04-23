from datetime import datetime

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.logic.conflict_detector import ConflictType


class ServiceStopCreate(BaseModel):
    sequence: int = Field(..., ge=0)
    node_id: str
    # AwareDatetime rejects naive inputs at the schema boundary so callers can't
    # accidentally depend on server-side UTC assumption.
    arrival_time: AwareDatetime | None = None
    departure_time: AwareDatetime | None = None


class ServiceStopRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    sequence: int
    node_id: str
    arrival_time: datetime | None
    departure_time: datetime | None


class ServiceCreate(BaseModel):
    vehicle_id: int
    stops: list[ServiceStopCreate] = Field(..., min_length=2)

    @model_validator(mode="after")
    def stops_ordered_by_sequence(self) -> "ServiceCreate":
        seqs = [s.sequence for s in self.stops]
        if seqs != sorted(seqs):
            raise ValueError("stops must be ordered by sequence")
        if len(seqs) != len(set(seqs)):
            raise ValueError("stop sequences must be unique")
        return self


class ServiceUpdate(BaseModel):
    vehicle_id: int | None = None
    stops: list[ServiceStopCreate] | None = Field(default=None, min_length=2)

    @model_validator(mode="after")
    def stops_ordered_if_present(self) -> "ServiceUpdate":
        if self.stops is not None:
            seqs = [s.sequence for s in self.stops]
            if seqs != sorted(seqs):
                raise ValueError("stops must be ordered by sequence")
            if len(seqs) != len(set(seqs)):
                raise ValueError("stop sequences must be unique")
        return self


class ServiceRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    vehicle_id: int
    created_at: datetime
    updated_at: datetime
    stops: list[ServiceStopRead]


class ConflictRead(BaseModel):
    conflict_type: ConflictType
    service_ids: list[int]
    description: str
    # For INTERLOCKING: the set of block ids the pair conflicts on (aggregated).
    # For battery / vehicle conflicts: empty list.
    locations: list[str] = []
