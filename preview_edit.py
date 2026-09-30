"""Собрать эдит локально, ничего не отправляя в беседу: python preview_edit.py [тема] [трек]

    python preview_edit.py матадора        # под MATADORA из лучших кадров
    python preview_edit.py шашлыки sahara  # по теме и под трек

Всё как у бота в чате (кадры, подписи от Claude, монтаж), только видео ложится в preview/edit/.
Файлы качаются из Telegram токеном из .env; бот при этом может работать — ему это не мешает.
"""

import asyncio
import os
import sys
import time
from contextlib import nullcontext
from pathlib import Path

from aiogram import Bot

import bot as memebot

OUT = Path(__file__).parent / "preview" / "edit"


class _Quiet:
    """Вместо «бот отправляет видео…» в беседе — тишина."""

    @staticmethod
    def upload_video(**_):
        return nullcontext()


async def main() -> None:
    request = " ".join(sys.argv[1:])
    chats = memebot.storage.media_chats()
    if not chats:
        sys.exit("В базе нет медиа из бесед")
    chat_id = max(chats, key=lambda c: len(memebot.storage.edit_candidates(c)))

    bot = Bot(os.environ["BOT_TOKEN"])
    saved: list[Path] = []

    async def save_video(_chat_id, video, caption=None, **_):
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / f"edit_{time.strftime('%H%M%S')}.mp4"
        path.write_bytes(video.data)
        saved.append(path)
        print(f"{caption}\n→ {path.relative_to(Path(__file__).parent)}")

    bot.send_video = save_video
    memebot.ChatActionSender = _Quiet
    memebot.remember_own = lambda *_, **__: None
    try:
        title = (await bot.get_chat(chat_id)).title or "беседа"
        problem = await memebot.make_edit(bot, chat_id, title, request)
    finally:
        await bot.session.close()
    if problem:
        sys.exit(problem)


if __name__ == "__main__":
    asyncio.run(main())
