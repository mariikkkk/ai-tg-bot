"""Короткие ролики из интернета — поиск и скачивание YouTube Shorts через yt-dlp.

Из тиктока напрямую не выходит: он блокирует контент для российских IP («Your IP address is
blocked»), а поиск по нему закрыт от скриптов. Зато тиктоки массово перезаливают в Shorts.
Для YouTube yt-dlp нужен JavaScript-движок — берём Node.js, если он установлен.
"""

import asyncio
import logging
import os
import random
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import video

log = logging.getLogger("memebot.clips")

YTDLP = Path(sys.executable).with_name("yt-dlp")
NODE = shutil.which("node") or next(
    (p for p in ("/opt/homebrew/bin/node", "/usr/local/bin/node") if os.access(p, os.X_OK)), None
)
MAX_SECONDS = int(os.getenv("CLIP_MAX_SECONDS", "90"))
MAX_MB = 45  # ботам Telegram можно отправлять файлы до 50 МБ


@dataclass
class Clip:
    data: bytes
    title: str
    url: str


def available() -> bool:
    return os.getenv("CLIPS", "1") != "0" and YTDLP.exists() and NODE is not None and video.FFMPEG is not None


async def _run(*args: str, timeout: float) -> str:
    proc = await asyncio.create_subprocess_exec(
        str(YTDLP), "--no-warnings", "--js-runtimes", f"node:{NODE}", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("yt-dlp не уложился вовремя")
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp: {err.decode(errors='replace').strip()[-300:]}")
    return out.decode(errors="replace")


NOISE = re.compile(r"(?<![\wё])(тик ?ток\w*|tiktok|видос\w*|видео|ролик\w*|шортс\w*|shorts|рилс\w*|смешн\w*|прикол\w*)(?![\wё])",
                   re.IGNORECASE)


STOP_WORDS = set("how when what the and you your this that sound про как что это при для так вот тот эта "
                 "все всё его она они него когда где кто или".split())


def _stems(text: str) -> set[str]:
    """Основы значимых слов для сравнения запроса с названием ролика («михаила» ~ «михаил»)."""
    words = re.findall(r"[a-zа-яё0-9]+", text.lower())
    return {w[:5] for w in words if (len(w) >= 3 or w.isdigit()) and w not in STOP_WORDS}


async def search(query: str, results: int = 10) -> list[tuple[str, float, str]]:
    """[(id, длительность, название)] коротких роликов по запросу — только те, что похожи на запрос."""
    out = await _run("--flat-playlist", "--print", "%(id)s\t%(duration)s\t%(title)s",
                     f"ytsearch{results}:{query} shorts", timeout=40)
    wanted, scored = _stems(query), []
    need = min(2, len(wanted))  # из двух и больше слов запроса в названии должны быть хотя бы два
    for rank, line in enumerate(out.splitlines()):
        vid, dur, title = (line.split("\t") + ["", ""])[:3]
        try:
            seconds = float(dur)
        except ValueError:
            continue
        overlap = len(wanted & _stems(title))
        if 3 <= seconds <= MAX_SECONDS and overlap and overlap >= need:  # случайное видео хуже, чем никакого
            scored.append((-overlap, rank, vid, seconds, title))
    return [(vid, seconds, title) for *_, vid, seconds, title in sorted(scored)]


async def find_clip(query: str) -> Clip | None:
    """Скачивает короткий ролик по запросу: один из самых подходящих, чтобы не повторяться."""
    query = " ".join(NOISE.sub(" ", query).split()[:4]) or query
    found = await search(query)
    if not found and len(query.split()) > 2:  # длинный запрос YouTube понимает плохо — укоротим
        found = await search(" ".join(query.split()[:2]))
    top = found[:3]
    random.shuffle(top)
    for vid, _, title in top:
        url = f"https://www.youtube.com/shorts/{vid}"
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp, "clip.mp4")
            try:
                await _run(
                    "-q", "--ffmpeg-location", video.FFMPEG, "--max-filesize", f"{MAX_MB}M",
                    # H.264 + AAC: так Telegram проигрывает ролик прямо в чате
                    "-f", "bv*[vcodec^=avc1][height<=720]+ba[ext=m4a]/b[ext=mp4][height<=720]/b[ext=mp4]",
                    "--merge-output-format", "mp4", "-o", str(dst), url,
                    timeout=120,
                )
            except RuntimeError as e:
                log.warning("Не скачал %s: %s", url, e)
                continue
            if dst.exists() and dst.stat().st_size <= MAX_MB * 1024 * 1024:
                return Clip(dst.read_bytes(), title, url)
    return None
