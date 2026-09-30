"""Локальное превью мемов без Telegram.

    python preview.py                 # на сгенерированной картинке и тестовом видео
    python preview.py cat.jpg vid.mp4 # на своих файлах

Результат — в папке preview/.
"""

import asyncio
import io
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

import markov
import render
import video

OUT = Path(__file__).parent / "preview"

SAMPLE_CHAT = """
ну и кто опять съел мою шаурму
я вообще-то на диете с понедельника
пацаны завтра идём на шашлыки или как
кто последний раз видел Серёгу
Серёга опять уснул в маршрутке
понедельник опять начался без меня
шашлыки отменяются идёт дождь
я просто хотел поспать а тут вы
кто скинет домашку тот красавчик
завтра точно начну бегать
моя шаурма была лучшим что случилось за неделю
""".strip().splitlines()


def sample_image() -> bytes:
    img = Image.new("RGB", (800, 600))
    draw = ImageDraw.Draw(img)
    for y in range(600):
        draw.line([(0, y), (800, y)], fill=(40 + y // 5, 90, 200 - y // 4))
    draw.ellipse((250, 150, 550, 450), fill=(255, 200, 60))
    draw.ellipse((330, 250, 370, 290), fill="black")
    draw.ellipse((430, 250, 470, 290), fill="black")
    draw.arc((320, 300, 480, 400), 20, 160, fill="black", width=8)
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def sample_video() -> bytes:
    path = OUT / "_sample.mp4"
    subprocess.run(
        [video.FFMPEG, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )
    data = path.read_bytes()
    path.unlink()
    return data


async def main(files: list[str]) -> None:
    OUT.mkdir(exist_ok=True)
    babbler = markov.Babbler(SAMPLE_CHAT)
    images = [Path(f).read_bytes() for f in files if not f.lower().endswith((".mp4", ".mov", ".gif"))]
    videos = [Path(f).read_bytes() for f in files if f.lower().endswith((".mp4", ".mov", ".gif"))]
    if not files:
        images, videos = [sample_image()], [sample_video()]

    for i, data in enumerate(images):
        for style in render.STYLES:
            for fry in (False, True) if style == "classic" else (False,):
                out = render.meme_image(data, style, babbler.phrase(60), babbler.phrase(60), fry)
                name = OUT / f"img{i}_{style}{'_fried' if fry else ''}.jpg"
                name.write_bytes(out)
                print("→", name)

    for i, data in enumerate(videos):
        for style in video.STYLES:
            out = await video.meme_video(data, style, babbler.phrase(60), babbler.phrase(80))
            name = OUT / f"vid{i}_{style}.mp4"
            name.write_bytes(out)
            print("→", name)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
