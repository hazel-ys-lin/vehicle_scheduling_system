from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BlockConfig(Base):
    __tablename__ = "block_configs"

    block_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    traversal_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
