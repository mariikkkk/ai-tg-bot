"""Кто из чата на фото — локально, без облака (OpenCV: YuNet находит лица, SFace делает «отпечаток» лица).

Бот узнаёт только тех, кто сам попросил: /face на своё фото. Отпечатки хранятся только у них (в базе
на этом компьютере); чужие лица на фотках не запоминаются — только отметка, кто из записавшихся там есть.
Модели (Apache 2.0, OpenCV Zoo) качаются сами при первом запуске в data/models/.
"""

import os
import ssl
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import certifi
import numpy as np

try:
    import cv2

    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)  # без лишних предупреждений в логе бота
except ImportError:  # без OpenCV бот просто не узнаёт лица
    cv2 = None
except AttributeError:
    pass

MODELS = Path(os.getenv("DB_PATH", Path(__file__).parent / "data" / "bot.db")).parent / "models"
ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models/"
DETECTOR = "face_detection_yunet_2023mar.onnx"
RECOGNIZER = "face_recognition_sface_2021dec.onnx"
URLS = {DETECTOR: ZOO + "face_detection_yunet/" + DETECTOR, RECOGNIZER: ZOO + "face_recognition_sface/" + RECOGNIZER}

MATCH = 0.47     # косинусная похожесть, выше которой «это он» (SFace советует 0.363; на фото из чата
                 # один и тот же человек даёт ~0.6–0.7, а 0.40–0.45 — уже бывают чужие: лучше промолчать)
MARGIN = 0.05    # если лицо почти одинаково похоже на двоих — не угадываем
MIN_FACE = 0.04  # лицо меньше этой доли стороны кадра не узнаём — слишком мелко
MAX_SIDE = 1280  # большие фото уменьшаем: быстрее и не хуже
SAMPLES = 10     # сколько фото одного человека держим

_lock = threading.Lock()  # модели OpenCV не потокобезопасны
_models: tuple | None = None


@dataclass
class Face:
    feature: np.ndarray  # нормированный «отпечаток», 128 чисел
    cx: float            # центр лица по горизонтали, 0 — левый край, 1 — правый
    size: float          # сторона лица в долях стороны кадра


def available() -> bool:
    return cv2 is not None and all((MODELS / name).exists() for name in URLS)


def ensure_models() -> None:
    """Докачивает модели, если их нет (≈40 МБ, один раз)."""
    if cv2 is None:
        return
    MODELS.mkdir(parents=True, exist_ok=True)
    context = ssl.create_default_context(cafile=certifi.where())
    for name, url in URLS.items():
        dst = MODELS / name
        if not dst.exists():
            tmp = dst.with_suffix(".part")
            with urllib.request.urlopen(url, timeout=120, context=context) as src, open(tmp, "wb") as out:
                out.write(src.read())
            tmp.rename(dst)


def _load():
    global _models
    if _models is None:
        _models = (cv2.FaceDetectorYN.create(str(MODELS / DETECTOR), "", (320, 320), 0.8, 0.3, 5000),
                   cv2.FaceRecognizerSF.create(str(MODELS / RECOGNIZER), ""))
    return _models


def find(image: bytes) -> list[Face]:
    """Лица на картинке (JPEG/PNG) с отпечатками — слева направо."""
    img = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return []
    h, w = img.shape[:2]
    if (scale := MAX_SIDE / max(h, w)) < 1:
        img = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]
    found = []
    with _lock:
        detector, recognizer = _load()
        detector.setInputSize((w, h))
        _, rows = detector.detect(img)
        for row in rows if rows is not None else []:
            x, _, fw, fh = row[:4]
            if max(fw, fh) < MIN_FACE * max(w, h):
                continue
            feature = recognizer.feature(recognizer.alignCrop(img, row)).flatten().astype(np.float32)
            found.append(Face(feature / np.linalg.norm(feature), float((x + fw / 2) / w), float(max(fw, fh) / max(w, h))))
    return sorted(found, key=lambda f: f.cx)


def identify(found: list[Face], gallery: list[tuple[str, np.ndarray]]) -> list[tuple[str, Face]]:
    """Кто есть кто: каждому лицу — самый похожий из записавшихся (если похож достаточно).
    Один человек — одно лицо на кадре. gallery — [(имя, отпечаток)], у человека может быть несколько отпечатков."""
    best: dict[tuple[int, str], float] = {}  # (лицо, человек) → лучшая похожесть среди его фото
    for i, face in enumerate(found):
        for name, feature in gallery:
            best[i, name] = max(best.get((i, name), -1.0), float(face.feature @ feature))
    ambiguous = set()
    for i in range(len(found)):
        top = sorted((sim for (j, _), sim in best.items() if j == i), reverse=True)[:2]
        if len(top) == 2 and top[0] - top[1] < MARGIN:
            ambiguous.add(i)
    pairs = sorted(((sim, i, name) for (i, name), sim in best.items() if i not in ambiguous), reverse=True)
    faces_used, names_used, matched = set(), set(), []
    for similarity, i, name in pairs:
        if similarity < MATCH:
            break
        if i not in faces_used and name not in names_used:
            faces_used.add(i)
            names_used.add(name)
            matched.append((name, found[i]))
    return sorted(matched, key=lambda m: m[1].cx)


def describe(matched: list[tuple[str, Face]], total: int) -> str:
    """«Дима (слева) и Катя (справа)» — для Claude и истории разговора."""
    if not matched:
        return ""
    if len(matched) == 1 or total == 1:
        names = ", ".join(name for name, _ in matched)
    else:
        where = lambda f: "слева" if f.cx < 0.38 else "справа" if f.cx > 0.62 else "в центре"
        names = ", ".join(f"{name} ({where(face)})" for name, face in matched)
    others = total - len(matched)
    return names + (" и ещё кто-то незнакомый" if others == 1 else f" и ещё {others} незнакомых" if others else "")


def to_bytes(feature: np.ndarray) -> bytes:
    return feature.astype(np.float32).tobytes()


def from_bytes(data: bytes) -> np.ndarray:
    return np.frombuffer(data, np.float32)
