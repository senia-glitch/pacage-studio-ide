from sqlmodel import SQLModel


class Base(SQLModel):
    pass


class User(Base, table=True):
    """Пользователь."""


class Order(SQLModel, table=True):
    __tablename__ = "orders"
