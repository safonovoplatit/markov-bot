import asyncio
import logging
import os
import re
import sqlite3
import time
from pathlib import Path

from telegram import InlineQueryResultArticle, InputTextMessageContent
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import NetworkError, TelegramError
from telegram.ext import ApplicationBuilder, CommandHandler, InlineQueryHandler, filters

from quotes import load_quotes

# Вставьте токен из BotFather между кавычками.
BOT_TOKEN = "9009686574:AAE9pp6DI9_LyDPyBP2qRhcYVTeicsN4AjU"

CHANNEL_ID = "-1004375412282"

POST_INTERVAL_SECONDS = 200

ROOT = Path(__file__).resolve().parent
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def corpus_paths():
    if explicit := os.getenv("BOOTSTRAP_HTML"):
        return [Path(explicit)]
    directory = Path(os.getenv("CORPUS_DIR", ROOT))
    return sorted(path for path in directory.iterdir()
                  if path.is_file() and path.suffix.lower() in {".html", ".htm"})


def format_quote(quote, signature):
    return "\n\n".join(part for part in (quote, signature, "Думайте. Подписаться.") if part)


class ChannelBot:
    def __init__(self):
        self.generator, count = load_quotes(corpus_paths())
        logger.info("Загружено сообщений: %d", count)
        channel = os.getenv("CHANNEL_ID", "").strip() or CHANNEL_ID.strip()
        self.channel = int(channel) if re.fullmatch(r"-100\d+", channel) else channel
        if channel and not (isinstance(self.channel, int) or re.fullmatch(r"@\w+", channel)):
            raise ValueError("CHANNEL_ID должен быть @username или числовым ID канала вида -100…")
        self.admins = {int(value) for value in os.getenv("ADMIN_IDS", "").split(",") if value.strip()}
        self.signature = os.getenv("BOT_SIGNATURE", "").strip()
        if len(self.signature) > 1500:
            raise ValueError("BOT_SIGNATURE должна содержать не более 1500 символов.")
        self.lock = asyncio.Lock()
        self.history = None
        if self.channel:
            path = Path(os.getenv("HISTORY_DB", ROOT / "data" / "posts.sqlite3"))
            path.parent.mkdir(parents=True, exist_ok=True)
            self.history = sqlite3.connect(path)
            with self.history:
                self.history.execute(
                    "CREATE TABLE IF NOT EXISTS posts (channel TEXT, quote TEXT, message_id INTEGER)"
                )

    def recent(self):
        if self.history is None:
            return ()
        return tuple(quote for (quote,) in self.history.execute(
            "SELECT quote FROM posts WHERE channel = ? ORDER BY rowid DESC LIMIT 30",
            (str(self.channel),),
        ))

    def draft(self, seed=""):
        return self.generator.generate(seed, exclude=self.recent())

    async def start(self, update, context):
        text = "/quote [тема] — придумать цитату. В inline-режиме: @имя_бота тема."
        if update.effective_user.id in self.admins:
            text += "\n/post [тема] — опубликовать цитату. Без темы отправит последний черновик.\n"
            text += "/reload — перечитать HTML.\n/id — показать ваш Telegram ID."
        else:
            text += "\n/id — показать ваш Telegram ID."
        text += "\n/makeq [тема] — создать и опубликовать цитату (для администраторов)."
        await update.message.reply_text(text)

    async def user_id(self, update, context):
        await update.message.reply_text(str(update.effective_user.id))

    async def preview(self, update, context):
        quote = self.draft(" ".join(context.args))
        context.user_data["draft"] = quote
        await update.message.reply_text(format_quote(quote, self.signature))

    async def inline(self, update, context):
        quote = self.generator.generate(update.inline_query.query)
        result = InlineQueryResultArticle(
            id="quote", title=quote[:60],
            input_message_content=InputTextMessageContent(format_quote(quote, self.signature)),
        )
        await update.inline_query.answer([result], cache_time=1)

    async def publish(self, bot, seed="", draft=None):
        if self.history is None:
            raise ValueError("Задайте CHANNEL_ID перед публикацией.")
        async with self.lock:
            if draft in self.recent():
                raise ValueError("Этот черновик уже недавно публиковался. Создайте новый через /quote.")
            quote = draft or self.draft(seed)
            message = await bot.send_message(self.channel, format_quote(quote, self.signature))
            with self.history:
                self.history.execute("INSERT INTO posts VALUES (?, ?, ?)",
                                     (str(self.channel), quote, message.message_id))
            return message

    async def post(self, update, context):
        await self.publish_command(update, context, use_draft=True)

    async def makeq(self, update, context):
        await self.publish_command(update, context, use_draft=False)

    async def publish_command(self, update, context, *, use_draft):
        user_id = update.effective_user.id
        draft = context.user_data.get("draft") if use_draft and not context.args else None
        try:
            if user_id not in self.admins:
                if use_draft or not self.channel:
                    await update.message.reply_text("Добавьте свой ID из /id в ADMIN_IDS.")
                    return
                member = await context.bot.get_chat_member(self.channel, user_id)
                if member.status not in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR):
                    await update.message.reply_text("Команда /makeq доступна администраторам канала.")
                    return
            message = await self.publish(context.bot, " ".join(context.args), draft)
        except (ValueError, TelegramError) as exc:
            await update.message.reply_text(f"Ошибка публикации: {exc}")
            return
        if use_draft:
            context.user_data.pop("draft", None)
        await update.message.reply_text(f"Опубликовано. Пост №{message.message_id}.")

    async def reload(self, update, context):
        if update.effective_user.id not in self.admins:
            return
        try:
            generator, count = await asyncio.to_thread(lambda: load_quotes(corpus_paths()))
        except (OSError, ValueError) as exc:
            await update.message.reply_text(f"Не удалось загрузить HTML: {exc}")
            return
        self.generator = generator
        await update.message.reply_text(f"Выгрузки обновлены. Прочитано сообщений: {count}.")

    async def start_channel(self, application):
        await self.check_channel(application)
        if self.channel:
            await self.publish_and_log(application.bot)
            application.job_queue.run_repeating(
                self.scheduled_post,
                interval=POST_INTERVAL_SECONDS,
                first=POST_INTERVAL_SECONDS,
                name="channel-post",
            )

    async def check_channel(self, application):
        if not self.channel:
            logger.info("Укажите CHANNEL_ID, чтобы включить автопубликацию.")
            return
        try:
            chat = await application.bot.get_chat(self.channel)
            if chat.type != ChatType.CHANNEL:
                logger.error("CHANNEL_ID должен указывать на Telegram-канал.")
                return
            member = await application.bot.get_chat_member(chat.id, application.bot.id)
            if member.status != ChatMemberStatus.ADMINISTRATOR or not member.can_post_messages:
                logger.error("Дайте боту права администратора канала с разрешением публиковать.")
                return
        except TelegramError as exc:
            logger.error("Не удалось проверить канал %s: %s", self.channel, exc)
            return
        logger.info("Канал %s доступен. Следующие публикации каждые %s секунд.",
                    self.channel, POST_INTERVAL_SECONDS)

    async def scheduled_post(self, context):
        await self.publish_and_log(context.bot)

    async def publish_and_log(self, bot):
        try:
            message = await self.publish(bot)
        except (ValueError, TelegramError) as exc:
            logger.error("Не удалось опубликовать в %s: %s", self.channel, exc)
            return
        logger.info("Опубликован пост №%s в %s.", message.message_id, self.channel)


def run_bot(token, channel_bot):
    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(channel_bot.start_channel)
        .connect_timeout(15)
        .read_timeout(30)
        .get_updates_connect_timeout(15)
        .get_updates_read_timeout(30)
        .build()
    )
    for command, callback in (
        ("start", channel_bot.start), ("id", channel_bot.user_id),
        ("quote", channel_bot.preview), ("post", channel_bot.post),
        ("makeq", channel_bot.makeq), ("reload", channel_bot.reload),
    ):
        app.add_handler(CommandHandler(command, callback, filters=filters.ChatType.PRIVATE))
    app.add_handler(InlineQueryHandler(channel_bot.inline))
    app.run_polling(allowed_updates=["message", "inline_query"], bootstrap_retries=2)


def main():
    token = os.getenv("BOT_TOKEN", "").strip() or BOT_TOKEN.strip()
    if not token:
        raise SystemExit("Укажите токен в BOT_TOKEN в файле bot.py или в переменной окружения.")

    channel_bot = ChannelBot()
    for attempt in range(1, 4):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            run_bot(token, channel_bot)
            return
        except NetworkError as exc:
            if attempt == 3:
                raise SystemExit(
                    f"Telegram Bot API недоступен после трёх попыток ({type(exc).__name__}). "
                    "Проверьте подключение к api.telegram.org."
                ) from None
            logger.warning("Ошибка подключения к Telegram (%s). Попытка %s/3 через 5 секунд.",
                           type(exc).__name__, attempt + 1)
        finally:
            loop.close()
            asyncio.set_event_loop(None)
        time.sleep(5)


if __name__ == "__main__":
    main()
