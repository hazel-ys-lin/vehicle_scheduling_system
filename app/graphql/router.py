"""Strawberry → FastAPI glue.

The schema is mounted at `/graphql`. Each request opens a fresh DB session
(via the existing `get_db` dependency) and attaches a `Loaders` bundle so
nested resolvers batch their lookups.
"""

import asyncio
from typing import Any

import strawberry
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from strawberry.fastapi import GraphQLRouter

from app.database import get_db
from app.graphql.loaders import Loaders
from app.graphql.resolvers import Query

schema = strawberry.Schema(query=Query)


async def _context(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    # Strawberry schedules sibling fields concurrently, but AsyncSession is not
    # safe for concurrent use. Serialize all DB access through one lock per
    # request — read-only gateway, so sequential execution is acceptable.
    return {"db": db, "loaders": Loaders(db), "db_lock": asyncio.Lock()}


graphql_router: GraphQLRouter = GraphQLRouter(schema, context_getter=_context)
