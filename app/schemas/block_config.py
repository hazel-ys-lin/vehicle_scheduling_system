from pydantic import BaseModel, Field


class BlockConfigUpdate(BaseModel):
    traversal_seconds: int = Field(..., gt=0)


class BlockConfigRead(BaseModel):
    model_config = {"from_attributes": True}

    block_id: str
    traversal_seconds: int
