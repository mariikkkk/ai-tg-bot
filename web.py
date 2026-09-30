"""Свежие мемы из интернета: Мемепедия (RSS) и веб-поиск Яндекса (тем же ключом, что картинки).

Знания Claude о мемах заканчиваются датой его обучения — отсюда бот узнаёт, что в тренде сейчас.
Веб-поиск стоит 488 ₽ за 1000 запросов (~0,5 ₽ за запрос), RSS — бесплатно.
"""

import base64
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import pics

SEARCH_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"
MEMEPEDIA_FEED = "https://memepedia.ru/feed/"
RSS_UA = "MemeBot/1.0 (RSS reader)"
_TAGS = re.compile(r"<[^>]+>")


@dataclass
class Snippet:
    title: str
    url: str
    text: str

    def line(self) -> str:
        return f"— {self.title}: {self.text}" if self.text else f"— {self.title}"


def _clean(raw: str) -> str:
    return " ".join(html.unescape(_TAGS.sub(" ", raw or "")).split())


def available() -> bool:
    return pics.available()


async def search(query: str, results: int = 8) -> list[Snippet]:
    """Веб-поиск Яндекса: заголовки и фрагменты текста с найденных страниц."""
    body = {
        "query": {"searchType": "SEARCH_TYPE_RU", "queryText": query[:400], "familyMode": "FAMILY_MODE_MODERATE"},
        "folderId": pics.folder_id(),
        "responseFormat": "FORMAT_XML",
        "groupSpec": {"groupsOnPage": str(results)},
    }
    async with pics.https_session(20) as session:
        async with session.post(SEARCH_URL, json=body, headers={"Authorization": f"Api-Key {pics.api_key()}"}) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Yandex Search API: HTTP {resp.status}: {(await resp.text())[:300]}")
            data = await resp.json()
    snippets = []
    for doc in ET.fromstring(base64.b64decode(data["rawData"])).iter("doc"):
        title = _clean("".join(doc.find("title").itertext())) if doc.find("title") is not None else ""
        text = " … ".join(_clean("".join(p.itertext())) for p in doc.iter("passage"))
        snippets.append(Snippet(title, doc.findtext("url") or "", text[:400]))
    return snippets[:results]


async def memepedia(pages: int = 3) -> list[Snippet]:
    """Свежие статьи Мемепедии: заголовок, короткое описание и теги (по 10 на страницу RSS)."""
    found = []
    async with pics.https_session(20) as session:
        for page in range(1, pages + 1):
            url = MEMEPEDIA_FEED + (f"?paged={page}" if page > 1 else "")
            # Браузерам Мемепедия отдаёт HTML, читалкам — RSS (почему-то с кодом 302 и RSS прямо в теле).
            async with session.get(url, headers={"User-Agent": RSS_UA}, allow_redirects=False) as resp:
                if resp.status not in (200, 302) or "xml" not in resp.headers.get("Content-Type", ""):
                    break
                xml = await resp.read()
            for item in ET.fromstring(xml).iter("item"):
                tags = ", ".join(c.text for c in item.findall("category") if c.text)
                text = _clean(item.findtext("description") or "")[:350]
                found.append(Snippet(_clean(item.findtext("title") or ""), item.findtext("link") or "",
                                     f"{text} [{tags}]" if tags else text))
    return found
