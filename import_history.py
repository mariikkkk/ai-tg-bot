"""Импорт старой переписки из экспорта Telegram Desktop (формат HTML).

    python import_history.py "~/Downloads/Telegram Desktop/ChatExport_2026-09-27"
    python import_history.py ПАПКА --dry-run      # только посчитать, ничего не записывая
    python import_history.py ПАПКА --chat-id -100123...  # если id беседы известен

Тексты идут в генератор фраз, фотки/видео/гифки — в пул для мемов (копируются в data/media/).
В HTML-экспорте нет id чата, поэтому бот сам привяжет историю к беседе с тем же названием,
как только увидит там любое сообщение. Повторный импорт того же экспорта дублей не создаёт.
"""

import argparse
import hashlib
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

from dotenv import load_dotenv

import markov
from storage import Storage

VOID_TAGS = {"br", "img", "hr", "input", "meta", "link", "source", "wbr"}
DATE_RE = re.compile(r"(\d\d)\.(\d\d)\.(\d{4}) (\d\d):(\d\d):(\d\d)(?: UTC([+-])(\d\d):?(\d\d))?")
VIA_BOT_RE = re.compile(r"\s+via\s+@\S+\s*$")


@dataclass
class ExportMessage:
    id: int
    author: str | None
    date: int = 0
    text: str = ""
    media: list[tuple[str, str]] = field(default_factory=list)  # (kind, путь внутри экспорта)
    via_bot: bool = False


class ExportPage(HTMLParser):
    """Разбирает одну страницу messagesN.html."""

    def __init__(self, last_author: str | None = None):
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.messages: list[ExportMessage] = []
        self.last_author = last_author  # у «склеенных» сообщений автор не повторяется
        self._stack: list[set[str]] = []
        self._msg: ExportMessage | None = None
        self._msg_depth = 0
        self._capture: str | None = None
        self._capture_depth = 0
        self._buf: list[str] = []
        self._video_href: str | None = None

    def _inside(self, cls: str) -> bool:
        return any(cls in c for c in self._stack)

    def _start(self, what: str) -> None:
        self._capture, self._capture_depth, self._buf = what, len(self._stack), []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = set((a.get("class") or "").split())
        if tag in VOID_TAGS:
            if tag == "br" and self._capture == "text":
                self._buf.append("\n")
            return
        self._stack.append(cls)
        if self._capture:
            return

        if tag == "div" and {"text", "bold"} <= cls and self._inside("page_header"):
            self._start("title")
        elif tag == "div" and {"message", "default"} <= cls:
            joined = "joined" in cls
            msg_id = int(a.get("id", "message0").removeprefix("message") or 0)
            self._msg = ExportMessage(id=msg_id, author=self.last_author if joined else None)
            self._msg_depth = len(self._stack)
            self.messages.append(self._msg)
        elif self._msg is None:
            return
        elif tag == "div" and "from_name" in cls and not self._inside("forwarded"):
            self._start("author")
        elif tag == "div" and "text" in cls and not self._inside("reply_to"):
            self._start("text")
        elif tag == "div" and "date" in cls and not self._inside("forwarded"):
            self._msg.date = parse_date(a.get("title", ""))
        elif tag == "a" and (href := a.get("href")):
            if "photo_wrap" in cls:
                self._msg.media.append(("photo", href))
            elif "animated_wrap" in cls:
                self._msg.media.append(("animation", href))
            elif "video_file_wrap" in cls:
                self._msg.media.append(("video", href))
            elif "media_video" in cls:
                self._video_href = href  # вид (видео/гифка/кружок) — в заголовке ниже
        elif tag == "div" and "media_video" in cls:
            self._msg.media.append(("missing", ""))  # видео не попало в экспорт
        elif tag == "div" and {"title", "bold"} <= cls and self._video_href:
            self._start("video_title")

    def handle_endtag(self, tag):
        if tag in VOID_TAGS or not self._stack:
            return
        depth = len(self._stack)
        if self._capture and depth == self._capture_depth:
            self._finish()
        if self._msg and depth == self._msg_depth:
            self._msg = None
            self._video_href = None
        self._stack.pop()

    def handle_data(self, data):
        if self._capture:
            self._buf.append(data)

    def _finish(self) -> None:
        what, text = self._capture, "".join(self._buf).strip()
        self._capture = None
        if what == "title" and self.title is None:
            self.title = " ".join(text.split())
        elif what == "author":
            self._msg.via_bot = bool(VIA_BOT_RE.search(text))
            self._msg.author = self.last_author = VIA_BOT_RE.sub("", " ".join(text.split()))
        elif what == "text":
            self._msg.text = text
        elif what == "video_title":
            kind = "animation" if text.lower().startswith("animation") else "video"
            self._msg.media.append((kind, self._video_href))
            self._video_href = None


def parse_date(title: str) -> int:
    m = DATE_RE.search(title)
    if not m:
        return 0
    day, month, year, hh, mm, ss, sign, tzh, tzm = m.groups()
    tz = timezone.utc
    if sign:
        offset = timedelta(hours=int(tzh), minutes=int(tzm))
        tz = timezone(offset if sign == "+" else -offset)
    return int(datetime(int(year), int(month), int(day), int(hh), int(mm), int(ss), tzinfo=tz).timestamp())


def read_export(folder: Path) -> tuple[str, list[ExportMessage]]:
    pages = sorted(folder.glob("messages*.html"), key=lambda p: int(p.stem.removeprefix("messages") or 1))
    if not pages:
        raise SystemExit(
            f"В {folder} нет messages.html. Нужна папка экспорта Telegram Desktop в формате HTML."
        )
    title, messages, author = None, [], None
    for page in pages:
        parser = ExportPage(last_author=author)
        parser.feed(page.read_text(encoding="utf-8"))
        title = title or parser.title
        messages += parser.messages
        author = parser.last_author
    return title or folder.name, messages


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def import_export(
    storage: Storage, folder: Path, chat_id: int | None = None, with_media: bool = True, dry_run: bool = False
) -> dict:
    title, messages = read_export(folder)
    if chat_id is not None and not dry_run:
        storage.set_import_target(title, chat_id)
        attached = True
    elif dry_run:
        chat_id, attached = 0, False
    else:
        chat_id, attached = storage.import_target(title)
    done = storage.imported_ids(chat_id) if not dry_run else set()

    msg_ids, phrases, media, dialog = [], [], [], []
    stats = {"title": title, "messages": len(messages), "skipped": 0, "photo": 0, "video": 0, "animation": 0,
             "missing": 0, "attached": attached}
    for msg in messages:
        cleaned = "" if msg.via_bot or msg.text.startswith("/") else markov.clean(msg.text)
        if cleaned:
            dialog.append((msg.id, msg.author or "?", cleaned, msg.date))  # дубли отсекает база
        if msg.id in done:
            stats["skipped"] += 1
            continue
        msg_ids.append(msg.id)
        if msg.via_bot:
            continue
        if cleaned:
            phrases.append((cleaned, msg.date))
        for kind, href in msg.media if with_media else ():
            src = folder / href
            if kind == "missing" or not src.is_file():
                stats["missing"] += 1
                continue
            stats[kind] += 1
            if dry_run:
                continue
            digest = sha1_of(src)
            rel = f"media/{digest}{src.suffix.lower()}"
            if not (storage.base / rel).exists():
                storage.media_root.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, storage.base / rel)
            media.append((kind, f"sha1:{digest}", rel, msg.date))

    stats["phrases"] = len(phrases)
    if not dry_run:
        storage.import_history(chat_id, msg_ids, phrases, media, dialog)
    return stats


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Импорт старой переписки из экспорта Telegram Desktop (HTML)")
    ap.add_argument("folder", type=lambda p: Path(p).expanduser(), help="папка ChatExport_...")
    ap.add_argument("--chat-id", type=int, help="id беседы, если знаешь (иначе бот привяжет по названию)")
    ap.add_argument("--no-media", action="store_true", help="только тексты")
    ap.add_argument("--dry-run", action="store_true", help="только посчитать, ничего не записывать")
    args = ap.parse_args()

    here = Path(__file__).parent
    storage = Storage(os.getenv("DB_PATH", str(here / "data" / "bot.db")), 0)
    s = import_export(storage, args.folder, args.chat_id, not args.no_media, args.dry_run)

    print(f"Чат «{s['title']}»: сообщений в экспорте {s['messages']}"
          + (f", уже были загружены раньше {s['skipped']}" if s["skipped"] else ""))
    print(f"{'Нашёл' if args.dry_run else 'Загрузил'}: фраз {s['phrases']}, фоток {s['photo']}, "
          f"видео {s['video']}, гифок {s['animation']}")
    if s["missing"]:
        print(f"Не скачано в экспорте: {s['missing']} медиа — включи их в настройках экспорта, если нужны")
    if not args.dry_run and not s["attached"]:
        print(f"Как только бот увидит сообщение в беседе «{s['title']}», он подхватит эту историю.")


if __name__ == "__main__":
    sys.exit(main())
