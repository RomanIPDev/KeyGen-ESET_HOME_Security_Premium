# === Импорты стандартных и сторонних библиотек ===
import os
import sys
import time
import logging
import asyncio
import aiohttp  # Асинхронные HTTP-запросы
from datetime import datetime
from collections import defaultdict
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Tuple, Any
from html import escape  # Экранирование HTML для сообщений Telegram

from dotenv import load_dotenv  # Загрузка переменных окружения из .env
from telegram import (
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# === Настройка логирования ===
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "eset_bot.log")


class SecretsFilter(logging.Filter):
    def __init__(self, secrets: list):
        self.secrets = [s for s in secrets if s]
        super().__init__()

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(secret in msg for secret in self.secrets if secret)


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    fmt="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

file_handler = RotatingFileHandler(
    LOG_PATH, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
)
file_handler.setFormatter(formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)


def user_repr(user: Optional[Any]) -> str:
    if not user:
        return "неизвестный пользователь"
    return f"{user.id} ({user.full_name})"


required_env_vars = [
    "TELEGRAM_BOT_TOKEN",
    "GITHUB_TOKEN",
    "REPO_OWNER",
    "REPO_NAME",
    "WORKFLOW_FILE_NAME",
    "REF",
]

load_dotenv()
missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    raise EnvironmentError(f"Missing environment variables: {', '.join(missing_vars)}")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_OWNER = os.getenv("REPO_OWNER")
REPO_NAME = os.getenv("REPO_NAME")
WORKFLOW_FILE = os.getenv("WORKFLOW_FILE_NAME")
REF = os.getenv("REF")

env_path = os.path.abspath(".env")
if os.path.isfile(env_path) and os.path.dirname(env_path) != SCRIPT_DIR:
    logger.warning(
        "Файл .env находится вне директории скрипта! Это может быть небезопасно."
    )

if not TOKEN or not GITHUB_TOKEN:
    logger.critical("Отсутствуют обязательные токены! Завершение работы.")
    sys.exit(1)

secrets_filter = SecretsFilter([TOKEN, GITHUB_TOKEN])
logger.addFilter(secrets_filter)

user_tasks: Dict[int, asyncio.Task] = {}


class RateLimiter:
    def __init__(self, default_cooldown: int = 30):
        self.default_cooldown = default_cooldown
        self.last_calls: Dict[Tuple[int, str], float] = defaultdict(float)

    async def check(self, update: Update, command: str) -> bool:
        user = update.effective_user
        if not user:
            logger.warning("Не удалось определить пользователя")
            return False

        key = (user.id, command)
        now = time.time()
        elapsed = now - self.last_calls[key]

        if elapsed < self.default_cooldown:
            await self.notify_cooldown(update, self.default_cooldown - elapsed)
            return False

        self.last_calls[key] = now
        return True

    async def notify_cooldown(self, update: Update, remaining: float) -> None:
        message = f"⏳ Подождите {int(remaining)} сек. перед новым запросом"
        if update.callback_query:
            await update.callback_query.answer(message, show_alert=True)
        elif update.message:
            await update.message.reply_text(message)


rate_limiter = RateLimiter()


async def run_github_workflow(user: Optional[Any]) -> Tuple[int, str]:
    logger.info(f"Запуск workflow GitHub для пользователя {user_repr(user)}")

    try:
        url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/{WORKFLOW_FILE}/dispatches"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json={"ref": REF}
            ) as response:
                if response.status == 204:
                    return (
                        204,
                        "Workflow успешно запущен. Ключ будет отправлен отдельным сообщением в течение минуты.",
                    )

                error_messages = {
                    401: "Неверный токен доступа GitHub",
                    403: "Достигнут лимит запросов к GitHub API",
                    404: "Workflow не найден",
                    422: "Некорректные параметры запроса",
                    429: "Слишком много запросов — попробуйте позже",
                }

                if response.status in error_messages:
                    return response.status, error_messages[response.status]

                try:
                    error_data = await response.json()
                    message = error_data.get("message", "Неизвестная ошибка")
                except aiohttp.ContentTypeError:
                    message = "Некорректный формат ответа"

                logger.error(f"Ошибка GitHub: {response.status} - {message}")
                return response.status, message

    except aiohttp.ClientConnectorError as e:
        logger.error(f"Ошибка подключения: {str(e)}")
        return 503, "Сервер GitHub недоступен"
    except aiohttp.ServerTimeoutError as e:
        logger.error(f"Таймаут подключения: {str(e)}")
        return 504, "Таймаут соединения с GitHub"
    except asyncio.CancelledError:
        logger.info(f"Workflow отменён для пользователя {user_repr(user)}")
        raise
    except Exception as e:
        logger.error(f"Ошибка выполнения workflow: {str(e)}")
        return 500, "Внутренняя ошибка сервера"


async def handle_get_key_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    user = update.effective_user
    logger.info(f"Пользователь {user_repr(user)} запросил ключ")

    if not await rate_limiter.check(update, "get_key"):
        return

    try:
        processing_msg = await send_waiting_message(update)
        task = asyncio.create_task(run_github_workflow(user))
        user_tasks[user.id] = task
        logger.info(
            f"Задача сохранена для пользователя {user.id}, всего задач: {len(user_tasks)}"
        )

        status, message = await asyncio.wait_for(task, timeout=300)
        response = format_github_response(status, escape(message))

    except asyncio.TimeoutError:
        response = "⌛️ Превышено время ожидания ответа"
        logger.warning(f"Таймаут запроса от {user_repr(user)}")
    except asyncio.CancelledError:
        response = "⛔️ Запрос был отменён пользователем."
        logger.info(f"Запрос отменён: {user_repr(user)}")
    except Exception as e:
        response = "⚠️ Внутренняя ошибка"
        logger.error(f"Ошибка обработки запроса: {str(e)}")
    finally:
        user_tasks.pop(user.id, None)
        logger.info(f"Задача для пользователя {user.id} удалена из списка активных")

    await update_response_message(processing_msg, response)
    logger.info(f"Запрос от {user_repr(user)} обработан")


async def welcome_new_members(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            await update.message.reply_text(
                "Спасибо за добавление! Используйте /get_key"
            )
        else:
            await update.message.reply_text(
                f"Добро пожаловать, {escape(member.full_name)}!"
            )


async def handle_error(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Ошибка: {context.error}", exc_info=True)
    if update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ Произошла ошибка. Мы уже работаем над её устранением!"
        )


async def handle_unknown_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await update.message.reply_text("Неизвестная команда. Попробуйте /get_key")


async def send_waiting_message(update: Update):
    if update.callback_query:
        return await update.callback_query.edit_message_text("⏳ Обработка запроса...")
    return await update.message.reply_text("⏳ Обработка запроса...")


def format_github_response(status: int, message: str) -> str:
    timestamp = datetime.now().strftime("%H:%M:%S")
    success = status == 204
    if success:
        details = f"🔑 Ключ генерируется\n{message}"
    else:
        details = f"Сообщение: {message}"

    return (
        f"{'✅ Успешно!' if success else '❌ Ошибка!'}\n"
        f"🕒 {timestamp}\n"
        f"Статус: {status}\n"
        f"{details}"
    )


async def update_response_message(msg, text):
    if hasattr(msg, "edit_text"):
        await msg.edit_text(text)
    else:
        # Если сообщение — reply, отправляем новое
        pass


def main():
    logger.info("Запуск бота")

    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("get_key", handle_get_key_command))
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_members)
    )
    application.add_handler(MessageHandler(filters.COMMAND, handle_unknown_command))
    application.add_error_handler(handle_error)

    application.run_polling()


if __name__ == "__main__":
    main()
