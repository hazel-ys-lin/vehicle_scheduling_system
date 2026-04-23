from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.block_config import BlockConfig
from app.schemas.block_config import BlockConfigRead, BlockConfigUpdate
from app.topology import BLOCKS

router = APIRouter(prefix="/blocks", tags=["blocks"])


@router.get("", response_model=list[BlockConfigRead])
async def list_blocks(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BlockConfig).order_by(BlockConfig.block_id))
    return result.scalars().all()


@router.put("/{block_id}", response_model=BlockConfigRead)
async def update_block(
    block_id: str, payload: BlockConfigUpdate, db: AsyncSession = Depends(get_db)
):
    if block_id not in BLOCKS:
        raise HTTPException(status_code=404, detail=f"Block '{block_id}' not in topology")
    config = await db.get(BlockConfig, block_id)
    if not config:
        raise HTTPException(status_code=404, detail="Block config not found")
    config.traversal_seconds = payload.traversal_seconds
    await db.commit()
    await db.refresh(config)
    return config
