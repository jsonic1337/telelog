from os import getenv

from sqlmodel import SQLModel, create_engine

from db.models.logged_message import LoggedMessage, StoredFile  # noqa: F401

DATABASE_URL = getenv("DATABASE_URL", "sqlite:///database.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

def init():
    SQLModel.metadata.create_all(engine)
