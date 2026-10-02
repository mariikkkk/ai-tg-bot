"""Прогон бота на фейковом Telegram: python smoke_test.py

Подменяет сетевую сессию aiogram, скармливает диспетчеру апдейты и проверяет,
что бот отвечает нужными методами. Настоящий токен не нужен.

    SMOKE_AI=1 python smoke_test.py   # плюс один настоящий ответ от Claude Code (тратит подписку)
"""

import asyncio
import os
import tempfile
from datetime import datetime
from pathlib import Path

os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DEFAULT_CHANCE"] = "0"
# Тесты не ходят в Яндекс: без ключей картинки, поиск и голос выключены (где нужно — подменяем).
os.environ["YANDEX_SEARCH_API_KEY"] = os.environ["YANDEX_SPEECH_API_KEY"] = ""
os.environ["OWNER_ID"] = ""  # ограничение «где можно» проверяется отдельно, в своём тесте
WITH_AI = os.getenv("SMOKE_AI") == "1"
os.environ["AI"] = "1" if WITH_AI else "0"

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.methods import (  # noqa: E402
    GetChatMember, GetFile, GetMe, GetStickerSet, SendAnimation, SendChatAction, SendMessage, SendPhoto,
    SendSticker, SendVideo, SendVoice, SetMessageReaction,
)
from aiogram.types import (  # noqa: E402
    Chat, ChatMemberMember, File, Message, MessageOriginUser, PhotoSize, Sticker, StickerSet, Update, User, Video,
    Voice,
)

import bot as memebot  # noqa: E402
import preview  # noqa: E402

ME = User(id=42, is_bot=True, first_name="Мемобот", username="memobot", can_read_all_group_messages=True)
VASYA = User(id=1, is_bot=False, first_name="Вася")
GROUP = Chat(id=-100500, type="supergroup", title="Беседа")
OUT = Path(__file__).parent / "preview"


class FakeTelegram(BaseSession):
    def __init__(self, files: dict[str, bytes]):
        super().__init__()
        self.files = files
        self.sent: list = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, GetMe):
            return ME
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u", file_path=method.file_id)
        if isinstance(method, SendChatAction):
            return True
        if isinstance(method, GetChatMember):
            return ChatMemberMember(user=VASYA)
        if isinstance(method, GetStickerSet):
            return StickerSet(name=method.name, title="пак", sticker_type="regular", stickers=[
                sticker(f"{method.name}-{i}", emoji) for i, emoji in enumerate(["😭", "🤡", "😭"])])
        if isinstance(method, SetMessageReaction):
            self.sent.append(method)
            return True
        if isinstance(method, (SendMessage, SendPhoto, SendVideo, SendAnimation, SendSticker, SendVoice)):
            self.sent.append(method)
            n = len(self.sent)
            extra = {}
            if isinstance(method, SendPhoto):  # как настоящий Telegram: у отправленного фото есть file_id
                self.files[f"sent{n}"] = method.photo.data
                extra["photo"] = [PhotoSize(file_id=f"sent{n}", file_unique_id=f"sent-uid{n}", width=600, height=600)]
            return Message(message_id=10_000 + n, date=datetime.now(), chat=GROUP, from_user=ME, **extra)
        if type(method).__name__ in ("EditMessageText", "AnswerCallbackQuery", "LeaveChat"):
            self.sent.append(method)
            return True
        raise NotImplementedError(type(method).__name__)

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield self.files[url.rsplit("/", 1)[-1]]


_next_id = 0


def sticker(uid: str, emoji: str, set_name: str | None = None) -> Sticker:
    return Sticker(file_id=f"file-{uid}", file_unique_id=uid, type="regular", width=512, height=512,
                   is_animated=False, is_video=False, emoji=emoji, set_name=set_name)

# Мини-экспорт в разметке Telegram Desktop: текст, «склеенное» сообщение, фото, сервисное,
# ответ, сообщение через инлайн-бота и нескачанное видео.
FAKE_EXPORT = """<!DOCTYPE html><html><body><div class="page_wrap">
<div class="page_header"><div class="content"><div class="text bold">
Дача  777
</div></div></div>
<div class="page_body chat_page"><div class="history">
<div class="message service" id="message-1"><div class="body details">Вася создал группу</div></div>
<div class="message default clearfix" id="message1"><div class="pull_left userpic_wrap"></div><div class="body">
 <div class="pull_right date details" title="15.05.2026 23:45:54 UTC+03:00">23:45</div>
 <div class="from_name">Вася</div>
 <div class="text">Шашлыки или как</div></div></div>
<div class="message default clearfix joined" id="message2"><div class="body">
 <div class="pull_right date details" title="15.05.2026 23:46:00 UTC+03:00">23:46</div>
 <div class="reply_to details">In reply to <a href="#go_to_message1">this message</a></div>
 <div class="text">кто <strong>с нами</strong><br>пишите &amp; не тупите</div></div></div>
<div class="message default clearfix" id="message3"><div class="body">
 <div class="pull_right date details" title="16.05.2026 10:00:00 UTC+03:00">10:00</div>
 <div class="from_name">Петя</div>
 <div class="media_wrap clearfix"><a class="photo_wrap clearfix pull_left" href="photos/photo_1.jpg">
  <img class="photo" src="photos/photo_1_thumb.jpg"/></a></div>
 <div class="text">смотрите что нашёл</div></div></div>
<div class="message default clearfix" id="message4"><div class="body">
 <div class="pull_right date details" title="16.05.2026 10:01:00 UTC+03:00">10:01</div>
 <div class="from_name">Петя <span class="details">via @gif</span></div>
 <div class="text">гифка от бота</div></div></div>
<div class="message default clearfix" id="message5"><div class="body">
 <div class="pull_right date details" title="16.05.2026 10:02:00 UTC+03:00">10:02</div>
 <div class="from_name">Вася</div>
 <div class="media_wrap clearfix"><div class="media clearfix pull_left media_video">
  <div class="fill pull_left"></div><div class="body"><div class="title bold">Video message</div>
  <div class="description">Not included, change data exporting settings to download.</div></div></div></div>
</div></div>
</div></div></div></body></html>
"""


def update(chat=GROUP, **fields) -> Update:
    global _next_id
    _next_id += 1
    msg = Message(message_id=_next_id, date=datetime.now(), chat=chat, from_user=VASYA, **fields)
    return Update(update_id=_next_id, message=msg)


def check_startup_order() -> None:
    """Запуск бота должен стоять в самом конце bot.py: всё, что ниже него, к старту ещё не определено."""
    import ast

    body = ast.parse(Path(memebot.__file__).read_text(encoding="utf-8")).body
    last = body[-1]
    assert isinstance(last, ast.If) and "__main__" in ast.unparse(last.test), "в конце bot.py должен быть запуск"
    assert sum(isinstance(n, ast.If) and "__main__" in ast.unparse(n.test) for n in body) == 1


def check_no_duplicate_names() -> None:
    """Ни в одном модуле имя на верхнем уровне не определено дважды: второе молча затирает первое."""
    import ast
    from collections import Counter

    for path in Path(memebot.__file__).parent.glob("*.py"):
        names = []
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
            elif isinstance(node, ast.Assign):
                names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        dups = [name for name, n in Counter(names).items() if n > 1]
        assert not dups, f"{path.name}: определено дважды: {dups}"


async def main():
    check_startup_order()
    check_no_duplicate_names()
    session = FakeTelegram({"photo1": preview.sample_image(), "video1": preview.sample_video(), "voice1": b"ogg"})
    bot = Bot("123:TEST", session=session)
    dp = Dispatcher()
    dp.include_router(memebot.router)

    async def feed(expect=None, **fields):
        before = len(session.sent)
        await dp.feed_update(bot, update(**fields))
        new = session.sent[before:]
        if expect is not None:
            got = [type(m).__name__ for m in new]
            assert got == expect, f"{fields.get('text') or fields.get('caption')!r}: ждал {expect}, получил {got}"
        return new

    # Бот учится молча (шанс 0%).
    for line in preview.SAMPLE_CHAT:
        await feed([], text=line)
    photo = update(photo=[PhotoSize(file_id="photo1", file_unique_id="p1", width=800, height=600)])
    await dp.feed_update(bot, photo)
    await feed([], video=Video(file_id="video1", file_unique_id="v1", width=640, height=360, duration=3, file_size=1000))
    # Дубликат не должен добавиться второй раз.
    await feed([], photo=[PhotoSize(file_id="photo1", file_unique_id="p1", width=800, height=600)])

    stats = (await feed(["SendMessage"], text="/stats"))[0].text
    assert "фоток: 1" in stats and "видео: 1" in stats and f"фраз: {len(preview.SAMPLE_CHAT)}" in stats, stats
    print("✓ /stats:", stats.replace("\n", " | "))

    sent = await feed(["SendPhoto"], text="/meme")
    (OUT / "bot_meme.jpg").write_bytes(sent[0].photo.data)
    sent = await feed(["SendPhoto"], text="/dem@memobot Когда бот заработал | с первого раза")
    (OUT / "bot_dem.jpg").write_bytes(sent[0].photo.data)
    print("✓ /meme и /dem прислали картинки")

    await feed(["SendPhoto"], text="/meme верх | низ", reply_to_message=photo.message)
    print("✓ /meme ответом на фото")

    sent = await feed(["SendVideo"], text="/vid")
    (OUT / "bot_vid.mp4").write_bytes(sent[0].video.data)
    print("✓ /vid прислал видео")

    phrase = (await feed(["SendMessage"], text="/gen шаурму"))[0].text
    print("✓ /gen шаурму →", phrase)

    bot_msg = Message(message_id=10_001, date=datetime.now(), chat=GROUP, from_user=ME, text="мем")
    got = await feed(text="ты кто вообще", reply_to_message=bot_msg)
    assert len(got) == 1, got
    print("✓ ответ на реплай боту:", type(got[0]).__name__)

    assert "только админ" in (await feed(["SendMessage"], text="/chance 100"))[0].text
    print("✓ /chance от не-админа отклонён")
    assert "админ" in (await feed(["SendMessage"], text="/forget да"))[0].text

    # Спонтанный мем: 100% шанс, кулдаун работает.
    memebot.storage.set_chance(GROUP.id, 1.0)
    assert len(await feed(text="ну что там")) == 1
    await feed([], text="а ещё?")
    print("✓ спонтанный мем при 100% шансе, второй придушен кулдауном")

    private = Chat(id=1, type="private")
    assert "Ок, теперь 5%" in (await feed(["SendMessage"], chat=private, text="/chance 5"))[0].text
    await feed(["SendPhoto"], chat=private, photo=[PhotoSize(file_id="photo1", file_unique_id="p1", width=8, height=6)])
    print("✓ в личке: /chance работает, фото → мем из него")

    assert "Всё забыл" in (await feed(["SendMessage"], chat=private, text="/forget да"))[0].text
    assert memebot.storage.stats(private.id) == {"phrases": 0}
    print("✓ /forget стирает память")

    # Импорт HTML-экспорта и авто-привязка по названию беседы.
    export = Path(tempfile.mkdtemp())
    (export / "photos").mkdir()
    (export / "photos" / "photo_1.jpg").write_bytes(preview.sample_image())
    (export / "messages.html").write_text(FAKE_EXPORT, encoding="utf-8")
    import import_history  # noqa: PLC0415
    s = import_history.import_export(memebot.storage, export)
    assert (s["phrases"], s["photo"], s["missing"], s["attached"]) == (3, 1, 1, False), s
    again = import_history.import_export(memebot.storage, export)
    assert again["skipped"] == 5 and again["phrases"] == 0, again
    bus = Chat(id=-100667, type="supergroup", title="Дача 777")
    got = await feed(["SendMessage"], chat=bus, text="всем привет")
    assert "вспомнил" in got[0].text, got[0].text
    stats = memebot.storage.stats(bus.id)
    assert stats == {"photo": 1, "phrases": 4}, stats  # 3 из экспорта + «всем привет»
    assert "Шашлыки или как" in memebot.storage.phrases(bus.id)
    await feed(["SendPhoto"], chat=bus, text="/meme")  # мем из локального файла
    await feed([], chat=bus, text="ещё сообщение")  # второй раз не привязывает
    print("✓ импорт HTML-экспорта: повтор без дублей, привязка по названию, мем из импортированной фотки")

    # Claude просит картинку отдельной строкой или в конце реплики.
    import ai  # noqa: PLC0415
    ai.PICTURES = True
    r = ai._tidy("ща покажу\n[картинка: кот в шапке]", "Мемобот", no_periods=True)
    assert (r.lines, r.picture) == (["ща покажу"], "кот в шапке"), r
    r = ai._tidy("Мемобот: лол [Картинка: грустный Саня]", "Мемобот", no_periods=True)
    assert (r.lines, r.picture) == (["лол"], "грустный Саня"), r
    ai.PICTURES = False
    assert ai._tidy("[картинка: кот]", "Мемобот", True).picture is None  # без ключа — никаких картинок
    r = ai._tidy("[скинул мем: КАНЬЕ ЗНАЕТ / кот в панике]", "Мемобот", True)
    assert r.empty, r  # пометки из истории не утекают в чат
    r = ai._tidy("ахахах\n[реакция: ❤️]\n[стикер: 😭]", "Мемобот", True)
    assert (r.lines, r.reaction, r.sticker) == (["ахахах"], "❤", "😭"), r
    assert ai._tidy("[реакция: 🦖]", "Мемобот", True).empty  # такую реакцию Telegram не даст
    print("✓ пометки [картинка/стикер/реакция] разбираются, [скинул мем: …] вырезается")

    # Стикеры: запоминаются, пак докачивается целиком, бот шлёт по эмодзи.
    await feed([], sticker=sticker("s1", "😭", set_name="pack1"))
    await asyncio.sleep(0.1)  # пак качается в фоне
    assert memebot.storage.sticker_emojis(GROUP.id)[0] == "😭"
    assert memebot.storage.random_sticker(GROUP.id, "🤡") == "file-pack1-1"

    # Бот «видит» стикеры: рассмотренные попадают в меню, и Claude выбирает стикер по номеру.
    memebot.storage.set_sticker_description("pack1-1", "клоун ржёт до слёз")
    assert memebot.storage.sticker_menu(GROUP.id) == [("file-pack1-1", "🤡", "клоун ржёт до слёз")]
    real_ask, real_avail = ai._ask, ai.available

    async def fake_ask(system, prompt, image=None, model=None):
        assert "1 — 🤡 — клоун ржёт до слёз" in system  # меню стикеров в закэшированной части
        return {"result": "лол\n[стикер: 1]", "usage": {}}
    ai._ask, ai.available = fake_ask, (lambda: True)
    r = await ai.say(GROUP.id, "Мемобот", "Беседа", memebot.examples_loader(GROUP.id), "Вася")
    assert (r.lines, r.sticker_file) == (["лол"], "file-pack1-1"), r
    ai._ask, ai.available = real_ask, real_avail
    ai.refresh(GROUP.id)
    assert "[стикер 🤡: клоун ржёт до слёз]" == memebot.sticker_label(sticker("pack1-1", "🤡"))
    print("✓ рассмотренные стикеры: подписи в истории, меню для Claude, выбор по номеру")

    # Ответ Claude с реакцией и стикером доходит до Telegram (Claude подменён заглушкой).
    real_available, real_say, real_pick = ai.available, ai.say, ai.pick_reactions
    ai.available = lambda: True
    memebot.AI_REPLY_SHARE = 1.0  # без случайных «мемов вместо ответа» — тесту нужна определённость

    async def fake_say(*_args, **_kwargs):
        return ai.Reply(lines=["ну ты даёшь"], reaction="🤡", sticker="😭")
    ai.say = fake_say
    got = await feed(text="@memobot ты кто", reply_to_message=None)
    assert [type(m).__name__ for m in got] == ["SetMessageReaction", "SendMessage", "SendSticker"], got
    assert got[0].reaction[0].emoji == "🤡" and got[2].sticker.startswith("file-")

    # Обращение по имени без тега и продолжение разговора без обращения.
    async def no_reactions(*_args):
        return []
    ai.pick_reactions = no_reactions
    calls = []

    async def fake_say2(*_args, maybe=False, **_kwargs):
        calls.append(maybe)
        if maybe and len(calls) > 2:
            return None  # Claude решил, что это не ему («-»)
        return ai.Reply(lines=["чего надо"])
    ai.say = fake_say2
    assert "SendMessage" in [type(m).__name__ for m in await feed(text="Мемоботу привет, ты тут?")]
    assert "SendMessage" in [type(m).__name__ for m in await feed(text="а ты что думаешь")]  # продолжение
    assert await feed(text="пацаны го в доту") == []  # Claude промолчал…
    assert await feed(text="кто со мной") == []       # …и разговор закончился — даже не спрашиваем
    assert calls == [False, True, True], calls
    # Без прямого обращения — не больше FOLLOWUP_MAX ответов подряд, а то надоедает.
    calls.clear()

    async def chatty(*_args, maybe=False, **_kwargs):
        calls.append(maybe)
        return ai.Reply(lines=[f"ответ {len(calls)}"])
    ai.say = chatty
    await feed(text="Мемобот, поговорим?")
    for i in range(memebot.FOLLOWUP_MAX + 2):
        await feed(text=f"и ещё {i}")
    assert calls == [False] + [True] * memebot.FOLLOWUP_MAX, calls
    memebot._talks.clear()
    print("✓ отвечает, когда зовут по имени без тега, и на продолжение разговора (но не бесконечно); молчит, когда не ему")

    # Не заедает: свои недавние реплики и заезженные слова — в подсказке; повтор — переспросить, не вышло — промолчать.
    rc = Chat(id=-100444, type="supergroup", title="Повторы")
    for line in ("лаваш опять порвался", "кринж кринж братан", "ну кринж же", "кринж навсегда", "ладно, понял тебя"):
        ai.remember(rc.id, "Мемобот", line)
    ai.remember(rc.id, "Вася", "бот как дела")
    assert ai.overused_words(ai.own_lines(rc.id, "Мемобот")) == ["кринж"]
    assert ai.repeats("Лаваш опять порвался!!", ai.own_lines(rc.id, "Мемобот")) and not ai.repeats("лол", ["ахах"])
    drafts = iter(["лаваш опять порвался", "норм, на смене", "лаваш опять порвался", "лаваш опять порвался"])
    prompts = []

    async def fake_repeat_ask(system, prompt, image=None, model=None, **_):
        prompts.append(prompt)
        return {"result": next(drafts), "usage": {}}

    real_say_ask, real_say_avail = ai._ask, ai.available
    ai._ask, ai.available = fake_repeat_ask, (lambda: True)
    r = await real_say(rc.id, "Мемобот", "Повторы", memebot.examples_loader(rc.id), "Вася")
    assert r.lines == ["норм, на смене"] and len(prompts) == 2, (r, prompts)
    assert "заладил: кринж" in prompts[0] and "повторяет то, что ты уже говорил" in prompts[1]
    assert await real_say(rc.id, "Мемобот", "Повторы", memebot.examples_loader(rc.id), "Вася") is None  # дважды то же
    ai._ask, ai.available = real_say_ask, real_say_avail
    assert "частые слова" not in ai.style_of(["кринж", "кринж брат", "кринж"] * 20).rules
    print("✓ не заедает: заезженные слова в подсказке, повтор — переспрашивает, снова повтор — молчит")

    # Голосовые: расшифровка (SpeechKit подменён), обращение голосом, /text.
    import speech  # noqa: PLC0415
    real_speech = speech.available, speech.transcribe
    heard = []

    async def fake_transcribe(data):
        heard.append(data)
        return "мемобот ты вообще слышишь голосовые"
    speech.available, speech.transcribe = (lambda: True), fake_transcribe
    memebot._talks.clear()
    voice = update(voice=Voice(file_id="voice1", file_unique_id="vo1", duration=4))
    await dp.feed_update(bot, voice)
    assert heard == [b"ogg"] and "слышишь голосовые" in memebot.storage.phrases(GROUP.id)[-1]
    assert "SendMessage" in [type(m).__name__ for m in session.sent[-3:]]  # позвали голосом — ответил
    got = await feed(["SendMessage"], text="/text", reply_to_message=voice.message)
    assert got[0].text == "🗣 мемобот ты вообще слышишь голосовые" and len(heard) == 1  # второй раз не платим
    speech.available, speech.transcribe = real_speech
    print("✓ голосовые: расшифровываются, бот учится на них и отвечает, если позвали голосом; /text")

    # Без рекурсии: свои мемы — пересланные или пересохранённые — бот в мемы не берёт.
    recur = Chat(id=-100777, type="supergroup", title="Рекурсия")
    await feed([], chat=recur, photo=[PhotoSize(file_id="photo1", file_unique_id="orig1", width=800, height=600)])
    sent = await feed(["SendPhoto"], chat=recur, text="/dem")  # у демотиватора есть рамка — отпечаток надёжный
    meme_uid = f"sent-uid{len(session.sent)}"
    meme_bytes = sent[0].photo.data
    bot_user = User(id=bot.id, is_bot=True, first_name="Мемобот")
    await feed([], chat=recur, forward_origin=MessageOriginUser(date=datetime.now(), sender_user=bot_user),
               photo=[PhotoSize(file_id="fwd", file_unique_id="fwd-uid", width=600, height=600)])
    await feed([], chat=recur, photo=[PhotoSize(file_id="again", file_unique_id=meme_uid, width=600, height=600)])
    assert memebot.storage.stats(recur.id).get("photo") == 1, memebot.storage.stats(recur.id)  # только оригинал
    # Мем сохранили в галерею и залили заново: другой file_unique_id, пережатая картинка.
    from PIL import Image  # noqa: PLC0415
    import io  # noqa: PLC0415
    img = Image.open(io.BytesIO(meme_bytes))
    buf = io.BytesIO()
    img.resize((img.width * 3 // 4, img.height * 3 // 4)).save(buf, "JPEG", quality=60)
    session.files["reup"] = buf.getvalue()
    await feed([], chat=recur, photo=[PhotoSize(file_id="reup", file_unique_id="reup-uid", width=450, height=450)])
    assert memebot.storage.stats(recur.id).get("photo") == 2  # пока не скачали — не знаем
    probe = update(chat=recur, text="x").message.as_(bot)
    picked = {(await memebot.pick_media(probe, ("photo",)))[0].file_id for _ in range(12)}
    assert picked == {"photo1"} and memebot.storage.stats(recur.id).get("photo") == 1, picked  # узнал по отпечатку
    # /nomeme: ответом на картинку — больше не брать её в мемы.
    orig = update(chat=recur, photo=[PhotoSize(file_id="photo1", file_unique_id="orig1", width=800, height=600)])
    assert "больше не делаю" in (await feed(["SendMessage"], chat=recur, text="/nomeme", reply_to_message=orig.message))[0].text
    assert memebot.storage.stats(recur.id).get("photo") is None
    print("✓ без рекурсии: пересланные и пересохранённые мемы бота не идут в мемы, /nomeme")

    # Кто есть кто: /name (менять — только админ), подпись автора «ник (имя)», блок в промпте.
    assert "только админ" in (await feed(["SendMessage"], text="/name Вася = Василий"))[0].text
    dm = Chat(id=777, type="private")
    assert "не знаю" in (await feed(["SendMessage"], chat=dm, text="/name"))[0].text
    assert "Запомнил" in (await feed(["SendMessage"], chat=dm, text="/name вася = Василий"))[0].text
    await feed(["SendMessage"], chat=dm, text="/name Shadow = Дима")
    await feed(["SendMessage"], chat=dm, text="/name Дима Бандит = Дима")
    listing = (await feed(["SendMessage"], chat=dm, text="/name"))[0].text
    assert "Дима: Shadow, Дима Бандит" in listing and "Василий: вася" in listing, listing
    assert memebot.author_of(update(chat=dm, text="йо").message) == "Вася (Василий)"
    assert memebot.author_of(update(text="йо").message) == "Вася"  # в другом чате имя не знаем
    block = ai._people_block(memebot.storage.people(dm.id))
    assert "Дима — Shadow, Дима Бандит" in block, block
    assert "Забыл" in (await feed(["SendMessage"], chat=dm, text="/name ВАСЯ ="))[0].text
    assert memebot.author_of(update(chat=dm, text="йо").message) == "Вася"
    print("✓ кто есть кто: /name, подпись «ник (имя)», блок в промпте")

    # Эдиты: разбор запроса, /tracks, /edit (монтаж подменён), «мало материала», пометка [эдит: …].
    import edits  # noqa: PLC0415
    import json  # noqa: PLC0415
    lib_dir = memebot.storage.base / "tracks"
    lib_dir.mkdir(exist_ok=True)
    (lib_dir / "tracks.json").write_text(json.dumps([
        {"file": "m.m4a", "title": "MATADORA — DJ Asul", "url": "u1", "period": 0.52, "drop": 16.9, "duration": 38.5},
        {"file": "s.m4a", "title": "Sahara — Hensonn", "url": "u2", "period": 0.58, "drop": 40.2, "duration": 67.5},
    ]))
    track, theme = memebot.split_edit_request("дача матадору")
    assert track.title.startswith("MATADORA") and theme == "дача", (track, theme)
    assert memebot.split_edit_request("")[0] is None
    assert "MATADORA" in (await feed(["SendMessage"], text="/tracks"))[0].text
    edit_chat = Chat(id=-100888, type="supergroup", title="Эдиты")
    assert "Мало материала" in (await feed(["SendMessage"], chat=edit_chat, text="/edit"))[0].text
    for i in range(8):
        await feed([], chat=edit_chat, photo=[PhotoSize(file_id="photo1", file_unique_id=f"e{i}", width=800, height=600)])
    for media_id, *_ in memebot.storage.unrated_media(100):
        memebot.storage.set_media_info(media_id, 2, "пацаны на даче", "photo")
    built = []

    async def fake_fetch(bot_, row, folder):
        return edits.Clip(Path(f"/tmp/{row['id']}.jpg"), "photo", 0.0)

    async def fake_build(slots, track_path, beats, captions, out, lead_beats):
        built.append((len(slots), track_path.name, beats.period, lead_beats))
        out.write_bytes(b"mp4")
        return out
    real = memebot.fetch_clip, edits.build_edit_async
    memebot.fetch_clip, edits.build_edit_async = fake_fetch, fake_build
    got = await feed(chat=edit_chat, text="/edit дача матадора")
    assert isinstance(got[0], SendVideo), getattr(got[0], "text", got)
    assert got[0].caption == "🎵 MATADORA — DJ Asul" and built[0][1] == "m.m4a" and built[0][2] == 0.52, (got, built)
    memebot.fetch_clip, edits.build_edit_async = real
    ai.EDITS_ON = True
    r = ai._tidy("ща сделаю\n[эдит: матадора]", "Мемобот", True)
    assert (r.lines, r.edit) == (["ща сделаю"], "матадора"), r
    assert ai._tidy("[эдит]", "Мемобот", True).edit == ""
    ai.EDITS_ON = False
    # Из видео берутся живые куски: кружочек «лицо 3 с, потом 7 с листа бумаги» — на интро и только лицо.
    import random  # noqa: PLC0415
    face_then_paper = edits.Clip(Path("/tmp/c1.mp4"), "circle", 10.0, [45.0] * 12 + [8.0] * 28)
    dark = edits.Clip(Path("/tmp/c2.mp4"), "circle", 10.0, [2.0] * 40)
    photos = [edits.Clip(Path(f"/tmp/p{i}.jpg"), "photo", 0.0) for i in range(10)]
    for seed in range(20):
        slots, lead, _ = edits.plan_velocity([dark, face_then_paper, *photos], 0.52, 16.9, 21.6, random.Random(seed))
        assert slots[0].src == face_then_paper.path and slots[0].start + lead * 0.52 <= 3.25, slots[0]
        for s in slots:
            if s.src == face_then_paper.path:
                assert s.start < 3, s
    print("✓ эдиты: трек и тема из запроса, /tracks, /edit, мало материала, пометка [эдит], живые куски видео")

    # Бот говорит голосом: по пометке [голосом] от Claude и по /say (синтез подменён).
    said = []

    async def fake_synthesize(text, style=None):
        said.append((text, style))
        return b"OggS-voice"
    real_tts = speech.tts_available, speech.synthesize
    speech.tts_available, speech.synthesize = (lambda: True), fake_synthesize
    ai.VOICE_ON = True
    r = ai._tidy("ну ты даёшь марат 😂\n[голосом]", "Мемобот", True)
    assert (r.lines, r.voice, r.voice_style) == (["ну ты даёшь марат 😂"], True, None), r
    r = ai._tidy("внимание [голосом: Демон]", "Мемобот", True)
    assert (r.lines, r.voice, r.voice_style) == (["внимание"], True, "Демон"), r

    async def fake_say3(*_args, **_kwargs):
        return ai.Reply(lines=["ну ты даёшь марат 😂"], voice=True, voice_style="бурундук")
    ai.say = fake_say3
    memebot._talks.clear()
    got = await feed(text="мемобот скажи голосом что думаешь про марата")
    assert [type(m).__name__ for m in got] == ["SendVoice"], got  # голосом — и текстом не дублирует
    assert got[0].voice.data == b"OggS-voice" and said == [("ну ты даёшь марат 😂", "бурундук")]
    got = await feed(["SendVoice"], text="/say всем салам")
    assert said[-1] == ("всем салам", None)
    await feed(["SendVoice"], text="/say Демон на колени")
    assert said[-1] == ("на колени", "Демон")
    ai.forget(GROUP.id)
    speech.tts_available, speech.synthesize = real_tts
    ai.VOICE_ON = False
    memebot._talks.clear()  # разговор с ботом закончился
    print("✓ голос бота: отвечает голосовым по пометке [голосом], /say")

    # Ролики: пометка [видос: …] от Claude и /clip (поиск подменён).
    import clips  # noqa: PLC0415
    real_clips = clips.available, clips.find_clip
    wanted = []

    async def fake_find_clip(query):
        wanted.append(query)
        return clips.Clip(b"mp4-bytes", "Сырный михаил но это анимация", "https://youtu.be/x")
    clips.available, clips.find_clip = (lambda: True), fake_find_clip
    ai.CLIPS_ON = True
    r = ai._tidy("лови\n[видос: сырный михаил]", "Мемобот", True)
    assert (r.lines, r.clip) == (["лови"], "сырный михаил"), r

    async def fake_say4(*_args, **_kwargs):
        return ai.Reply(lines=["лови"], clip="сырный михаил")
    ai.say = fake_say4
    got = await feed(text="мемобот скинь видос про сырного михаила")
    assert [type(m).__name__ for m in got] == ["SendMessage", "SendVideo"], got
    await feed(["SendVideo"], text="/clip толя саммер")
    assert wanted == ["сырный михаил", "толя саммер"], wanted
    clips.available, clips.find_clip = real_clips
    ai.CLIPS_ON = False
    memebot._talks.clear()
    print("✓ ролики: по пометке [видос: …] от Claude и по /clip")

    # Периодические реакции: примерно раз в 6–12 сообщений бот сам ставит реакцию.
    async def fake_pick(chat_id, *_args):
        return [(max(mid for mid, _ in ai._history[chat_id] if mid), "🔥")]
    ai.pick_reactions = fake_pick
    memebot._last_react_pass.clear()
    reactions = []
    for i in range(12):
        reactions += [m for m in await feed(text=f"сообщение {i}") if isinstance(m, SetMessageReaction)]
    assert len(reactions) == 1 and reactions[0].reaction[0].emoji == "🔥", reactions
    ai.available, ai.say, ai.pick_reactions = real_available, real_say, real_pick
    print("✓ стикеры запоминаются (с паком), реакции и стикеры от Claude доходят, реакции раз в пачку сообщений")

    # Долгая память и инициатива (Claude подменён): летопись, планы, бот пишет первым, зовёт пропавших.
    import time  # noqa: PLC0415
    mem = Chat(id=-100777, type="supergroup", title="Память")
    for i in range(40):
        memebot.storage.add_dialog(mem.id, "пельмешка (Катя)", f"сообщение {i}")
    memebot.storage.add_dialog(mem.id, "пельмешка (Катя)", "завтра экзамен по матану, молитесь")
    silent_answer = "-"

    async def fake_ask(system, prompt, image=None, model=None, **_):
        assert "Сейчас" in prompt or "летопись" in system  # бот знает, какое сейчас время
        if "редактор" in system:  # проход «вычисти»: отдаём как есть, только с тегами (их надо срезать)
            return {"result": prompt[prompt.index("<lore>"):], "usage": {}}
        if "Ты ведёшь летопись" in system and "<digests>" in prompt:  # сборка из выжимок
            assert "- Катя — сдаёт матан" in prompt and "закулисье" in prompt
            return {"result": "Вот:\n## Люди\n- Катя — сдаёт матан\n## Приколы и словечки\n## Истории\n## Сейчас", "usage": {}}
        if "Ты ведёшь летопись" in system:  # выжимка из куска переписки
            assert "завтра экзамен" in prompt and "— " in prompt  # переписка с датами
            return {"result": "Выжимка:\n## Люди\n- Катя — сдаёт матан", "usage": {}}
        if "не забывать" in system:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() + 5 * 3600))
            assert "передразнивание" in prompt  # шутки и чужие слова планами не считаются
            return {"result": f"{when} | Дима | экзамен по матану | завтра экзамен по матану, молитесь\n"
                              f"{when} | Дима | дача | в субботу едем на дачу всей толпой\nчушь без формата", "usage": {}}
        assert "<lore>" in system and "сдаёт матан" in system  # летопись — в закэшированной части
        if "не пишет в чат" in prompt:
            assert "Дима не пишет в чат уже 8" in prompt
            return {"result": silent_answer, "usage": {}}
        assert "писал: «завтра экзамен по матану, молитесь»" in prompt and "<context>" in prompt, prompt
        assert "сообщение 39" in prompt  # что писали вокруг — чтобы не спросить всерьёз про шутку
        return {"result": "Катя ну че, сдала матан?", "usage": {}}

    ai._ask, ai.available = fake_ask, (lambda: True)
    await memebot.update_lore(bot, mem.id)
    lore_text = memebot.storage.lore(mem.id)["text"]
    assert lore_text.startswith("## Люди") and "lore>" not in lore_text, lore_text  # «Вот:» и теги срезаны
    assert [d["text"] for d in memebot.storage.lore_digests(mem.id)] == ["## Люди\n- Катя — сдаёт матан"]
    await memebot.update_lore(bot, mem.id)  # нового нет — ничего не делает
    await memebot.update_plans(bot, mem.id)
    plans = memebot.storage.open_plans(mem.id)
    # Кто — настоящий автор фразы (модель сказала «Дима»), а план про фразу, которой в чате не было, — отброшен.
    assert [(p["who"], p["quote"]) for p in plans] == [("Катя", "завтра экзамен по матану, молитесь")], plans
    memebot.storage.touch_member(mem.id, 5, "пельмешка")
    memebot.storage.set_person(mem.id, "пельмешка", "Катя")
    ai.refresh(mem.id)
    with memebot.storage.db:
        memebot.storage.db.execute("UPDATE plans SET ask_at = ?", (int(time.time()) - 60,))  # пора спрашивать
    before = len(session.sent)
    assert await memebot.initiative_tick(bot, mem.id)
    sent = [m for m in session.sent[before:] if isinstance(m, SendMessage)]
    assert [(m.chat_id, m.text) for m in sent] == [(mem.id, "Катя ну че, сдала матан?")], sent
    assert memebot._talks[mem.id].user_id == 5  # ответит без тега — бот поймёт, что это ему
    assert not memebot.storage.open_plans(mem.id)
    assert not await memebot.initiative_tick(bot, mem.id)  # не чаще раза в полтора часа
    memebot.storage.add_plan(mem.id, "Катя", "старый план без цитаты", int(time.time()) - 60)
    assert await memebot.ask_about_plan(bot, mem.id, time.time()) is None  # не знаем, всерьёз ли — не спрашиваем
    assert not memebot.storage.open_plans(mem.id)
    assert ai.strip_contacts("**Паша (Паша, @pasha_tg)** — почта pasha@ya.ru, качается") == "**Паша (Паша)** — качается"
    # Пропавший: писал много, молчит 8 дней. Claude может и промолчать — тогда не повторяем сразу.
    memebot.storage.touch_member(mem.id, 6, "Shadow")
    memebot.storage.set_person(mem.id, "Shadow", "Дима")
    with memebot.storage.db:
        memebot.storage.db.execute("UPDATE members SET messages = 30, last_seen = ? WHERE user_id = 6",
                                   (int(time.time()) - 8 * 86400 - 60,))
        memebot.storage.db.execute("DELETE FROM initiatives WHERE chat_id = ?", (mem.id,))
    assert not await memebot.initiative_tick(bot, mem.id)  # Claude ответил «-»
    silent_answer = "Дима ты живой вообще"
    assert not await memebot.initiative_tick(bot, mem.id)  # сегодня уже звал
    with memebot.storage.db:
        memebot.storage.db.execute("DELETE FROM initiatives WHERE chat_id = ?", (mem.id,))
    before = len(session.sent)
    assert await memebot.initiative_tick(bot, mem.id)
    assert [m.text for m in session.sent[before:] if isinstance(m, SendMessage)] == ["Дима ты живой вообще"]
    assert memebot._talks[mem.id].user_id == 6
    assert memebot.fit_ask_time(int(time.time()) + 60, time.time()) is None  # слишком скоро — не спрашиваем
    ai._ask, ai.available = real_ask, real_avail
    dirty = ("**Катя (пельмешка)** — строит города, шутливо флиртует с ботом («краш», «зайка»), рисует. Донатила стримеру.\n"
             "- 18.06 — Дима спорил про выборы.\n- 21.09 — застряли на аттракционе.")
    clean, touched = ai.scrub_lore(dirty)
    assert clean == "**Катя (пельмешка)** — строит города, рисует.\n- 21.09 — застряли на аттракционе.", clean
    print("✓ память: летопись в промпте, планы → бот сам спрашивает, зовёт пропавших, не надоедает, стоп-слова")

    # Характер: первое знакомство, настроение и отношения в каждом запросе, обиды остывают, своя жизнь.
    ch = Chat(id=-100778, type="supergroup", title="Характер")
    for i in range(12):
        memebot.storage.add_dialog(ch.id, "Лёха Кринж", f"бот ты тупой {i}")
    assert memebot.state_text(ch.id) == "Настроение: обычное"

    async def fake_ask2(system, prompt, image=None, model=None, **_):
        if "внутренний голос" in system:
            assert "Это первая оценка" in prompt and "бот ты тупой" in prompt
            return {"result": "настроение: задет | Лёха доёбывал\nЛёха Кринж (Лёха): -2 | доёбывал политикой\n"
                              "Катя: +1 | угарнула\nМемобот: +2 | сам себе", "usage": {}}
        if "ситком" in system:
            assert "Распиши сегодняшний день" in prompt
            return {"result": "событие: 10:00 | Мемобота поставили на кассу в шаурмечной\n"
                              "событие: 23:30 | Бублик уронил копилку на приору\nрасписание:\n08:00 | едет в электричке\n"
                              "11:00 | на кассе в шаурмечной\n19:00 | дома, Даша доёбывает\n01:00 | спит", "usage": {}}
        assert "<life>" in system and "на кассу" in system  # жизнь — в закэшированной части
        assert "Настроение: задет" in prompt and "расскажи, что у тебя случилось" in prompt  # состояние — в запросе
        return {"result": "пацаны меня на кассу поставили прикиньте", "usage": {}}

    ai._ask, ai.available = fake_ask2, (lambda: True)
    await memebot.update_feelings(bot, ch.id, first=True)
    state = memebot.state_text(ch.id)
    assert "Настроение: задет — Лёха доёбывал" in state and "- Катя: чуть теплее обычного (угарнула)" in state, state
    assert "- Лёха: бесит (доёбывал политикой)" in state and "Мемобот" not in state, state
    assert "Лёха: бесит" in ai._prompt(ch.id, "задача")
    mood_text = (await feed(["SendMessage"], chat=ch, text="/mood"))[0].text
    assert "Настроение: задет" in mood_text and "- Лёха: бесит" in mood_text, mood_text
    with memebot.storage.db:
        memebot.storage.db.execute("UPDATE relations SET updated_at = ?", (int(time.time()) - 9 * 86400,))
    assert "- Лёха" not in memebot.state_text(ch.id)  # за 9 дней обида остыла
    noon = time.mktime(time.strptime(time.strftime("%Y-%m-%d") + " 12:00", "%Y-%m-%d %H:%M"))
    await memebot.update_life(bot, now=noon)
    await memebot.update_life(bot, now=noon + 60)  # день уже расписан — второй раз не расписывает
    assert len(memebot.storage.life(until=noon + 86400)) == 2
    assert "поставили на кассу" in ai.LIFE and "копилку" not in ai.LIFE  # вечернее ещё не случилось — не знает
    assert memebot.current_activity(noon - 3 * 3600) == "едет в электричке"  # 9 утра
    assert memebot.current_activity(noon) == "на кассе в шаурмечной"
    assert memebot.current_activity(noon + 13.5 * 3600) == "спит"  # полвторого ночи — ещё «вчерашний» день
    memebot.refresh_life(noon + 12 * 3600)
    assert "копилку" in ai.LIFE  # в полночь — уже случилось
    memebot.refresh_life(noon)
    with memebot.storage.db:  # в чате затишье уже час
        memebot.storage.db.execute("UPDATE dialog SET added_at = ? WHERE chat_id = ?", (int(noon), ch.id))
    ai.set_state(ch.id, memebot.state_text(ch.id))
    before = len(session.sent)
    assert await memebot.initiative_tick(bot, ch.id, now=noon + 3600)
    assert [m.text for m in session.sent[before:] if isinstance(m, SendMessage)] == ["пацаны меня на кассу поставили прикиньте"]
    assert not await memebot.share_life(bot, ch.id, noon + 3600)  # уже рассказал
    ai._ask, ai.available = real_ask, real_avail
    eps = [{"text": t} for t in ("Даша: видео 300 просмотров", "видео уже 500 просмотров, мама рада",
                                 "Даша сказала, видео 700 просмотров", "Мемобот купил кефир")]
    assert set(ai.stale_themes(eps, "Мемобот")) == {"видео", "просмотров"}  # приелось — сериал это закроет
    print("✓ характер: настроение и отношения в запросе, /mood, обиды остывают, своя жизнь и рассказ о ней, без заезженных линий")

    # Лица (поиск лиц подменён — в тестовых картинках лиц нет): записаться можно только самому, узнаёт
    # на присланных фото (история для Claude, база для эдитов), подсказывает подпись к мему, /face забудь.
    import faces  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    def face(*xs, cx=0.5, size=0.3):
        v = np.zeros(128, np.float32)
        v[:len(xs)] = xs
        return faces.Face(v / np.linalg.norm(v), cx, size)

    session.files.update({"selfie": b"selfie", "duo": b"duo", "crowd": b"crowd"})
    shown = {b"selfie": [face(1, 0)], b"duo": [face(0.99, 0.1, cx=0.2), face(0, 1, cx=0.8)],
             b"crowd": [face(1, 0, size=0.2), face(0, 1, size=0.19)]}
    real_find, real_faces_on = faces.find, faces.available
    faces.find, faces.available = (lambda image: list(shown.get(image, []))), (lambda: True)
    fc = Chat(id=-100555, type="supergroup", title="Лица")
    pic = lambda fid: [PhotoSize(file_id=fid, file_unique_id=f"u-{fid}", width=800, height=600)]  # noqa: E731
    assert "своё фото" in (await feed(["SendMessage"], chat=fc, text="/face"))[0].text
    assert "несколько лиц" in (await feed(["SendMessage"], chat=fc, photo=pic("crowd"), caption="/face"))[0].text
    kolya_photo = Message(message_id=5, date=datetime.now(), chat=fc, photo=pic("selfie"),
                          from_user=User(id=7, is_bot=False, first_name="Коля"))
    assert "только себя" in (await feed(["SendMessage"], chat=fc, text="/face", reply_to_message=kolya_photo))[0].text
    got = await feed(["SendMessage"], chat=fc, photo=pic("selfie"), caption="/face")
    assert "Запомнил тебя, Вася" in got[0].text and memebot.storage.face_people(fc.id) == [("Вася", 1)], got[0].text
    await feed([], chat=fc, photo=pic("duo"))
    await asyncio.gather(*list(memebot._background))
    assert "на фото: Вася и ещё кто-то незнакомый" in ai.history_lines(fc.id)[-1], ai.history_lines(fc.id)
    with memebot.storage.db:
        memebot.storage.db.execute("UPDATE media SET score = 2, description = 'двое на улице' WHERE chat_id = ?", (fc.id,))
    assert [r["people"] for r in memebot.storage.edit_candidates(fc.id)] == ["Вася"]  # эдит «про Васю» найдёт
    captured = {}

    async def fake_caption(chat_id, name, title, load, image, style, who=""):
        captured["who"] = who
        return "верх", "низ"

    real_caption, real_ai = ai.meme_caption, ai.available
    ai.meme_caption, ai.available = fake_caption, (lambda: True)
    await memebot.meme_texts(update(chat=fc, text="x").message.as_(bot), "classic", b"duo", None)
    assert captured["who"] == "Вася и ещё кто-то незнакомый", captured
    ai.meme_caption, ai.available = real_caption, real_ai
    assert "Вася" in (await feed(["SendMessage"], chat=fc, text="/faces"))[0].text
    assert "Забыл" in (await feed(["SendMessage"], chat=fc, text="/face забудь"))[0].text
    assert memebot.storage.face_people(fc.id) == [] and memebot.who_is_on(fc.id, b"duo") == ""
    assert [r["people"] for r in memebot.storage.edit_candidates(fc.id)] == [None]  # отметки стёрты
    faces.find, faces.available = real_find, real_faces_on
    twins = [("Катя", face(1, 0).feature), ("Лёха", face(0, 1).feature)]
    assert faces.identify([face(1, 0.97)], twins) == []  # похож на обоих почти одинаково — не угадываем
    print("✓ лица: только себя, узнаёт на фото, подсказка к мему, эдит по человеку, /faces, /face забудь")

    # Подписка ChatGPT (Codex CLI): CLI подменён скриптом — проверяем, как бот его зовёт и как читает ответ.
    import stat  # noqa: PLC0415
    import sys  # noqa: PLC0415
    fake_dir = Path(tempfile.mkdtemp())
    fake_codex = fake_dir / "codex"  # обёртка: в пути к Python бывают пробелы, а шебанг их не любит
    fake_codex.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake_dir / "codex.py"}" "$@"\n')
    (fake_dir / "codex.py").write_text(r"""
import json, os, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
dev = next((a.split("=", 1)[1] for a in args if a.startswith("developer_instructions=")), "null")
image = args[args.index("-i") + 1] if "-i" in args else ""
ok = (args[0] == "exec" and "shell_tool" in args and 'web_search="disabled"' in args and "--ephemeral" in args
      and "OPENAI_API_KEY" not in os.environ and json.loads(dev) == "ты «бот»\nбез \"кавычек\"" and prompt == "привет"
      and (not image or open(image, "rb").read() == b"jpeg"))
open(args[args.index("-o") + 1], "w").write("йо" if ok else "не так: " + " ".join(args))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 5}}))
""")
    fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IEXEC)
    real_provider = (ai.PROVIDER, ai.CLI, ai.PROVIDER_CHOICE)
    ai_flag = os.environ["AI"]
    os.environ.update(CODEX_COMMAND=str(fake_codex), CLAUDE_COMMAND="/нет/такого/claude", OPENAI_API_KEY="sk-test", AI="1")
    ai.PROVIDER_CHOICE = "auto"
    assert ai._pick_provider() == ("codex", str(fake_codex))  # Claude Code нет — берёт Codex
    ai.PROVIDER, ai.CLI = "codex", str(fake_codex)
    r = await ai._ask("ты «бот»\nбез \"кавычек\"", "привет", image=b"jpeg", think=False)
    assert r["result"] == "йо" and r["usage"]["cache_read_input_tokens"] == 80, r
    for key in ("CODEX_COMMAND", "CLAUDE_COMMAND", "OPENAI_API_KEY"):
        del os.environ[key]
    os.environ["AI"] = ai_flag
    ai.PROVIDER, ai.CLI, ai.PROVIDER_CHOICE = real_provider
    print("✓ подписка ChatGPT: Codex без команд и поиска, персона в developer_instructions, картинка, только подписка")

    # Где можно: решает владелец. Чужая беседа — молчим, не запоминаем, спрашиваем владельца кнопками.
    from aiogram.methods import AnswerCallbackQuery, EditMessageText, LeaveChat  # noqa: PLC0415
    from aiogram.types import CallbackQuery, ChatMemberLeft, ChatMemberUpdated  # noqa: PLC0415
    memebot.OWNER_ID = VASYA.id
    petya, stranger = User(id=2, is_bot=False, first_name="Петя"), Chat(id=-100999, type="group", title="Чужая")
    owner_dm = Chat(id=VASYA.id, type="private")

    def from_petya(chat, text):
        global _next_id
        _next_id += 1
        return Update(update_id=_next_id, message=Message(message_id=_next_id, date=datetime.now(), chat=chat,
                                                           from_user=petya, text=text))

    async def press(user, data):
        global _next_id
        _next_id += 1
        cb = CallbackQuery(id=str(_next_id), from_user=user, chat_instance="x", data=data,
                           message=Message(message_id=1, date=datetime.now(), chat=owner_dm, text="?"))
        before = len(session.sent)
        await dp.feed_update(bot, Update(update_id=_next_id, callback_query=cb))
        return session.sent[before:]

    before = len(session.sent)
    await dp.feed_update(bot, from_petya(stranger, "бот привет"))
    await dp.feed_update(bot, from_petya(stranger, "ау"))
    asks = session.sent[before:]
    assert len(asks) == 1 and asks[0].chat_id == VASYA.id and "«Чужая»" in asks[0].text and asks[0].reply_markup, asks
    assert memebot.storage.dialog_tail(stranger.id, 5) == []  # ничего не запомнил
    got = await press(petya, f"allow:{stranger.id}")  # не владелец — нельзя
    assert isinstance(got[0], AnswerCallbackQuery) and got[0].show_alert and not memebot.chat_allowed(stranger.id)
    got = await press(VASYA, f"allow:{stranger.id}")
    assert any(isinstance(m, EditMessageText) and "разрешил" in m.text for m in got) and memebot.chat_allowed(stranger.id)
    await dp.feed_update(bot, from_petya(stranger, "теперь запомни меня"))
    assert memebot.storage.dialog_tail(stranger.id, 5)[-1][2] == "теперь запомни меня"
    assert memebot.chat_allowed(petya.id)  # участник разрешённой беседы может писать в личку
    got = await press(VASYA, f"deny:{stranger.id}")
    assert any(isinstance(m, LeaveChat) and m.chat_id == stranger.id for m in got) and not memebot.chat_allowed(stranger.id)
    before = len(session.sent)
    await dp.feed_update(bot, from_petya(stranger, "а я снова тут"))
    assert [type(m).__name__ for m in session.sent[before:]] == ["LeaveChat"]  # запрещённая — сразу уходит
    assert not memebot.chat_allowed(3)  # незнакомый человек в личке — молчим
    mine = Chat(id=-100998, type="group", title="Своя")
    await dp.feed_update(bot, Update(update_id=999_999, my_chat_member=ChatMemberUpdated(
        chat=mine, from_user=VASYA, date=datetime.now(), old_chat_member=ChatMemberLeft(user=ME),
        new_chat_member=ChatMemberMember(user=ME))))
    assert memebot.chat_allowed(mine.id)  # владелец добавил сам — можно сразу
    listing = await feed(chat=owner_dm, text="/chats")
    assert any("«Своя» — ✅" in m.text for m in listing) and any("«Чужая» — 🚫" in m.text for m in listing), listing
    memebot.OWNER_ID = 0
    print("✓ где можно: чужая беседа молчит и ждёт владельца, кнопки только у владельца, выход, /chats")

    if WITH_AI:
        memebot.AI_REPLY_SHARE = 1.0
        memebot.storage.set_chance(GROUP.id, 0)
        await feed([], text="пацаны кто сегодня на шашлыки")
        await feed([], text="я пас, у меня дедлайн горит")
        got = await feed(["SendMessage"], text="@memobot а ты поедешь на шашлыки?")
        print("✓ живой ответ Claude:", got[0].text)

    await bot.session.close()
    print("\nВсё ок 🎉")


if __name__ == "__main__":
    asyncio.run(main())
