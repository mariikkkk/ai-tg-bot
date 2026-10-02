"""Мем-бот для беседы: запоминает фотки, видео, гифки и сообщения, а потом сам клепает из них мемы."""

import asyncio
import logging
import os
import random
import re
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand, BufferedInputFile, CallbackQuery, Chat, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup,
    Message, MessageOriginUser, ReactionTypeEmoji, ReplyParameters,
)
from aiogram.utils.chat_action import ChatActionSender
from dotenv import load_dotenv

# До импорта наших модулей: ai.py читает свои настройки (CLAUDE_MODEL и т.д.) прямо при импорте.
load_dotenv(Path(__file__).parent / ".env")

import ai  # noqa: E402
import clips  # noqa: E402
import edits  # noqa: E402
import faces  # noqa: E402
import markov  # noqa: E402
import pics  # noqa: E402
import speech  # noqa: E402
import tracks  # noqa: E402
import web  # noqa: E402
import render  # noqa: E402
import video  # noqa: E402
from storage import Media, Storage  # noqa: E402

DB_PATH = os.getenv("DB_PATH", str(Path(__file__).parent / "data" / "bot.db"))
# После скольки процентов сообщений бот сам кидает мем (можно менять в чате через /chance).
DEFAULT_CHANCE = float(os.getenv("DEFAULT_CHANCE", "3")) / 100
# Сам по себе — не чаще раза в столько секунд.
COOLDOWN = int(os.getenv("COOLDOWN", "60"))
# Bot API не отдаёт ботам файлы больше 20 МБ.
MAX_DOWNLOAD = 20 * 1024 * 1024

IMAGE_STYLE_WEIGHTS = {"demotivator": 4, "classic": 3, "caption": 3}
DEEP_FRY_CHANCE = 0.07
# Как ещё зовут бота, кроме имени в Telegram (через запятую, падежи ловятся сами).
BOT_ALIASES = [a.strip().lower() for a in os.getenv("BOT_ALIASES", "бот").split(",") if a.strip()]
# Сколько секунд после ответа бота следующие сообщения того же человека считаются, возможно, ему.
FOLLOWUP_WINDOW = 90
# Когда к боту обращаются текстом: такая доля — живой ответ Claude, остальное — мем в ответ.
AI_REPLY_SHARE = 0.9

HELP = """Я запоминаю все фотки, видосы, гифки и сообщения в этом чате, а потом сам делаю из них мемы 🤡

/meme — мем из случайной фотки. Ответь командой на фото или видео — сделаю из него. Свой текст: /meme верх | низ
/dem — демотиватор
/vid — мем из видео или гифки
/pic запрос — картинка из интернета (Яндекс Картинки)
/edit — тикток-эдит под фонк из ваших фоток, видео и кружочков; /edit дача — по теме; /edit матадора — под трек
/tracks — какие треки есть · /track название — добавить свой
/clip запрос — короткий ролик (тиктоки и шортсы с YouTube)
/name Shadow = Дима — кто есть кто (или ответь на сообщение: /name Дима); /name — список
/nomeme — ответь на картинку, и я больше не буду делать из неё мемы
/trends — какие свежие мемы и тренды я знаю (обновляю раз в день из интернета)
/text — ответь на голосовое или кружочек, пришлю расшифровку
/say текст — скажу голосом (или ответь /say на сообщение — озвучу его)
/say демон текст — другим голосом: бурундук, демон, робот, пьяный, рация, эхо, перегруз, злая, шёпот, диктор, быстро
/gen [слово] — сгенерировать фразу
/chance [0–100] — как часто я сам кидаю мемы (в % сообщений)
/stats — что я помню
/lore — что я помню про вас (летопись беседы)
/face — скинь своё фото с этой подписью, и я буду узнавать тебя на фотках (/face забудь — забыть); /faces — кого знаю в лицо
/mood — как я сейчас
/forget — забыть всё про этот чат (только админы)

Позови по имени, тегни или ответь на моё сообщение — отвечу. Ответишь фоткой — сделаю из неё мем."""

COMMANDS = [
    BotCommand(command="meme", description="Мем из случайной фотки (или ответом на фото)"),
    BotCommand(command="dem", description="Демотиватор"),
    BotCommand(command="vid", description="Мем из видео или гифки"),
    BotCommand(command="pic", description="Картинка из интернета: /pic запрос"),
    BotCommand(command="edit", description="Тикток-эдит под фонк: /edit, /edit тема, /edit матадора"),
    BotCommand(command="tracks", description="Треки для эдитов"),
    BotCommand(command="track", description="Добавить трек для эдитов: /track название"),
    BotCommand(command="clip", description="Смешной ролик (тиктоки, шортсы): /clip запрос"),
    BotCommand(command="name", description="Кто есть кто: /name Shadow = Дима"),
    BotCommand(command="face", description="Запомни моё лицо (своё фото с подписью /face)"),
    BotCommand(command="nomeme", description="Не делать мемы из этой картинки (ответом на неё)"),
    BotCommand(command="text", description="Расшифровать голосовое (ответом на него)"),
    BotCommand(command="say", description="Сказать голосом: /say текст (или ответом на сообщение)"),
    BotCommand(command="trends", description="Какие мемы и тренды я сейчас знаю"),
    BotCommand(command="gen", description="Сгенерировать фразу"),
    BotCommand(command="chance", description="Как часто я сам кидаю мемы"),
    BotCommand(command="stats", description="Что я помню"),
    BotCommand(command="help", description="Что я умею"),
]

log = logging.getLogger("memebot")
storage = Storage(DB_PATH, DEFAULT_CHANCE)
LIB = tracks.Library(storage.base / "tracks")
router = Router()


# --- запоминание ---


def media_of(msg: Message | None) -> tuple[str, str, str] | None:
    """(kind, file_id, file_unique_id), если в сообщении есть фото/видео/гифка."""
    if msg is None:
        return None
    if msg.photo:
        photo = msg.photo[-1]  # самое большое разрешение
        return "photo", photo.file_id, photo.file_unique_id
    for kind, obj in (("animation", msg.animation), ("video", msg.video), ("video", msg.video_note)):
        if obj and (obj.file_size or 0) <= MAX_DOWNLOAD:
            return kind, obj.file_id, obj.file_unique_id
    return None


def as_source(found: tuple[str, str, str] | None) -> Media | None:
    return Media(id=0, kind=found[0], file_id=found[1]) if found else None


MEDIA_LABELS = {"photo": "[фото]", "video": "[видео]", "animation": "[гифка]"}


def remember(msg: Message, spoken: str | None = None) -> None:
    """spoken — расшифровка голосового или кружочка: бот учится на ней, как на обычном тексте."""
    found = media_of(msg)
    if found and not is_other_bot(msg) and not is_own_media(msg, found[2]):
        storage.add_media(msg.chat.id, *found)
    if found and found[0] == "photo" and not is_other_bot(msg) and faces.available() and \
            storage.faces_version(msg.chat.id):
        run_in_background(tag_faces(msg, found[1], found[2]))  # кто из записавшихся на фото
    if msg.from_user and not msg.from_user.is_bot:
        storage.touch_member(msg.chat.id, msg.from_user.id, msg.from_user.full_name)  # кто когда писал
    text = msg.text or msg.caption or spoken or ""
    if text.startswith("/") or is_other_bot(msg):
        return
    author = author_of(msg)
    if cleaned := markov.clean(text):
        storage.add_phrase(msg.chat.id, cleaned)
        storage.add_dialog(msg.chat.id, author, cleaned)

    if msg.sticker:
        remember_sticker(msg)

    # Контекст разговора для живых ответов.
    label = MEDIA_LABELS[found[0]] if found else sticker_label(msg.sticker) if msg.sticker else ""
    if msg.voice or msg.video_note:
        label = ("[голосовое]" if msg.voice else "[кружочек]") + ("" if spoken else " (не разобрать)")
    if msg.reply_to_message:
        author += f" (в ответ {author_of(msg.reply_to_message)})"
    ai.remember(msg.chat.id, author, f"{label} {text}", msg.message_id)
    note_for_feelings(msg.bot, msg.chat.id)


_background: set[asyncio.Task] = set()


def sticker_kind(st) -> str:
    return "video" if st.is_video else "animated" if st.is_animated else "static"


def sticker_label(st) -> str:
    """[стикер 😭: кот рыдает в подушку] — если бот уже рассмотрел этот стикер."""
    desc = storage.sticker_description(st.file_unique_id)
    return f"[стикер {st.emoji or ''}: {desc}]" if desc else f"[стикер {st.emoji or ''}]"


def remember_sticker(msg: Message) -> None:
    st = msg.sticker
    thumb = st.thumbnail.file_id if st.thumbnail else None
    storage.add_sticker(msg.chat.id, st.file_id, st.file_unique_id, st.emoji or "", st.set_name, used=True,
                        thumb_id=thumb, kind=sticker_kind(st))
    # Новый для чата стикерпак качаем целиком — так у бота сразу есть стикеры на все эмоции.
    if st.set_name and storage.new_sticker_set(msg.chat.id, st.set_name):
        task = asyncio.create_task(learn_sticker_set(msg.bot, msg.chat.id, st.set_name))
        _background.add(task)
        task.add_done_callback(_background.discard)


async def learn_sticker_set(bot: Bot, chat_id: int, set_name: str) -> None:
    try:
        sticker_set = await bot.get_sticker_set(set_name)
    except Exception as e:  # noqa: BLE001
        log.warning("Не скачал стикерпак %s: %s", set_name, e)
        return
    for st in sticker_set.stickers:
        thumb = st.thumbnail.file_id if st.thumbnail else None
        storage.add_sticker(chat_id, st.file_id, st.file_unique_id, st.emoji or "", set_name, used=False,
                            thumb_id=thumb, kind=sticker_kind(st))
    log.info("Запомнил стикерпак %s: %d стикеров", set_name, len(sticker_set.stickers))
    ai.refresh(chat_id)  # чтобы Claude сразу узнал про новые стикеры


def is_own_media(msg: Message, unique_id: str) -> bool:
    """Мем или картинка, которую прислал сам бот, — переслали или вернули в чат. Из такого мемы не делаем."""
    origin = msg.forward_origin
    if isinstance(origin, MessageOriginUser) and origin.sender_user.id == msg.bot.id:
        return True
    return storage.is_own(unique_id)


def remember_own(sent: Message | None, image: bytes | None = None) -> None:
    """Запоминает, что отправил бот, — чтобы не делать мемы из своих же мемов."""
    if not sent:
        return
    ids = [p.file_unique_id for p in sent.photo or []]
    ids += [obj.file_unique_id for obj in (sent.video, sent.animation, sent.document) if obj]
    storage.mark_own(ids)
    if image:
        storage.add_own_hash(render.fingerprint(image))


def author_of(msg: Message) -> str:
    """Как подписать автора для Claude: ник, а если знаем, как человека зовут, — «Shadow (Дима)»."""
    if msg.sender_chat:
        return msg.sender_chat.title or "аноним"
    if not msg.from_user:
        return "кто-то"
    nick = msg.from_user.full_name
    name = real_name(msg.chat.id, nick, msg.from_user.username)
    return f"{nick} ({name})" if name else nick


def real_name(chat_id: int, nick: str, username: str | None = None) -> str | None:
    keys = {nick.casefold()} | ({username.casefold(), "@" + username.casefold()} if username else set())
    for known, name in storage.people(chat_id):
        if known.casefold() in keys and name.casefold() != nick.casefold():
            return name
    return None


def is_other_bot(msg: Message) -> bool:
    # Анонимные админы и посты канала тоже приходят «от бота», но у них есть sender_chat.
    return bool(msg.from_user and msg.from_user.is_bot and msg.sender_chat is None)


# --- генерация ---


def make_texts(phrases: list[str], style: str, seed: str | None = None) -> tuple[str, str]:
    babbler = markov.Babbler(phrases)
    if style == "demotivator":
        return babbler.phrase(60, seed), babbler.phrase(90) if random.random() < 0.7 else ""
    if style == "classic":
        top, bottom = babbler.phrase(50, seed), babbler.phrase(50)
        roll = random.random()
        if roll < 0.2:
            return "", top
        if roll < 0.35:
            return top, ""
        return top, bottom
    return babbler.phrase(110, seed), ""


async def texts_for(msg: Message, style: str, seed: str | None) -> tuple[str, str]:
    phrases = storage.phrases(msg.chat.id)
    return await asyncio.to_thread(make_texts, phrases, style, seed)


def examples_loader(chat_id: int) -> ai.ExamplesLoader:
    """Примеры переписки для Claude; зовётся, только когда их пора обновить."""
    return lambda: (
        storage.phrases(chat_id),  # по ним считается стиль чата
        storage.random_phrases(chat_id, ai.EXAMPLE_PHRASES),
        storage.dialog_windows(chat_id, count=ai.EXAMPLE_WINDOWS, size=ai.WINDOW_LINES),
        storage.sticker_menu(chat_id),  # рассмотренные — Claude выбирает по смыслу
        storage.sticker_emojis(chat_id),
        storage.people(chat_id),  # кто есть кто
        storage.lore(chat_id)["text"],  # летопись — долгая память
    )


async def meme_texts(msg: Message, style: str, image: bytes, seed: str | None) -> tuple[str, str]:
    """Подпись к мему: Claude смотрит на картинку и разговор; если не вышло — Марков."""
    if ai.available():
        name, title = await bot_name(msg.bot), msg.chat.title or "личка"
        who = await asyncio.to_thread(who_is_on, msg.chat.id, image)
        got = await ai.meme_caption(msg.chat.id, name, title, examples_loader(msg.chat.id), image, style, who)
        if got and any(got):
            return got
    return await texts_for(msg, style, seed)


def parse_texts(args: str | None) -> tuple[str, str] | None:
    if not args or not args.strip():
        return None
    first, _, second = args.partition("|")
    return first.strip(), second.strip()


async def download(bot: Bot, file_id: str) -> bytes:
    buf = await bot.download(file_id, timeout=120)
    return buf.getvalue()


async def pick_media(msg: Message, kinds: tuple[str, ...]) -> tuple[Media, bytes] | None:
    """Случайное медиа из памяти чата; пропавшие файлы выкидываем и берём другое."""
    for _ in range(3):
        media = storage.random_media(msg.chat.id, kinds)
        if media is None:
            return None
        try:
            if media.path:  # из импорта истории — лежит локально
                data = await asyncio.to_thread(storage.local_file(media).read_bytes)
            else:
                data = await download(msg.bot, media.file_id)
        except (TelegramBadRequest, FileNotFoundError) as e:
            log.warning("media %s недоступно (%s), забываю", media.id, e)
            storage.delete_media(media.id)
            continue
        # Свой же мем, пересохранённый и залитый заново, — узнаём по отпечатку и выкидываем.
        if media.kind == "photo" and storage.looks_own(await asyncio.to_thread(render.fingerprint, data)):
            log.info("media %s — это мой же мем, забываю", media.id)
            storage.delete_media(media.id)
            continue
        return media, data
    return None


async def bot_name(bot: Bot) -> str:
    return (await bot.me()).first_name


def caption_note(text1: str, text2: str) -> str:
    return f"[скинул мем: {' / '.join(t for t in (text1, text2) if t)}]"


def _action(msg: Message, kind: str) -> ChatActionSender:
    thread = msg.message_thread_id if msg.is_topic_message else None
    return getattr(ChatActionSender, kind)(chat_id=msg.chat.id, bot=msg.bot, message_thread_id=thread)


async def send_image_meme(msg, *, source=None, style=None, texts=None, seed=None, reply=False) -> bool:
    picked = (source, await download(msg.bot, source.file_id)) if source else await pick_media(msg, ("photo",))
    if not picked:
        return False
    _, data = picked
    style = style or random.choices(list(IMAGE_STYLE_WEIGHTS), weights=IMAGE_STYLE_WEIGHTS.values())[0]
    fry = random.random() < DEEP_FRY_CHANCE
    async with _action(msg, "upload_photo"):
        text1, text2 = texts or await meme_texts(msg, style, data, seed)
        jpeg = await asyncio.to_thread(render.meme_image, data, style, text1, text2, fry)
    send = msg.reply_photo if reply else msg.answer_photo
    # Классический мем — текст поверх фото без рамки — по отпечатку почти не отличим от самой фотки:
    # запомни его отпечаток, и бот выкинет исходную фотку. Такие узнаём только по файлу и пересылке.
    remember_own(await send(BufferedInputFile(jpeg, "meme.jpg")), jpeg if style != "classic" else None)
    ai.remember(msg.chat.id, await bot_name(msg.bot), caption_note(text1, text2))
    return True


async def send_video_meme(msg, *, source=None, style=None, texts=None, seed=None, reply=False) -> bool:
    kinds = ("video", "animation")
    picked = (source, await download(msg.bot, source.file_id)) if source else await pick_media(msg, kinds)
    if not picked:
        return False
    media, data = picked
    if style not in video.STYLES:
        style = random.choice(video.STYLES)
    async with _action(msg, "upload_video"):
        if not texts and ai.available():
            try:  # Claude смотрит на характерный кадр
                texts = await meme_texts(msg, style, await video.frame(data), seed)
            except Exception as e:  # noqa: BLE001
                log.warning("не вышло достать кадр из видео: %s", e)
        text1, text2 = texts or await texts_for(msg, style, seed)
        mp4 = await video.meme_video(data, style, text1, text2)
    file = BufferedInputFile(mp4, "meme.mp4")
    if media.kind == "animation":
        remember_own(await (msg.reply_animation if reply else msg.answer_animation)(file))
    else:
        remember_own(await (msg.reply_video if reply else msg.answer_video)(file, supports_streaming=True))
    ai.remember(msg.chat.id, await bot_name(msg.bot), caption_note(text1, text2))
    return True


async def send_phrase(msg: Message, *, seed=None, reply=False) -> None:
    phrases = storage.phrases(msg.chat.id)
    text = await asyncio.to_thread(lambda: markov.Babbler(phrases).phrase(200, seed))
    await (msg.reply if reply else msg.answer)(text)
    ai.remember(msg.chat.id, await bot_name(msg.bot), text)


async def send_ai(msg: Message, *, author: str | None, reply=False, maybe=False) -> bool:
    """Живой ответ от Claude. False — если ИИ недоступен или упёрся в лимит."""
    if not ai.available():
        return False
    chat_id, name = msg.chat.id, await bot_name(msg.bot)
    async with _action(msg, "typing"):
        lookup = await meme_lookup(msg) if author else None
        answer = await ai.say(
            chat_id, name, msg.chat.title or "личка", examples_loader(chat_id), author=author, maybe=maybe,
            lookup=lookup,
        )
    if not answer:
        return False
    reacted = answer.reaction and await set_reaction(msg, msg.message_id, answer.reaction)
    voiced = bool(answer.voice and answer.lines and voice_allowed(msg)
                  and await send_voice(msg, " ".join(answer.lines), reply=reply, style=answer.voice_style))
    if voiced:
        answer.lines = []  # сказано голосом — текстом не дублируем
    # Как живые: несколько коротких сообщений подряд, с паузой «набора».
    for i, line in enumerate(answer.lines):
        if i:
            await msg.bot.send_chat_action(chat_id, "typing")
            await asyncio.sleep(random.uniform(0.7, 1.8))
        await (msg.reply if reply and i == 0 else msg.answer)(line)
        ai.remember(chat_id, name, line)
    if answer.lines:
        log.info("Сказал в %s: %s", chat_id, " / ".join(answer.lines)[:300])
    sent = bool(reacted or voiced or answer.lines)
    # Стикеры не чаще раза в STICKER_EVERY ответов, а то Claude ими заваливает.
    _replies_since_sticker[chat_id] = _replies_since_sticker.get(chat_id, STICKER_EVERY) + 1
    if answer.sticker and (_replies_since_sticker[chat_id] > STICKER_EVERY or not sent):
        _replies_since_sticker[chat_id] = 0
        sent = await send_sticker(msg, answer.sticker, reply=reply and not sent, file_id=answer.sticker_file) or sent
    if answer.picture:
        sent = await send_picture(msg, answer.picture, reply=reply and not sent) or sent
    if answer.edit is not None:  # попросили эдит — собираем в фоне (это ~полминуты)
        run_in_background(edit_and_report(msg, answer.edit))
        sent = True
    if answer.clip and clip_allowed(msg):
        found = (await send_clip(msg, answer.clip, reply=reply and not sent)
                 or await send_picture(msg, answer.clip, reply=reply and not sent))  # хоть картинку по теме
        if not found and answer.lines:
            await msg.answer("видос не нашёл 🤷")  # а то «держи» без видоса
        sent = found or sent
    return sent  # ничего не отправили — пусть ответит мем


STICKER_EVERY = 3
_replies_since_sticker: dict[int, int] = {}
VOICE_EVERY = 4  # голосом — не чаще раза в столько ответов, если голосом не попросили
_replies_since_voice: dict[int, int] = {}
ASKED_FOR_VOICE = re.compile(r"голос|скажи|озвуч|спой|крикни|произнеси", re.IGNORECASE)


def voice_allowed(msg: Message) -> bool:
    chat_id = msg.chat.id
    _replies_since_voice[chat_id] = _replies_since_voice.get(chat_id, VOICE_EVERY) + 1
    asked = ASKED_FOR_VOICE.search(msg.text or msg.caption or "")
    if asked or _replies_since_voice[chat_id] > VOICE_EVERY:
        _replies_since_voice[chat_id] = 0
        return True
    return False


async def send_voice(msg: Message, text: str, *, reply=False, style: str | None = None) -> bool:
    """Голосовое от бота (Yandex SpeechKit + эффекты). style — бурундук, демон, робот… (speech.VOICE_STYLES)."""
    if not speech.tts_available():
        return False
    style = style if speech.voice_style(style) else None
    try:
        async with _action(msg, "record_voice"):
            ogg = await speech.synthesize(text, style)
    except Exception as e:  # noqa: BLE001
        log.warning("Не озвучил: %s", e)
        return False
    await (msg.reply_voice if reply else msg.answer_voice)(BufferedInputFile(ogg, "voice.ogg"))
    log.info("Отправил голосовое (%s): %d символов", style or "обычный голос", len(text))
    note = f"[голосовое, голос: {style}]" if style else "[голосовое]"
    ai.remember(msg.chat.id, await bot_name(msg.bot), f"{note} {speech.speakable(text)}")
    return True


MEME_TOPIC = re.compile(r"мем|тренд|тик ?ток|рилс|шортс|челлендж|звук из", re.IGNORECASE)
QUESTION = re.compile(r"\?|(?<![\wё])(что|чё|че|кто|знаешь|слышал\w*|шаришь|откуда|как|зачем|почему)(?![\wё])",
                      re.IGNORECASE)


async def meme_lookup(msg: Message) -> str | None:
    """Спросили про мем или тренд — поищем в интернете, чтобы ответить в теме, а не выдумывать."""
    text = msg.text or msg.caption or _transcripts.get(getattr(msg.voice, "file_unique_id", ""), "")
    if not web.available():
        return None
    me = await msg.bot.me()
    # Имя бота вырезаем до проверки: иначе «мем» нашёлся бы в каком-нибудь «Мемоботе».
    query = name_pattern(me.first_name).sub(" ", text.replace(f"@{me.username}", " "))
    query = " ".join(query.split()).strip(" ,?!")
    if not (MEME_TOPIC.search(query) and QUESTION.search(text)):
        return None
    if not re.search("мем|тренд", query, re.IGNORECASE):
        query += " мем"
    try:
        found = await web.search(query, results=5)
    except Exception as e:  # noqa: BLE001
        log.warning("Не поискал «%s»: %s", query, e)
        return None
    log.info("Поискал в интернете: «%s» — %d результатов", query, len(found))
    return "\n".join(s.line() for s in found) or None


CLIP_EVERY = 5  # ролик — не чаще раза в столько ответов, если его не просили
_replies_since_clip: dict[int, int] = {}
ASKED_FOR_CLIP = re.compile(r"видос|видео|тик ?ток|клип|шортс|рилс|ролик", re.IGNORECASE)


def clip_allowed(msg: Message) -> bool:
    chat_id = msg.chat.id
    _replies_since_clip[chat_id] = _replies_since_clip.get(chat_id, CLIP_EVERY) + 1
    if ASKED_FOR_CLIP.search(msg.text or msg.caption or "") or _replies_since_clip[chat_id] > CLIP_EVERY:
        _replies_since_clip[chat_id] = 0
        return True
    return False


async def send_clip(msg: Message, query: str, *, reply=False) -> bool:
    """Короткий ролик из YouTube Shorts по запросу."""
    if not clips.available():
        return False
    try:
        async with _action(msg, "upload_video"):
            clip = await clips.find_clip(query)
    except Exception as e:  # noqa: BLE001
        log.warning("Не нашёл ролик «%s»: %s", query, e)
        return False
    if not clip:
        return False
    remember_own(await (msg.reply_video if reply else msg.answer_video)(
        BufferedInputFile(clip.data, "clip.mp4"), supports_streaming=True
    ))
    log.info("Кинул ролик по запросу «%s»: %s", query, clip.title[:60])
    ai.remember(msg.chat.id, await bot_name(msg.bot), f"[кинул видос: {clip.title}]")
    return True


async def set_reaction(msg: Message, message_id: int, emoji: str) -> bool:
    try:
        await msg.bot.set_message_reaction(msg.chat.id, message_id, [ReactionTypeEmoji(emoji=emoji)])
    except Exception as e:  # noqa: BLE001 — реакции могут быть запрещены в чате
        log.warning("Не поставил реакцию %s: %s", emoji, e)
        return False
    log.info("Поставил реакцию %s на сообщение %s", emoji, message_id)
    return True


async def send_sticker(msg: Message, emoji: str, *, reply=False, file_id: str | None = None) -> bool:
    """Стикер: выбранный Claude по номеру из меню или случайный на эту эмоцию из тех, что в ходу в чате.
    Если стикеров нет — само эмодзи (в телеге оно большое)."""
    if emoji.isdigit() and not file_id:
        return False  # номер мимо меню
    file_id = file_id or storage.random_sticker(msg.chat.id, emoji)
    if file_id:
        sent = await (msg.reply_sticker if reply else msg.answer_sticker)(file_id)
        label = sticker_label(sent.sticker) if sent and sent.sticker else f"[стикер {emoji}]"
    else:
        await (msg.reply if reply else msg.answer)(emoji)
        label = f"[стикер {emoji}]"
    ai.remember(msg.chat.id, await bot_name(msg.bot), label)
    return True


async def send_picture(msg: Message, query: str, *, reply=False) -> bool:
    """Картинка из Яндекс Картинок по запросу."""
    if not pics.available():
        return False
    try:
        async with _action(msg, "upload_photo"):
            jpeg = await pics.find_picture(query)
    except Exception as e:  # noqa: BLE001
        log.warning("Поиск картинки «%s» не удался: %s", query, e)
        return False
    if not jpeg:
        return False
    remember_own(await (msg.reply_photo if reply else msg.answer_photo)(BufferedInputFile(jpeg, "pic.jpg")), jpeg)
    log.info("Кинул картинку по запросу «%s»", query)
    ai.remember(msg.chat.id, await bot_name(msg.bot), f"[кинул картинку: {query}]")
    return True


async def send_random(msg: Message, *, seed=None, reply=False) -> None:
    roll = random.random()
    if roll < 0.2 and await send_video_meme(msg, seed=seed, reply=reply):
        return
    if roll < 0.7 and await send_image_meme(msg, seed=seed, reply=reply):
        return
    # Вклиниться в разговор: живой репликой, если есть о чём, иначе бредом Маркова.
    if ai.recent_lines(msg.chat.id) >= 5 and await send_ai(msg, author=None, reply=reply):
        return
    await send_phrase(msg, seed=seed, reply=reply)


async def is_admin(msg: Message) -> bool:
    if msg.chat.type == ChatType.PRIVATE:
        return True
    if msg.sender_chat and msg.sender_chat.id == msg.chat.id:  # анонимный админ
        return True
    if not msg.from_user:
        return False
    member = await msg.bot.get_chat_member(msg.chat.id, msg.from_user.id)
    return member.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)


# --- где боту можно быть: решает владелец ---

OWNER_ID = int(os.getenv("OWNER_ID") or 0)  # твой Telegram id; без него бот работает везде, как раньше


def chat_allowed(chat_id: int) -> bool:
    """Беседа — если владелец разрешил; личка — владельцу и участникам разрешённых бесед."""
    if not OWNER_ID:
        return True
    if chat_id > 0:
        return chat_id == OWNER_ID or storage.is_member_of_allowed(chat_id)
    access = storage.chat_access(chat_id)
    return bool(access) and access[0] == "allowed"


def group_chats(chat_ids: list[int]) -> list[int]:
    """Только разрешённые — для фоновых задач (летопись, эдиты, «написать первым»)."""
    return [c for c in chat_ids if chat_allowed(c)]


def access_buttons(chat_id: int, allowed: bool) -> InlineKeyboardMarkup:
    if allowed:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🚫 Запретить и выйти", callback_data=f"deny:{chat_id}")]])
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Разрешить", callback_data=f"allow:{chat_id}"),
        InlineKeyboardButton(text="🚫 Выйти", callback_data=f"deny:{chat_id}")]])


async def ask_owner(bot: Bot, chat: Chat, who: str | None = None) -> None:
    """Незнакомая беседа: молчим и спрашиваем владельца (один раз). Запрещённая — выходим."""
    access = storage.chat_access(chat.id)
    if access and access[0] == "denied":
        await leave(bot, chat.id)
        return
    if access and access[0] == "pending":
        return
    storage.set_chat_access(chat.id, "pending", chat.title)
    log.info("Меня добавили в «%s» (%s) — спрашиваю владельца", chat.title, chat.id)
    try:
        await bot.send_message(OWNER_ID, f"Меня добавили в беседу «{chat.title}»" + (f" ({who})" if who else "")
                               + ". Пока молчу и ничего не запоминаю. Разрешить?",
                               reply_markup=access_buttons(chat.id, allowed=False))
    except TelegramBadRequest as e:  # владелец ни разу не писал боту — написать ему нельзя
        log.warning("Не смог спросить владельца (напиши боту /start в личку): %s", e)


async def leave(bot: Bot, chat_id: int) -> None:
    try:
        await bot.leave_chat(chat_id)
    except Exception as e:  # noqa: BLE001 — уже не там
        log.info("Не вышел из %s: %s", chat_id, e)


@router.message.outer_middleware()
async def only_allowed_chats(handler, msg: Message, data):
    """Бот работает только там, где разрешил владелец; в остальных местах молчит и ничего не запоминает."""
    if chat_allowed(msg.chat.id):
        if OWNER_ID and msg.chat.id < 0 and msg.chat.title and storage.chat_access(msg.chat.id)[1] != msg.chat.title:
            storage.set_chat_access(msg.chat.id, "allowed", msg.chat.title)  # беседу переименовали
        return await handler(msg, data)
    if msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        await ask_owner(msg.bot, msg.chat)
    return None


@router.my_chat_member()
async def on_membership(update: ChatMemberUpdated) -> None:
    """Бота добавили в беседу: если добавил владелец — сразу можно, иначе спросить владельца."""
    if not OWNER_ID or update.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if update.new_chat_member.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        return
    by = update.from_user
    if by and by.id == OWNER_ID:
        storage.set_chat_access(update.chat.id, "allowed", update.chat.title)
        log.info("Владелец добавил меня в «%s» — работаю", update.chat.title)
    elif not chat_allowed(update.chat.id):
        if (access := storage.chat_access(update.chat.id)) and access[0] == "denied":
            storage.set_chat_access(update.chat.id, "pending", update.chat.title)  # снова добавили — спросим снова
        await ask_owner(update.bot, update.chat, f"добавил {by.full_name}" if by else None)


@router.callback_query(F.data.regexp(r"^(allow|deny):-?\d+$"))
async def on_access_decision(cb: CallbackQuery) -> None:
    if not OWNER_ID or cb.from_user.id != OWNER_ID:
        await cb.answer("Это решает хозяин бота", show_alert=True)
        return
    action, chat_id = cb.data.split(":")
    chat_id = int(chat_id)
    title = (storage.chat_access(chat_id) or ("", str(chat_id)))[1] or str(chat_id)
    if action == "allow":
        storage.set_chat_access(chat_id, "allowed")
        text = f"✅ «{title}» — разрешил. Работаю там."
    else:
        storage.set_chat_access(chat_id, "denied")
        await leave(cb.bot, chat_id)
        text = f"🚫 «{title}» — вышел оттуда и больше туда не хожу (пока снова не разрешишь)."
    log.info("Владелец: %s «%s»", action, title)
    if cb.message:
        await cb.message.edit_text(text)
    await cb.answer()


@router.message(Command("chats"))
async def cmd_chats(msg: Message) -> None:
    """Владельцу в личке: где бот есть и куда ему можно."""
    if not OWNER_ID or msg.chat.id != OWNER_ID:
        return
    known = [c for c in storage.chat_accesses() if c[0] < 0]
    if not known:
        await msg.reply("Я пока ни в одной беседе")
        return
    names = {"allowed": "✅ разрешена", "pending": "⏳ ждёт решения", "denied": "🚫 запрещена"}
    for chat_id, status, title in known:
        await msg.answer(f"«{title or chat_id}» — {names.get(status, status)}",
                         reply_markup=access_buttons(chat_id, allowed=status == "allowed"))


@router.message.outer_middleware()
async def attach_imported_history(handler, msg: Message, data):
    """Подхватывает импорт старой переписки, когда впервые видит беседу с тем же названием."""
    if msg.chat.title and storage.attach_import(msg.chat.id, msg.chat.title):
        s = storage.stats(msg.chat.id)
        log.info("Импорт истории привязан к чату %s «%s»", msg.chat.id, msg.chat.title)
        await msg.answer(
            f"О, я вспомнил старую переписку 👀 {s['phrases']} фраз и {s.get('photo', 0)} фоток. Щас начнётся"
        )
    return await handler(msg, data)


# --- команды ---


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_help(msg: Message) -> None:
    await msg.answer(HELP)


@router.message(Command("meme", "dem", "vid"))
async def cmd_meme(msg: Message, command: CommandObject) -> None:
    remember(msg)
    style = "demotivator" if command.command == "dem" else None
    texts = parse_texts(command.args)
    # Медиа из самого сообщения (фото с подписью /meme) или из того, на что ответили.
    source = as_source(media_of(msg) or media_of(msg.reply_to_message))
    want_video = command.command == "vid" or (source is not None and source.kind != "photo")
    if want_video and source and source.kind == "photo":
        source = None
    try:
        if want_video:
            if not await send_video_meme(msg, source=source, style=style, texts=texts, reply=True):
                await msg.reply("Видосов пока нет — скиньте в чат пару видео или гифок")
        elif not await send_image_meme(msg, source=source, style=style, texts=texts, reply=True):
            await msg.reply("Фоток пока нет — скиньте в чат пару картинок")
    except Exception:
        log.exception("не удалось сделать мем")
        await msg.reply("Не вышло, сорян 🤷")


_transcripts: dict[str, str] = {}  # file_unique_id → текст, чтобы не платить за одно голосовое дважды


async def transcribe_voice(msg: Message) -> str | None:
    """Текст голосового или кружочка через SpeechKit; None — если это не голосовое или не вышло."""
    media = msg.voice or msg.video_note
    if not media or not speech.available():
        return None
    if media.file_unique_id not in _transcripts:
        try:
            text = await speech.transcribe(await download(msg.bot, media.file_id))
        except Exception as e:  # noqa: BLE001
            log.warning("Не расшифровал голосовое: %s", e)
            return None
        log.info("Расшифровал %s: %d с, %d слов", "голосовое" if msg.voice else "кружочек",
                 media.duration or 0, len(text.split()))
        if len(_transcripts) > 1000:
            _transcripts.clear()
        _transcripts[media.file_unique_id] = text
    return _transcripts[media.file_unique_id] or None


@router.message(Command("say"))
async def cmd_say(msg: Message, command: CommandObject) -> None:
    target = msg.reply_to_message
    args = (command.args or "").strip()
    first, _, rest = args.partition(" ")
    style = first if speech.voice_style(first) else None  # /say демон всем салам
    if style:
        args = rest.strip()
    text = args or (target and (target.text or target.caption)) or ""
    if not speech.tts_available():
        await msg.reply("Голос не настроен — нужен ключ Yandex AI Studio в .env")
    elif not speech.speakable(text):
        await msg.reply("Напиши, что сказать: /say всем привет — или ответь /say на сообщение.\n"
                        "Голоса: " + ", ".join(speech.VOICE_STYLES) + " — например /say демон всем салам")
    elif not await send_voice(msg, text, reply=True, style=style):
        await msg.reply("Не вышло озвучить 🤷")


@router.message(Command("trends"))
async def cmd_trends(msg: Message) -> None:
    if not ai.MEMES_DIGEST:
        await msg.reply("Ещё не собрал свежие мемы — нужен ключ Yandex AI Studio в .env, и дай мне пару минут")
        return
    await msg.reply(("Что сейчас в тренде (обновляю раз в день):\n" + ai.MEMES_DIGEST)[:4000])


@router.message(Command("text"))
async def cmd_text(msg: Message) -> None:
    target = msg.reply_to_message
    if not target or not (target.voice or target.video_note):
        await msg.reply("Ответь этой командой на голосовое или кружочек")
        return
    if not speech.available():
        await msg.reply("Расшифровка не настроена — нужен ключ Yandex AI Studio в .env")
        return
    spoken = await transcribe_voice(target)
    await msg.reply(f"🗣 {spoken}" if spoken else "Ничего не разобрал 🤷")


@router.message(Command("edit"))
async def cmd_edit(msg: Message, command: CommandObject) -> None:
    await edit_and_report(msg, command.args or "")


@router.message(Command("tracks"))
async def cmd_tracks(msg: Message) -> None:
    lib = LIB.tracks()
    if not lib:
        await msg.reply("Треков пока нет — добавь: /track matadora")
        return
    await msg.reply("🎵 Треки для эдитов:\n" + "\n".join(f"— {t.title} ({60 / t.period:.0f} BPM)" for t in lib)
                    + "\n\nЭдит под конкретный: /edit матадора · добавить свой: /track название")


@router.message(Command("track"))
async def cmd_track(msg: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await msg.reply("Напиши, какой трек добавить: /track murder in my mind — или ссылку на YouTube")
        return
    try:
        async with _action(msg, "record_voice"):
            track = await LIB.add(query)
    except Exception as e:  # noqa: BLE001
        log.warning("Не добавил трек «%s»: %s", query, e)
        await msg.reply("Не нашёл или не скачал такой трек 🤷")
        return
    ai.refresh(msg.chat.id)
    await msg.reply(f"Добавил: {track.title} ({60 / track.period:.0f} BPM, дроп на {track.drop:.0f} с)")


@router.message(Command("name"))
async def cmd_name(msg: Message, command: CommandObject) -> None:
    """Кто есть кто: /name Shadow = Дима · ответом на сообщение: /name Дима · /name Shadow = — забыть · /name — список."""
    args = (command.args or "").strip()
    reply_to = msg.reply_to_message
    if "=" in args:
        nick, name = (part.strip().strip("«»\"'") for part in args.split("=", 1))
    elif args and reply_to and reply_to.from_user and not reply_to.from_user.is_bot:
        nick, name = reply_to.from_user.full_name, args
    else:
        people = storage.people(msg.chat.id)
        if not people:
            await msg.reply("Я пока не знаю, кто есть кто. Научи: /name ник = имя")
            return
        nicks: dict[str, list[str]] = {}
        for known, name in people:
            nicks.setdefault(name, []).append(known)
        await msg.reply("Кто есть кто:\n" + "\n".join(f"— {name}: {', '.join(n)}" for name, n in nicks.items())
                        + "\n\nДобавить: /name ник = имя (или ответь на сообщение: /name имя) · забыть: /name ник =")
        return
    if not nick:
        await msg.reply("Напиши так: /name Shadow = Дима")
        return
    if not await is_admin(msg):
        await msg.reply("Менять может только админ")
        return
    if name:
        storage.set_person(msg.chat.id, nick[:64], name[:64])
        await msg.reply(f"Запомнил: {nick[:64]} — это {name[:64]} 👌")
    elif storage.forget_person(msg.chat.id, nick):
        await msg.reply(f"Забыл, кто такой {nick}")
    else:
        await msg.reply("Такого ника я не знаю")
    ai.refresh(msg.chat.id)


@router.message(Command("lore"))
async def cmd_lore(msg: Message) -> None:
    """Что бот помнит про беседу — его летопись."""
    text = storage.lore(msg.chat.id)["text"]
    if not text:
        await msg.reply("Я ещё не дочитал вашу переписку — дай время 📖")
        return
    for start in range(0, len(text), 4000):
        await msg.answer(text[start:start + 4000])


@router.message(Command("mood"))
async def cmd_mood(msg: Message) -> None:
    """Как бот сейчас: настроение, отношения, что у него в жизни."""
    await msg.reply(state_text(msg.chat.id))


FACE_HOW = ("Скинь своё фото с подписью /face (или ответь /face на своё фото, где ты один) — запомню лицо и буду "
            "узнавать тебя на фотках, в мемах и эдитах. /face забудь — забыть. Запомнить можно только себя.")
FACE_FORGET = {"забудь", "забыть", "удали", "удалить", "стоп", "хватит", "нет"}


@router.message(Command("face"))
async def cmd_face(msg: Message, command: CommandObject) -> None:
    """Записаться, чтобы бот узнавал в лицо (только себя), или забыть своё лицо."""
    if not faces.available():
        await msg.reply("Лица узнавать я пока не умею — на компе нет OpenCV")
        return
    if msg.chat.type == ChatType.PRIVATE or not msg.from_user:
        await msg.reply("Это в беседе: скинь туда своё фото с подписью /face")
        return
    user, chat_id = msg.from_user, msg.chat.id
    if (command.args or "").strip().lower() in FACE_FORGET:
        forgot = storage.forget_faces(chat_id, user.id)
        await msg.reply("Забыл твоё лицо 🫡 Больше не узнаю тебя на фотках" if forgot else "Я тебя и так в лицо не знаю")
        return
    source = msg if msg.photo else msg.reply_to_message if msg.reply_to_message and msg.reply_to_message.photo else None
    if not source:
        await msg.reply(FACE_HOW)
        return
    if source is not msg and (not source.from_user or source.from_user.id != user.id):
        await msg.reply("Запомнить можно только себя — скинь своё фото с подписью /face 🙂")
        return
    found = await asyncio.to_thread(faces.find, await download(msg.bot, source.photo[-1].file_id))
    found.sort(key=lambda f: -f.size)
    if not found:
        await msg.reply("Не вижу тут лица 🤔 Нужно фото, где лицо нормально видно")
        return
    if len(found) > 1 and found[1].size > found[0].size * 0.6:
        await msg.reply("Тут несколько лиц — скинь фото, где ты один (или где ты крупнее всех)")
        return
    name = real_name(chat_id, user.full_name, user.username) or user.first_name
    count = storage.add_face(chat_id, user.id, name, faces.to_bytes(found[0].feature), keep=faces.SAMPLES)
    log.info("%s записал лицо в %s (%d фото)", name, chat_id, count)
    if count == 1:
        await msg.reply(f"Запомнил тебя, {name} 👀 Теперь узнаю тебя на фотках, в мемах и эдитах.\n"
                        "Лицо хранится только у меня на компе, /face забудь — стереть. Скинешь ещё пару фото "
                        "с разных ракурсов — буду узнавать увереннее.")
    else:
        await msg.reply(f"Добавил ещё фото ({count}) — теперь узнаю тебя увереннее 👌")


@router.message(Command("faces"))
async def cmd_faces(msg: Message) -> None:
    people = storage.face_people(msg.chat.id)
    if not people:
        await msg.reply("Я пока никого тут не знаю в лицо. " + FACE_HOW)
        return
    await msg.reply("Узнаю в лицо: " + ", ".join(name for name, _ in people)
                    + "\n\nЗаписаться: своё фото с подписью /face · забыть себя: /face забудь")


@router.message(Command("nomeme"))
async def cmd_nomeme(msg: Message) -> None:
    """Ответом на картинку или видео: больше не делать из этого мемы (например, это уже мой мем)."""
    found = media_of(msg.reply_to_message)
    if not found:
        await msg.reply("Ответь этой командой на картинку, видео или гифку")
        return
    storage.mark_own([found[2]])
    storage.forget_media(msg.chat.id, found[2])
    if found[0] == "photo":
        try:
            storage.add_own_hash(render.fingerprint(await download(msg.bot, found[1])))
        except Exception as e:  # noqa: BLE001 — не скачалось: хватит и file_unique_id
            log.warning("Не снял отпечаток: %s", e)
    await msg.reply("Ок, из этого мемы больше не делаю")


@router.message(Command("clip"))
async def cmd_clip(msg: Message, command: CommandObject) -> None:
    if not clips.available():
        await msg.reply("Ролики не настроены — нужны yt-dlp и Node.js (см. README)")
        return
    query = (command.args or "").strip()
    if not query:
        await msg.reply("Напиши, что искать: /clip сырный михаил")
        return
    if not await send_clip(msg, query, reply=True):
        await msg.reply("Не нашёл ролик 🤷")


@router.message(Command("pic"))
async def cmd_pic(msg: Message, command: CommandObject) -> None:
    if not pics.available():
        await msg.reply("Поиск картинок не настроен — нужен ключ Yandex Search API в .env")
        return
    query = (command.args or "").strip()
    if not query:
        await msg.reply("Напиши, что искать: /pic котик в шапке")
        return
    if not await send_picture(msg, query, reply=True):
        await msg.reply("Ничего не нашёл 🤷")


@router.message(Command("gen"))
async def cmd_gen(msg: Message, command: CommandObject) -> None:
    seed = command.args.split()[0] if command.args else None
    await send_phrase(msg, seed=seed, reply=True)


@router.message(Command("chance"))
async def cmd_chance(msg: Message, command: CommandObject) -> None:
    chat_id = msg.chat.id
    if not command.args:
        current = storage.chance(chat_id) * 100
        await msg.reply(f"Сейчас я сам кидаю мем примерно после {current:g}% сообщений.\nПоменять: /chance 5")
        return
    if not await is_admin(msg):
        await msg.reply("Менять может только админ")
        return
    try:
        value = float(command.args.strip().rstrip("%").replace(",", "."))
    except ValueError:
        value = -1
    if not 0 <= value <= 100:
        await msg.reply("Нужно число от 0 до 100, например /chance 5")
        return
    storage.set_chance(chat_id, value / 100)
    tail = " — буду молчать, пока не позовут" if value == 0 else ""
    await msg.reply(f"Ок, теперь {value:g}%{tail}")


@router.message(Command("stats"))
async def cmd_stats(msg: Message) -> None:
    s = storage.stats(msg.chat.id)
    await msg.reply(
        "Я помню:\n"
        f"🖼 фоток: {s.get('photo', 0)}\n"
        f"🎬 видео: {s.get('video', 0)}\n"
        f"🌀 гифок: {s.get('animation', 0)}\n"
        f"💬 фраз: {s['phrases']}\n\n"
        f"Сам кидаю мем после ~{storage.chance(msg.chat.id) * 100:g}% сообщений"
    )


@router.message(Command("forget"))
async def cmd_forget(msg: Message, command: CommandObject) -> None:
    if not await is_admin(msg):
        await msg.reply("Стирать память может только админ")
        return
    if (command.args or "").strip().lower() != "да":
        await msg.reply("Точно? Я забуду все фотки, видео и фразы этого чата.\nЕсли да — напиши /forget да")
        return
    storage.forget(msg.chat.id)
    ai.forget(msg.chat.id)
    await msg.reply("Всё забыл. Кто вы? 👀")


# --- все остальные сообщения ---


@router.message()
async def on_message(msg: Message, bot: Bot) -> None:
    spoken = await transcribe_voice(msg)
    remember(msg, spoken)
    if is_other_bot(msg):
        return

    me = await bot.me()
    text = msg.text or msg.caption or spoken or ""
    reply_to = msg.reply_to_message
    addressed = (
        msg.chat.type == ChatType.PRIVATE
        or (reply_to and reply_to.from_user and reply_to.from_user.id == me.id)
        or f"@{me.username}".lower() in text.lower()
        or bool(name_pattern(me.first_name).search(text))
    )
    try:
        if addressed:
            await answer_addressed(msg, text)
        elif is_followup(msg, me.id):
            await answer_followup(msg)
        elif random.random() < storage.chance(msg.chat.id) and storage.try_claim_post(msg.chat.id, COOLDOWN):
            await send_random(msg)
        elif msg.chat.type != ChatType.PRIVATE:
            await maybe_react(msg)
    except Exception:
        log.exception("не удалось ответить")


# Реакции: примерно раз в столько сообщений бот смотрит свежую переписку и реагирует на смешное.
REACT_EVERY = (6, 12)
REACT_PAUSE = 45  # и не чаще раза в столько секунд
_react_countdown: dict[int, int] = {}
_last_react_pass: dict[int, float] = {}


async def maybe_react(msg: Message) -> None:
    if not ai.available():
        return
    chat_id = msg.chat.id
    left = _react_countdown.get(chat_id, random.randint(*REACT_EVERY)) - 1
    _react_countdown[chat_id] = left
    if left > 0 or time.monotonic() - _last_react_pass.get(chat_id, 0) < REACT_PAUSE:
        return
    _react_countdown[chat_id] = random.randint(*REACT_EVERY)
    _last_react_pass[chat_id] = time.monotonic()
    name, title = await bot_name(msg.bot), msg.chat.title or "личка"
    for message_id, emoji in await ai.pick_reactions(chat_id, name, title, examples_loader(chat_id)):
        await set_reaction(msg, message_id, emoji)


@lru_cache
def name_pattern(first_name: str) -> re.Pattern:
    """«Мемобот», «мемобота», «мемоботом», «бот», «бота»… — как к боту обращаются без тега."""
    words = []
    for part in first_name.lower().split():
        if len(part) >= 3:
            stem = part[:-1] if len(part) > 3 and part[-1] in "аяоеиыуюь" else part
            words.append(re.escape(stem) + r"[а-яё]{0,3}")
    for alias in BOT_ALIASES:
        words.append(re.escape(alias) + r"(?:а|у|е|ом|ой|и|ы|ик|яра)?")
    return re.compile(r"(?<![\wё])(?:" + "|".join(words) + r")(?![\wё])", re.IGNORECASE)


@dataclass
class _Talk:
    user_id: int
    until: float
    turns: int = 0  # сколько раз подряд бот ответил без прямого обращения


_talks: dict[int, _Talk] = {}  # с кем бот сейчас разговаривает в чате


FOLLOWUP_MAX = 3  # без прямого обращения — не больше стольких ответов подряд, а то надоедает


def start_talk(msg: Message, followup: bool = False) -> None:
    """Бот ответил — следующие FOLLOWUP_WINDOW секунд может ответить и без обращения, но не бесконечно."""
    if not msg.from_user or msg.chat.type == ChatType.PRIVATE:
        return
    old = _talks.get(msg.chat.id)
    turns = old.turns + 1 if followup and old and old.user_id == msg.from_user.id else 0
    if turns >= FOLLOWUP_MAX:
        _talks.pop(msg.chat.id, None)  # хватит: дальше — только если позовут
        return
    _talks[msg.chat.id] = _Talk(msg.from_user.id, time.monotonic() + FOLLOWUP_WINDOW, turns)


def is_followup(msg: Message, bot_id: int) -> bool:
    """Сообщение без обращения, но от того, с кем бот только что говорил, — может, это ему."""
    talk = _talks.get(msg.chat.id)
    if not talk or not msg.from_user or msg.from_user.id != talk.user_id or time.monotonic() > talk.until:
        return False
    reply_to = msg.reply_to_message
    if reply_to and reply_to.from_user and reply_to.from_user.id != bot_id:
        return False  # ответил кому-то другому
    return bool(msg.text or msg.voice or msg.video_note) and ai.available()


async def answer_followup(msg: Message) -> None:
    if await send_ai(msg, author=author_of(msg), maybe=True):
        start_talk(msg, followup=True)  # разговор продолжается
    else:
        _talks.pop(msg.chat.id, None)  # Claude решил, что это не ему


async def answer_addressed(msg: Message, text: str) -> None:
    words = [w for w in map(markov.normalize, text.split()) if len(w) >= 3 and not w.startswith("@")]
    seed = random.choice(words) if words else None
    # Кружочек, в котором к боту обратились, — это разговор, а не заготовка для мема.
    source = None if msg.video_note else as_source(media_of(msg))
    if source and source.kind == "photo":
        await send_image_meme(msg, source=source, seed=seed, reply=True)
    elif source:
        await send_video_meme(msg, source=source, seed=seed, reply=True)
    elif ai.available():
        # С ИИ бред Маркова в ответ на обращение выглядит тупо: если Claude не ответил
        # (лимит, сбой) или выпал редкий шанс — лучше мем.
        if random.random() < AI_REPLY_SHARE and await send_ai(msg, author=author_of(msg), reply=True):
            start_talk(msg)
            return
        if not await send_image_meme(msg, seed=seed, reply=True):
            await send_phrase(msg, seed=seed, reply=True)
    elif random.random() < 0.5:
        await send_phrase(msg, seed=seed, reply=True)
    else:
        await send_random(msg, seed=seed, reply=True)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Нет BOT_TOKEN: скопируй .env.example в .env и впиши токен от @BotFather")

    bot = Bot(token)
    dp = Dispatcher()
    dp.include_router(router)

    me = await wait_for_telegram(bot)
    log.info("Запущен как @%s", me.username)
    ai.PICTURES = ai.available() and pics.available()
    ai.VOICE_ON = ai.available() and speech.tts_available()
    ai.CLIPS_ON = ai.available() and clips.available()
    ai.EDITS_ON = edits.available() and bool(LIB.tracks())
    if edits.available() and not LIB.tracks() and clips.available():
        log.info("Эдиты: треков нет — качаю стартовую библиотеку фонка в фоне")
        run_in_background(fill_library())
    else:
        log.info("Эдиты: %s", f"{len(LIB.tracks())} треков" if ai.EDITS_ON else "выключены (EDITS=0, нет ffmpeg или треков)")
    log.info("Ролики: %s", "YouTube Shorts через yt-dlp" if clips.available() else "выключены (нет yt-dlp или Node.js)")
    ai.VOICE_STYLES = {name: st.about for name, st in speech.VOICE_STYLES.items()}
    log.info("Картинки из интернета: %s", "Yandex Search API" if pics.available() else "выключены (нет ключа в .env)")
    log.info("Голосовые: %s", "Yandex SpeechKit" if speech.available() else "не расшифровываю (нет ключа в .env)")
    log.info("Голос бота: %s", f"{speech.TTS_VOICE} ({speech.TTS_EMOTION or 'обычный'})" if speech.tts_available() else "выключен")
    if ai.available():
        log.info("Живые ответы: %s через %s, модель %s", ai.NAME, ai.CLI, ai.model_label())
    else:
        log.info("Ни Claude Code, ни Codex не найдены — отвечаю только мемами и бредом Маркова")
    if not me.can_read_all_group_messages:
        log.warning(
            "Privacy mode включён — в группах я вижу только команды. "
            "Выключи: @BotFather → /setprivacy → Disable, потом перезайди ботом в чат."
        )
    try:
        await bot.set_my_commands(COMMANDS)
    except TelegramNetworkError as e:  # меню команд не критично — обновится при следующем запуске
        log.warning("Не удалось обновить меню команд: %s", e)
    if OWNER_ID and not storage.chat_accesses():  # первый запуск с ограничением: где уже работал — там можно
        for chat_id in storage.dialog_chats():
            storage.set_chat_access(chat_id, "allowed", await chat_title(bot, chat_id))
    log.info("Где можно: %s", "решает владелец %s" % OWNER_ID if OWNER_ID else "везде (OWNER_ID не задан)")
    run_in_background(watch_stickers(bot))
    run_in_background(watch_memes())
    run_in_background(watch_media(bot))
    run_in_background(watch_weekly(bot))
    refresh_life()
    for chat_id in group_chats(storage.dialog_chats()):
        ai.set_state(chat_id, state_text(chat_id))
    run_in_background(watch_memory(bot))
    run_in_background(watch_initiative(bot))
    if faces.cv2 is not None:
        try:
            await asyncio.to_thread(faces.ensure_models)
        except Exception as e:  # noqa: BLE001
            log.warning("Не скачал модели для лиц: %s", e)
    log.info("Лица: %s", "узнаю тех, кто записался через /face" if faces.available() else "выключены (нет OpenCV)")
    run_in_background(watch_faces(bot))
    log.info("Память: %s", ", ".join(f"{c}: летопись {len(storage.lore(c)['text'])} знаков"
                                     for c in group_chats(storage.dialog_chats())) or "пока нет бесед")
    log.info("Пишу первым: %s", "да" if INITIATIVE and ai.available() else "нет")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())  # дальше обрывы сети aiogram переживает сам


def run_in_background(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
          "ноябрь", "декабрь"]
MEMES_EVERY = 24 * 3600  # шпаргалку по свежим мемам обновляем раз в сутки


async def watch_memes() -> None:
    """Раз в день собирает свежие мемы (Мемепедия + поиск Яндекса) в шпаргалку для Claude."""
    path = storage.base / "memes.md"
    while True:
        stale = not path.exists() or time.time() - path.stat().st_mtime > MEMES_EVERY
        if stale and web.available() and ai.available():
            try:
                await build_memes(path)
            except Exception as e:  # noqa: BLE001
                log.warning("Не собрал свежие мемы: %s", e)
        if path.exists() and (digest := path.read_text(encoding="utf-8").strip()) != ai.MEMES_DIGEST:
            ai.MEMES_DIGEST = digest
            ai._packs.clear()  # шпаргалка в закэшированной части промпта — пересоберём
            log.info("Шпаргалка по мемам: %d мемов", digest.count("\n") + 1)
        await asyncio.sleep(3600)


async def build_memes(path: Path) -> None:
    now = time.localtime()
    month = f"{MONTHS[now.tm_mon - 1]} {now.tm_year}"
    sources = await web.memepedia(pages=3)
    for query in (f"мемы {month}", f"тренды тикток {month}", "новые мемы неделя тикток"):
        sources += await web.search(query, results=8)
    text = "\n".join(s.line() for s in sources)
    digest = await ai.build_memes_digest(text, time.strftime("%d.%m.%Y"))
    if digest:
        path.write_text(digest, encoding="utf-8")


STICKER_BATCH = 16  # столько стикеров Claude рассматривает за раз (сеткой 4×4)


async def watch_stickers(bot: Bot) -> None:
    """Фоном рассматривает стикеры чата и запоминает, что на них, — любимые первыми."""
    if not ai.available():
        return
    # У паков, скачанных до того, как бот научился видеть, не было превью — докачиваем.
    for chat_id, set_name in storage.db.execute("SELECT chat_id, set_name FROM sticker_sets").fetchall():
        await learn_sticker_set(bot, chat_id, set_name)
    while True:
        batch = storage.undescribed_stickers(STICKER_BATCH)
        if not batch:
            await asyncio.sleep(60)
            continue
        images, ready, network_down = [], [], False
        for unique_id, file_id, thumb_id, kind in batch:
            try:
                data = await sticker_image(bot, file_id, thumb_id, kind)
            except TelegramNetworkError:
                network_down = True  # сеть до Telegram отвалилась — не беда, попробуем позже
                break
            except Exception as e:  # noqa: BLE001 — битый файл и т.п.
                log.debug("не скачал стикер %s: %s", unique_id, e)
                data = None
            if data:
                images.append(data)
                ready.append(unique_id)
            else:
                storage.set_sticker_description(unique_id, "")  # рассмотреть нечем — больше не пробуем
        if network_down:
            await asyncio.sleep(60)
            continue
        if images:
            try:
                descriptions = await ai.describe_images(images)
            except Exception as e:  # noqa: BLE001
                log.warning("Не вышло рассмотреть стикеры: %s", e)
                await asyncio.sleep(60)
                continue
            chats = set()
            for unique_id, desc in zip(ready, descriptions):
                chats.update(storage.set_sticker_description(unique_id, desc or ""))
            for chat_id in chats:
                ai.refresh(chat_id)  # меню стикеров в промпте обновится при следующем ответе
        await asyncio.sleep(5)


async def sticker_image(bot: Bot, file_id: str, thumb_id: str | None, kind: str) -> bytes | None:
    """Картинка стикера для Claude: сам файл у обычных, превью у анимированных и видео."""
    if kind == "static":
        data = await download(bot, file_id)
        if not data.startswith(b"\x1f\x8b") and not data.startswith(b"\x1a\x45\xdf\xa3"):
            return data  # webp/png; .tgs (gzip) и .webm сюда не попадают
        kind = "video" if data.startswith(b"\x1a\x45\xdf\xa3") else "animated"
    if thumb_id:
        return await download(bot, thumb_id)
    if kind == "video":
        return await video.frame(await download(bot, file_id))
    return None  # анимированный без превью — рассмотреть нечем


async def wait_for_telegram(bot: Bot):
    """На старте Telegram бывает недоступен — ждём, а не падаем."""
    delay = 2
    while True:
        try:
            return await bot.me()
        except TelegramNetworkError as e:
            log.warning("Telegram недоступен (%s), пробую снова через %d с", e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)



# --- тикток-эдиты ---

_edit_lock = asyncio.Lock()  # монтаж тяжёлый — по одному
TRANSLIT = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
                          "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
                          "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh",
                          "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"})
EDIT_MIN_CLIPS = 6
EDIT_FILLER = {"под", "про", "на", "из", "с", "в", "и", "трек", "трека", "музыку", "эдит", "эдита"}


def split_edit_request(request: str) -> tuple[tracks.Track | None, str | None]:
    """«дача матадора» → (трек MATADORA, тема «дача»): слова, похожие на название трека, — это трек."""
    track, rest = None, []
    for word in request.lower().split():
        stems = {word[:5], word.translate(TRANSLIT)[:5]}
        hits = [t for t in LIB.tracks() if len(word) >= 4 and any(st in t.title.lower() for st in stems)]
        if hits and not track:
            track = random.choice(hits)
        else:
            rest.append(word)
    rest = [w for w in rest if w not in EDIT_FILLER]
    return track, " ".join(rest).strip() or None


async def fetch_clip(bot: Bot, row: dict, folder: Path) -> edits.Clip:
    if row["path"]:
        path = storage.base / row["path"]
    else:
        path = folder / f"m{row['id']}{'.jpg' if row['kind'] == 'photo' else '.mp4'}"
        await bot.download(row["file_id"], destination=path, timeout=120)
    if row["kind"] == "photo":
        return edits.Clip(path, "photo", 0.0, await asyncio.to_thread(edits.photo_activity, path))
    duration, _, _ = await asyncio.to_thread(edits.probe, path)
    lively = await asyncio.to_thread(edits.activity, path)
    return edits.Clip(path, "circle" if row["shape"] == "circle" else "video", duration, lively)


async def make_edit(bot: Bot, chat_id: int, chat_title: str, request: str = "", *, reply_to: int | None = None,
                    since: int = 0, weekly: bool = False) -> str | None:
    """Собирает и отправляет эдит. None — получилось, иначе — почему нет."""
    if not edits.available() or not LIB.tracks():
        return "Эдиты не настроены: нет ffmpeg или треков (/track название)"
    track, theme = split_edit_request(request)
    track = track or LIB.pick()
    rows = storage.edit_candidates(chat_id, since)
    if theme:
        try:
            items = [(r["id"], r["description"] + (f" · на фото: {r['people']}" if r["people"] else ""))
                     for r in rows if r["description"]]
            ids = await ai.pick_for_theme(theme, items[:400])
        except Exception as e:  # noqa: BLE001 — Claude не помог с темой: возьмём просто хорошие кадры
            log.warning("Не подобрал кадры по теме «%s»: %s", theme, e)
            ids = []
        by_id = {r["id"]: r for r in rows}
        chosen = [by_id[i] for i in ids if i in by_id]
        if len(chosen) < 8:  # по теме мало — добиваем просто хорошими кадрами
            chosen += [r for r in sorted(rows, key=lambda r: -r["score"]) if r not in chosen][:16 - len(chosen)]
    else:
        chosen = sorted(rows, key=lambda r: -r["score"])[:30]  # порядок внутри одной оценки — случайный
    if len(chosen) < EDIT_MIN_CLIPS:
        left = storage.unrated_count()
        return "Мало материала для эдита — " + (f"я ещё рассматриваю {left} фоток и видео, подожди" if left
                                                  else "скиньте больше фоток, видосов и кружочков")
    name = await bot_name(bot)
    async with _edit_lock, ChatActionSender.upload_video(bot=bot, chat_id=chat_id):
        with tempfile.TemporaryDirectory() as tmp:
            clips_ = []
            for row in chosen:
                try:
                    clips_.append(await fetch_clip(bot, row, Path(tmp)))
                except Exception as e:  # noqa: BLE001 — один файл не скачался, не беда
                    log.warning("Не взял в эдит media %s: %s", row["id"], e)
            if len(clips_) < EDIT_MIN_CLIPS:
                return "Не смог скачать материал для эдита — сеть до Telegram шалит, попробуй ещё раз"
            slots, lead, windows = edits.plan_velocity(clips_, track.period, track.drop, track.duration - track.drop,
                                                       random.Random())
            texts = await ai.edit_captions(chat_id, name, chat_title, examples_loader(chat_id),
                                           theme or ("итоги недели" if weekly else None))
            captions = [edits.Caption(text, a, b) for text, (a, b) in zip(texts, windows)]
            out = Path(tmp) / "edit.mp4"
            started = time.monotonic()
            await edits.build_edit_async(slots, LIB.path(track), edits.Beats(track.period, track.drop, track.duration),
                                         captions, out, lead_beats=lead)
            data = out.read_bytes()
    label = ("🗓 итоги недели · " if weekly else "") + f"🎵 {track.title}"
    reply = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
    sent = await bot.send_video(chat_id, BufferedInputFile(data, "edit.mp4"), caption=label,
                                supports_streaming=True, reply_parameters=reply)
    remember_own(sent)
    ai.remember(chat_id, name, f"[скинул эдит под {track.title}]")
    log.info("Эдит под %s: %d кадров, тема «%s», собран за %.1f с", track.title, len(slots), theme or "-",
             time.monotonic() - started)
    return None


async def edit_and_report(msg: Message, request: str) -> None:
    try:
        problem = await make_edit(msg.bot, msg.chat.id, msg.chat.title or "личка", request, reply_to=msg.message_id)
    except Exception:
        log.exception("эдит не собрался")
        problem = "Эдит не собрался, сорян 🤷"
    if problem:
        await msg.reply(problem)


MEDIA_BATCH = 16


async def media_frame(bot: Bot, kind: str, file_id: str, path: str | None, folder: Path) -> tuple[bytes | None, str]:
    """Кадр для «рассматривания» и форма: photo | circle (кружочек) | video."""
    if kind == "photo":
        data = (storage.base / path).read_bytes() if path else await download(bot, file_id)
        return data, "photo"
    src = storage.base / path if path else folder / f"{file_id[-12:]}.mp4"
    if not path:
        await bot.download(file_id, destination=src, timeout=120)
    duration, w, h = await asyncio.to_thread(edits.probe, src)
    frame = await asyncio.to_thread(edits.video_sheet, src, duration)
    return frame or None, "circle" if kind == "video" and w == h and w else "video"


async def watch_media(bot: Bot) -> None:
    """Фоном «рассматривает» фотки, видео и кружочки: годится ли для эдита и что на кадре."""
    if not ai.available():
        return
    while True:
        batch = storage.unrated_media(MEDIA_BATCH)
        if not batch:
            await asyncio.sleep(120)
            continue
        images, ready, network_down = [], [], False
        with tempfile.TemporaryDirectory() as tmp:
            for media_id, kind, file_id, path in batch:
                try:
                    frame, shape = await media_frame(bot, kind, file_id, path, Path(tmp))
                except TelegramNetworkError:
                    network_down = True
                    break
                except Exception as e:  # noqa: BLE001 — битый или удалённый файл
                    log.debug("не рассмотрел media %s: %s", media_id, e)
                    frame, shape = None, ""
                if frame:
                    images.append(frame)
                    ready.append((media_id, shape))
                else:
                    storage.set_media_info(media_id, 0, "", shape)
        if network_down:
            await asyncio.sleep(60)
            continue
        if images:
            try:
                reviews = await ai.review_media(images)
            except Exception as e:  # noqa: BLE001
                log.warning("Не вышло рассмотреть кадры: %s", e)
                await asyncio.sleep(60)
                continue
            for (media_id, shape), review in zip(ready, reviews):
                score, description = review or (0, "")
                storage.set_media_info(media_id, score, description, shape)
        left = storage.unrated_count()
        if len(batch) == MEDIA_BATCH and (left < MEDIA_BATCH or left % (MEDIA_BATCH * 8) < MEDIA_BATCH):  # разбор завала
            log.info("Рассмотрел кадры для эдитов, осталось %d", left)
        await asyncio.sleep(3)


WEEKLY_DAY, WEEKLY_HOURS = 6, range(19, 22)  # воскресенье, вечер
WEEKLY_MIN_CLIPS = 10


async def watch_weekly(bot: Bot) -> None:
    """По воскресеньям вечером — эдит «итоги недели» из того, что скинули за неделю."""
    while True:
        now = time.localtime()
        if ai.EDITS_ON and now.tm_wday == WEEKLY_DAY and now.tm_hour in WEEKLY_HOURS:
            week, since = time.strftime("%G-W%V"), int(time.time()) - 7 * 86400
            for chat_id in group_chats(storage.media_chats()):
                if len(storage.edit_candidates(chat_id, since)) < WEEKLY_MIN_CLIPS:
                    continue
                if not storage.claim_weekly(chat_id, week):
                    continue
                try:
                    title = (await bot.get_chat(chat_id)).title or "беседа"
                    problem = await make_edit(bot, chat_id, title, since=since, weekly=True)
                    if problem:
                        log.info("Итоги недели для %s не собрались: %s", chat_id, problem)
                except Exception:
                    log.exception("итоги недели не собрались")
        await asyncio.sleep(1800)



# --- долгая память и инициатива ---

LORE_NIGHT_HOURS = range(4, 7)  # по ночам летопись дописывается тем, что было за день
LORE_EVERY_ROWS = 800           # или раньше, если столько набралось
LORE_MIN_ROWS = 30              # меньше — летопись заводить рано
PLANS_EVERY, PLANS_MIN_ROWS = 2 * 3600, 15  # планы — раз в пару часов, если есть что читать
INITIATIVE = os.getenv("INITIATIVE", "1") != "0"
INITIATIVE_HOURS = (11.0, 22.5)  # когда бот может писать первым
INITIATIVE_PER_DAY, INITIATIVE_GAP = 3, 90 * 60
SILENT_DAYS, SILENT_MIN_MESSAGES = 6, 15  # кого звать: писал хотя бы столько, но молчит столько дней
TALK_AFTER_INITIATIVE = 30 * 60  # столько ждём ответа того, к кому бот обратился сам


async def chat_title(bot: Bot, chat_id: int) -> str:
    try:
        return (await bot.get_chat(chat_id)).title or "беседа"
    except Exception:  # noqa: BLE001
        return "беседа"


async def update_lore(bot: Bot, chat_id: int) -> None:
    """Дочитывает переписку в летопись: выжимка из каждого куска, потом одна сборка (так не теряется старое).
    Выжимки сохраняются — если прервётся, продолжит с места."""
    state = storage.lore(chat_id)
    pending = storage.lore_digests(chat_id, after=state["upto"])  # уже выжатые, но ещё не собранные
    rows = storage.dialog_after(chat_id, pending[-1]["upto"] if pending else state["upto"])
    title, name, people = await chat_title(bot, chat_id), await bot_name(bot), storage.people(chat_id)
    chunks = ai.lore_chunks(rows) if rows else []
    for i, chunk in enumerate(chunks, 1):
        text = await ai.lore_digest(title, name, chunk, people)
        storage.add_lore_digest(chat_id, chunk[-1][0], chunk[0][3], chunk[-1][3], text)
        log.info("Летопись %s: выжимка %d/%d", chat_id, i, len(chunks))
    pending = storage.lore_digests(chat_id, after=state["upto"])
    if not pending:
        return
    periods = [(ai._period([(0, "", "", d["first_at"]), (0, "", "", d["last_at"])]), d["text"]) for d in pending]
    lore = await ai.lore_merge(title, name, state["text"], periods, people)
    storage.save_lore(chat_id, lore, pending[-1]["upto"])
    log.info("Летопись %s собрана: %d знаков", chat_id, len(lore))
    ai.refresh(chat_id)


def plan_source(rows: list[tuple[int, str, str, int]], quote: str) -> int | None:
    """Где в переписке эта фраза (номер строки) — или None, если её там нет."""
    best, best_score = None, 0.0
    for i, (_, _, text, _) in enumerate(rows):
        score = ai.similarity(quote, text) + (1.0 if ai._plain(quote) and ai._plain(quote) in ai._plain(text) else 0.0)
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= 0.6 else None


def fit_ask_time(at: int, now: float) -> int | None:
    """Когда спрашивать: днём, не раньше чем через час и не позже чем через две недели."""
    if not now + 3600 <= at <= now + 15 * 86400:
        return None
    t = time.localtime(at)
    hour, (first, last) = t.tm_hour + t.tm_min / 60, INITIATIVE_HOURS
    if hour < first:
        at += int((first - hour) * 3600) + random.randint(0, 3600)
    elif hour > last - 0.5:
        at += int((24 - hour + first) * 3600) + random.randint(0, 3600)  # завтра днём
    return at


async def update_plans(bot: Bot, chat_id: int) -> None:
    """Ищет в новых сообщениях, у кого что намечено, — чтобы потом спросить."""
    state, now = storage.lore(chat_id), time.time()
    if state["plans_upto"]:
        rows = storage.dialog_after(chat_id, state["plans_upto"], limit=800)
    else:  # первый раз — только последние пару дней: старое уже неактуально
        rows = [r for r in storage.dialog_tail(chat_id, 1500) if r[3] >= now - 2 * 86400]
    if rows:
        planned = storage.open_plans(chat_id)
        found = await ai.find_plans(await bot_name(bot), rows, storage.people(chat_id), planned)
        for at, who, about, quote in found:
            source = plan_source(rows, quote)
            if source is None:  # такой фразы в переписке нет — модель додумала: не спрашиваем
                log.info("План «%s» отброшен: не нашёл фразу «%s»", about, quote)
                continue
            who = canonical_name(chat_id, re.sub(r"\s*\(в ответ .*\)$", "", rows[source][1]))  # настоящий автор
            context = ai.format_dialog(rows[max(0, source - 3):source + 2])[:700]
            if ai.LORE_BANNED.search(f"{about} {quote}") or any(
                    p["who"] == who and ai.similar(p["about"], about) for p in planned):
                continue  # личное — или про это уже помним
            if when := fit_ask_time(at, now):
                storage.add_plan(chat_id, who, about, when, quote=rows[source][2][:300], context=context)
                planned.append({"who": who, "about": about})
                log.info("Спрошу %s про «%s» — %s", who, about, time.strftime("%d.%m %H:%M", time.localtime(when)))
    last = rows[-1][0] if rows else (storage.dialog_tail(chat_id, 1) or [(0,)])[0][0]
    storage.save_plans_upto(chat_id, last)


async def watch_memory(bot: Bot) -> None:
    """Фоном: летопись (первый раз — вся история, дальше по ночам) и планы друзей (раз в пару часов)."""
    if not ai.available():
        return
    await asyncio.sleep(20)  # пусть сначала всё запустится
    while True:
        for chat_id in group_chats(storage.dialog_chats()):
            state, now = storage.lore(chat_id), time.time()
            new = storage.dialog_count_after(chat_id, state["upto"])
            night = time.localtime(now).tm_hour in LORE_NIGHT_HOURS and now - state["updated_at"] > 12 * 3600
            fresh = storage.dialog_count_after(chat_id, state["plans_upto"])
            since = now - state["plans_at"]
            ai.set_state(chat_id, state_text(chat_id))  # настроение со временем проходит, обиды остывают
            try:
                if storage.feelings(chat_id) is None and storage.dialog_count_after(chat_id, 0) >= LORE_MIN_ROWS:
                    await update_feelings(bot, chat_id, first=True)
                if (not state["text"] and new >= LORE_MIN_ROWS) or (state["text"] and new and
                                                                     (night or new >= LORE_EVERY_ROWS)):
                    await update_lore(bot, chat_id)
                if not state["plans_at"] or (fresh >= PLANS_MIN_ROWS and since >= PLANS_EVERY) or \
                        (fresh and since >= 12 * 3600):
                    await update_plans(bot, chat_id)
            except Exception as e:  # noqa: BLE001 — не вышло сейчас, выйдет в следующий раз
                log.warning("Память для %s не обновилась: %s", chat_id, e)
        try:
            await update_life(bot)
        except Exception as e:  # noqa: BLE001
            log.warning("Жизнь бота не сдвинулась: %s", e)
        await asyncio.sleep(600)


def human_ago(then: float, now: float) -> str:
    days = (time.localtime(now).tm_yday - time.localtime(then).tm_yday) % 366
    return {0: "сегодня", 1: "вчера", 2: "позавчера"}.get(days, f"{days} дн. назад")


def user_for_name(chat_id: int, who: str) -> int | None:
    """user_id участника по имени или нику (из плана вида «Катя»)."""
    key = who.casefold().strip()
    first = key.split()[0] if key else ""
    for m in storage.members(chat_id):
        names = {m["nick"].casefold()}
        if name := real_name(chat_id, m["nick"]):
            names.add(name.casefold())
        if key in names or any(n.split()[0] == first for n in names if n.strip()):
            return m["user_id"]
    return None


async def say_first(bot: Bot, chat_id: int, task: str, target: int | None = None) -> bool:
    """Бот сам пишет в чат. target — от кого ждём ответа (ответит без тега — бот поймёт, что это ему)."""
    name = await bot_name(bot)
    reply = await ai.initiative(chat_id, name, await chat_title(bot, chat_id), examples_loader(chat_id), task,
                                storage.dialog_tail(chat_id, 60))
    if not reply:
        return False
    voiced = False
    if reply.voice and speech.tts_available():
        try:
            ogg = await speech.synthesize(" ".join(reply.lines), reply.voice_style
                                          if speech.voice_style(reply.voice_style) else None)
            await bot.send_voice(chat_id, BufferedInputFile(ogg, "voice.ogg"))
            voiced = True
        except Exception as e:  # noqa: BLE001 — не озвучилось — напишем текстом
            log.warning("Не озвучил: %s", e)
    for line in [] if voiced else reply.lines:
        await bot.send_chat_action(chat_id, "typing")
        await asyncio.sleep(random.uniform(1.0, 2.5))
        await bot.send_message(chat_id, line)
    for line in reply.lines:
        ai.remember(chat_id, name, ("[голосовое] " if voiced else "") + line)
    if reply.sticker_file:
        await bot.send_sticker(chat_id, reply.sticker_file)
    if target:
        _talks[chat_id] = _Talk(target, time.monotonic() + TALK_AFTER_INITIATIVE)
    log.info("Написал первым в %s: %s", chat_id, " / ".join(reply.lines))
    return True


async def initiative_tick(bot: Bot, chat_id: int, now: float | None = None) -> bool:
    """Если есть повод и бот не надоел — пишет первым: про планы друзей, зовёт пропавших, рассказывает о своей жизни."""
    now = now or time.time()
    recent = storage.initiatives_since(chat_id, int(now - 86400))
    if len(recent) >= INITIATIVE_PER_DAY or (recent and now - recent[0] < INITIATIVE_GAP):
        return False
    for step in (ask_about_plan, call_silent, share_life):
        sent = await step(bot, chat_id, now)
        if sent is not None:  # попробовал (написал или Claude решил промолчать) — на этот раз хватит
            return sent
    return False


async def ask_about_plan(bot: Bot, chat_id: int, now: float) -> bool | None:
    for plan in storage.open_plans(chat_id):
        if plan["ask_at"] > now:
            return None
        if now - plan["ask_at"] > 2 * 86400:  # пропустили (бот лежал) — уже не в тему
            storage.set_plan_status(plan["id"], "skipped")
            continue
        if not plan["quote"]:  # старый план без исходной фразы — не знаем, всерьёз ли это было
            storage.set_plan_status(plan["id"], "skipped")
            continue
        task = ai.TASK_FOLLOWUP.format(who=plan["who"], quote=plan["quote"], context=plan["context"],
                                       when=human_ago(plan["created_at"], now).capitalize())
        sent = await say_first(bot, chat_id, task, user_for_name(chat_id, plan["who"]))
        storage.set_plan_status(plan["id"], "asked" if sent else "skipped")
        if sent:
            storage.log_initiative(chat_id, f"plan:{plan['id']}")
        return sent
    return None


async def call_silent(bot: Bot, chat_id: int, now: float) -> bool | None:
    """Пропавшие: писали, но давно молчат, а чат живой. Не чаще раза в день и одного и того же — раз в две недели."""
    tail = storage.dialog_tail(chat_id, 1)
    if not tail or now - tail[0][3] > 86400 or storage.initiatives_since(chat_id, int(now - 86400), "silent:%"):
        return None
    for m in storage.members(chat_id):
        days = int((now - m["last_seen"]) / 86400)
        if m["messages"] < SILENT_MIN_MESSAGES or days < SILENT_DAYS or \
                storage.initiatives_since(chat_id, int(now - 14 * 86400), f"silent:{m['user_id']}"):
            continue
        storage.log_initiative(chat_id, f"silent:{m['user_id']}")  # даже если Claude решит промолчать
        who = real_name(chat_id, m["nick"]) or m["nick"]
        return await say_first(bot, chat_id, ai.TASK_SILENT.format(who=who, days=days), m["user_id"])
    return None


_life_tries: dict[tuple[int, int], int] = {}


async def share_life(bot: Bot, chat_id: int, now: float) -> bool | None:
    """Свежая новость из жизни бота — рассказать, когда в живом чате затишье (до трёх попыток)."""
    episodes = storage.life(1, until=now)  # только то, что уже случилось
    if not episodes:
        return None
    episode = episodes[-1]
    key, tail = (chat_id, episode["id"]), storage.dialog_tail(chat_id, 1)
    if now - episode["at"] > 36 * 3600 or _life_tries.get(key, 0) >= 3 or \
            storage.initiatives_since(chat_id, 0, f"life:{episode['id']}"):
        return None
    if not tail or not 20 * 60 <= now - tail[0][3] <= 12 * 3600:  # чат живой, но сейчас затишье
        return None
    _life_tries[key] = _life_tries.get(key, 0) + 1
    sent = await say_first(bot, chat_id, ai.TASK_LIFE.format(event=episode["text"]))
    if sent:
        storage.log_initiative(chat_id, f"life:{episode['id']}")
    return sent


async def watch_initiative(bot: Bot) -> None:
    """Раз в 5–10 минут: не пора ли написать первым (только днём)."""
    if not INITIATIVE or not ai.available():
        return
    while True:
        await asyncio.sleep(random.uniform(300, 600))
        t = time.localtime()
        if not INITIATIVE_HOURS[0] <= t.tm_hour + t.tm_min / 60 <= INITIATIVE_HOURS[1]:
            continue
        for chat_id in group_chats(storage.dialog_chats()):
            try:
                await initiative_tick(bot, chat_id)
            except Exception as e:  # noqa: BLE001
                log.warning("Не написал первым в %s: %s", chat_id, e)



# --- характер ---

FEEL_EVERY, FEEL_GAP = 25, 15 * 60  # раз в столько сообщений (но не чаще) бот прислушивается к себе
MOOD_TTL = 6 * 3600                 # настроение проходит само
RELATION_FADE_DAYS = 3              # отношения остывают на 1 за столько дней
LIFE_START = 7  # с этого часа бот расписывает свой день; что раньше — ещё «вчера» (после полуночи)
RELATION_WORDS = {5: "любимчик", 4: "любимчик", 3: "кореш", 2: "тепло", 1: "чуть теплее обычного",
                  -1: "слегка бесит", -2: "бесит", -3: "соперник", -4: "вечный бэф", -5: "вечный бэф"}
_feel_count: dict[int, int] = {}
_feel_at: dict[int, float] = {}


def canonical_name(chat_id: int, raw: str) -> str:
    """Имя человека, как его зовут в «кто есть кто» (Claude может написать ник или «Shadow (Дима)»)."""
    raw = raw.strip()
    if m := re.match(r"(.+?)\s*\((.+)\)$", raw):
        raw = m.group(2).strip()
    for nick, name in storage.people(chat_id):
        if raw.casefold() in (nick.casefold(), name.casefold()):
            return name
    return raw


def relation_score(rel: dict, now: float) -> int:
    fade = int((now - rel["updated_at"]) / (RELATION_FADE_DAYS * 86400))
    return max(0, rel["score"] - fade) if rel["score"] > 0 else min(0, rel["score"] + fade)


def state_text(chat_id: int) -> str:
    """Настроение, где бот по жизни и отношения словами — для модели и для /mood."""
    now, feelings = time.time(), storage.feelings(chat_id)
    fresh = feelings and now - feelings["at"] < MOOD_TTL
    mood = (f"{feelings['mood']} — {feelings['why']}" if feelings["why"] else feelings["mood"]) if fresh else "обычное"
    lines = [f"Настроение: {mood}"]
    if activity := current_activity(now):
        lines.append(f"По жизни сейчас: {activity}")
    rels = [(r["name"], relation_score(r, now), r["note"]) for r in storage.relations(chat_id)]
    rels = sorted((r for r in rels if r[1]), key=lambda r: -r[1])
    if rels:
        lines.append("Отношения:")
        for i, (name, score, note) in enumerate(rels):
            word = RELATION_WORDS[score] if score < 4 or i == 0 else "кореш"  # любимчик — только один
            lines.append(f"- {name}: {word}" + (f" ({note})" if note else ""))
    return "\n".join(lines)


def note_for_feelings(bot: Bot, chat_id: int) -> None:
    """Каждые FEEL_EVERY сообщений (но не чаще FEEL_GAP) — прислушаться к себе в фоне."""
    _feel_count[chat_id] = _feel_count.get(chat_id, 0) + 1
    if _feel_count[chat_id] >= FEEL_EVERY and time.monotonic() - _feel_at.get(chat_id, -FEEL_GAP) >= FEEL_GAP \
            and ai.available():
        _feel_count[chat_id], _feel_at[chat_id] = 0, time.monotonic()
        run_in_background(update_feelings(bot, chat_id))


async def update_feelings(bot: Bot, chat_id: int, first: bool = False) -> None:
    """Как последние сообщения повлияли на настроение бота и отношения. first — первое знакомство по всей переписке."""
    lines = ([f"{a}: {t}" for _, a, t, _ in storage.dialog_tail(chat_id, 300)] if first
             else ai.history_lines(chat_id))
    if len(lines) < 5:
        return
    try:
        mood, changes = await ai.feel(await bot_name(bot), lines, state_text(chat_id), storage.people(chat_id), first)
    except Exception as e:  # noqa: BLE001
        log.warning("Не прислушался к себе в %s: %s", chat_id, e)
        return
    if mood or first:
        storage.set_feelings(chat_id, *(mood or ("обычное", "")))
    now, known = time.time(), {r["name"].casefold(): r for r in storage.relations(chat_id)}
    for raw, delta, note in changes:
        if not delta:
            continue
        name = canonical_name(chat_id, raw)
        old = known.get(name.casefold())
        score = max(-5, min(5, (relation_score(old, now) if old else 0) + delta))
        storage.set_relation(chat_id, old["name"] if old else name, score, note[:90])  # только последнее
    ai.set_state(chat_id, state_text(chat_id))
    log.info("Характер в %s: %s", chat_id, state_text(chat_id).replace("\n", " · "))


def day_key(now: float) -> str:
    """Какой это «день» бота: до LIFE_START утра — ещё вчерашний."""
    return time.strftime("%Y-%m-%d", time.localtime(now - LIFE_START * 3600))


def at_for(day: str, hhmm: str) -> int:
    """Время пункта распорядка: «01:00» дня D — это уже ночь после D."""
    hour, minute = map(int, hhmm.split(":"))
    at = time.mktime(time.strptime(f"{day} {hour % 24:02d}:{minute:02d}", "%Y-%m-%d %H:%M"))
    return int(at + (86400 if hour < LIFE_START else 0))


def current_activity(now: float | None = None) -> str:
    """Где бот сейчас по жизни — последний наступивший пункт распорядка (сегодня или вчера)."""
    now = now or time.time()
    for day in (day_key(now), day_key(now - 86400)):
        passed = sorted((at_for(day, hhmm), what) for hhmm, what in storage.life_day(day) if at_for(day, hhmm) <= now)
        if passed:
            return passed[-1][1]
    return ""


def refresh_life(now: float | None = None) -> None:
    """Что уже случилось в жизни бота — в закэшированную часть промпта (пересобирается, только если поменялось)."""
    episodes = storage.life(3, until=now)
    text = "Недавно:\n" + "\n".join(
        f"{time.strftime('%d.%m %H:%M', time.localtime(e['at']))} — {e['text']}" for e in episodes) if episodes else ""
    if text != ai.LIFE:
        ai.LIFE = text
        for chat_id in group_chats(storage.dialog_chats()):
            ai.refresh(chat_id)


def life_talk(bot_first_name: str, now: float) -> list[str]:
    """Что недавно говорили в чатах ему и он сам: советы друзей про его жизнь и его рассказы о себе."""
    called, lines = name_pattern(bot_first_name), []
    for chat_id in group_chats(storage.dialog_chats()):
        rows = [r for r in storage.dialog_tail(chat_id, 400) if r[3] >= now - 36 * 3600 and called.search(r[2])]
        lines += [f"{author}: {text[:200]}" for _, author, text, _ in rows[-8:]]
        lines += [line[:200] for line in ai.history_lines(chat_id) if line.startswith(f"{bot_first_name}:")][-6:]
    return lines[-20:]


async def update_life(bot: Bot, now: float | None = None) -> None:
    """Каждое утро — серия дня: распорядок (где он в течение дня) и 1–2 события в конкретное время."""
    now = now or time.time()
    refresh_life(now)  # события, время которых наступило, — в память
    day = day_key(now)
    if storage.life_day(day):
        return
    name = await bot_name(bot)
    events, slots = await ai.life_day(name, storage.life(8, until=now), life_talk(name, now))
    if not slots:
        log.warning("Не расписал свой день — попробую позже")
        return
    storage.set_life_day(day, slots)
    for hhmm, text in events:
        storage.add_life(text, at=at_for(day, hhmm))
    log.info("День бота: %s · события: %s", " / ".join(f"{t} {w}" for t, w in slots),
             " / ".join(f"{t} {e}" for t, e in events) or "—")
    refresh_life(now)


# --- лица ---

def face_gallery(chat_id: int) -> list:
    return [(name, faces.from_bytes(feature)) for name, feature in storage.face_gallery(chat_id)]


def recognize(chat_id: int, image: bytes, gallery: list | None = None) -> tuple[list[str], str]:
    """Кого из записавшихся видно на картинке: ([имена], «Дима (слева), Катя (справа)»). Пусто — никого."""
    gallery = face_gallery(chat_id) if gallery is None else gallery
    if not faces.available() or not gallery:
        return [], ""
    try:
        found = faces.find(image)
    except Exception as e:  # noqa: BLE001 — битая картинка
        log.debug("не нашёл лица: %s", e)
        return [], ""
    matched = faces.identify(found, gallery)
    return [name for name, _ in matched], faces.describe(matched, len(found))


def who_is_on(chat_id: int, image: bytes) -> str:
    return recognize(chat_id, image)[1]


async def tag_faces(msg: Message, file_id: str, unique_id: str) -> None:
    """Прислали фото — кто на нём из записавшихся: в историю для Claude и в базу (для эдитов «про Диму»)."""
    try:
        image = await download(msg.bot, file_id)
    except Exception as e:  # noqa: BLE001
        log.debug("не скачал фото для лиц: %s", e)
        return
    names, who = await asyncio.to_thread(recognize, msg.chat.id, image)
    storage.set_media_people_by_uid(msg.chat.id, unique_id, ", ".join(names))
    if who:
        ai.annotate(msg.chat.id, msg.message_id, who)
        log.info("На фото в %s узнал: %s", msg.chat.id, who)


async def watch_faces(bot: Bot) -> None:
    """Фоном: на каких старых фото и видео есть записавшиеся (пересматривает, когда кто-то записался)."""
    if not faces.available():
        return
    while True:
        busy = False
        for chat_id in group_chats(storage.media_chats()):
            if not (gallery := face_gallery(chat_id)) or not (rows := storage.media_for_faces(chat_id, 20)):
                continue
            busy, seen = True, 0
            with tempfile.TemporaryDirectory() as tmp:
                for media_id, kind, file_id, path in rows:
                    try:
                        frame, _ = await media_frame(bot, kind, file_id, path, Path(tmp))
                    except TelegramNetworkError:
                        break  # сеть легла — продолжим потом
                    except Exception as e:  # noqa: BLE001 — файл пропал или битый
                        log.debug("не посмотрел лица в media %s: %s", media_id, e)
                        frame = None
                    names = (await asyncio.to_thread(recognize, chat_id, frame, gallery))[0] if frame else []
                    storage.set_media_people(media_id, ", ".join(names))
                    seen += bool(names)
            log.info("Лица в %s: пересмотрел %d фото/видео, узнал людей на %d", chat_id, len(rows), seen)
        await asyncio.sleep(3 if busy else 120)



async def fill_library() -> None:
    """Свежая установка: докачать стартовые фонки (yt-dlp) и включить эдиты."""
    added = await LIB.ensure_starter()
    ai.EDITS_ON = edits.available() and bool(LIB.tracks())
    log.info("Эдиты: скачал %d треков, %s", added, "включены" if ai.EDITS_ON else "так и не включились")
    for chat_id in group_chats(storage.dialog_chats()):
        ai.refresh(chat_id)  # правило про [эдит] — в промпт


if __name__ == "__main__":
    asyncio.run(main())
