"""Các hàm tính toán thuần (numpy) cho phần thực nghiệm: percentile, quét ngưỡng nhận diện,
đối chiếu dữ liệu giữa hai CSDL. Tách khỏi CLI để có unit test và để báo cáo dùng lại đúng công thức."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np


# ------------------------------------------------------------------ latency / throughput
def percentile(values: Sequence[float], p: float) -> float:
    """Percentile thứ p (0..100) với nội suy tuyến tính (giống numpy.percentile mặc định)."""
    if not values:
        return float("nan")
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    k = (len(xs) - 1) * (p / 100.0)
    lo, hi = int(np.floor(k)), int(np.ceil(k))
    return float(xs[lo] + (xs[hi] - xs[lo]) * (k - lo))


def summarize_latencies(latencies_ms: Sequence[float]) -> Dict[str, float]:
    if not latencies_ms:
        return {"count": 0}
    return {
        "count": len(latencies_ms),
        "mean_ms": float(np.mean(latencies_ms)),
        "p50_ms": percentile(latencies_ms, 50),
        "p95_ms": percentile(latencies_ms, 95),
        "p99_ms": percentile(latencies_ms, 99),
        "max_ms": float(max(latencies_ms)),
    }


# ------------------------------------------------------------------ quét ngưỡng
def best_match(gallery: Dict[str, np.ndarray], probe: np.ndarray) -> Tuple[Optional[str], float]:
    """Người trong gallery giống probe nhất. Embedding đã chuẩn hoá L2 nên cosine = tích vô hướng.
    Mỗi người có nhiều embedding -> lấy giá trị lớn nhất (giống truy vấn pgvector LIMIT 1)."""
    best_name, best_sim = None, -1.0
    for name, embs in gallery.items():
        sim = float(np.max(embs @ probe))
        if sim > best_sim:
            best_name, best_sim = name, sim
    return best_name, best_sim


def sweep_thresholds(gallery: Dict[str, np.ndarray], probes: Iterable[Tuple[Optional[str], np.ndarray]],
                     thresholds: Sequence[float]) -> List[Dict[str, float]]:
    """probes: (tên thật, embedding); tên thật = None nghĩa là người lạ (không có trong gallery).

    Với mỗi ngưỡng t, quyết định của hệ thống là: nhận ra người giống nhất nếu sim >= t, ngược lại "khách mới".
      * correct_rate  : tỉ lệ khách quen được nhận ĐÚNG người
      * frr           : khách quen bị bỏ sót (sim < t) -> hệ thống tạo khách trùng
      * wrong_person  : khách quen bị nhận NHẦM sang người khác (sim >= t nhưng sai người) - lỗi nguy hiểm nhất
      * far           : người lạ bị nhận nhầm thành khách đã có (sim >= t)
      * decision_acc  : tỉ lệ quyết định đúng trên toàn bộ probe
    """
    scored = []
    for true_name, emb in probes:
        pred, sim = best_match(gallery, emb)
        scored.append((true_name, pred, sim))
    known = [x for x in scored if x[0] is not None]
    unknown = [x for x in scored if x[0] is None]

    rows = []
    for t in thresholds:
        correct = sum(1 for tn, pn, s in known if s >= t and pn == tn)
        missed = sum(1 for _, _, s in known if s < t)
        wrong = sum(1 for tn, pn, s in known if s >= t and pn != tn)
        false_acc = sum(1 for _, _, s in unknown if s >= t)
        good_decisions = correct + sum(1 for _, _, s in unknown if s < t)
        total = len(scored)
        rows.append({
            "threshold": round(float(t), 4),
            "known_probes": len(known),
            "unknown_probes": len(unknown),
            "correct_rate": correct / len(known) if known else float("nan"),
            "frr": missed / len(known) if known else float("nan"),
            "wrong_person_rate": wrong / len(known) if known else float("nan"),
            "far": false_acc / len(unknown) if unknown else float("nan"),
            "decision_acc": good_decisions / total if total else float("nan"),
        })
    return rows


def pick_threshold(rows: List[Dict[str, float]]) -> Dict[str, float]:
    """Ngưỡng cho độ chính xác quyết định cao nhất; nếu hoà thì chọn ngưỡng CAO hơn (ít nhận nhầm hơn)."""
    valid = [r for r in rows if not np.isnan(r["decision_acc"])]
    if not valid:
        return {}
    top = max(r["decision_acc"] for r in valid)
    return max((r for r in valid if r["decision_acc"] == top), key=lambda r: r["threshold"])


def frange(start: float, stop: float, step: float) -> List[float]:
    n = int(round((stop - start) / step))
    return [round(start + i * step, 6) for i in range(n + 1)]


# ------------------------------------------------------------------ đối chiếu MySQL <-> pgvector
def diff_ids(mysql_customer_ids: Iterable[int], vector_customer_ids: Iterable[int]) -> Tuple[Set[int], Set[int]]:
    """(khách MySQL không có embedding, embedding không có khách MySQL): hai dạng dữ liệu mồ côi
    mà saga có thể để lại nếu việc bù trừ cũng thất bại."""
    m, v = set(mysql_customer_ids), set(vector_customer_ids)
    return m - v, v - m
