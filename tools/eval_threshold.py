"""Thực nghiệm chọn ngưỡng cosine (đề bài, mục 12): quét 0.30 -> 0.60, đo tỉ lệ nhận đúng / nhận nhầm.

Chạy OFFLINE (không cần RabbitMQ/CSDL), cần model InsightFace:

    python -m tools.eval_threshold --dataset dataset --gallery-per-person 2 --unknown-people 3

Cấu trúc dataset:  dataset/<tên_người>/<ảnh>.jpg  (mỗi người 5-10 ảnh, có sự đồng ý của người chụp)
  * gallery-per-person ảnh đầu tiên của mỗi người dùng để "enroll" (đề bài gợi ý 1-2 ảnh);
  * phần ảnh còn lại là probe (test);
  * unknown-people người cuối (theo thứ tự tên) KHÔNG được enroll, mọi ảnh của họ là "người lạ" để đo nhận nhầm.

Embedding được cache vào results/.embeddings.npz (đổi bằng --cache) để lần chạy sau chỉ tốn vài giây.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Tuple

import numpy as np

from tools.metrics import frange, pick_threshold, sweep_thresholds

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp")


def list_people(root: str) -> Dict[str, List[str]]:
    people = {}
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if os.path.isdir(d) and not name.startswith("."):
            files = sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(IMAGE_EXT))
            if files:
                people[name] = files
    return people


def compute_embeddings(people: Dict[str, List[str]], cache_path: str) -> Tuple[Dict[str, List[np.ndarray]], List[str]]:
    from client.imaging import load_image
    from common.config import load_settings
    from worker.face_engine import FaceEngine, evaluate_faces

    cache = {}
    if os.path.exists(cache_path):
        with np.load(cache_path, allow_pickle=False) as z:
            cache = {k: z[k] for k in z.files}

    settings = load_settings()
    engine = FaceEngine(settings)
    out: Dict[str, List[np.ndarray]] = {}
    skipped: List[str] = []
    for name, files in people.items():
        out[name] = []
        for path in files:
            key = path.replace(os.sep, "/")
            if key in cache:
                emb = cache[key]
                if emb.size:
                    out[name].append(emb)
                else:
                    skipped.append(path)
                continue
            img = load_image(path)
            analysis = evaluate_faces(img, engine.detect_and_embed(img), settings)
            if analysis.status is None:
                cache[key] = analysis.embedding
                out[name].append(analysis.embedding)
            else:
                cache[key] = np.zeros(0, dtype=np.float32)       # nhớ ảnh hỏng để khỏi chạy lại
                skipped.append(f"{path} ({analysis.reason})")
    np.savez(cache_path, **cache)
    return out, skipped


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", required=True)
    p.add_argument("--gallery-per-person", type=int, default=2)
    p.add_argument("--unknown-people", type=int, default=0)
    p.add_argument("--start", type=float, default=0.30)
    p.add_argument("--stop", type=float, default=0.60)
    p.add_argument("--step", type=float, default=0.02)
    p.add_argument("--out", default="results/threshold_eval.json")
    p.add_argument("--cache", default="results/.embeddings.npz", help="nơi lưu cache embedding")
    args = p.parse_args()

    people = list_people(args.dataset)
    if len(people) < 2:
        print("Cần ít nhất 2 người trong dataset", file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(args.cache) or ".", exist_ok=True)
    embeddings, skipped = compute_embeddings(people, args.cache)

    names = [n for n in people if embeddings[n]]
    unknown = set(names[len(names) - args.unknown_people:]) if args.unknown_people > 0 else set()
    gallery: Dict[str, np.ndarray] = {}
    probes = []
    for name in names:
        embs = embeddings[name]
        if name in unknown:
            probes += [(None, e) for e in embs]
        else:
            k = min(args.gallery_per_person, len(embs) - 1) if len(embs) > 1 else len(embs)
            gallery[name] = np.stack(embs[:k])
            probes += [(name, e) for e in embs[k:]]

    rows = sweep_thresholds(gallery, probes, frange(args.start, args.stop, args.step))
    best = pick_threshold(rows)

    print(f"{len(gallery)} người trong gallery · {len(unknown)} người lạ · {len(probes)} ảnh test · "
          f"bỏ qua {len(skipped)} ảnh không dùng được")
    print(f"{'ngưỡng':>7} {'đúng':>7} {'bỏ sót(FRR)':>12} {'nhầm người':>11} {'FAR người lạ':>13} {'quyết định đúng':>16}")
    for r in rows:
        mark = "  <== chọn" if best and r["threshold"] == best["threshold"] else ""
        print(f"{r['threshold']:>7.2f} {r['correct_rate']:>7.1%} {r['frr']:>12.1%} {r['wrong_person_rate']:>11.1%} "
              f"{r['far']:>13.1%} {r['decision_acc']:>16.1%}{mark}")
    if best:
        print(f"\nNgưỡng đề xuất: {best['threshold']:.2f}  (đặt SIM_THRESHOLD={best['threshold']:.2f} cho worker)")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"gallery_people": len(gallery), "unknown_people": len(unknown), "probes": len(probes),
                   "skipped": skipped, "rows": rows, "best": best}, fh, ensure_ascii=False, indent=2)
    print(f"đã lưu {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
