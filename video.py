"""Мемы из видео и гифок: текст рисуем Pillow в PNG, а ffmpeg клеит его с видео."""

import asyncio
import shutil
import tempfile
from pathlib import Path

import render

STYLES = ("demotivator", "caption")

VIDEO_WIDTH = 480  # хватает для чата и быстро кодируется
MAX_SECONDS = 60
TIMEOUT = 180

# Системный ffmpeg, если есть, иначе тот, что приехал с pip-пакетом imageio-ffmpeg.
try:
    FFMPEG = shutil.which("ffmpeg") or __import__("imageio_ffmpeg").get_ffmpeg_exe()
except Exception:  # noqa: BLE001
    FFMPEG = None

# Кодирование тяжёлое — не больше одного ролика за раз.
_encode_lock = asyncio.Semaphore(1)


def _layout(style: str, text1: str, text2: str):
    """Возвращает (картинку-оверлей, filter_complex)."""
    vw = VIDEO_WIDTH
    if style == "demotivator":
        pad, gap, line = render.dem_geometry(vw)
        outer = pad - gap - line
        block = render.dem_block(vw + 2 * pad, pad, text1, text2)
        graph = (
            f"[0:v]scale={vw}:-2,setsar=1,"
            f"pad=iw+{2 * gap}:ih+{2 * gap}:{gap}:{gap}:black,"
            f"pad=iw+{2 * line}:ih+{2 * line}:{line}:{line}:white,"
            f"pad=iw+{2 * outer}:ih+{outer + block.height}:{outer}:{outer}:black[bg];"
            f"[bg][1:v]overlay=0:main_h-overlay_h,format=yuv420p[v]"
        )
        return block, graph
    if style == "caption":
        bar = render.caption_bar(vw, text1, vw * 0.6)
        graph = (
            f"[0:v]scale={vw}:-2,setsar=1,pad=iw:ih+{bar.height}:0:{bar.height}:white[bg];"
            f"[bg][1:v]overlay=0:0,format=yuv420p[v]"
        )
        return bar, graph
    raise ValueError(f"unknown video style: {style}")


async def frame(data: bytes) -> bytes:
    """Характерный кадр из видео (JPEG) — чтобы Claude понял, что там, и придумал подпись."""
    if not FFMPEG:
        raise RuntimeError("ffmpeg не найден")
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp, "in"), Path(tmp, "frame.jpg")
        src.write_bytes(data)
        proc = await asyncio.create_subprocess_exec(
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
            "-vf", "thumbnail=50,scale=768:-2", "-frames:v", "1", str(dst),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), 60)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("ffmpeg не вытащил кадр вовремя")
        if proc.returncode != 0 or not dst.exists():
            raise RuntimeError(f"ffmpeg не вытащил кадр: {err.decode(errors='replace')[-300:]}")
        return dst.read_bytes()


async def meme_video(data: bytes, style: str, text1: str, text2: str = "") -> bytes:
    if not FFMPEG:
        raise RuntimeError("ffmpeg не найден: поставь ffmpeg или pip install imageio-ffmpeg")

    overlay, graph = await asyncio.to_thread(_layout, style, text1, text2)
    with tempfile.TemporaryDirectory() as tmp:
        src, png, dst = Path(tmp, "in"), Path(tmp, "overlay.png"), Path(tmp, "out.mp4")
        src.write_bytes(data)
        overlay.save(png)
        cmd = [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-t", str(MAX_SECONDS), "-i", str(src),
            "-i", str(png),
            "-filter_complex", graph,
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(dst),
        ]
        async with _encode_lock:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
            )
            try:
                _, err = await asyncio.wait_for(proc.communicate(), TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise RuntimeError("ffmpeg не уложился в таймаут")
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg упал: {err.decode(errors='replace')[-500:]}")
        return dst.read_bytes()
