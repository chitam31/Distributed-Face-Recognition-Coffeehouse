"""Nhận diện khuôn mặt: SCRFD (detect + căn chỉnh) -> ArcFace 512-d (InsightFace, gói buffalo_l)
chạy trên ONNX Runtime.

Tách làm hai phần:
  * FaceEngine.detect_and_embed(): phần phụ thuộc insightface (nạp muộn, chỉ worker/tools cần).
  * evaluate_faces(): logic thuần numpy/cv2 chọn mặt, lọc chất lượng, quyết định
    NO_FACE / MULTIPLE_FACES. Phần này có unit test, không cần model.

Lưu ý giấy phép: các model pretrained của InsightFace (buffalo_l) chỉ dành cho nghiên cứu phi thương mại.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cv2
import numpy as np

from common.config import Settings
from common.messages import MessageError, Status

log = logging.getLogger("face_engine")


@dataclass
class FaceCandidate:
    bbox: Tuple[float, float, float, float]      # x1, y1, x2, y2 (pixel)
    det_score: float
    embedding: np.ndarray                        # (512,) float32, đã chuẩn hoá L2

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)


@dataclass
class FaceAnalysis:
    """Kết quả phân tích một ảnh. status=None nghĩa là có đúng một khuôn mặt dùng được."""
    status: Optional[str] = None                 # None | NO_FACE | MULTIPLE_FACES
    embedding: Optional[np.ndarray] = None
    n_faces: int = 0                             # số mặt phát hiện được (sau lọc kích thước/điểm)
    reason: str = ""
    det_score: Optional[float] = None
    quality: dict = field(default_factory=dict)


def laplacian_variance(img_bgr: np.ndarray, bbox: Tuple[float, float, float, float],
                       margin: float = 0.1, size: int = 112) -> float:
    """Độ nét của vùng mặt: phương sai Laplacian trên ảnh xám đã đưa về size x size
    (chuẩn hoá kích thước để so sánh được giữa các ảnh)."""
    h, w = img_bgr.shape[:2]
    x1, y1, x2, y2 = bbox
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    x1, y1 = int(max(0, x1 - mx)), int(max(0, y1 - my))
    x2, y2 = int(min(w, x2 + mx)), int(min(h, y2 + my))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return 0.0
    crop = cv2.cvtColor(img_bgr[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    crop = cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(crop, cv2.CV_64F).var())


def evaluate_faces(img_bgr: np.ndarray, faces: List[FaceCandidate], s: Settings) -> FaceAnalysis:
    """Chọn khuôn mặt để nhận diện và loại ảnh kém chất lượng (không tạo khách rác)."""
    usable = [f for f in faces
              if f.det_score >= s.min_det_score and min(f.width, f.height) >= s.min_face_px]
    if not usable:
        if faces:
            reason = (f"phát hiện {len(faces)} mặt nhưng quá nhỏ hoặc độ tin cậy thấp "
                      f"(cần ≥{s.min_face_px}px, điểm ≥{s.min_det_score})")
        else:
            reason = "không phát hiện khuôn mặt"
        return FaceAnalysis(status=Status.NO_FACE, n_faces=0, reason=reason)

    if len(usable) > 1 and s.multi_face_policy == "reject":
        return FaceAnalysis(status=Status.MULTIPLE_FACES, n_faces=len(usable),
                            reason=f"có {len(usable)} khuôn mặt trong ảnh, vui lòng chụp lại từng người")

    best = max(usable, key=lambda f: f.area)     # chính sách "largest": lấy mặt lớn nhất (gần camera nhất)
    blur = laplacian_variance(img_bgr, best.bbox)
    if blur < s.min_blur_var:
        return FaceAnalysis(status=Status.NO_FACE, n_faces=len(usable),
                            reason=f"ảnh quá mờ (độ nét {blur:.1f} < {s.min_blur_var:g}), vui lòng chụp lại",
                            quality={"blur_var": round(blur, 1)})

    emb = np.asarray(best.embedding, dtype=np.float32)
    norm = float(np.linalg.norm(emb))
    if norm == 0.0 or not np.isfinite(norm):
        return FaceAnalysis(status=Status.NO_FACE, n_faces=len(usable), reason="không trích được đặc trưng khuôn mặt")
    emb = emb / norm                              # đảm bảo L2 = 1 để cosine = tích vô hướng
    return FaceAnalysis(status=None, embedding=emb, n_faces=len(usable), det_score=float(best.det_score),
                        quality={"blur_var": round(blur, 1), "face_px": int(min(best.width, best.height))})


def decode_jpeg(jpeg: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise MessageError("không giải mã được ảnh JPEG")
    return img


def limit_ort_threads(n: int) -> None:
    """Giới hạn số luồng tính toán của MỌI InferenceSession ONNX Runtime tạo sau lệnh này.

    Mặc định ONNX Runtime dùng hết nhân CPU cho MỖI session. Chạy N worker trên cùng một máy thì N worker
    tranh nhau toàn bộ nhân (thread oversubscription) và thông lượng không tăng. Đặt ORT_THREADS sao cho
    (số worker x ORT_THREADS) <= số nhân thì thêm worker mới thực sự tăng thông lượng.
    Dùng cách vá __init__ vì insightface 0.7.3 không chuyển tiếp tham số sess_options.
    """
    import onnxruntime as ort

    original = ort.InferenceSession.__init__
    if getattr(original, "_coffee_threads", None) == n:
        return
    base = getattr(original, "_coffee_original", original)

    def patched(self, path_or_bytes, sess_options=None, *args, **kwargs):
        if sess_options is None:
            sess_options = ort.SessionOptions()
        sess_options.intra_op_num_threads = n
        sess_options.inter_op_num_threads = 1
        return base(self, path_or_bytes, sess_options, *args, **kwargs)

    patched._coffee_threads = n
    patched._coffee_original = base
    ort.InferenceSession.__init__ = patched


class FaceEngine:
    """Bao InsightFace FaceAnalysis. Chỉ nạp module detection + recognition (bỏ landmark 3D, tuổi/giới)
    để nhanh hơn và tốn ít RAM hơn."""

    def __init__(self, settings: Settings):
        self.s = settings
        self._app = None

    def load(self) -> None:
        if self._app is not None:
            return
        from insightface.app import FaceAnalysis        # nạp muộn: nặng, và không cần khi chạy test

        if self.s.ort_threads > 0:
            limit_ort_threads(self.s.ort_threads)
        providers = ["CPUExecutionProvider"]
        if self.s.use_cuda:
            providers.insert(0, "CUDAExecutionProvider")
        root = os.environ.get("INSIGHTFACE_HOME", "~/.insightface")
        app = FaceAnalysis(name=self.s.model_name, root=root,
                           allowed_modules=["detection", "recognition"], providers=providers)
        app.prepare(ctx_id=0 if self.s.use_cuda else -1, det_thresh=self.s.min_det_score, det_size=(640, 640))
        self._app = app
        log.info("đã nạp model %s (providers=%s, ort_threads=%s)", self.s.model_name, providers,
                 self.s.ort_threads or "auto")

    def warm_up(self) -> None:
        """Chạy thử một ảnh trắng để lần nhận diện đầu tiên không bị chậm (cold start)."""
        self.load()
        self._app.get(np.full((320, 320, 3), 255, dtype=np.uint8))

    def detect_and_embed(self, img_bgr: np.ndarray) -> List[FaceCandidate]:
        self.load()
        out = []
        for f in self._app.get(img_bgr):
            emb = getattr(f, "normed_embedding", None)
            if emb is None:
                continue
            out.append(FaceCandidate(bbox=tuple(float(v) for v in f.bbox),
                                     det_score=float(f.det_score), embedding=np.asarray(emb, dtype=np.float32)))
        return out

    def analyze(self, jpeg: bytes) -> FaceAnalysis:
        img = decode_jpeg(jpeg)
        return evaluate_faces(img, self.detect_and_embed(img), self.s)
