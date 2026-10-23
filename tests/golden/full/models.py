from typing import Annotated

from sqlmodel import Field, SQLModel


class Base(SQLModel):
    pass


class User(Base, table=True):
    """Пользователь."""

    id: Annotated[int, Field(primary_key=True)]
    name: str = ""


class Order(SQLModel, table=True):
    __tablename__ = "orders"

    id: int
    status: str = "new"
