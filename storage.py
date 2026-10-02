"""SQLite-хранилище: медиа, фразы для генератора и настройки чатов.

Медиа, которые бот увидел сам, храним как file_id — файлы остаются у Telegram.
Медиа из импорта истории лежат локально в data/media/ (поле path).
"""

import json
import random
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id             INTEGER PRIMARY KEY,
    chat_id        INTEGER NOT NULL,
    kind           TEXT    NOT NULL,          -- photo | video | animation
    file_id        TEXT    NOT NULL,          -- пусто у импортированных
    file_unique_id TEXT    NOT NULL,
    added_at       INTEGER NOT NULL,
    path           TEXT,                      -- относительно папки с базой, только у импортированных
    UNIQUE (chat_id, file_unique_id)
);
CREATE INDEX IF NOT EXISTS media_chat_kind ON media (chat_id, kind);

CREATE TABLE IF NOT EXISTS phrases (
    id       INTEGER PRIMARY KEY,
    chat_id  INTEGER NOT NULL,
    text     TEXT    NOT NULL,
    added_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS phrases_chat ON phrases (chat_id, id);

CREATE TABLE IF NOT EXISTS chats (
    chat_id   INTEGER PRIMARY KEY,
    chance    REAL    NOT NULL,
    last_post INTEGER NOT NULL DEFAULT 0
);

-- Какие сообщения из экспорта уже загружены — чтобы повторный импорт не плодил дубли.
CREATE TABLE IF NOT EXISTS imported (
    chat_id INTEGER NOT NULL,
    msg_id  INTEGER NOT NULL,
    PRIMARY KEY (chat_id, msg_id)
);

-- Переписка с авторами — чтобы живые ответы видели, как в чате общаются друг с другом.
CREATE TABLE IF NOT EXISTS dialog (
    id       INTEGER PRIMARY KEY,
    chat_id  INTEGER NOT NULL,
    msg_id   INTEGER,                 -- номер сообщения из экспорта; у живых NULL
    author   TEXT    NOT NULL,
    text     TEXT    NOT NULL,
    added_at INTEGER NOT NULL,
    UNIQUE (chat_id, msg_id)
);
CREATE INDEX IF NOT EXISTS dialog_chat ON dialog (chat_id, id);

-- Стикеры, которые видели в чате (и целиком их паки): бот шлёт их по эмодзи.
CREATE TABLE IF NOT EXISTS stickers (
    chat_id        INTEGER NOT NULL,
    file_id        TEXT    NOT NULL,
    file_unique_id TEXT    NOT NULL,
    emoji          TEXT    NOT NULL DEFAULT '',
    set_name       TEXT,
    uses           INTEGER NOT NULL DEFAULT 0,   -- сколько раз слали в чате: любимые бот шлёт чаще
    PRIMARY KEY (chat_id, file_unique_id)
);
CREATE TABLE IF NOT EXISTS sticker_sets (
    chat_id  INTEGER NOT NULL,
    set_name TEXT    NOT NULL,
    PRIMARY KEY (chat_id, set_name)
);

-- То, что прислал сам бот (или что запретили /nomeme): такое не превращаем в мемы — без рекурсии.
CREATE TABLE IF NOT EXISTS own_media (
    file_unique_id TEXT PRIMARY KEY
);
-- «Отпечатки» картинок, которые отправил бот: узнаём свой мем, даже если его пересохранили и залили заново.
CREATE TABLE IF NOT EXISTS own_hashes (
    hash INTEGER PRIMARY KEY
);

-- Долгая память: летопись беседы (её ведёт Claude) и докуда она дочитана.
CREATE TABLE IF NOT EXISTS lore (
    chat_id    INTEGER PRIMARY KEY,
    text       TEXT    NOT NULL DEFAULT '',
    upto       INTEGER NOT NULL DEFAULT 0,  -- id последней учтённой строки dialog
    updated_at INTEGER NOT NULL DEFAULT 0,
    plans_upto INTEGER NOT NULL DEFAULT 0,  -- до какой строки dialog искали планы
    plans_at   INTEGER NOT NULL DEFAULT 0
);

-- Выжимки из кусков переписки, из которых собирается летопись (чтобы пересобрать, не перечитывая всё).
CREATE TABLE IF NOT EXISTS lore_digests (
    chat_id  INTEGER NOT NULL,
    upto     INTEGER NOT NULL,  -- id последней строки dialog в куске
    first_at INTEGER NOT NULL,
    last_at  INTEGER NOT NULL,
    text     TEXT    NOT NULL,
    PRIMARY KEY (chat_id, upto)
);

-- О чём бот сам спросит потом («Катя, ну че, сдала?»).
CREATE TABLE IF NOT EXISTS plans (
    id         INTEGER PRIMARY KEY,
    chat_id    INTEGER NOT NULL,
    who        TEXT    NOT NULL,
    about      TEXT    NOT NULL,
    ask_at     INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'open'  -- open | asked | skipped
);

-- Живые участники: кто когда писал последний раз — чтобы позвать пропавших.
CREATE TABLE IF NOT EXISTS members (
    chat_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    nick      TEXT    NOT NULL,
    messages  INTEGER NOT NULL DEFAULT 0,
    last_seen INTEGER NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

-- Когда бот сам писал первым — чтобы не надоедать.
CREATE TABLE IF NOT EXISTS initiatives (
    chat_id INTEGER NOT NULL,
    kind    TEXT    NOT NULL,  -- plan:<id> | silent:<user_id>
    at      INTEGER NOT NULL
);

-- Лица тех, кто сам записался (/face): «отпечатки» для узнавания на фото. Чужие лица не храним.
CREATE TABLE IF NOT EXISTS faces (
    id       INTEGER PRIMARY KEY,
    chat_id  INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    name     TEXT    NOT NULL,
    feature  BLOB    NOT NULL,
    added_at INTEGER NOT NULL
);

-- Распорядок дня бота: где он и что делает в течение дня (JSON: [[«ЧЧ:ММ», «что делает»], ...]).
CREATE TABLE IF NOT EXISTS life_days (
    day      TEXT    PRIMARY KEY,  -- ГГГГ-ММ-ДД
    schedule TEXT    NOT NULL,
    at       INTEGER NOT NULL
);

-- Где боту можно быть: беседы разрешает владелец (OWNER_ID).
CREATE TABLE IF NOT EXISTS allowed_chats (
    chat_id INTEGER PRIMARY KEY,
    title   TEXT    NOT NULL DEFAULT '',
    status  TEXT    NOT NULL,  -- allowed | pending | denied
    at      INTEGER NOT NULL
);

-- Характер: настроение бота в беседе и отношения с каждым.
CREATE TABLE IF NOT EXISTS feelings (
    chat_id INTEGER PRIMARY KEY,
    mood    TEXT    NOT NULL,
    why     TEXT    NOT NULL DEFAULT '',
    at      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS relations (
    chat_id    INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    score      INTEGER NOT NULL DEFAULT 0,  -- от -5 (вечный бэф) до +5 (любимчик)
    note       TEXT    NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (chat_id, name)
);

-- Своя жизнь бота: эпизоды «сериала» (общие для всех бесед).
CREATE TABLE IF NOT EXISTS life (
    id     INTEGER PRIMARY KEY,
    at     INTEGER NOT NULL,
    text   TEXT    NOT NULL,
    status TEXT    NOT NULL
);

-- Кто есть кто: ник в Telegram (или подпись в старой переписке) → как человека зовут.
CREATE TABLE IF NOT EXISTS people (
    chat_id INTEGER NOT NULL,
    nick    TEXT    NOT NULL,
    name    TEXT    NOT NULL,
    PRIMARY KEY (chat_id, nick)
);

-- В какие недели бот уже собирал «итоги недели» (эдит по воскресеньям).
CREATE TABLE IF NOT EXISTS weekly_edits (
    chat_id INTEGER NOT NULL,
    week    TEXT    NOT NULL,
    PRIMARY KEY (chat_id, week)
);

-- В HTML-экспорте нет id чата, только название. Импорт ложится во временный chat_id,
-- а бот перевешивает его на настоящий, когда увидит беседу с таким названием.
CREATE TABLE IF NOT EXISTS import_targets (
    title    TEXT    PRIMARY KEY,   -- нормализованное название чата
    chat_id  INTEGER NOT NULL,
    attached INTEGER NOT NULL DEFAULT 0
);
"""

TEMP_CHAT_BASE = 7_000_000_000_000_000_000  # таких id у Telegram не бывает


def norm_emoji(emoji: str) -> str:
    """Без «вариационного селектора»: ❤️ и ❤ — одно и то же."""
    return emoji.replace("\ufe0f", "").strip()


def normalize_title(title: str) -> str:
    return " ".join(title.split()).casefold()

# Сколько последних фраз на чат держать — старые вытесняются, чтобы бот «жил» актуальным.
MAX_PHRASES_PER_CHAT = 20_000
MAX_DIALOG_PER_CHAT = 30_000


@dataclass
class Media:
    id: int
    kind: str
    file_id: str
    path: str | None = None


class Storage:
    def __init__(self, path: str, default_chance: float):
        self.base = Path(path).parent
        self.base.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        if "path" not in {row[1] for row in self.db.execute("PRAGMA table_info(media)")}:
            self.db.execute("ALTER TABLE media ADD COLUMN path TEXT")  # база от первой версии
        media_cols = {row[1] for row in self.db.execute("PRAGMA table_info(media)")}
        for col, kind in (("description", "TEXT"), ("score", "INTEGER"), ("shape", "TEXT"),  # для эдитов
                          ("people", "TEXT"), ("faces_at", "INTEGER")):  # кто из записавшихся на фото
            if col not in media_cols:
                self.db.execute(f"ALTER TABLE media ADD COLUMN {col} {kind}")
        plan_cols = {row[1] for row in self.db.execute("PRAGMA table_info(plans)")}
        for col in ("quote", "context"):  # дословная фраза и сообщения вокруг — чтобы не спросить про шутку всерьёз
            if col not in plan_cols:
                self.db.execute(f"ALTER TABLE plans ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        sticker_cols = {row[1] for row in self.db.execute("PRAGMA table_info(stickers)")}
        for col in ("description", "thumb_id", "kind"):  # база до того, как бот научился видеть стикеры
            if col not in sticker_cols:
                self.db.execute(f"ALTER TABLE stickers ADD COLUMN {col} TEXT")
        self.default_chance = default_chance

    # --- медиа ---

    def add_media(self, chat_id: int, kind: str, file_id: str, file_unique_id: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO media (chat_id, kind, file_id, file_unique_id, added_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (chat_id, kind, file_id, file_unique_id, int(time.time())),
            )

    def random_media(self, chat_id: int, kinds: tuple[str, ...]) -> Media | None:
        marks = ",".join("?" * len(kinds))
        row = self.db.execute(
            f"SELECT id, kind, file_id, path FROM media WHERE chat_id = ? AND kind IN ({marks})"
            " ORDER BY RANDOM() LIMIT 1",
            (chat_id, *kinds),
        ).fetchone()
        return Media(*row) if row else None

    @property
    def media_root(self) -> Path:
        return self.base / "media"

    def local_file(self, media: Media) -> Path:
        return self.base / media.path

    def delete_media(self, media_id: int) -> None:
        with self.db:
            self.db.execute("DELETE FROM media WHERE id = ?", (media_id,))

    # --- «рассмотренные» медиа для эдитов ---

    def unrated_media(self, limit: int) -> list[tuple[int, str, str, str | None]]:
        """Медиа, которые бот ещё не рассмотрел: (id, kind, file_id, path); свежие — первыми."""
        return self.db.execute(
            "SELECT id, kind, file_id, path FROM media WHERE score IS NULL ORDER BY added_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def unrated_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM media WHERE score IS NULL").fetchone()[0]

    def set_media_info(self, media_id: int, score: int, description: str, shape: str) -> None:
        with self.db:
            self.db.execute("UPDATE media SET score = ?, description = ?, shape = ? WHERE id = ?",
                            (score, description, shape, media_id))

    def edit_candidates(self, chat_id: int, since: int = 0) -> list[dict]:
        """Медиа, годные для эдита (оценка ≥ 1): id, kind, file_id, path, описание, оценка, форма, когда прислали."""
        rows = self.db.execute(
            "SELECT id, kind, file_id, path, description, score, shape, added_at, people FROM media"
            " WHERE chat_id = ? AND score >= 1 AND added_at >= ? ORDER BY RANDOM()",
            (chat_id, since),
        ).fetchall()
        keys = ("id", "kind", "file_id", "path", "description", "score", "shape", "added_at", "people")
        return [dict(zip(keys, row)) for row in rows]

    def claim_weekly(self, chat_id: int, week: str) -> bool:
        """True — если «итоги недели» для этой недели ещё не собирали (и теперь отмечено, что собираем)."""
        with self.db:
            return self.db.execute("INSERT OR IGNORE INTO weekly_edits VALUES (?, ?)", (chat_id, week)).rowcount == 1

    def media_chats(self) -> list[int]:
        return [r[0] for r in self.db.execute("SELECT DISTINCT chat_id FROM media WHERE chat_id < 0")]

    # --- своё (что прислал сам бот) ---

    def mark_own(self, unique_ids: list[str]) -> None:
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO own_media VALUES (?)", [(u,) for u in unique_ids if u])

    def is_own(self, unique_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM own_media WHERE file_unique_id = ?", (unique_id,)).fetchone() is not None

    def add_own_hash(self, h: int) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO own_hashes VALUES (?)", (h - (1 << 64) if h >= 1 << 63 else h,))

    def looks_own(self, h: int, max_distance: int = 4) -> bool:
        """Похожа ли картинка (по отпечатку) на мем, который отправлял бот."""
        return any(bin((h ^ (row[0] % (1 << 64))) & ((1 << 64) - 1)).count("1") <= max_distance
                   for row in self.db.execute("SELECT hash FROM own_hashes"))

    def forget_media(self, chat_id: int, unique_id: str) -> int:
        with self.db:
            return self.db.execute(
                "DELETE FROM media WHERE chat_id = ? AND file_unique_id = ?", (chat_id, unique_id)
            ).rowcount

    # --- долгая память ---

    def lore(self, chat_id: int) -> dict:
        row = self.db.execute(
            "SELECT text, upto, updated_at, plans_upto, plans_at FROM lore WHERE chat_id = ?", (chat_id,)
        ).fetchone() or ("", 0, 0, 0, 0)
        return dict(zip(("text", "upto", "updated_at", "plans_upto", "plans_at"), row))

    def save_lore(self, chat_id: int, text: str, upto: int) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO lore (chat_id) VALUES (?)", (chat_id,))
            self.db.execute("UPDATE lore SET text = ?, upto = ?, updated_at = ? WHERE chat_id = ?",
                            (text, upto, int(time.time()), chat_id))

    def add_lore_digest(self, chat_id: int, upto: int, first_at: int, last_at: int, text: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO lore_digests VALUES (?, ?, ?, ?, ?)",
                            (chat_id, upto, first_at, last_at, text))

    def lore_digests(self, chat_id: int, after: int = 0) -> list[dict]:
        """Выжимки по порядку (только после after)."""
        rows = self.db.execute(
            "SELECT upto, first_at, last_at, text FROM lore_digests WHERE chat_id = ? AND upto > ? ORDER BY upto",
            (chat_id, after),
        ).fetchall()
        return [dict(zip(("upto", "first_at", "last_at", "text"), r)) for r in rows]

    def save_plans_upto(self, chat_id: int, upto: int) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO lore (chat_id) VALUES (?)", (chat_id,))
            self.db.execute("UPDATE lore SET plans_upto = ?, plans_at = ? WHERE chat_id = ?",
                            (upto, int(time.time()), chat_id))

    def dialog_after(self, chat_id: int, after_id: int, limit: int = 100_000) -> list[tuple[int, str, str, int]]:
        """Строки переписки после after_id по порядку: (id, автор, текст, когда)."""
        return self.db.execute(
            "SELECT id, author, text, added_at FROM dialog WHERE chat_id = ? AND id > ? ORDER BY id LIMIT ?",
            (chat_id, after_id, limit),
        ).fetchall()

    def dialog_tail(self, chat_id: int, count: int) -> list[tuple[int, str, str, int]]:
        """Последние count строк переписки по порядку: (id, автор, текст, когда)."""
        rows = self.db.execute(
            "SELECT id, author, text, added_at FROM dialog WHERE chat_id = ? ORDER BY id DESC LIMIT ?", (chat_id, count)
        ).fetchall()
        return rows[::-1]

    def dialog_count_after(self, chat_id: int, after_id: int) -> int:
        return self.db.execute("SELECT COUNT(*) FROM dialog WHERE chat_id = ? AND id > ?", (chat_id, after_id)).fetchone()[0]

    def dialog_chats(self) -> list[int]:
        """Беседы (не лички), где есть переписка."""
        return [r[0] for r in self.db.execute("SELECT DISTINCT chat_id FROM dialog WHERE chat_id < 0")]

    def add_plan(self, chat_id: int, who: str, about: str, ask_at: int, quote: str = "", context: str = "") -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO plans (chat_id, who, about, ask_at, created_at, quote, context) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (chat_id, who, about, ask_at, int(time.time()), quote, context),
            )

    def open_plans(self, chat_id: int) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, who, about, ask_at, created_at, quote, context FROM plans WHERE chat_id = ? AND status = 'open'"
            " ORDER BY ask_at", (chat_id,),
        ).fetchall()
        return [dict(zip(("id", "who", "about", "ask_at", "created_at", "quote", "context"), r)) for r in rows]

    def set_plan_status(self, plan_id: int, status: str) -> None:
        with self.db:
            self.db.execute("UPDATE plans SET status = ? WHERE id = ?", (status, plan_id))

    def touch_member(self, chat_id: int, user_id: int, nick: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO members (chat_id, user_id, nick, messages, last_seen) VALUES (?, ?, ?, 1, ?)"
                " ON CONFLICT (chat_id, user_id) DO UPDATE SET nick = excluded.nick, messages = messages + 1,"
                " last_seen = excluded.last_seen",
                (chat_id, user_id, nick, int(time.time())),
            )

    def members(self, chat_id: int) -> list[dict]:
        rows = self.db.execute(
            "SELECT user_id, nick, messages, last_seen FROM members WHERE chat_id = ?", (chat_id,)
        ).fetchall()
        return [dict(zip(("user_id", "nick", "messages", "last_seen"), r)) for r in rows]

    def log_initiative(self, chat_id: int, kind: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO initiatives VALUES (?, ?, ?)", (chat_id, kind, int(time.time())))

    def initiatives_since(self, chat_id: int, since: int, kind: str | None = None) -> list[int]:
        """Когда бот писал первым (с since), новые — первыми; kind — только такие («silent:%» — все такого вида)."""
        sql, args = "SELECT at FROM initiatives WHERE chat_id = ? AND at >= ?", [chat_id, since]
        if kind:
            sql, args = sql + " AND kind LIKE ?", args + [kind]
        return [r[0] for r in self.db.execute(sql + " ORDER BY at DESC", args)]

    # --- лица ---

    def add_face(self, chat_id: int, user_id: int, name: str, feature: bytes, keep: int) -> int:
        """Добавляет отпечаток лица (старые сверх keep — выкидывает). Возвращает, сколько их у человека."""
        with self.db:
            self.db.execute("INSERT INTO faces (chat_id, user_id, name, feature, added_at) VALUES (?, ?, ?, ?, ?)",
                            (chat_id, user_id, name, feature, int(time.time())))
            self.db.execute("UPDATE faces SET name = ? WHERE chat_id = ? AND user_id = ?", (name, chat_id, user_id))
            self.db.execute(
                "DELETE FROM faces WHERE chat_id = ? AND user_id = ? AND id NOT IN ("
                " SELECT id FROM faces WHERE chat_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?)",
                (chat_id, user_id, chat_id, user_id, keep),
            )
        return self.db.execute("SELECT COUNT(*) FROM faces WHERE chat_id = ? AND user_id = ?",
                               (chat_id, user_id)).fetchone()[0]

    def face_gallery(self, chat_id: int) -> list[tuple[str, bytes]]:
        return self.db.execute("SELECT name, feature FROM faces WHERE chat_id = ?", (chat_id,)).fetchall()

    def face_people(self, chat_id: int) -> list[tuple[str, int]]:
        return self.db.execute("SELECT name, COUNT(*) FROM faces WHERE chat_id = ? GROUP BY user_id ORDER BY name",
                               (chat_id,)).fetchall()

    def forget_faces(self, chat_id: int, user_id: int) -> int:
        """Стирает лицо человека и отметки «кто на фото» — они пересчитаются без него."""
        with self.db:
            n = self.db.execute("DELETE FROM faces WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)).rowcount
            if n:
                self.db.execute("UPDATE media SET people = NULL, faces_at = NULL WHERE chat_id = ?", (chat_id,))
        return n

    def faces_version(self, chat_id: int) -> int:
        """Когда последний раз кто-то записал лицо: фото, просмотренные раньше, надо пересмотреть."""
        return self.db.execute("SELECT COALESCE(MAX(added_at), 0) FROM faces WHERE chat_id = ?", (chat_id,)).fetchone()[0]

    def media_for_faces(self, chat_id: int, limit: int) -> list[tuple[int, str, str, str | None]]:
        """Фото и видео, где ещё не искали записавшихся (или искали до новой записи): (id, kind, file_id, path)."""
        return self.db.execute(
            "SELECT id, kind, file_id, path FROM media WHERE chat_id = ? AND kind IN ('photo', 'video')"
            " AND (faces_at IS NULL OR faces_at < ?) ORDER BY added_at DESC LIMIT ?",
            (chat_id, self.faces_version(chat_id), limit),
        ).fetchall()

    def set_media_people(self, media_id: int, people: str) -> None:
        with self.db:
            self.db.execute("UPDATE media SET people = ?, faces_at = ? WHERE id = ?", (people, int(time.time()), media_id))

    def set_media_people_by_uid(self, chat_id: int, unique_id: str, people: str) -> None:
        with self.db:
            self.db.execute("UPDATE media SET people = ?, faces_at = ? WHERE chat_id = ? AND file_unique_id = ?",
                            (people, int(time.time()), chat_id, unique_id))

    # --- где можно быть ---

    def chat_access(self, chat_id: int) -> tuple[str, str] | None:
        """(статус, название) беседы или None, если бот о ней ещё не спрашивал."""
        return self.db.execute("SELECT status, title FROM allowed_chats WHERE chat_id = ?", (chat_id,)).fetchone()

    def set_chat_access(self, chat_id: int, status: str, title: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO allowed_chats VALUES (?, ?, ?, ?) ON CONFLICT (chat_id) DO UPDATE SET"
                " status = excluded.status, title = CASE WHEN excluded.title != '' THEN excluded.title ELSE title END,"
                " at = excluded.at",
                (chat_id, title or "", status, int(time.time())),
            )

    def chat_accesses(self) -> list[tuple[int, str, str]]:
        """(chat_id, статус, название) всех бесед, о которых знает бот."""
        return self.db.execute("SELECT chat_id, status, title FROM allowed_chats ORDER BY status, title").fetchall()

    def is_member_of_allowed(self, user_id: int) -> bool:
        return self.db.execute(
            "SELECT 1 FROM members m JOIN allowed_chats a ON a.chat_id = m.chat_id"
            " WHERE m.user_id = ? AND a.status = 'allowed' LIMIT 1", (user_id,)
        ).fetchone() is not None

    # --- характер ---

    def feelings(self, chat_id: int) -> dict | None:
        row = self.db.execute("SELECT mood, why, at FROM feelings WHERE chat_id = ?", (chat_id,)).fetchone()
        return dict(zip(("mood", "why", "at"), row)) if row else None

    def set_feelings(self, chat_id: int, mood: str, why: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO feelings VALUES (?, ?, ?, ?)", (chat_id, mood, why, int(time.time())))

    def relations(self, chat_id: int) -> list[dict]:
        rows = self.db.execute(
            "SELECT name, score, note, updated_at FROM relations WHERE chat_id = ? ORDER BY score DESC", (chat_id,)
        ).fetchall()
        return [dict(zip(("name", "score", "note", "updated_at"), r)) for r in rows]

    def set_relation(self, chat_id: int, name: str, score: int, note: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO relations VALUES (?, ?, ?, ?, ?)",
                            (chat_id, name, score, note, int(time.time())))

    def add_life(self, text: str, status: str = "", at: int | None = None) -> int:
        """Эпизод жизни бота; at — когда он случится (может быть и позже, чем сейчас)."""
        with self.db:
            return self.db.execute("INSERT INTO life (at, text, status) VALUES (?, ?, ?)",
                                   (int(at or time.time()), text, status)).lastrowid

    def life(self, count: int = 10, until: float | None = None) -> list[dict]:
        """Последние эпизоды жизни бота по порядку (только те, что уже случились к until)."""
        rows = self.db.execute("SELECT id, at, text, status FROM life WHERE at <= ? ORDER BY at DESC, id DESC LIMIT ?",
                               (int(until or time.time()), count)).fetchall()
        return [dict(zip(("id", "at", "text", "status"), r)) for r in rows[::-1]]

    def life_day(self, day: str) -> list[tuple[str, str]]:
        row = self.db.execute("SELECT schedule FROM life_days WHERE day = ?", (day,)).fetchone()
        return [tuple(slot) for slot in json.loads(row[0])] if row else []

    def set_life_day(self, day: str, schedule: list[tuple[str, str]]) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO life_days VALUES (?, ?, ?)",
                            (day, json.dumps(schedule, ensure_ascii=False), int(time.time())))

    # --- кто есть кто ---

    def people(self, chat_id: int) -> list[tuple[str, str]]:
        """(ник, имя) для чата."""
        return self.db.execute("SELECT nick, name FROM people WHERE chat_id = ? ORDER BY name, nick", (chat_id,)).fetchall()

    def set_person(self, chat_id: int, nick: str, name: str) -> None:
        with self.db:
            self._drop_person(chat_id, nick)
            self.db.execute("INSERT INTO people VALUES (?, ?, ?)", (chat_id, nick, name))

    def forget_person(self, chat_id: int, nick: str) -> bool:
        with self.db:
            return self._drop_person(chat_id, nick) > 0

    def _drop_person(self, chat_id: int, nick: str) -> int:
        """Ники сравниваем без учёта регистра (SQLite сам так не умеет с кириллицей)."""
        same = [n for n, _ in self.people(chat_id) if n.casefold() == nick.casefold()]
        for n in same:
            self.db.execute("DELETE FROM people WHERE chat_id = ? AND nick = ?", (chat_id, n))
        return len(same)

    # --- фразы ---

    def add_phrase(self, chat_id: int, text: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO phrases (chat_id, text, added_at) VALUES (?, ?, ?)",
                (chat_id, text, int(time.time())),
            )
            self._trim_phrases(chat_id)

    def _trim_phrases(self, chat_id: int) -> None:
        self.db.execute(
            "DELETE FROM phrases WHERE chat_id = ? AND id <= ("
            "  SELECT id FROM phrases WHERE chat_id = ? ORDER BY id DESC LIMIT 1 OFFSET ?)",
            (chat_id, chat_id, MAX_PHRASES_PER_CHAT),
        )

    def phrases(self, chat_id: int) -> list[str]:
        rows = self.db.execute("SELECT text FROM phrases WHERE chat_id = ?", (chat_id,))
        return [r[0] for r in rows]

    def random_phrases(self, chat_id: int, limit: int) -> list[str]:
        rows = self.db.execute(
            "SELECT text FROM phrases WHERE chat_id = ? ORDER BY RANDOM() LIMIT ?", (chat_id, limit)
        )
        return [r[0] for r in rows]

    # --- стикеры ---

    def add_sticker(self, chat_id: int, file_id: str, unique_id: str, emoji: str, set_name: str | None,
                    used: bool, thumb_id: str | None = None, kind: str = "static") -> None:
        """kind: static | animated | video. thumb_id — превью (у анимированных и видео смотрим на него)."""
        with self.db:
            self.db.execute(
                "INSERT INTO stickers (chat_id, file_id, file_unique_id, emoji, set_name, uses, thumb_id, kind)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (chat_id, file_unique_id)"
                " DO UPDATE SET uses = uses + excluded.uses, file_id = excluded.file_id,"
                "   thumb_id = COALESCE(excluded.thumb_id, thumb_id), kind = excluded.kind",
                (chat_id, file_id, unique_id, norm_emoji(emoji), set_name, int(used), thumb_id, kind),
            )
        # Описание уже есть у такого же стикера из другого чата — переиспользуем.
        self.db.execute(
            "UPDATE stickers SET description = (SELECT description FROM stickers"
            "  WHERE file_unique_id = ? AND description IS NOT NULL LIMIT 1)"
            " WHERE chat_id = ? AND file_unique_id = ? AND description IS NULL",
            (unique_id, chat_id, unique_id),
        )
        self.db.commit()

    def undescribed_stickers(self, limit: int) -> list[tuple[str, str, str | None, str]]:
        """Стикеры, которые бот ещё не рассмотрел: (unique_id, file_id, thumb_id, kind); любимые — первыми."""
        return self.db.execute(
            "SELECT file_unique_id, MIN(file_id), MIN(thumb_id), MIN(COALESCE(kind, 'static'))"
            " FROM stickers WHERE description IS NULL"
            " GROUP BY file_unique_id ORDER BY SUM(uses) DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def set_sticker_description(self, unique_id: str, description: str) -> list[int]:
        """Сохраняет описание («» — рассмотреть не вышло). Возвращает чаты, где этот стикер есть."""
        with self.db:
            self.db.execute("UPDATE stickers SET description = ? WHERE file_unique_id = ?", (description, unique_id))
        return [r[0] for r in self.db.execute("SELECT chat_id FROM stickers WHERE file_unique_id = ?", (unique_id,))]

    def sticker_description(self, unique_id: str) -> str | None:
        row = self.db.execute(
            "SELECT description FROM stickers WHERE file_unique_id = ? AND description != '' LIMIT 1", (unique_id,)
        ).fetchone()
        return row[0] if row else None

    def sticker_menu(self, chat_id: int, limit: int = 60) -> list[tuple[str, str, str]]:
        """Рассмотренные стикеры чата для Claude: (file_id, эмодзи, что на нём); любимые — первыми."""
        return self.db.execute(
            "SELECT file_id, emoji, description FROM stickers"
            " WHERE chat_id = ? AND description IS NOT NULL AND description != ''"
            " ORDER BY uses DESC, RANDOM() LIMIT ?",
            (chat_id, limit),
        ).fetchall()

    def new_sticker_set(self, chat_id: int, set_name: str) -> bool:
        """True, если этот пак в чате раньше не встречался (значит, его пора скачать целиком)."""
        with self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO sticker_sets (chat_id, set_name) VALUES (?, ?)", (chat_id, set_name)
            )
        return cur.rowcount == 1

    def sticker_emojis(self, chat_id: int, limit: int = 40) -> list[str]:
        """На какие эмодзи у чата есть стикеры — сначала самые ходовые."""
        rows = self.db.execute(
            "SELECT emoji FROM stickers WHERE chat_id = ? AND emoji != ''"
            " GROUP BY emoji ORDER BY SUM(uses) DESC, COUNT(*) DESC LIMIT ?",
            (chat_id, limit),
        )
        return [r[0] for r in rows]

    def random_sticker(self, chat_id: int, emoji: str) -> str | None:
        rows = self.db.execute(
            "SELECT file_id, uses FROM stickers WHERE chat_id = ? AND emoji = ?", (chat_id, norm_emoji(emoji))
        ).fetchall()
        if not rows:
            return None
        return random.choices([r[0] for r in rows], weights=[r[1] + 1 for r in rows])[0]

    # --- переписка с авторами ---

    def add_dialog(self, chat_id: int, author: str, text: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO dialog (chat_id, author, text, added_at) VALUES (?, ?, ?, ?)",
                (chat_id, author, text, int(time.time())),
            )
            self._trim_dialog(chat_id)

    def _trim_dialog(self, chat_id: int) -> None:
        self.db.execute(
            "DELETE FROM dialog WHERE chat_id = ? AND id <= ("
            "  SELECT id FROM dialog WHERE chat_id = ? ORDER BY id DESC LIMIT 1 OFFSET ?)",
            (chat_id, chat_id, MAX_DIALOG_PER_CHAT),
        )

    def dialog_windows(self, chat_id: int, count: int, size: int) -> list[list[tuple[str, str]]]:
        """Несколько случайных кусков переписки подряд: [(автор, текст), ...]."""
        starts = self.db.execute(
            "SELECT id FROM dialog WHERE chat_id = ? ORDER BY RANDOM() LIMIT ?", (chat_id, count)
        ).fetchall()
        return [
            self.db.execute(
                "SELECT author, text FROM dialog WHERE chat_id = ? AND id >= ? ORDER BY id LIMIT ?",
                (chat_id, start, size),
            ).fetchall()
            for (start,) in starts
        ]

    # --- импорт истории ---

    def imported_ids(self, chat_id: int) -> set[int]:
        rows = self.db.execute("SELECT msg_id FROM imported WHERE chat_id = ?", (chat_id,))
        return {r[0] for r in rows}

    def import_history(
        self,
        chat_id: int,
        msg_ids: list[int],
        phrases: list[tuple[str, int]],  # (текст, unixtime)
        media: list[tuple[str, str, str, int]],  # (kind, unique_id, путь от base, unixtime)
        dialog: list[tuple[int, str, str, int]] = (),  # (msg_id, автор, текст, unixtime)
    ) -> None:
        with self.db:
            # Переписку пишем для всех сообщений, даже уже импортированных: её могло не быть в старой версии.
            self.db.executemany(
                "INSERT OR IGNORE INTO dialog (chat_id, msg_id, author, text, added_at) VALUES (?, ?, ?, ?, ?)",
                [(chat_id, msg_id, author, text, ts) for msg_id, author, text, ts in dialog],
            )
            self._trim_dialog(chat_id)
            self.db.executemany(
                "INSERT INTO phrases (chat_id, text, added_at) VALUES (?, ?, ?)",
                [(chat_id, text, ts) for text, ts in phrases],
            )
            self.db.executemany(
                "INSERT OR IGNORE INTO media (chat_id, kind, file_id, file_unique_id, added_at, path)"
                " VALUES (?, ?, '', ?, ?, ?)",
                [(chat_id, kind, uid, ts, path) for kind, uid, path, ts in media],
            )
            self.db.executemany(
                "INSERT OR IGNORE INTO imported (chat_id, msg_id) VALUES (?, ?)",
                [(chat_id, msg_id) for msg_id in msg_ids],
            )
            self._trim_phrases(chat_id)

    def import_target(self, title: str) -> tuple[int, bool]:
        """(chat_id, привязан ли уже к настоящему чату) для импорта беседы с таким названием."""
        key = normalize_title(title)
        with self.db:
            row = self.db.execute(
                "SELECT chat_id, attached FROM import_targets WHERE title = ?", (key,)
            ).fetchone()
            if row:
                return row[0], bool(row[1])
            temp = TEMP_CHAT_BASE + self.db.execute("SELECT COUNT(*) FROM import_targets").fetchone()[0]
            self.db.execute("INSERT INTO import_targets (title, chat_id) VALUES (?, ?)", (key, temp))
        return temp, False

    def set_import_target(self, title: str, chat_id: int) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO import_targets (title, chat_id, attached) VALUES (?, ?, 1)"
                " ON CONFLICT (title) DO UPDATE SET chat_id = excluded.chat_id, attached = 1",
                (normalize_title(title), chat_id),
            )

    def attach_import(self, chat_id: int, title: str) -> bool:
        """Если для беседы с таким названием ждёт импорт — перевешивает его на chat_id."""
        key = normalize_title(title)
        row = self.db.execute(
            "SELECT chat_id FROM import_targets WHERE title = ? AND attached = 0", (key,)
        ).fetchone()
        if not row:
            return False
        with self.db:
            for table in ("media", "phrases", "imported", "dialog"):
                self.db.execute(
                    f"UPDATE OR IGNORE {table} SET chat_id = ? WHERE chat_id = ?", (chat_id, row[0])
                )
                self.db.execute(f"DELETE FROM {table} WHERE chat_id = ?", (row[0],))  # дубли
            self.db.execute(
                "UPDATE import_targets SET chat_id = ?, attached = 1 WHERE title = ?", (chat_id, key)
            )
            self._trim_phrases(chat_id)
        return True

    # --- настройки чата ---

    def chance(self, chat_id: int) -> float:
        row = self.db.execute("SELECT chance FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        return row[0] if row else self.default_chance

    def set_chance(self, chat_id: int, chance: float) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO chats (chat_id, chance) VALUES (?, ?)"
                " ON CONFLICT (chat_id) DO UPDATE SET chance = excluded.chance",
                (chat_id, chance),
            )

    def try_claim_post(self, chat_id: int, cooldown: int) -> bool:
        """Атомарно проверяет кулдаун и, если он прошёл, «занимает» слот для поста."""
        now = int(time.time())
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO chats (chat_id, chance) VALUES (?, ?)",
                (chat_id, self.default_chance),
            )
            cur = self.db.execute(
                "UPDATE chats SET last_post = ? WHERE chat_id = ? AND last_post <= ?",
                (now, chat_id, now - cooldown),
            )
        return cur.rowcount == 1

    # --- статистика / сброс ---

    def stats(self, chat_id: int) -> dict[str, int]:
        counts = dict(
            self.db.execute(
                "SELECT kind, COUNT(*) FROM media WHERE chat_id = ? GROUP BY kind", (chat_id,)
            ).fetchall()
        )
        counts["phrases"] = self.db.execute(
            "SELECT COUNT(*) FROM phrases WHERE chat_id = ?", (chat_id,)
        ).fetchone()[0]
        return counts

    def forget(self, chat_id: int) -> None:
        local = [r[0] for r in self.db.execute(
            "SELECT path FROM media WHERE chat_id = ? AND path IS NOT NULL", (chat_id,)
        )]
        with self.db:
            for table in ("media", "phrases", "imported", "dialog", "stickers", "sticker_sets", "people", "lore",
                          "lore_digests", "plans", "members", "initiatives", "feelings", "relations", "faces"):
                self.db.execute(f"DELETE FROM {table} WHERE chat_id = ?", (chat_id,))
        # Файлы из импорта удаляем, если на них не ссылается другой чат.
        for path in local:
            if not self.db.execute("SELECT 1 FROM media WHERE path = ?", (path,)).fetchone():
                (self.base / path).unlink(missing_ok=True)
