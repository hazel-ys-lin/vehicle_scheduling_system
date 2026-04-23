from pydantic import BaseModel, Field


class VehicleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    battery_level: float = Field(default=80.0, ge=0, le=100)


class VehicleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    battery_level: float | None = Field(default=None, ge=0, le=100)


class VehicleRead(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    battery_level: float
