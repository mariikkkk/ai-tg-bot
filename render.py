"""Рендер мемов на картинках (Pillow).

Стили:
  demotivator — чёрная рамка, белая обводка, крупный заголовок и подпись снизу;
  classic     — белый Impact с чёрной обводкой сверху и снизу;
  caption     — белая плашка с текстом над картинкой.
"""

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

STYLES = ("demotivator", "classic", "caption")

FONT_DIR = Path(__file__).parent / "fonts"

# Сначала ищем fonts/<вид>.ttf (можно подложить свой шрифт), потом системные.
# Нужны шрифты с кириллицей.
FONT_CANDIDATES = {
    "impact": [
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/Library/Fonts/Impact.ttf",
        "C:/Windows/Fonts/impact.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "serif": [
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "C:/Windows/Fonts/times.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    ],
    "sans": [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "sans_bold": [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
}


@lru_cache
def _font_path(kind: str) -> str:
    for path in [FONT_DIR / f"{kind}.ttf", *map(Path, FONT_CANDIDATES[kind])]:
        if path.is_file():
            return str(path)
    raise RuntimeError(
        f"Не нашёл шрифт «{kind}» с кириллицей. Положи любой .ttf в fonts/{kind}.ttf"
    )


@lru_cache(maxsize=256)
def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_font_path(kind), size)


def _line_height(font: ImageFont.FreeTypeFont, spacing: float) -> int:
    ascent, descent = font.getmetrics()
    return int((ascent + descent) * spacing)


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            candidate = f"{cur} {word}".strip()
            if font.getlength(candidate) <= max_w:
                cur = candidate
                continue
            if cur:
                lines.append(cur)
            # Слово шире строки — режем по буквам.
            while len(word) > 1 and font.getlength(word) > max_w:
                cut = len(word) - 1
                while cut > 1 and font.getlength(word[:cut]) > max_w:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            cur = word
        lines.append(cur)
    return lines


def _fit(text, kind, max_w, max_h, start, minimum, spacing=1.1):
    """Подбирает самый крупный кегль, при котором текст влезает в прямоугольник."""
    size = int(start)
    while True:
        font = _font(kind, size)
        lines = _wrap(text, font, max_w)
        line_h = _line_height(font, spacing)
        if len(lines) * line_h <= max_h or size <= minimum:
            return font, lines, line_h
        size = max(minimum, int(size * 0.9))


def _draw_lines(draw, lines, font, line_h, cx, y, fill, stroke=0, stroke_fill=None):
    for line in lines:
        draw.text(
            (cx, y), line, font=font, fill=fill, anchor="ma",
            stroke_width=stroke, stroke_fill=stroke_fill,
        )
        y += line_h


def _load(data: bytes) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, "white")
        img = Image.alpha_composite(bg, img)
    img = img.convert("RGB")
    scale = 900 / max(img.size)
    if img.width * scale < 450:
        scale = 450 / img.width
    if abs(scale - 1) > 0.02:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    return img


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:]


# --- блоки текста (их же использует видео) ---


def dem_geometry(img_w: int) -> tuple[int, int, int]:
    """Отступ до рамки, чёрный зазор, толщина белой линии. Всё чётное — для видео."""
    even = lambda v: max(2, int(v) // 2 * 2)
    return even(img_w * 0.09), even(img_w / 110), even(img_w / 260)


def dem_block(width: int, pad: int, title: str, subtitle: str) -> Image.Image:
    """Нижняя часть демотиватора: заголовок и подпись на чёрном."""
    t_font, t_lines, t_lh = _fit(
        _upper_first(title), "serif", width * 0.9, width * 0.32, width / 11, 20
    )
    top, gap, bottom = int(pad * 0.35), int(pad * 0.15), int(pad * 0.6)
    height = top + t_lh * len(t_lines) + bottom
    if subtitle:
        s_font, s_lines, s_lh = _fit(subtitle, "sans", width * 0.88, width * 0.2, width / 26, 14)
        height += gap + s_lh * len(s_lines)

    block = Image.new("RGB", (width, height + height % 2), "black")
    draw = ImageDraw.Draw(block)
    y = top
    _draw_lines(draw, t_lines, t_font, t_lh, width / 2, y, "white")
    if subtitle:
        y += t_lh * len(t_lines) + gap
        _draw_lines(draw, s_lines, s_font, s_lh, width / 2, y, "white")
    return block


def caption_bar(width: int, text: str, max_h: float) -> Image.Image:
    """Белая плашка с чёрным жирным текстом (стиль «когда...»)."""
    font, lines, lh = _fit(text, "sans_bold", width * 0.92, max_h, width / 13, 16, spacing=1.15)
    pad = int(width * 0.045)
    height = lh * len(lines) + 2 * pad
    bar = Image.new("RGB", (width, height + height % 2), "white")
    _draw_lines(ImageDraw.Draw(bar), lines, font, lh, width / 2, pad, "black")
    return bar


# --- стили ---


def demotivator(img: Image.Image, title: str, subtitle: str = "") -> Image.Image:
    if img.width != 600 or img.height > 700:
        scale = min(600 / img.width, 700 / img.height)
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    pad, gap, line = dem_geometry(img.width)
    frame = gap + line
    width = img.width + 2 * pad
    block = dem_block(width, pad, title, subtitle)

    canvas = Image.new("RGB", (width, pad + img.height + frame + block.height), "black")
    canvas.paste(img, (pad, pad))
    ImageDraw.Draw(canvas).rectangle(
        (pad - frame, pad - frame, pad + img.width + frame - 1, pad + img.height + frame - 1),
        outline="white", width=line,
    )
    canvas.paste(block, (0, pad + img.height + frame))
    return canvas


def classic(img: Image.Image, top: str, bottom: str = "") -> Image.Image:
    img = img.copy()
    w, h = img.size
    draw = ImageDraw.Draw(img)
    margin = int(h * 0.03)
    for text, at_top in ((top, True), (bottom, False)):
        if not text:
            continue
        font, lines, lh = _fit(text.upper(), "impact", w * 0.94, h * 0.3, w / 9, 18, spacing=1.05)
        stroke = max(2, font.size // 14)
        y = margin if at_top else h - margin - lh * len(lines)
        _draw_lines(draw, lines, font, lh, w / 2, y, "white", stroke, "black")
    return img


def caption(img: Image.Image, text: str, _unused: str = "") -> Image.Image:
    bar = caption_bar(img.width, text, img.height * 0.6)
    canvas = Image.new("RGB", (img.width, bar.height + img.height), "white")
    canvas.paste(bar, (0, 0))
    canvas.paste(img, (0, bar.height))
    return canvas


def deep_fry(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Color(img).enhance(2.8)
    img = ImageEnhance.Contrast(img).enhance(1.6)
    img = ImageEnhance.Sharpness(img).enhance(5)
    for quality in (10, 6):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        buf.seek(0)
        img = Image.open(buf).convert("RGB")
    return img


RENDERERS = {"demotivator": demotivator, "classic": classic, "caption": caption}


def meme_image(data: bytes, style: str, text1: str, text2: str = "", fry: bool = False) -> bytes:
    img = RENDERERS[style](_load(data), text1, text2)
    if fry:
        img = deep_fry(img)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


def fingerprint(data: bytes) -> int:
    """64-битный «отпечаток» картинки (dHash): почти не меняется от пережатия и уменьшения,
    поэтому свой мем бот узнает, даже если его сохранили и залили заново."""
    img = Image.open(io.BytesIO(data)).convert("L").resize((9, 8), Image.LANCZOS)
    px = list(img.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits
