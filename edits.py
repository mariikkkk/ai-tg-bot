"""Тикток-эдиты из фоток, видео и кружочков чата: нарезка в бит, зумы, вспышки, тряска, велосити.

Всё монтирует ffmpeg локально: каждый «слот» рендерится отдельным клипом 720×1280, потом клипы
склеиваются, сверху — подписи (Pillow) и трек, нарезанный с дропа.
"""

import asyncio
import io
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

import render
import video

W, H, FPS = 720, 1280, 30


# --- трек: темп, удары, дроп ---

@dataclass
class Beats:
    period: float        # секунд между ударами
    drop: float          # время самого мощного удара (начало дропа)
    duration: float


def _onsets(path: Path, sr: int = 22050, hop: int = 512, win: int = 1024):
    """«Ударность» звука по кадрам (spectral flux) и громкость — всё, что нужно для поиска бита и дропа."""
    pcm = subprocess.run([video.FFMPEG, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(pcm, dtype=np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop]
    spec = np.abs(np.fft.rfft(frames * np.hanning(win), axis=1))
    flux = np.maximum(np.diff(np.log1p(spec), axis=0), 0).sum(axis=1)
    return (flux - flux.mean()) / (flux.std() + 1e-9), sr / hop, x, sr


def _alignment(flux: np.ndarray, lag: float, lo: int, hi: int) -> tuple[float, float]:
    """Насколько сетка ударов с шагом lag попадает в реальные удары на отрезке [lo, hi): (сила, лучшая фаза)."""
    best = (-9.0, 0.0)
    for phase in np.arange(0, lag, 1.0):
        idx = np.round(np.arange(lo + phase, hi - 1, lag)).astype(int)
        idx = idx[idx < len(flux) - 1]  # округление не должно вылезать за конец звука
        if len(idx) < 4:
            continue
        score = float(np.maximum(flux[idx], flux[idx + 1]).mean())
        if score > best[0]:
            best = (score, lo + phase)
    return best


def analyze_track(path: Path) -> Beats:
    """Темп и дроп. Темп подбирается точно (до сотых BPM) по тому куску, что пойдёт в эдит:
    ошибка в четверть удара в минуту за 15 секунд уводит нарезку с бита."""
    flux, fps, x, sr = _onsets(path)
    duration = len(x) / sr
    # Дроп — где громкость сильнее всего выросла; ищем там, где после него хватит трека на эдит.
    rms = np.sqrt(np.convolve(x ** 2, np.ones(sr // 4) / (sr // 4), "valid"))[:: sr // 4]
    jump = rms[8:] - np.maximum(rms[4:-4], rms[:-8])
    times = (np.arange(len(jump)) + 8) / 4
    room = (times >= 1.5) & (times <= duration - 12)
    drop_guess = float(times[room][np.argmax(jump[room])]) if room.any() else min(1.5, duration / 4)
    lo = max(0, int((drop_guess - 4) * fps))
    hi = min(len(flux), int((drop_guess + 16) * fps))
    # Грубо: «гребёнка» по автокорреляции (удары совпадают и на кратных интервалах).
    ac = np.correlate(flux, flux, "full")[len(flux) - 1:]
    comb = lambda bpm: sum(ac[int(round(k * 60 * fps / bpm))] / k for k in range(1, 5)
                           if int(round(k * 60 * fps / bpm)) < len(ac))
    coarse = max(np.arange(70, 180, 0.25), key=comb)
    # Точно: кандидаты (темп и его половина/двойка/полуторка) уточняем по попаданию в удары у дропа.
    best = (-9.0, 0.0, 0.0)
    for bpm in {coarse, coarse * 2, coarse / 2, coarse * 1.5, coarse / 1.5}:
        if not 70 <= bpm <= 180:
            continue
        for fine in np.arange(bpm * 0.985, bpm * 1.015, 0.02):
            score, phase = _alignment(flux, 60 * fps / fine, lo, hi)
            if score > best[0]:
                best = (score, fine, phase)
    _, bpm, phase = best
    lag = 60 * fps / bpm
    while lag / fps > 0.75:  # резать удобно на 0,35–0,75 с: медленный бит дробим, быстрый берём через раз
        lag /= 2
    while lag / fps < 0.35:
        lag *= 2
    beat_times = np.arange(phase % lag, len(flux), lag) / fps
    drop = float(beat_times[np.argmin(abs(beat_times - drop_guess))])
    return Beats(lag / fps, drop, duration)


# --- слоты монтажа ---

@dataclass
class Slot:
    src: Path
    kind: str                  # photo | video | circle (кружочек)
    beats: float               # длительность в ударах
    fx: list[str] = field(default_factory=list)   # punch, zoomin, zoomout, shake, flash, glitch, bw, velocity
    start: float = 0.0         # откуда брать кусок видео


def _compose(kind: str) -> str:
    """Кадр 720×1280: фото и видео — на весь экран, кружочек — кругом на размытом фоне из себя же."""
    cover = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if kind != "circle":
        return f"[0:v]{cover},setsar=1[base]"
    d = 600
    return (
        f"[0:v]split[a][b];[a]{cover},gblur=sigma=30,eq=brightness=-0.25[bg];"
        f"[b]scale={d}:{d},format=yuva420p,"
        f"geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='if(lte(hypot(X-{d / 2},Y-{d / 2}),{d / 2 - 2}),255,0)'[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2-60,setsar=1[base]"
    )


def _speed_ramp(dur: float, start: float) -> tuple[str, float]:
    """Велосити: быстро → замедление → быстро. Возвращает (фильтр, сколько секунд исходника нужно)."""
    parts = [(0.35 * dur, 2.2), (0.45 * dur, 0.35), (0.20 * dur, 2.2)]  # (длительность на выходе, скорость)
    chains, t = ["[0:v]split=3[s0][s1][s2]"], start
    for i, (out, speed) in enumerate(parts):
        src = out * speed
        chains.append(f"[s{i}]trim=start={t:.3f}:duration={src:.3f},setpts=(PTS-STARTPTS)/{speed}[v{i}]")
        t += src
    return ";".join(chains) + ";[v0][v1][v2]concat=n=3:v=1:a=0", t - start


def render_slot(slot: Slot, period: float, out: Path, frames: int | None = None) -> None:
    """frames — точная длина в кадрах (по общей сетке эдита, чтобы округление не копилось и нарезка не уезжала от бита)."""
    frames = frames or max(1, round(slot.beats * period * FPS))
    dur = frames / FPS
    inputs = ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(slot.src)] if slot.kind == "photo" else \
             ["-ss", f"{slot.start:.3f}", "-i", str(slot.src)]
    graph = _compose(slot.kind)
    chain = "[base]"
    if "velocity" in slot.fx and slot.kind != "photo":
        ramp, _ = _speed_ramp(dur, 0)
        graph = graph.replace("[0:v]", "[r]", 1)
        graph = ramp + "[r];" + graph
    steps = [f"fps={FPS}", f"trim=duration={dur:.3f}", "setpts=PTS-STARTPTS"]
    if "punch" in slot.fx:     # удар: резко приблизили и отпускаем
        steps.append(f"zoompan=z='1.28-0.28*min(on/7,1)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={W}x{H}:fps={FPS}")
    elif "zoomin" in slot.fx:  # медленный наезд
        steps.append(f"zoompan=z='1+0.18*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={W}x{H}:fps={FPS}")
    elif "zoomout" in slot.fx:
        steps.append(f"zoompan=z='1.2-0.2*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={W}x{H}:fps={FPS}")
    if "shake" in slot.fx:
        steps.append(f"crop={W - 48}:{H - 84}:x='24+20*sin(t*43)':y='42+24*cos(t*51)',scale={W}:{H}")
    if "glitch" in slot.fx:
        steps.append("rgbashift=rh=-10:bh=10:gv=4")
    if "bw" in slot.fx:
        steps.append("hue=s=0,eq=contrast=1.45:brightness=-0.03")
    else:  # фонк-цветокор: темно, контрастно, чуть холодно
        steps.append("eq=contrast=1.25:saturation=0.85:brightness=-0.04,colorbalance=bs=0.08:rs=-0.03")
    steps.append("vignette=PI/4.5")
    if "flash" in slot.fx:
        steps.append("fade=t=in:st=0:d=0.14:color=white")
    steps += [f"tpad=stop_mode=clone:stop={frames}", f"trim=end_frame={frames}", f"setpts=N/{FPS}/TB",
              "format=yuv420p"]
    graph += f";{chain}{','.join(steps)}[v]"
    subprocess.run([video.FFMPEG, "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[v]",
                    "-an", "-r", str(FPS), "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "20", str(out)], check=True)


def _count_frames(path: Path) -> int:
    info = subprocess.run([video.FFMPEG, "-hide_banner", "-i", str(path), "-map", "0:v", "-c", "copy", "-f", "null", "-"],
                          capture_output=True, text=True).stderr
    found = re.findall(r"frame=\s*(\d+)", info)
    return int(found[-1]) if found else 0


# --- подписи ---

@dataclass
class Caption:
    text: str
    start: float
    end: float


def caption_png(text: str, path: Path) -> None:
    """Прозрачный кадр с подписью в стиле тиктока: белый жирный текст с обводкой, чуть ниже центра."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font, lines, lh = render._fit(text, "impact", W * 0.86, H * 0.25, 76, 34, spacing=1.08)
    y = int(H * 0.70) - lh * len(lines) // 2
    render._draw_lines(draw, lines, font, lh, W / 2, y, "white", max(3, font.size // 12), "black")
    img.save(path)


# --- сборка ---

def build_edit(slots: list[Slot], track: Path, beats: Beats, captions: list[Caption], out: Path,
               lead_beats: float) -> Path:
    """lead_beats — сколько ударов до дропа идёт раскачка (с неё начинается эдит)."""
    total_beats = sum(s.beats for s in slots)
    duration = total_beats * beats.period
    track_start = max(0.0, beats.drop - lead_beats * beats.period)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        parts, beat_pos = [], 0.0
        for i, slot in enumerate(slots):
            first = round(beat_pos * beats.period * FPS)
            beat_pos += slot.beats
            part = tmp / f"slot{i:02}.mp4"
            want = round(beat_pos * beats.period * FPS) - first
            render_slot(slot, beats.period, part, frames=want)
            if (got := _count_frames(part)) != want:  # иначе нарезка молча уедет от бита
                raise RuntimeError(f"кусок {i} ({slot.src.name}) вышел {got} кадров вместо {want}")
            parts.append(part)
        (tmp / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run([video.FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
                        "-c", "copy", str(tmp / "cut.mp4")], check=True)
        inputs = ["-i", str(tmp / "cut.mp4"), "-ss", f"{track_start:.3f}", "-t", f"{duration:.3f}", "-i", str(track)]
        graph, last = [], "[0:v]"
        for i, cap in enumerate(captions):
            png = tmp / f"cap{i}.png"
            caption_png(cap.text, png)
            # Зацикленный поток на всю длину: однокадровая картинка к середине эдита для ffmpeg уже «кончилась».
            inputs += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(png)]
            graph.append(f"{last}[{i + 2}:v]overlay=0:0:enable='between(t,{cap.start:.2f},{cap.end:.2f})'[c{i}]")
            last = f"[c{i}]"
        graph.append(f"{last}fade=t=out:st={duration - 0.5:.2f}:d=0.5,format=yuv420p[v]")
        graph.append(f"[1:a]afade=t=out:st={duration - 0.8:.2f}:d=0.8[a]")
        subprocess.run([video.FFMPEG, "-y", "-v", "error", *inputs, "-filter_complex", ";".join(graph),
                        "-map", "[v]", "-map", "[a]", "-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", str(out)], check=True)
    return out


async def build_edit_async(*args, **kwargs) -> Path:
    return await asyncio.to_thread(build_edit, *args, **kwargs)


# --- раскладка «фонк-велосити» ---

@dataclass
class Clip:
    path: Path
    kind: str        # photo | video | circle
    duration: float  # у фото 0
    activity: list[float] = field(default_factory=list)  # насколько живой кадр, каждые ACTIVITY_STEP секунд


ACTIVITY_STEP = 0.25
DULL = 14  # ниже — тёмный или скучный кадр (белый лист, экран с текстом): в эдит, если есть из чего выбрать, не берём


def activity(path: Path) -> list[float]:
    """Насколько живое видео каждые 0,25 с. Главное — детали в кадре (лица, люди, движ дают ~25–30,
    лист бумаги, экран с текстом, темнота — меньше 10), немного — контраст и движение (трясущаяся камера
    тоже движение, поэтому с потолком)."""
    w, h = 48, 84
    raw = subprocess.run([video.FFMPEG, "-v", "error", "-i", str(path), "-an", "-vf",
                          f"fps={1 / ACTIVITY_STEP},crop=iw*0.7:ih*0.7,scale={w}:{h},format=gray",  # центр — внутри кружочка
                          "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    n = len(raw) // (w * h)
    if not n:
        return []
    frames = np.frombuffer(raw[:n * w * h], np.uint8).reshape(n, h, w).astype(np.float32)
    detail = np.abs(np.diff(frames, axis=1)).mean((1, 2)) + np.abs(np.diff(frames, axis=2)).mean((1, 2))
    motion = np.r_[0, np.abs(np.diff(frames, axis=0)).mean((1, 2))]
    return (detail + 0.3 * frames.std((1, 2)) + 0.15 * np.minimum(motion, 60)).round(1).tolist()


def photo_activity(path: Path) -> list[float]:
    """То же для фото — одно число (без движения, поэтому чуть ниже, чем у такого же видео)."""
    img = ImageOps.exif_transpose(Image.open(path)).convert("L")
    w, h = img.size
    f = np.asarray(img.crop((w * 0.15, h * 0.15, w * 0.85, h * 0.85)).resize((48, 84)), np.float32)
    detail = np.abs(np.diff(f, axis=0)).mean() + np.abs(np.diff(f, axis=1)).mean()
    return [round(float(detail + 0.3 * f.std()), 1)]


def _level(clip: Clip, need: float) -> float:
    """Средняя «живость» лучшего куска длиной need; если не мерили — считаем средней."""
    return _liveliest(clip, need) / max(1, round(need / ACTIVITY_STEP)) if clip.activity else 25.0


def _liveliest(clip: Clip, need: float) -> float:
    """Сумма «живости» лучшего куска длиной need секунд (для выбора самого живого видео)."""
    win = max(1, round(need / ACTIVITY_STEP))
    if len(clip.activity) < win:
        return float(np.mean(clip.activity)) * win if clip.activity else 0.0
    return float(np.convolve(clip.activity, np.ones(win), "valid").max())


def _pick_start(clip: Clip, need: float, rng) -> float:
    """Откуда брать кусок видео длиной need: один из самых живых кусков (не всегда один и тот же)."""
    room = max(0.0, clip.duration - need)
    win = max(1, round(need / ACTIVITY_STEP))
    if room <= 0 or len(clip.activity) < win:
        return rng.uniform(0, room)
    sums = np.convolve(clip.activity, np.ones(win), "valid")
    starts = [i for i in range(len(sums)) if i * ACTIVITY_STEP <= room]
    best = sorted(starts, key=lambda i: -sums[i])[:4]
    return min(room, rng.choice(best) * ACTIVITY_STEP)


PUNCHES = [["punch", "flash"], ["punch", "shake"], ["punch", "glitch"], ["punch", "flash", "bw"],
           ["punch", "shake", "flash"], ["punch", "glitch", "flash"], ["punch"]]


def plan_velocity(clips: list[Clip], period: float, drop: float, track_left: float,
                  rng) -> tuple[list[Slot], float, list[tuple[float, float]]]:
    """Раскладка эдита по ударам. Возвращает (слоты, сколько ударов раскачки до дропа, окна для 3 подписей).

    раскачка до дропа (если в треке есть) → нарезка в бит → велосити (ускорение-замедление-ускорение)
    → нарезка на полбита → финальный кадр с отъездом. Длины частей каждый раз немного разные."""
    lively = [c for c in clips if _level(c, period) >= DULL]
    pool = lively if len(lively) >= 6 else clips[:]
    rng.shuffle(pool)
    order = iter(pool * 4)

    def slot(beats: float, fx: list[str], prefer: list[Clip] | None = None) -> Slot:
        c = prefer.pop(0) if prefer else next(order)
        start = _pick_start(c, beats * period * 1.4, rng) if c.duration else 0.0
        return Slot(c.path, c.kind, beats, fx, start)

    lead = min(4, int(drop / period))       # сколько ударов раскачки есть до дропа
    after = int((track_left - 0.5) / period)  # сколько ударов музыки после дропа
    drop_beats = min(rng.choice([8, 10, 12]), max(4, after - 13))
    rapid = 8 if after - drop_beats >= 13 else 4
    velo_need = 4 * period * 1.4  # велосити с замедлением съедает ~1,37 длины слота исходника
    long_enough = [c for c in pool if c.kind != "photo" and c.duration >= velo_need]
    velo = max(long_enough, key=lambda c: _level(c, velo_need), default=None)
    # На интро и в финал — самые живые кадры, кружочки в первую очередь (тот, что на велосити, — если других нет).
    need = max(lead, 3) * period
    heroes = sorted(pool, key=lambda c: (c.kind != "circle", c is velo, -_level(c, need)))

    slots = []
    if lead >= 2:
        slots.append(slot(lead, ["zoomin", "bw"], heroes[:1]))
    else:
        lead = 0
    slots += [slot(1, rng.choice(PUNCHES)) for _ in range(drop_beats)]
    t_velo = (lead + drop_beats) * period
    slots.append(Slot(velo.path, velo.kind, 4, ["velocity"], _pick_start(velo, velo_need, rng))
                 if velo else slot(4, ["zoomin"]))
    slots += [slot(0.5, rng.choice(PUNCHES)) for _ in range(rapid)]
    slots.append(slot(3, ["zoomout", "bw"], heroes[1:2] or heroes[:1]))
    total = sum(s.beats for s in slots) * period
    captions = [(0.15, max(lead * period - 0.05, 2 * period)),
                (t_velo + 0.35 * 4 * period, t_velo + 4 * period - 0.03),
                (total - 3 * period + 0.1, total)]
    return slots, lead, captions


# --- утилиты для бота ---

def available() -> bool:
    return os.getenv("EDITS", "1") != "0" and video.FFMPEG is not None


def probe(path: Path) -> tuple[float, int, int]:
    """(длительность, ширина, высота) видео."""
    info = subprocess.run([video.FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info)
    size = re.search(r"Video: .*?, (\d{2,5})x(\d{2,5})", info)
    seconds = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3)) if dur else 0.0
    return seconds, int(size.group(1)) if size else 0, int(size.group(2)) if size else 0


def video_sheet(path: Path, duration: float) -> bytes:
    """4 кадра из видео сеткой 2×2 (от начала к концу) — чтобы Claude оценил всё видео, а не один момент."""
    cell, got = 256, 0
    sheet = Image.new("RGB", (2 * cell, 2 * cell), "black")
    for i, part in enumerate((0.12, 0.38, 0.62, 0.88)):
        data = frame_at(path, duration * part)
        if not data:
            continue
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((cell - 4, cell - 4))
        sheet.paste(img, (i % 2 * cell + (cell - img.width) // 2, i // 2 * cell + (cell - img.height) // 2))
        got += 1
    if not got:
        return b""
    out = io.BytesIO()
    sheet.save(out, "JPEG", quality=85)
    return out.getvalue()


def frame_at(path: Path, seconds: float) -> bytes:
    """Кадр из видео (JPEG) — чтобы Claude посмотрел, что там."""
    return subprocess.run([video.FFMPEG, "-v", "error", "-ss", f"{seconds:.2f}", "-i", str(path), "-frames:v", "1",
                           "-vf", "scale=512:-2", "-f", "image2", "-vcodec", "mjpeg", "-"], capture_output=True).stdout
