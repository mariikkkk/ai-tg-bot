"""Цепи Маркова по сообщениям чата — генератор бреда в стиле «Сглыпы»."""

import random
import re
from collections import defaultdict

BEGIN, END = "\x02", "\x03"

URL_RE = re.compile(r"https?://\S+|www\.\S+|t\.me/\S+", re.IGNORECASE)

# Если чат ещё ничего не написал — хоть что-то на мем.
FALLBACK = [
    "когда скинул мем в беседу а все молчат",
    "я просто хотел поспать",
    "ну всё, приехали",
    "это база",
    "жиза",
    "никто:\nабсолютно никто:\nя:",
    "мой последний нерв",
    "понедельник опять",
    "а я говорил",
    "ещё одну серию и спать",
]


def clean(text: str) -> str:
    """Готовит сообщение к обучению: без ссылок, лишних пробелов и слишком длинных простыней."""
    text = URL_RE.sub("", text)
    text = " ".join(text.split())
    return text if 0 < len(text) <= 300 else ""


# Марков не понимает смысла — просто не даём ему склеивать фразы про мам (кодекс бота: мамы — святое).
BANNED = re.compile(r"(?<![а-яё])(мам|мать|матер|матуш)", re.IGNORECASE)


def normalize(word: str) -> str:
    return word.lower().strip(".,!?…:;\"'()«»")


class Chain:
    def __init__(self, texts: list[str], order: int = 1, reverse: bool = False):
        self.order = order
        self.model: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for text in texts:
            words = text.split()
            if reverse:
                words.reverse()
            if not words:
                continue
            seq = [BEGIN] * order + words + [END]
            for i in range(len(seq) - order):
                self.model[tuple(seq[i : i + order])].append(seq[i + order])

    def walk(self, state: tuple[str, ...], max_words: int) -> list[str]:
        """Слова, которые идут после state, пока цепь не дойдёт до конца фразы."""
        out: list[str] = []
        while len(out) < max_words:
            choices = self.model.get(state)
            if not choices:
                break
            word = random.choice(choices)
            if word == END:
                break
            out.append(word)
            state = (*state[1:], word)
        return out

    def generate(self, max_words: int = 14) -> str:
        return " ".join(self.walk((BEGIN,) * self.order, max_words))


class Babbler:
    """Генератор фраз по всем сообщениям одного чата."""

    def __init__(self, texts: list[str]):
        self.texts = texts
        self.originals = set(texts)
        self.forward = Chain(texts, order=1)
        self.backward = Chain(texts, order=1, reverse=True)
        self.chains = [self.forward]
        if len(texts) > 300:
            self.chains.append(Chain(texts, order=2))  # на большом корпусе звучит связнее
        self._vocab: dict[str, list[str]] | None = None

    def _spellings(self, seed: str) -> list[str]:
        """Как слово-затравка встречалось в чате (с регистром и пунктуацией)."""
        if self._vocab is None:
            self._vocab = defaultdict(list)
            for (word,) in self.forward.model:
                if word != BEGIN:
                    self._vocab[normalize(word)].append(word)
        return self._vocab.get(normalize(seed), [])

    def _one(self, seed: str | None) -> str:
        if seed and (spellings := self._spellings(seed)):
            # Растим фразу от слова в обе стороны.
            word = random.choice(spellings)
            left = self.backward.walk((word,), 8)
            right = self.forward.walk((word,), 8)
            return " ".join([*reversed(left), word, *right])
        return random.choice(self.chains).generate()

    def phrase(self, max_chars: int = 90, seed: str | None = None) -> str:
        """Новая фраза; старается не повторять сообщения из чата дословно."""
        if not self.texts:
            return random.choice(FALLBACK)
        best = ""
        for _ in range(25):
            phrase = self._one(seed)
            if not phrase or len(phrase) > max_chars or BANNED.search(phrase):
                continue
            best = best or phrase
            if phrase not in self.originals:
                return phrase
        return best or next((t[:max_chars] for t in random.sample(self.texts, min(50, len(self.texts)))
                             if not BANNED.search(t)), random.choice(FALLBACK))
