"""Библиотека фонка для эдитов: треки лежат в data/tracks/, темп и дроп посчитаны заранее.

Треки качаем с YouTube через yt-dlp (короткие «edit audio» — сразу лучший кусок).
Новые можно добавлять из чата: /track запрос.
"""

import asyncio
import json
import logging
import random
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import clips
import edits
import video

# Стартовая библиотека: (запрос на YouTube, как называть).
STARTER = [
    ("https://www.youtube.com/watch?v=3ZDCSQUxfq0", "MATADORA — DJ Asul"),
    ("https://www.youtube.com/watch?v=Ifl5rNjXxBQ", "MATADORA (slowed) — DJ Asul"),
    ("https://www.youtube.com/watch?v=dw7D3fgkZyQ", "Passo Bem Solto (slowed) — ATLXS"),
    ("https://www.youtube.com/watch?v=qQNSuHcO1GA", "Murder In My Mind — Kordhell"),
    ("https://www.youtube.com/watch?v=Vv9ra47zeVE", "Close Eyes — DVRST"),
    ("https://www.youtube.com/watch?v=_HhFxQBUwXI", "Sahara — Hensonn"),
    ("https://www.youtube.com/watch?v=Bv_916GeFRQ", "Metamorphosis — Interworld"),
    ("https://www.youtube.com/watch?v=-41QE_qgDRM", "Rave — Dxrk"),
    ("https://www.youtube.com/watch?v=MXsDMl5Sm9U", "Montagem Coma — Andromeda"),
    ("https://www.youtube.com/watch?v=453kuRmnxow", "SLAY! — Eternxlkz"),
    ("https://www.youtube.com/watch?v=kParTwwgxpo", "Automotivo Bibi Fogosa — Bibi Babydoll"),
    ("https://www.youtube.com/watch?v=YGUOoDsT-yI", "Funk Estranho — Alxike"),
    ("https://www.youtube.com/watch?v=V0qszQnr5D4", "Miguel Funk — Brazilian Phonk"),
    ("https://www.youtube.com/watch?v=9DPa40humGM", "dare — sayfalse, trxveler"),
]
log = logging.getLogger("memebot.tracks")
EDIT_SECONDS = 16  # столько трека нужно после дропа, чтобы влез эдит


@dataclass
class Track:
    file: str      # имя файла в папке треков
    title: str
    url: str
    period: float  # секунд между ударами
    drop: float    # где дроп
    duration: float


class Library:
    def __init__(self, folder: Path):
        self.folder = folder
        self.index = folder / "tracks.json"

    def tracks(self) -> list[Track]:
        if not self.index.exists():
            return []
        return [Track(**t) for t in json.loads(self.index.read_text(encoding="utf-8"))]

    def _save(self, tracks: list[Track]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self.index.write_text(json.dumps([asdict(t) for t in tracks], ensure_ascii=False, indent=1), encoding="utf-8")

    def path(self, track: Track) -> Path:
        return self.folder / track.file

    def pick(self, query: str | None = None) -> Track | None:
        """Трек по названию (если просили) или случайный."""
        tracks = self.tracks()
        if query:
            words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
            matching = [t for t in tracks if any(w in t.title.lower() for w in words)]
            tracks = matching or tracks
        return random.choice(tracks) if tracks else None

    async def add(self, source: str, title: str | None = None) -> Track:
        """Качает трек (ссылка или запрос на YouTube), считает темп и дроп, добавляет в библиотеку."""
        self.folder.mkdir(parents=True, exist_ok=True)
        if not source.startswith("http"):
            found = await clips.search(source + " edit audio")
            if not found:
                raise LookupError(f"не нашёл трек «{source}»")
            vid, _, found_title = found[0]
            source, title = f"https://www.youtube.com/watch?v={vid}", title or found_title
        vid = re.search(r"(?:v=|shorts/|youtu\.be/)([\w-]{6,})", source)
        name = f"{vid.group(1) if vid else abs(hash(source))}.m4a"
        dst = self.folder / name
        if not dst.exists():
            await clips._run("-q", "--ffmpeg-location", video.FFMPEG, "-f", "ba[ext=m4a]/ba", "-x",
                             "--audio-format", "m4a", "-o", str(dst.with_suffix(".%(ext)s")), source, timeout=120)
        beats = await asyncio.to_thread(edits.analyze_track, dst)
        track = Track(name, title or name, source, round(beats.period, 4), round(beats.drop, 3), round(beats.duration, 2))
        self._save([t for t in self.tracks() if t.file != name] + [track])
        return track

    async def ensure_starter(self) -> int:
        """Докачивает стартовую библиотеку (то, чего ещё нет). Не скачался один трек — не беда, берёт следующий.
        Возвращает, сколько треков добавил."""
        have, added = {t.url for t in self.tracks()}, 0
        for url, title in STARTER:
            if url in have:
                continue
            try:
                await self.add(url, title)
                added += 1
            except Exception as e:  # noqa: BLE001 — видео удалили, сеть, YouTube капризничает
                log.warning("Не скачал трек «%s»: %s", title, e)
        return added
