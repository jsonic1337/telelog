from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class LoggedMessage(SQLModel, table=True):
    __tablename__ = "logged_messages"
    __table_args__ = (
        UniqueConstraint(
            "business_connection_id",
            "chat_id",
            "message_id",
            name="uq_logged_message_business_chat_message",
        ),
    )

    db_id: int | None = Field(default=None, primary_key=True)
    business_connection_id: str = Field(index=True)
    chat_id: int = Field(index=True)
    message_id: int = Field(index=True)
    media_group_id: str | None = Field(default=None, index=True)

    content_type: str = Field(index=True)
    content: str = Field(default="")
    caption: str | None = None
    is_live_location: bool = Field(default=False)

    from_user_id: int | None = Field(default=None, index=True)
    from_username: str | None = None
    from_full_name: str | None = None
    chat_title: str | None = None

    raw_json: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: datetime | None = Field(default=None, index=True)
    saved_from_reply_at: datetime | None = Field(default=None)


class StoredFile(SQLModel, table=True):
    __tablename__ = "stored_files"

    id: int | None = Field(default=None, primary_key=True)
    message_db_id: int = Field(foreign_key="logged_messages.db_id", index=True)
    position: int = Field(default=0)

    content_type: str = Field(index=True)
    file_id: str
    file_unique_id: str | None = Field(default=None, index=True)
    local_path: str | None = None
    original_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    width: int | None = None
    height: int | None = None
    duration: int | None = None
