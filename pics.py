"""Картинки из интернета — поиск по Яндекс Картинкам через Yandex Search API (тот же индекс, что у @pic).

Ключ создаётся в Yandex AI Studio кнопкой «Создать API-ключ» (роль для поиска выдаётся сама).
Поиск картинок стоит 915 ₽ за 1000 запросов.
"""

import base64
import io
import logging
import os
import random
import ssl
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import aiohttp
import certifi
from PIL import Image, ImageOps

log = logging.getLogger("memebot.pics")

API_URL = "https://searchapi.api.cloud.yandex.net/v2/image/search"
# Умеренный семейный фильтр (по умолчанию у Яндекса): без «взрослого», если его не искать специально.
FAMILY_MODE = os.getenv("YANDEX_FAMILY_MODE", "FAMILY_MODE_MODERATE")
RESULTS = 20
MAX_IMAGE_BYTES = 10 * 1024 * 1024
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


# Python с python.org на маке не видит системные сертификаты — берём набор certifi, как aiogram.
_SSL = ssl.create_default_context(cafile=certifi.where())


def _session(timeout: float) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout), connector=aiohttp.TCPConnector(ssl=_SSL)
    )


def _key() -> str:
    return os.getenv("YANDEX_SEARCH_API_KEY", "").strip()


def _folder() -> str:
    return os.getenv("YANDEX_FOLDER_ID", "").strip()


def available() -> bool:
    return bool(_key() and _folder())


@dataclass
class Found:
    original: str | None  # ссылка на картинку на исходном сайте
    thumb: str | None     # превью с серверов Яндекса — мелкое, но открывается всегда
    width: int = 0        # размер оригинала
    height: int = 0


def parse_results(xml: bytes) -> list[Found]:
    """Картинки из XML-выдачи. Схема XML в документации не описана («может меняться без уведомления»),
    поэтому поля берём по именам и не падаем, если какого-то нет."""
    results = []
    for doc in ET.fromstring(xml).iter("doc"):
        fields = {el.tag.lower(): (el.text or "").strip() for el in doc.iter() if (el.text or "").strip()}
        original = fields.get("image-link")
        thumb = fields.get("thumbnail-link")
        if thumb:
            thumb += "&n=13"  # по умолчанию превью 150 px, так — 320 px
        if original or thumb:
            size = [int(v) if (v := fields.get(k, "")).isdigit() else 0 for k in ("original-width", "original-height")]
            results.append(Found(original, thumb, *size))
    return results


async def search(query: str) -> list[Found]:
    body = {
        "query": {"searchType": "SEARCH_TYPE_RU", "queryText": query[:400], "familyMode": FAMILY_MODE},
        "docsOnPage": str(RESULTS),
        "folderId": _folder(),
    }
    headers = {"Authorization": f"Api-Key {_key()}"}
    async with _session(20) as session:
        async with session.post(API_URL, json=body, headers=headers) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Yandex Search API: HTTP {resp.status}: {(await resp.text())[:300]}")
            data = await resp.json()
    return parse_results(base64.b64decode(data["rawData"]))


def _to_jpeg(data: bytes, min_side: int) -> bytes | None:
    """Проверяет, что это нормальная картинка, и приводит к JPEG до 1280 px."""
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        img.seek(0)  # у гифок — первый кадр
    except Exception:  # noqa: BLE001
        return None
    if min(img.size) < min_side:
        return None
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, "white")
        img = Image.alpha_composite(bg, img)
    img = img.convert("RGB")
    img.thumbnail((1280, 1280))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


async def _download(session: aiohttp.ClientSession, url: str) -> bytes | None:
    try:
        async with session.get(url, headers={"User-Agent": BROWSER_UA}) as resp:
            if resp.status != 200:
                return None
            chunks, size = [], 0
            async for chunk in resp.content.iter_chunked(1 << 16):  # дочитываем до конца
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except Exception:  # noqa: BLE001 — чужие сайты бывают медленными и кривыми, берём следующую
        return None


async def find_picture(query: str) -> bytes | None:
    """Картинка по запросу (JPEG) или None. Случайная из первых подходящих — чтобы не повторяться."""
    results = (await search(query))[:12]
    if not results:
        return None
    big = [r for r in results if min(r.width, r.height) >= 400]
    candidates = (big or results)[:8]
    random.shuffle(candidates)
    async with _session(10) as session:
        for found in candidates[:5]:
            # Оригинал лучше по качеству; превью с серверов Яндекса — надёжнее.
            for url, min_side in ((found.original, 300), (found.thumb, 150)):
                if url and (data := await _download(session, url)) and (jpeg := _to_jpeg(data, min_side)):
                    return jpeg
    log.warning("По запросу «%s» не скачалась ни одна картинка", query)
    return None


# Общее с другими модулями, которые ходят в Yandex Search API тем же ключом (web.py).
api_key, folder_id, https_session = _key, _folder, _session
