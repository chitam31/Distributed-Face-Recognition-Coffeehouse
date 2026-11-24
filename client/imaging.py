"""Chuẩn bị ảnh ở phía chi nhánh: resize cạnh dài tối đa 640px + nén JPEG,
để message gửi đi chỉ vài chục KB (đề bài, mục 7)."""
from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np

MAX_SIDE = 640
JPEG_QUALITY = 85


def resize_max_side(img: np.ndarray, max_side: int = MAX_SIDE) -> np.ndarray:
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest <= max_side:
        return img
    scale = max_side / float(longest)
    return cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)


def encode_jpeg(img: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise ValueError("không nén được ảnh JPEG")
    return buf.tobytes()


def prepare_jpeg(img_bgr: np.ndarray, max_side: int = MAX_SIDE, quality: int = JPEG_QUALITY) -> bytes:
    return encode_jpeg(resize_max_side(img_bgr, max_side), quality)


def load_image(path: str) -> np.ndarray:
    data = np.fromfile(path, dtype=np.uint8)            # fromfile + imdecode: đọc được cả đường dẫn có tiếng Việt
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"không đọc được ảnh: {path}")
    return img


def load_jpeg_for_sending(path: str) -> bytes:
    return prepare_jpeg(load_image(path))


class Camera:
    """Bao cv2.VideoCapture (webcam laptop mô phỏng ứng dụng di động)."""

    def __init__(self, index: int = 0, size: Optional[Tuple[int, int]] = (1280, 720)):
        self.cap = cv2.VideoCapture(index)
        if size:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])

    def is_open(self) -> bool:
        return bool(self.cap and self.cap.isOpened())

    def read(self) -> Optional[np.ndarray]:
        ok, frame = self.cap.read()
        return frame if ok else None

    def release(self) -> None:
        if self.cap:
            self.cap.release()
