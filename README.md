# Telegram Business Logger Bot

Бот для Telegram Business, который сохраняет входящие сообщения и присылает
владельцу копию, если сообщение удалили, отредактировали или если это
одноразовое/таймерное медиа из ответа.

Проект рассчитан на запуск через Docker Compose. В контейнере используется
Python 3.12, SQLite и локальная папка для медиафайлов.

## Возможности

- Логирует business-сообщения через `aiogram`.
- Сохраняет сообщения в SQLite по ключу `business_connection_id + chat_id + message_id`.
- Скачивает медиа в локальную папку, чтобы файл оставался доступен после удаления сообщения.
- Присылает владельцу удаленные сообщения: текст, фото, видео, документы, анимации, стикеры, голосовые, аудио, кружки, геопозиции, контакты, опросы, dice, venue, game.
- Поддерживает альбомы: удаленные фото/видео из одного media group отправляются одним альбомом, когда это возможно.
- Отслеживает редактирование сообщений и присылает старую/новую версию.
- Поддерживает все `ContentType` из `aiogram 3.21.0`: если Telegram добавит редкий или service-тип, бот сохранит тип и JSON-снимок вместо падения.
- Сохраняет reply-media только в режиме, заданном `REPLY_MEDIA_CAPTURE_MODE`.
- Автоматически чистит локальные медиа: после успешной отправки отчета удаляет уже ненужные файлы и раз в заданный интервал удаляет старые локальные копии.

## Ограничения

- Бот работает через Telegram Business API, поэтому его нужно подключить к Telegram Business-аккаунту.
- Если бот не видел сообщение до удаления, он сможет прислать только уведомление с ID, без содержимого.

## Переменные окружения

Создайте `.env` из `.env.example`.

```env
BOT_TOKEN=YOUR_BOT_TOKEN_HERE
REPLY_MEDIA_CAPTURE_MODE=protected
```

Доступные настройки:

- `BOT_TOKEN` - токен бота из BotFather. Обязателен.
- `REPLY_MEDIA_CAPTURE_MODE=protected` - сохранять медиа из ответа только если Telegram пометил исходное сообщение как protected. Режим по умолчанию.
- `REPLY_MEDIA_CAPTURE_MODE=all` - старое поведение: сохранять любое медиа, на которое вы ответили.
- `REPLY_MEDIA_CAPTURE_MODE=off` - полностью выключить сохранение медиа из ответов.
- `OWNER_ID` - fallback ID владельца, используется только если Telegram не вернул владельца business connection.
- `DATABASE_URL` - путь к базе. В Docker Compose уже задано `sqlite:////app/data/database.db`.
- `MEDIA_DIR` - папка медиа. В Docker Compose уже задано `/app/media`.
- `MEDIA_RETENTION_DAYS=7` - сколько дней хранить локальные копии медиа. `0` выключает фоновую TTL-очистку.
- `MEDIA_CLEANUP_INTERVAL_SECONDS=3600` - как часто запускать фоновую очистку.
- `DELETE_LOCAL_MEDIA_AFTER_REPORT=1` - удалять локальную копию сразу после успешной отправки владельцу удаленного, отредактированного или одноразового медиа.

## Подготовка бота в Telegram

1. Откройте `@BotFather`.
2. Создайте бота или выберите существующего.
3. Скопируйте токен и укажите его в `.env` как `BOT_TOKEN`.
4. В BotFather включите поддержку Telegram Business для бота.
5. В Telegram подключите бота к своему Business-аккаунту в настройках Telegram Business.
6. Запустите контейнер и отправьте боту `/start`, чтобы проверить, что он отвечает.


## Установка Docker на Ubuntu

Официальная инструкция Docker: https://docs.docker.com/engine/install/ubuntu/

Удалите конфликтующие старые пакеты:

```bash
sudo apt remove docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc
sudo apt update
```

Установите Docker

```bash
sudo curl -fsSL https://get.docker.com | sh
```

Проверьте установку:

```bash
sudo docker compose version
```

## Запуск через Docker Compose

1. Перейдите в папку проекта:

```bash
cd ~/telelog
```

2. Создайте `.env`.

```bash
cp .env.example .env
nano .env
```

3. Заполните минимум:

```env
BOT_TOKEN=YOUR_BOT_TOKEN_HERE
REPLY_MEDIA_CAPTURE_MODE=protected
```

4. Соберите и запустите контейнер:

```bash
docker compose up -d --build
```

5. Посмотрите логи:

```bash
docker compose logs -f bot
```

6. Проверьте статус:

```bash
docker compose ps
```

7. Напишите боту `/start` и подключите его к Telegram Business.

## Где хранятся данные

Docker Compose подключает две папки:

- `./data:/app/data` - база SQLite, файл `data/database.db`.
- `./media:/app/media` - сохраненные медиафайлы.

Эти папки остаются на хосте и не пропадают при пересоздании контейнера.

По умолчанию бот не хранит медиа бесконечно:

- после успешной отправки владельцу удаленного/одноразового/старой версии медиа локальный файл удаляется;
- файлы, которые еще не понадобились, чистятся фоном через `MEDIA_RETENTION_DAYS`;
- в базе остаются `file_id` и метаданные, поэтому для обычных медиа бот сможет попытаться отправить файл через Telegram `file_id`, даже если локальная копия уже удалена.

Если хотите хранить локальные копии дольше, увеличьте `MEDIA_RETENTION_DAYS` в `.env`.

## Управление контейнером

Остановить (в папке с проектом):

```bash
docker compose down
```

Запустить снова:

```bash
docker compose up -d
```

Пересобрать после изменения кода:

```bash
docker compose up -d --build
```

Посмотреть последние 100 строк логов:

```bash
docker compose logs --tail=100 bot
```

Перезапустить только бота:

```bash
docker compose restart bot
```

## Обновление

1. Остановите контейнер:

```bash
docker compose down
```

2. Обновите файлы проекта.

3. Пересоберите и запустите:

```bash
docker compose up -d --build
```

База и медиа сохранятся в `data` и `media`.

## Локальный запуск без Docker

Docker предпочтителен, но локальный запуск тоже возможен:

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Используйте Python 3.12. На Python 3.14 у pinned зависимостей могут отсутствовать
готовые wheel, и установка может потребовать Microsoft C++ Build Tools.
