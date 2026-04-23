from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.logic.battery import current_battery, emit_baseline, emit_manual_adjust
from app.models.service import Service
from app.models.vehicle import Vehicle
from app.schemas.vehicle import VehicleCreate, VehicleRead, VehicleUpdate

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


async def _name_taken(db: AsyncSession, name: str, *, exclude_id: int | None = None) -> bool:
    stmt = select(Vehicle.id).where(Vehicle.name == name)
    if exclude_id is not None:
        stmt = stmt.where(Vehicle.id != exclude_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def _to_read(db: AsyncSession, vehicle: Vehicle) -> VehicleRead:
    level = await current_battery(db, vehicle.id)
    return VehicleRead(id=vehicle.id, name=vehicle.name, battery_level=level)


@router.get("", response_model=list[VehicleRead])
async def list_vehicles(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Vehicle).order_by(Vehicle.id))
    vehicles = list(result.scalars().all())
    return [await _to_read(db, v) for v in vehicles]


@router.post("", response_model=VehicleRead, status_code=201)
async def create_vehicle(payload: VehicleCreate, db: AsyncSession = Depends(get_db)):
    if await _name_taken(db, payload.name):
        raise HTTPException(status_code=409, detail=f"Vehicle name '{payload.name}' already exists")
    vehicle = Vehicle(name=payload.name)
    db.add(vehicle)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Vehicle name already exists") from exc

    await emit_baseline(db, vehicle.id, payload.battery_level, datetime.now(UTC))

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Vehicle name already exists") from exc
    await db.refresh(vehicle)
    return await _to_read(db, vehicle)


@router.get("/{vehicle_id}", response_model=VehicleRead)
async def get_vehicle(vehicle_id: int, db: AsyncSession = Depends(get_db)):
    vehicle = await db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return await _to_read(db, vehicle)


@router.patch("/{vehicle_id}", response_model=VehicleRead)
async def update_vehicle(
    vehicle_id: int, payload: VehicleUpdate, db: AsyncSession = Depends(get_db)
):
    vehicle = await db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    if payload.name is not None and await _name_taken(db, payload.name, exclude_id=vehicle_id):
        raise HTTPException(status_code=409, detail=f"Vehicle name '{payload.name}' already exists")

    if payload.name is not None:
        vehicle.name = payload.name

    if payload.battery_level is not None:
        await emit_manual_adjust(db, vehicle_id, payload.battery_level, datetime.now(UTC))

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Vehicle name already exists") from exc
    await db.refresh(vehicle)
    return await _to_read(db, vehicle)


@router.delete("/{vehicle_id}", status_code=204)
async def delete_vehicle(vehicle_id: int, db: AsyncSession = Depends(get_db)):
    vehicle = await db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    count = await db.execute(
        select(func.count()).select_from(Service).where(Service.vehicle_id == vehicle_id)
    )
    if count.scalar_one() > 0:
        raise HTTPException(
            status_code=409,
            detail=(f"Vehicle {vehicle_id} has associated services; delete those services first."),
        )

    await db.delete(vehicle)
    await db.commit()
