import asyncio
import json
import logging
import mimetypes
import sys
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from os import getenv
from pathlib import Path
from uuid import uuid4

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import BusinessMessagesDeleted, FSInputFile, Message
from aiogram.utils.media_group import MediaGroupBuilder
from dotenv import load_dotenv
from sqlmodel import Session as SQLSession
from sqlmodel import select

import db
from db.models.logged_message import LoggedMessage, StoredFile

load_dotenv()


def env_int(name: str, default: int) -> int:
    value = getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    value = getenv(name)
    if value is None or value == "":
        return default
    return value.lower() in {"1", "true", "yes", "on"}


TOKEN = getenv("BOT_TOKEN")
OWNER_ID = int(getenv("OWNER_ID", "0") or "0") or None
MEDIA_DIR = Path(getenv("MEDIA_DIR", "media"))
REPLY_MEDIA_CAPTURE_MODE = getenv("REPLY_MEDIA_CAPTURE_MODE", "protected").lower()
MEDIA_RETENTION_DAYS = env_int("MEDIA_RETENTION_DAYS", 7)
MEDIA_CLEANUP_INTERVAL_SECONDS = env_int("MEDIA_CLEANUP_INTERVAL_SECONDS", 3600)
DELETE_LOCAL_MEDIA_AFTER_REPORT = env_bool("DELETE_LOCAL_MEDIA_AFTER_REPORT", True)

dp = Dispatcher()
logger = logging.getLogger(__name__)
owner_cache: dict[str, int] = {}

MEDIA_TYPES = {
    "photo",
    "video",
    "document",
    "animation",
    "sticker",
    "voice",
    "audio",
    "video_note",
}
GROUPABLE_MEDIA_TYPES = {"photo", "video"}
CAPTION_MEDIA_TYPES = {"photo", "video", "document", "animation", "voice", "audio"}

TYPE_NAMES = {
    "unknown": "неизвестный тип",
    "any": "любой тип",
    "text": "текст",
    "photo": "фото",
    "video": "видео",
    "document": "документ",
    "animation": "анимация",
    "sticker": "стикер",
    "voice": "голосовое",
    "audio": "аудио",
    "video_note": "кружок",
    "location": "геопозиция",
    "contact": "контакт",
    "poll": "опрос",
    "dice": "кубик",
    "venue": "место",
    "game": "игра",
    "paid_media": "платное медиа",
    "story": "история",
    "checklist": "чеклист",
    "new_chat_members": "новые участники",
    "left_chat_member": "участник вышел",
    "new_chat_title": "новое название чата",
    "new_chat_photo": "новое фото чата",
    "delete_chat_photo": "фото чата удалено",
    "group_chat_created": "создана группа",
    "supergroup_chat_created": "создана супергруппа",
    "channel_chat_created": "создан канал",
    "message_auto_delete_timer_changed": "изменен таймер автоудаления",
    "migrate_to_chat_id": "чат мигрировал",
    "migrate_from_chat_id": "чат мигрировал из",
    "pinned_message": "закрепленное сообщение",
    "invoice": "счет",
    "successful_payment": "успешный платеж",
    "refunded_payment": "возврат платежа",
    "users_shared": "пользователи отправлены",
    "user_shared": "пользователь отправлен",
    "chat_shared": "чат отправлен",
    "gift": "подарок",
    "unique_gift": "уникальный подарок",
    "connected_website": "подключенный сайт",
    "write_access_allowed": "разрешен доступ на запись",
    "passport_data": "паспортные данные",
    "proximity_alert_triggered": "сработало сближение",
    "boost_added": "добавлен буст",
    "chat_background_set": "изменен фон чата",
    "checklist_tasks_done": "задачи чеклиста отмечены",
    "checklist_tasks_added": "задачи чеклиста добавлены",
    "direct_message_price_changed": "изменена цена личных сообщений",
    "forum_topic_created": "создана тема форума",
    "forum_topic_edited": "изменена тема форума",
    "forum_topic_closed": "тема форума закрыта",
    "forum_topic_reopened": "тема форума открыта",
    "general_forum_topic_hidden": "общая тема скрыта",
    "general_forum_topic_unhidden": "общая тема показана",
    "giveaway_created": "розыгрыш создан",
    "giveaway": "розыгрыш",
    "giveaway_winners": "победители розыгрыша",
    "giveaway_completed": "розыгрыш завершен",
    "paid_message_price_changed": "изменена цена платных сообщений",
    "video_chat_scheduled": "видеочат запланирован",
    "video_chat_started": "видеочат начат",
    "video_chat_ended": "видеочат завершен",
    "video_chat_participants_invited": "участники приглашены в видеочат",
    "web_app_data": "данные web app",
    "message_thread_id": "ветка сообщений",
    "paid_star_count": "оплаченные звезды",
}

CONTENT_FIELD_ALIASES = {
    "user_shared": "user_shared",
    "users_shared": "users_shared",
}


@dataclass(slots=True)
class FileSource:
    content_type: str
    file_id: str
    file_unique_id: str | None
    extension: str
    original_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    width: int | None = None
    height: int | None = None
    duration: int | None = None


@dataclass(slots=True)
class FileData:
    content_type: str
    file_id: str
    file_unique_id: str | None
    local_path: str | None
    position: int = 0
    original_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    width: int | None = None
    height: int | None = None
    duration: int | None = None


@dataclass(slots=True)
class MessageData:
    business_connection_id: str
    chat_id: int
    message_id: int
    content_type: str
    content: str
    caption: str | None
    media_group_id: str | None
    is_live_location: bool
    from_user_id: int | None
    from_username: str | None
    from_full_name: str | None
    chat_title: str | None
    files: list[FileData]
    raw_json: str | None = None
    db_id: int | None = None
    deleted_at: datetime | None = None
    saved_from_reply_at: datetime | None = None
    mark_saved_from_reply: bool = False


def utcnow() -> datetime:
    return datetime.utcnow()


def html_escape(value: object) -> str:
    return escape(str(value), quote=False)


def enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def compact(value: str | None, limit: int = 2600) -> str:
    if not value:
        return ""
    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return f"{value[:limit]}\n...[обрезано {omitted} символов]"


def object_summary(value: object, limit: int = 1400) -> str:
    if value is None:
        return ""

    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump(exclude_none=True, mode="json")
            return compact(json.dumps(dumped, ensure_ascii=False), limit)
        except Exception:
            logger.debug("Failed to dump Telegram object", exc_info=True)

    return compact(str(value), limit)


def user_name(user: object) -> str:
    full_name = getattr(user, "full_name", None)
    username = getattr(user, "username", None)
    user_id = getattr(user, "id", None)

    if username:
        return f"@{username}"
    if full_name:
        return full_name
    return str(user_id or "неизвестно")


def safe_suffix(file_name: str | None, mime_type: str | None, fallback: str) -> str:
    if file_name:
        suffix = Path(file_name).suffix
        if suffix and len(suffix) <= 12:
            return suffix.lower()

    if mime_type:
        suffix = mimetypes.guess_extension(mime_type.split(";")[0].strip())
        if suffix:
            return suffix.lower()

    return fallback


def content_type_of(message: Message) -> str:
    return enum_value(message.content_type)


def plain_chat_title(message: Message) -> str:
    chat = message.chat
    first_name = getattr(chat, "first_name", None)
    last_name = getattr(chat, "last_name", None)
    full_name = " ".join(part for part in (first_name, last_name) if part)
    return chat.title or full_name or chat.username or str(chat.id)


def html_chat_label(data: MessageData) -> str:
    title = data.chat_title or str(data.chat_id)
    return f"{html_escape(title)} <code>{data.chat_id}</code>"


def html_author_label(data: MessageData) -> str:
    if data.from_username:
        return f"@{html_escape(data.from_username)}"
    if data.from_user_id:
        name = data.from_full_name or str(data.from_user_id)
        return f'<a href="tg://user?id={data.from_user_id}">{html_escape(name)}</a>'
    return html_escape(data.from_full_name or "неизвестно")


def type_name(content_type: str) -> str:
    return TYPE_NAMES.get(content_type, content_type)


def has_media_payload(data: MessageData) -> bool:
    return bool(data.files) or data.content_type in MEDIA_TYPES or data.content_type == "paid_media"


def base_context(data: MessageData) -> list[str]:
    return [
        f"Чат: {html_chat_label(data)}",
        f"Автор: {html_author_label(data)}",
        f"ID: <code>{data.message_id}</code>",
        f"Тип: {html_escape(type_name(data.content_type))}",
    ]


def message_content(message: Message, content_type: str) -> tuple[str, bool]:
    if content_type == "text":
        return message.text or "", False

    if content_type in MEDIA_TYPES:
        return message.caption or "", False

    if content_type == "location" and message.location:
        location = message.location
        content = f"{location.latitude}, {location.longitude}"
        return content, bool(getattr(location, "live_period", None))

    if content_type == "contact" and message.contact:
        contact = message.contact
        name = " ".join(
            part for part in (contact.first_name, contact.last_name) if part
        )
        phone = f"+{contact.phone_number}" if contact.phone_number else ""
        return ", ".join(part for part in (name, phone) if part), False

    if content_type == "poll" and message.poll:
        poll = message.poll
        options = ", ".join(option.text for option in poll.options)
        return f"{poll.question}\nВарианты: {options}" if options else poll.question, False

    if content_type == "dice" and message.dice:
        return f"{message.dice.emoji} -> {message.dice.value}", False

    if content_type == "venue" and message.venue:
        venue = message.venue
        return f"{venue.title}, {venue.address}", False

    if content_type == "game" and message.game:
        return message.game.title, False

    if content_type == "paid_media" and message.paid_media:
        media_types = [
            type_name(enum_value(item.type)) for item in message.paid_media.paid_media
        ]
        details = ", ".join(media_types) if media_types else "без доступных элементов"
        return f"Платное медиа: {message.paid_media.star_count} Stars; {details}", False

    if content_type == "story" and message.story:
        story = message.story
        story_chat = story.chat.title or story.chat.username or str(story.chat.id)
        return f"История: {story_chat}, ID {story.id}", False

    if content_type == "checklist" and message.checklist:
        tasks = [
            f"{task.id}. {task.text}"
            for task in message.checklist.tasks
        ]
        body = "\n".join(tasks)
        return f"{message.checklist.title}\n{body}" if body else message.checklist.title, False

    if content_type == "new_chat_members" and message.new_chat_members:
        return ", ".join(user_name(user) for user in message.new_chat_members), False

    if content_type == "left_chat_member" and message.left_chat_member:
        return user_name(message.left_chat_member), False

    if content_type == "new_chat_title" and message.new_chat_title:
        return message.new_chat_title, False

    if content_type == "message_auto_delete_timer_changed" and message.message_auto_delete_timer_changed:
        seconds = message.message_auto_delete_timer_changed.message_auto_delete_time
        return f"Новый таймер автоудаления: {seconds} сек.", False

    if content_type == "migrate_to_chat_id" and message.migrate_to_chat_id:
        return f"Новый chat_id: {message.migrate_to_chat_id}", False

    if content_type == "migrate_from_chat_id" and message.migrate_from_chat_id:
        return f"Старый chat_id: {message.migrate_from_chat_id}", False

    if content_type == "pinned_message" and message.pinned_message:
        pinned_type = content_type_of(message.pinned_message)
        return (
            f"Закреплено сообщение ID {message.pinned_message.message_id}, "
            f"тип: {type_name(pinned_type)}"
        ), False

    if content_type == "invoice" and message.invoice:
        return f"{message.invoice.title}\n{message.invoice.description}", False

    if content_type == "successful_payment" and message.successful_payment:
        payment = message.successful_payment
        return f"{payment.total_amount} {payment.currency}", False

    if content_type == "refunded_payment" and message.refunded_payment:
        payment = message.refunded_payment
        return f"{payment.total_amount} {payment.currency}", False

    if content_type == "web_app_data" and message.web_app_data:
        return f"{message.web_app_data.button_text}\n{message.web_app_data.data}", False

    field_name = CONTENT_FIELD_ALIASES.get(content_type, content_type)
    value = getattr(message, field_name, None)
    if value is not None:
        summary = object_summary(value)
        return summary or f"[{content_type}]", False

    return f"[{content_type}]", False


def collect_file_sources(message: Message, content_type: str) -> list[FileSource]:
    if content_type == "photo" and message.photo:
        photo = message.photo[-1]
        return [
            FileSource(
                content_type="photo",
                file_id=photo.file_id,
                file_unique_id=photo.file_unique_id,
                extension=".jpg",
                file_size=getattr(photo, "file_size", None),
                width=photo.width,
                height=photo.height,
            )
        ]

    if content_type == "video" and message.video:
        video = message.video
        return [
            FileSource(
                content_type="video",
                file_id=video.file_id,
                file_unique_id=video.file_unique_id,
                extension=safe_suffix(None, video.mime_type, ".mp4"),
                mime_type=video.mime_type,
                file_size=video.file_size,
                width=video.width,
                height=video.height,
                duration=video.duration,
            )
        ]

    if content_type == "document" and message.document:
        document = message.document
        return [
            FileSource(
                content_type="document",
                file_id=document.file_id,
                file_unique_id=document.file_unique_id,
                extension=safe_suffix(document.file_name, document.mime_type, ".bin"),
                original_name=document.file_name,
                mime_type=document.mime_type,
                file_size=document.file_size,
            )
        ]

    if content_type == "animation" and message.animation:
        animation = message.animation
        return [
            FileSource(
                content_type="animation",
                file_id=animation.file_id,
                file_unique_id=animation.file_unique_id,
                extension=safe_suffix(animation.file_name, animation.mime_type, ".mp4"),
                original_name=animation.file_name,
                mime_type=animation.mime_type,
                file_size=animation.file_size,
                width=animation.width,
                height=animation.height,
                duration=animation.duration,
            )
        ]

    if content_type == "sticker" and message.sticker:
        sticker = message.sticker
        if getattr(sticker, "is_video", False):
            extension = ".webm"
        elif getattr(sticker, "is_animated", False):
            extension = ".tgs"
        else:
            extension = ".webp"
        return [
            FileSource(
                content_type="sticker",
                file_id=sticker.file_id,
                file_unique_id=sticker.file_unique_id,
                extension=extension,
                file_size=sticker.file_size,
                width=sticker.width,
                height=sticker.height,
            )
        ]

    if content_type == "voice" and message.voice:
        voice = message.voice
        return [
            FileSource(
                content_type="voice",
                file_id=voice.file_id,
                file_unique_id=voice.file_unique_id,
                extension=safe_suffix(None, voice.mime_type, ".ogg"),
                mime_type=voice.mime_type,
                file_size=voice.file_size,
                duration=voice.duration,
            )
        ]

    if content_type == "audio" and message.audio:
        audio = message.audio
        return [
            FileSource(
                content_type="audio",
                file_id=audio.file_id,
                file_unique_id=audio.file_unique_id,
                extension=safe_suffix(audio.file_name, audio.mime_type, ".mp3"),
                original_name=audio.file_name,
                mime_type=audio.mime_type,
                file_size=audio.file_size,
                duration=audio.duration,
            )
        ]

    if content_type == "video_note" and message.video_note:
        video_note = message.video_note
        return [
            FileSource(
                content_type="video_note",
                file_id=video_note.file_id,
                file_unique_id=video_note.file_unique_id,
                extension=".mp4",
                file_size=video_note.file_size,
                duration=video_note.duration,
            )
        ]

    if content_type == "paid_media" and message.paid_media:
        sources: list[FileSource] = []
        for item in message.paid_media.paid_media:
            item_type = enum_value(item.type)
            if item_type == "photo" and getattr(item, "photo", None):
                photo = item.photo[-1]
                sources.append(
                    FileSource(
                        content_type="photo",
                        file_id=photo.file_id,
                        file_unique_id=photo.file_unique_id,
                        extension=".jpg",
                        file_size=getattr(photo, "file_size", None),
                        width=photo.width,
                        height=photo.height,
                    )
                )
            elif item_type == "video" and getattr(item, "video", None):
                video = item.video
                sources.append(
                    FileSource(
                        content_type="video",
                        file_id=video.file_id,
                        file_unique_id=video.file_unique_id,
                        extension=safe_suffix(None, video.mime_type, ".mp4"),
                        mime_type=video.mime_type,
                        file_size=video.file_size,
                        width=video.width,
                        height=video.height,
                        duration=video.duration,
                    )
                )
        return sources

    if content_type == "new_chat_photo" and message.new_chat_photo:
        photo = message.new_chat_photo[-1]
        return [
            FileSource(
                content_type="photo",
                file_id=photo.file_id,
                file_unique_id=photo.file_unique_id,
                extension=".jpg",
                file_size=getattr(photo, "file_size", None),
                width=photo.width,
                height=photo.height,
            )
        ]

    if content_type == "gift" and message.gift and message.gift.sticker:
        sticker = message.gift.sticker
        if getattr(sticker, "is_video", False):
            extension = ".webm"
        elif getattr(sticker, "is_animated", False):
            extension = ".tgs"
        else:
            extension = ".webp"
        return [
            FileSource(
                content_type="sticker",
                file_id=sticker.file_id,
                file_unique_id=sticker.file_unique_id,
                extension=extension,
                file_size=sticker.file_size,
                width=sticker.width,
                height=sticker.height,
            )
        ]

    return []


def reusable_local_path(
    source: FileSource, previous: MessageData | None, position: int
) -> str | None:
    if not previous:
        return None

    candidates: list[FileData] = []
    if position < len(previous.files):
        candidates.append(previous.files[position])
    candidates.extend(previous.files)

    for file in candidates:
        same_unique_id = (
            source.file_unique_id
            and file.file_unique_id
            and source.file_unique_id == file.file_unique_id
        )
        same_file_id = source.file_id == file.file_id
        if file.content_type == source.content_type and (same_unique_id or same_file_id):
            if file.local_path and Path(file.local_path).exists():
                return file.local_path

    return None


async def download_source(bot: Bot, source: FileSource) -> str | None:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    local_path = (MEDIA_DIR / f"{uuid4().hex}{source.extension}").resolve()

    try:
        telegram_file = await bot.get_file(source.file_id)
        await bot.download_file(telegram_file.file_path, destination=local_path)
        return str(local_path)
    except Exception:
        logger.exception("Failed to download Telegram file %s", source.file_id)
        local_path.unlink(missing_ok=True)
        return None


async def build_message_data(
    message: Message,
    *,
    bot: Bot | None = None,
    previous: MessageData | None = None,
    business_connection_id: str | None = None,
    mark_saved_from_reply: bool = False,
) -> MessageData:
    content_type = content_type_of(message)
    content, is_live_location = message_content(message, content_type)
    files: list[FileData] = []

    for position, source in enumerate(collect_file_sources(message, content_type)):
        local_path = reusable_local_path(source, previous, position)
        if not local_path:
            local_path = await download_source(bot or message.bot, source)
        files.append(
            FileData(
                content_type=source.content_type,
                file_id=source.file_id,
                file_unique_id=source.file_unique_id,
                local_path=local_path,
                position=position,
                original_name=source.original_name,
                mime_type=source.mime_type,
                file_size=source.file_size,
                width=source.width,
                height=source.height,
                duration=source.duration,
            )
        )

    from_user = message.from_user
    raw_json = None
    try:
        raw_json = message.model_dump_json(exclude_none=True)
    except Exception:
        logger.debug("Failed to serialize message %s", message.message_id, exc_info=True)

    return MessageData(
        business_connection_id=business_connection_id
        or message.business_connection_id
        or "",
        chat_id=message.chat.id,
        message_id=message.message_id,
        content_type=content_type,
        content=content,
        caption=message.caption,
        media_group_id=message.media_group_id,
        is_live_location=is_live_location,
        from_user_id=from_user.id if from_user else None,
        from_username=from_user.username if from_user else None,
        from_full_name=from_user.full_name if from_user else None,
        chat_title=plain_chat_title(message),
        files=files,
        raw_json=raw_json,
        mark_saved_from_reply=mark_saved_from_reply,
    )


def select_message(
    business_connection_id: str, chat_id: int, message_id: int
):
    return (
        select(LoggedMessage)
        .where(LoggedMessage.business_connection_id == business_connection_id)
        .where(LoggedMessage.chat_id == chat_id)
        .where(LoggedMessage.message_id == message_id)
    )


def load_message(
    session: SQLSession, business_connection_id: str, chat_id: int, message_id: int
) -> MessageData | None:
    message = session.exec(
        select_message(business_connection_id, chat_id, message_id)
    ).first()
    if not message:
        return None

    files = session.exec(
        select(StoredFile)
        .where(StoredFile.message_db_id == message.db_id)
        .order_by(StoredFile.position, StoredFile.id)
    ).all()

    return MessageData(
        db_id=message.db_id,
        business_connection_id=message.business_connection_id,
        chat_id=message.chat_id,
        message_id=message.message_id,
        media_group_id=message.media_group_id,
        content_type=message.content_type,
        content=message.content,
        caption=message.caption,
        is_live_location=message.is_live_location,
        from_user_id=message.from_user_id,
        from_username=message.from_username,
        from_full_name=message.from_full_name,
        chat_title=message.chat_title,
        files=[
            FileData(
                content_type=file.content_type,
                file_id=file.file_id,
                file_unique_id=file.file_unique_id,
                local_path=file.local_path,
                position=file.position,
                original_name=file.original_name,
                mime_type=file.mime_type,
                file_size=file.file_size,
                width=file.width,
                height=file.height,
                duration=file.duration,
            )
            for file in files
        ],
        raw_json=message.raw_json,
        deleted_at=message.deleted_at,
        saved_from_reply_at=message.saved_from_reply_at,
    )


def save_message(session: SQLSession, data: MessageData) -> None:
    now = utcnow()
    message = session.exec(
        select_message(data.business_connection_id, data.chat_id, data.message_id)
    ).first()

    if not message:
        message = LoggedMessage(
            business_connection_id=data.business_connection_id,
            chat_id=data.chat_id,
            message_id=data.message_id,
            created_at=now,
        )

    message.media_group_id = data.media_group_id
    message.content_type = data.content_type
    message.content = data.content or ""
    message.caption = data.caption
    message.is_live_location = data.is_live_location
    message.from_user_id = data.from_user_id
    message.from_username = data.from_username
    message.from_full_name = data.from_full_name
    message.chat_title = data.chat_title
    message.raw_json = data.raw_json
    message.updated_at = now
    message.deleted_at = None
    if data.mark_saved_from_reply and not message.saved_from_reply_at:
        message.saved_from_reply_at = now

    session.add(message)
    session.flush()

    old_files = session.exec(
        select(StoredFile).where(StoredFile.message_db_id == message.db_id)
    ).all()
    for old_file in old_files:
        session.delete(old_file)

    for file in data.files:
        session.add(
            StoredFile(
                message_db_id=message.db_id,
                position=file.position,
                content_type=file.content_type,
                file_id=file.file_id,
                file_unique_id=file.file_unique_id,
                local_path=file.local_path,
                original_name=file.original_name,
                mime_type=file.mime_type,
                file_size=file.file_size,
                width=file.width,
                height=file.height,
                duration=file.duration,
            )
        )

    session.commit()


def mark_deleted(session: SQLSession, data: MessageData) -> None:
    message = session.exec(
        select_message(data.business_connection_id, data.chat_id, data.message_id)
    ).first()
    if not message:
        return

    message.deleted_at = message.deleted_at or utcnow()
    session.add(message)
    session.commit()


def safe_media_file(path_value: str | None) -> Path | None:
    if not path_value:
        return None

    try:
        media_root = MEDIA_DIR.resolve()
        path = Path(path_value).resolve()
        path.relative_to(media_root)
    except (OSError, ValueError):
        return None

    return path


def delete_media_file(path_value: str | None) -> bool:
    path = safe_media_file(path_value)
    if not path or not path.exists() or not path.is_file():
        return False

    try:
        path.unlink()
        return True
    except OSError:
        logger.warning("Failed to delete media file %s", path, exc_info=True)
        return False


def clear_local_paths_for_messages(
    session: SQLSession,
    messages: list[MessageData],
    *,
    keep_paths: set[str] | None = None,
) -> None:
    keep_paths = keep_paths or set()
    paths = {
        file.local_path
        for message in messages
        for file in message.files
        if file.local_path and file.local_path not in keep_paths
    }
    if not paths:
        return

    for path in paths:
        delete_media_file(path)
        rows = session.exec(select(StoredFile).where(StoredFile.local_path == path)).all()
        for row in rows:
            row.local_path = None
            session.add(row)

    session.commit()

    for message in messages:
        for file in message.files:
            if file.local_path in paths:
                file.local_path = None


def cleanup_media_once() -> None:
    if MEDIA_RETENTION_DAYS <= 0:
        return

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = utcnow() - timedelta(days=MEDIA_RETENTION_DAYS)
    tracked_paths: set[str] = set()

    with SQLSession(db.engine) as session:
        rows = session.exec(select(StoredFile).where(StoredFile.local_path != None)).all()
        for row in rows:
            path = safe_media_file(row.local_path)
            if not path or not path.exists():
                row.local_path = None
                session.add(row)
                continue

            tracked_paths.add(str(path))
            modified_at = datetime.utcfromtimestamp(path.stat().st_mtime)
            if modified_at < cutoff:
                delete_media_file(row.local_path)
                row.local_path = None
                session.add(row)

        session.commit()

    for path in MEDIA_DIR.rglob("*"):
        if not path.is_file():
            continue
        resolved = str(path.resolve())
        if resolved in tracked_paths:
            continue
        modified_at = datetime.utcfromtimestamp(path.stat().st_mtime)
        if modified_at < cutoff:
            delete_media_file(str(path))


async def cleanup_media_loop() -> None:
    if MEDIA_RETENTION_DAYS <= 0 or MEDIA_CLEANUP_INTERVAL_SECONDS <= 0:
        return

    while True:
        try:
            await asyncio.to_thread(cleanup_media_once)
        except Exception:
            logger.exception("Media cleanup failed")

        await asyncio.sleep(MEDIA_CLEANUP_INTERVAL_SECONDS)


async def owner_chat_id(bot: Bot, business_connection_id: str) -> int:
    if business_connection_id in owner_cache:
        return owner_cache[business_connection_id]

    try:
        connection = await bot.get_business_connection(business_connection_id)
    except Exception:
        if OWNER_ID:
            return OWNER_ID
        raise

    owner_cache[business_connection_id] = connection.user_chat_id
    return connection.user_chat_id


async def send_notice(bot: Bot, chat_id: int, text: str) -> None:
    await bot.send_message(chat_id=chat_id, text=text)


def media_input(file: FileData) -> FSInputFile | str:
    if file.local_path and Path(file.local_path).exists():
        return FSInputFile(file.local_path)
    return file.file_id


async def send_file(
    bot: Bot, chat_id: int, file: FileData, caption: str | None = None
) -> None:
    media = media_input(file)
    kwargs = {"caption": caption} if caption and file.content_type in CAPTION_MEDIA_TYPES else {}

    if file.content_type == "photo":
        await bot.send_photo(chat_id=chat_id, photo=media, **kwargs)
    elif file.content_type == "video":
        await bot.send_video(chat_id=chat_id, video=media, **kwargs)
    elif file.content_type == "document":
        await bot.send_document(chat_id=chat_id, document=media, **kwargs)
    elif file.content_type == "animation":
        await bot.send_animation(chat_id=chat_id, animation=media, **kwargs)
    elif file.content_type == "voice":
        await bot.send_voice(chat_id=chat_id, voice=media, **kwargs)
    elif file.content_type == "audio":
        await bot.send_audio(chat_id=chat_id, audio=media, **kwargs)
    elif file.content_type == "video_note":
        await bot.send_video_note(chat_id=chat_id, video_note=media)
    elif file.content_type == "sticker":
        await bot.send_sticker(chat_id=chat_id, sticker=media)
    else:
        await send_notice(
            bot,
            chat_id,
            f"Неизвестный тип файла: <code>{html_escape(file.content_type)}</code>",
        )


async def try_send_media_group(
    bot: Bot, chat_id: int, files: list[FileData], caption: str | None
) -> bool:
    if len(files) < 2:
        return False
    if any(file.content_type not in GROUPABLE_MEDIA_TYPES for file in files):
        return False

    try:
        for offset in range(0, len(files), 10):
            chunk = files[offset : offset + 10]
            builder = MediaGroupBuilder(caption=caption if offset == 0 else None)
            for file in chunk:
                builder.add(type=file.content_type, media=media_input(file))
            await bot.send_media_group(chat_id=chat_id, media=builder.build())
        return True
    except Exception:
        logger.exception("Failed to send media group, falling back to single messages")
        return False


def render_content_block(label: str, value: str | None, limit: int = 2600) -> list[str]:
    if not value:
        return []
    return ["", f"<b>{label}:</b>", html_escape(compact(value, limit))]


def deleted_notice(data: MessageData, title: str = "🗑 Удалено сообщение") -> str:
    lines = [f"<b>{title}</b>", *base_context(data)]

    if has_media_payload(data):
        lines.extend(render_content_block("Подпись", data.caption or data.content, 900))
    else:
        lines.extend(render_content_block("Содержимое", data.content))

    return "\n".join(lines)


async def send_message_copy(
    bot: Bot, chat_id: int, data: MessageData, title: str
) -> None:
    notice = deleted_notice(data, title)

    if not data.files:
        await send_notice(bot, chat_id, notice)
        return

    first_media_type = data.files[0].content_type
    caption = notice if len(notice) <= 950 and first_media_type in CAPTION_MEDIA_TYPES else None
    if not caption:
        await send_notice(bot, chat_id, notice)

    if await try_send_media_group(bot, chat_id, data.files, caption):
        return

    for index, file in enumerate(data.files):
        await send_file(bot, chat_id, file, caption if index == 0 else None)


def group_deleted_messages(messages: list[MessageData]) -> list[list[MessageData]]:
    by_group: dict[str, list[MessageData]] = defaultdict(list)
    for message in messages:
        if message.media_group_id:
            by_group[message.media_group_id].append(message)

    result: list[list[MessageData]] = []
    used: set[tuple[str, int, int]] = set()
    for message in messages:
        key = (message.business_connection_id, message.chat_id, message.message_id)
        if key in used:
            continue

        group = by_group.get(message.media_group_id or "")
        if (
            group
            and len(group) > 1
            and all(item.files for item in group)
            and all(file.content_type in GROUPABLE_MEDIA_TYPES for item in group for file in item.files)
        ):
            ordered = sorted(group, key=lambda item: item.message_id)
            result.append(ordered)
            used.update(
                (item.business_connection_id, item.chat_id, item.message_id)
                for item in ordered
            )
        else:
            result.append([message])
            used.add(key)

    return result


async def send_deleted_group(
    bot: Bot, chat_id: int, messages: list[MessageData]
) -> None:
    if len(messages) == 1:
        await send_message_copy(bot, chat_id, messages[0], "🗑 Удалено сообщение")
        return

    first = messages[0]
    files = [file for message in messages for file in message.files]
    caption_lines = [
        "<b>🗑 Удален альбом</b>",
        *base_context(first),
        f"Сообщений: <code>{len(messages)}</code>",
    ]
    captions = [
        f"{message.message_id}: {message.caption or message.content}"
        for message in messages
        if message.caption or message.content
    ]
    if captions:
        caption_lines.extend(["", "<b>Подписи:</b>", html_escape(compact("\n".join(captions), 700))])

    caption = "\n".join(caption_lines)
    if len(caption) > 950:
        await send_notice(bot, chat_id, caption)
        caption = None

    if not await try_send_media_group(bot, chat_id, files, caption):
        if caption:
            await send_notice(bot, chat_id, caption)
        for index, file in enumerate(files):
            await send_file(bot, chat_id, file, None)


def file_signature(data: MessageData) -> list[tuple[str, str]]:
    return [
        (file.content_type, file.file_unique_id or file.file_id)
        for file in data.files
    ]


def changed(old: MessageData | None, new: MessageData) -> bool:
    if not old:
        return True
    return (
        old.content_type != new.content_type
        or old.content != new.content
        or old.caption != new.caption
        or old.is_live_location != new.is_live_location
        or file_signature(old) != file_signature(new)
    )


async def send_edit_notice(
    bot: Bot, chat_id: int, old: MessageData | None, new: MessageData
) -> None:
    if not old:
        await send_message_copy(
            bot,
            chat_id,
            new,
            "✏️ Отредактировано неизвестное ранее сообщение",
        )
        return

    if (
        old.content_type == "location"
        and old.is_live_location
        and new.content_type == "location"
        and not new.is_live_location
    ):
        lines = [
            "<b>⛔️ Трансляция геопозиции остановлена</b>",
            *base_context(new),
            "",
            f"Было: {html_escape(old.content)}",
            f"Стало: {html_escape(new.content)}",
        ]
        await send_notice(bot, chat_id, "\n".join(lines))
        return

    lines = ["<b>✏️ Сообщение отредактировано</b>", *base_context(new)]
    if old.content_type != new.content_type:
        lines.append(
            f"Тип изменен: {html_escape(type_name(old.content_type))} -> "
            f"{html_escape(type_name(new.content_type))}"
        )

    old_files = file_signature(old)
    new_files = file_signature(new)
    media_changed = old_files != new_files

    if not has_media_payload(old) and not has_media_payload(new):
        lines.extend(render_content_block("Было", old.content))
        lines.extend(render_content_block("Стало", new.content))
        await send_notice(bot, chat_id, "\n".join(lines))
        return

    if not media_changed and old.caption != new.caption:
        lines.extend(render_content_block("Подпись была", old.caption or old.content))
        lines.extend(render_content_block("Подпись стала", new.caption or new.content))
        await send_notice(bot, chat_id, "\n".join(lines))
        return

    await send_notice(bot, chat_id, "\n".join(lines))
    await send_message_copy(bot, chat_id, old, "Старая версия")
    await send_message_copy(bot, chat_id, new, "Новая версия")


def should_capture_reply_media(reply: Message) -> bool:
    mode = REPLY_MEDIA_CAPTURE_MODE
    if mode in {"off", "disabled", "none", "0", "false"}:
        return False

    reply_type = content_type_of(reply)
    if not collect_file_sources(reply, reply_type):
        return False

    if mode == "all":
        return True

    return bool(getattr(reply, "has_protected_content", False))


async def rescue_reply_media(message: Message, target_chat_id: int) -> None:
    reply = message.reply_to_message
    if not reply:
        return

    if not should_capture_reply_media(reply):
        return

    data = await build_message_data(
        reply,
        bot=message.bot,
        business_connection_id=message.business_connection_id,
        mark_saved_from_reply=True,
    )

    with SQLSession(db.engine) as session:
        save_message(session, data)

    await send_message_copy(
        message.bot,
        target_chat_id,
        data,
        "⏱ Сохранено одноразовое/таймерное медиа",
    )
    if DELETE_LOCAL_MEDIA_AFTER_REPORT:
        with SQLSession(db.engine) as session:
            clear_local_paths_for_messages(session, [data])


@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    await message.answer(
        "Бот запущен. Подключите его к Telegram Business, чтобы сохранять "
        "удаленные, отредактированные и одноразовые сообщения."
    )


@dp.business_message()
async def handle_business_message(message: Message) -> None:
    if not message.business_connection_id:
        return

    target_chat_id = await owner_chat_id(message.bot, message.business_connection_id)
    await rescue_reply_media(message, target_chat_id)

    data = await build_message_data(message)
    with SQLSession(db.engine) as session:
        save_message(session, data)


@dp.edited_business_message()
async def handle_edited_business_message(message: Message) -> None:
    if not message.business_connection_id:
        return

    target_chat_id = await owner_chat_id(message.bot, message.business_connection_id)

    with SQLSession(db.engine) as session:
        old = load_message(
            session,
            message.business_connection_id,
            message.chat.id,
            message.message_id,
        )

    new = await build_message_data(message, previous=old)

    with SQLSession(db.engine) as session:
        save_message(session, new)

    if new.content_type == "location" and new.is_live_location:
        return

    if not changed(old, new):
        return

    await send_edit_notice(message.bot, target_chat_id, old, new)
    if old and DELETE_LOCAL_MEDIA_AFTER_REPORT:
        keep_paths = {file.local_path for file in new.files if file.local_path}
        with SQLSession(db.engine) as session:
            clear_local_paths_for_messages(session, [old], keep_paths=keep_paths)


@dp.deleted_business_messages()
async def handle_deleted_business_messages(event: BusinessMessagesDeleted) -> None:
    target_chat_id = await owner_chat_id(event.bot, event.business_connection_id)
    found: list[MessageData] = []
    unknown: list[int] = []

    with SQLSession(db.engine) as session:
        for message_id in event.message_ids:
            data = load_message(
                session,
                event.business_connection_id,
                event.chat.id,
                message_id,
            )
            if not data:
                unknown.append(message_id)
                continue
            if data.deleted_at:
                continue
            found.append(data)

    if unknown:
        chat_title = event.chat.title or event.chat.username or str(event.chat.id)
        await send_notice(
            event.bot,
            target_chat_id,
            "\n".join(
                [
                    "<b>🗑 Удалены неизвестные сообщения</b>",
                    f"Чат: {html_escape(chat_title)} <code>{event.chat.id}</code>",
                    "ID: " + ", ".join(f"<code>{message_id}</code>" for message_id in unknown),
                    "Содержимого нет: бот не видел эти сообщения до удаления.",
                ]
            ),
        )

    for group in group_deleted_messages(found):
        await send_deleted_group(event.bot, target_chat_id, group)
        with SQLSession(db.engine) as session:
            for data in group:
                mark_deleted(session, data)
            if DELETE_LOCAL_MEDIA_AFTER_REPORT:
                clear_local_paths_for_messages(session, group)


async def main() -> None:
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Create .env from .env.example.")

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    db.init()

    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    cleanup_task = asyncio.create_task(cleanup_media_loop())
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
