"""Голосовые и кружочки → текст через Yandex SpeechKit (синхронное распознавание).

Ключ — тот же API-ключ Yandex AI Studio, что и для картинок (или отдельный YANDEX_SPEECH_API_KEY).
Цена — 0,16 ₽ за каждые 15 секунд аудио. Синхронно SpeechKit берёт до 30 секунд за запрос,
поэтому длинные голосовые режем ffmpeg на куски и распознаём параллельно.
"""

import asyncio
import os
import re
import ssl
import tempfile
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import certifi

import video

STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
CHUNK_SECONDS = 29  # лимит синхронного распознавания — 30 с и 1 МБ за запрос
MAX_SECONDS = int(os.getenv("VOICE_MAX_SECONDS", "300"))  # длиннее — расшифровываем только начало

_SSL = ssl.create_default_context(cafile=certifi.where())


def _key() -> str:
    return (os.getenv("YANDEX_SPEECH_API_KEY") or os.getenv("YANDEX_SEARCH_API_KEY") or "").strip()


def available() -> bool:
    return os.getenv("VOICE", "1") != "0" and bool(_key()) and video.FFMPEG is not None


async def _chunks(data: bytes) -> list[bytes]:
    """Любое голосовое или кружочек → куски OggOpus (моно, 48 кГц) по CHUNK_SECONDS."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "in")
        src.write_bytes(data)
        proc = await asyncio.create_subprocess_exec(
            video.FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
            "-t", str(MAX_SECONDS), "-vn", "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "24k",
            "-f", "segment", "-segment_time", str(CHUNK_SECONDS), "-segment_format", "ogg",
            str(Path(tmp, "part%03d.ogg")),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), 120)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("ffmpeg не нарезал аудио вовремя")
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg не нарезал аудио: {err.decode(errors='replace')[-300:]}")
        return [part.read_bytes() for part in sorted(Path(tmp).glob("part*.ogg"))]


async def _recognize(session: aiohttp.ClientSession, chunk: bytes) -> str:
    # Мат не фильтруем (profanityFilter=false по умолчанию) — пусть бот слышит, как говорят на самом деле.
    params = {"lang": "ru-RU", "format": "oggopus"}
    headers = {"Authorization": f"Api-Key {_key()}"}
    async with session.post(STT_URL, params=params, data=chunk, headers=headers) as resp:
        body = await resp.json(content_type=None)
        if resp.status != 200:
            raise RuntimeError(f"SpeechKit: HTTP {resp.status}: {str(body)[:300]}")
        return (body.get("result") or "").strip()


async def transcribe(data: bytes) -> str:
    """Текст голосового (или звука из кружочка). Пустая строка — если там никто ничего не сказал."""
    chunks = await _chunks(data)
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=_SSL)) as session:
        texts = await asyncio.gather(*(_recognize(session, chunk) for chunk in chunks))
    return " ".join(t for t in texts if t)


# --- синтез: бот сам говорит голосом ---

TTS_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
# Голоса API v1 для русского: filipp, ermil (+good), zahar (+good), madi_ru,
# jane (+good, evil), omazh (+evil), marina (+whisper, friendly). Цена — 1342 ₽ за 1 млн символов.
TTS_VOICE = os.getenv("TTS_VOICE", "zahar")
TTS_EMOTION = os.getenv("TTS_EMOTION", "good")  # пусто — для голосов без амплуа
TTS_SPEED = os.getenv("TTS_SPEED", "1.1")
_UNSPEAKABLE = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]|\\[[^\\]]*\\]")


def tts_available() -> bool:
    return os.getenv("TTS", "1") != "0" and bool(_key())


def speakable(text: str) -> str:
    """Без эмодзи и пометок в скобках — синтезатор их не прочитает, а «[голосом]» вслух звучит глупо."""
    return " ".join(_UNSPEAKABLE.sub(" ", text).split())[:1000]


@dataclass(frozen=True)
class VoiceStyle:
    about: str                  # подсказка для Claude, когда такой голос к месту
    voice: str | None = None    # голос SpeechKit; None — голос бота по умолчанию
    emotion: str | None = None  # амплуа голоса (только если voice задан)
    speed: float | None = None
    fx: str | None = None       # звуковые эффекты ffmpeg — отсюда и «мемность»


def _pitch(k: float) -> str:
    """Выше/ниже тоном при той же скорости речи."""
    return f"asetrate=48000*{k},aresample=48000,atempo={1 / k:.4f}"


VOICE_STYLES = {
    "бурундук": VoiceStyle("писклявый бурундук", fx=_pitch(1.55)),
    "демон": VoiceStyle("низкий зловещий демон", speed=0.95,
                        fx=_pitch(0.68) + ",aecho=0.8:0.7:40|80:0.35|0.25,volume=4dB"),
    "робот": VoiceStyle("робот", fx="afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)'"
                                    ":win_size=512:overlap=0.75,volume=13dB,alimiter=limit=0.9"),
    "пьяный": VoiceStyle("пьяный: медленно и плывёт", speed=0.8, fx=_pitch(0.93) + ",vibrato=f=3:d=0.4"),
    "рация": VoiceStyle("хрипит, как по рации", fx="highpass=f=400,lowpass=f=2800,"
                                                 "acrusher=bits=8:mode=log:aa=1,volume=2"),
    "эхо": VoiceStyle("с эхом, как в подъезде", fx="aecho=0.8:0.9:120|240|360:0.5|0.35|0.2"),
    "перегруз": VoiceStyle("орёт в перегруз, как bass boosted", speed=1.1,
                           fx="bass=g=15,volume=4,acrusher=bits=10:mode=log:aa=1,alimiter=limit=0.9"),
    "злая": VoiceStyle("раздражённая злая тётка", voice="jane", emotion="evil", speed=1.15),
    "шёпот": VoiceStyle("шёпотом, как в ASMR", voice="marina", emotion="whisper",
                        fx="aecho=0.6:0.5:30:0.2,volume=9dB,alimiter=limit=0.9"),
    "диктор": VoiceStyle("торжественный диктор", voice="filipp", speed=0.9, fx="aecho=0.8:0.85:80|160:0.3|0.2"),
    "быстро": VoiceStyle("скороговоркой", speed=2.2),
}
_STYLES_BY_KEY = {k.replace("ё", "е"): v for k, v in VOICE_STYLES.items()}


def voice_style(name: str | None) -> VoiceStyle | None:
    return _STYLES_BY_KEY.get((name or "").lower().replace("ё", "е").strip(" :"))


async def _apply_fx(ogg: bytes, fx: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp, "in.ogg"), Path(tmp, "out.ogg")
        src.write_bytes(ogg)
        proc = await asyncio.create_subprocess_exec(
            video.FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-af", fx,
            "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "48k", str(dst),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), 60)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg не наложил эффект: {err.decode(errors='replace')[-300:]}")
        return dst.read_bytes()


async def synthesize(text: str, style: str | None = None) -> bytes:
    """Голосовое (OggOpus — как раз формат голосовых Telegram). style — из VOICE_STYLES."""
    text = speakable(text)
    if not text:
        raise ValueError("нечего озвучивать")
    st = voice_style(style) or VoiceStyle("")
    voice = st.voice or TTS_VOICE
    emotion = st.emotion if st.voice else TTS_EMOTION
    speed = st.speed or float(TTS_SPEED)
    form = {"text": text, "lang": "ru-RU", "voice": voice, "format": "oggopus", "speed": str(speed)}
    if emotion:
        form["emotion"] = emotion
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(ssl=_SSL)) as session:
        async with session.post(TTS_URL, data=form, headers={"Authorization": f"Api-Key {_key()}"}) as resp:
            body = await resp.read()
            if resp.status != 200:
                raise RuntimeError(f"SpeechKit TTS: HTTP {resp.status}: {body[:300]!r}")
    return await _apply_fx(body, st.fx) if st.fx else body
