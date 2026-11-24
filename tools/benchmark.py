"""Đo latency (p50/p95) và throughput qua RabbitMQ, đồng thời cho thấy việc chia việc giữa các worker.

    # chạy với 1 worker, rồi 2, rồi 3 (docker compose up -d --scale worker=N), mỗi lần một nhãn riêng:
    python -m tools.benchmark --images dataset --requests 60 --concurrency 12 --workers 1 --label w1
    python -m tools.benchmark --images dataset --requests 60 --concurrency 12 --workers 2 --label w2
    python -m tools.benchmark --images dataset --requests 60 --concurrency 12 --workers 3 --label w3
    python -m tools.make_charts          # vẽ biểu đồ từ results/*.json

Mô phỏng nhiều chi nhánh gửi cùng lúc: mỗi thread là một client với connection và reply queue riêng.
Dùng auto_enroll=false nên benchmark KHÔNG tạo khách mới trong CSDL.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import queue
import sys
import threading
import time
from collections import Counter
from dataclasses import replace
from typing import Any, Dict, List

from client.imaging import load_jpeg_for_sending
from client.rpc_client import BranchRpcClient, RpcError
from common.config import load_settings
from common.messages import build_recognize_request
from tools.metrics import summarize_latencies

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp")


def collect_images(root: str) -> List[str]:
    found = []
    for base, _dirs, files in os.walk(root):
        found += [os.path.join(base, f) for f in sorted(files) if f.lower().endswith(IMAGE_EXT)]
    return sorted(found)


def run_benchmark(args) -> Dict[str, Any]:
    settings = load_settings()
    overrides = {k: v for k, v in (("rabbit_host", args.host), ("rabbit_user", args.user),
                                   ("rabbit_pass", args.password)) if v}
    settings = replace(settings, **overrides) if overrides else settings

    paths = collect_images(args.images)
    if not paths:
        raise SystemExit(f"không tìm thấy ảnh trong {args.images}")
    jpegs = [load_jpeg_for_sending(p) for p in paths]
    print(f"{len(jpegs)} ảnh · {args.requests} request · {args.concurrency} client song song")

    work: "queue.Queue[int]" = queue.Queue()
    for i in range(args.requests):
        work.put(i)
    records: List[Dict[str, Any]] = []
    lock = threading.Lock()
    branches = [b.strip() for b in args.branches.split(",") if b.strip()]

    def worker_thread(tid: int) -> None:
        client = BranchRpcClient(settings, branches[tid % len(branches)], timeout_s=args.timeout, max_attempts=args.attempts)
        try:
            client.connect()
            while True:
                try:
                    i = work.get_nowait()
                except queue.Empty:
                    return
                msg = build_recognize_request(client.branch_id, jpegs[i % len(jpegs)], auto_enroll=False)
                t0 = time.perf_counter()
                try:
                    resp = client.recognize(msg)
                    status, worker, wms = resp.get("status"), resp.get("worker"), resp.get("latency_ms")
                except RpcError as exc:
                    status, worker, wms = f"FAIL:{type(exc).__name__}", None, None
                latency = (time.perf_counter() - t0) * 1000
                with lock:
                    records.append({"i": i, "branch": client.branch_id, "status": status, "worker": worker,
                                    "latency_ms": round(latency, 1), "worker_ms": wms, "attempts": client.last_attempts})
        finally:
            client.close()

    started = time.perf_counter()
    threads = [threading.Thread(target=worker_thread, args=(t,), daemon=True) for t in range(args.concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - started

    ok = [r for r in records if not str(r["status"]).startswith("FAIL")]
    lat = [r["latency_ms"] for r in ok]
    summary = {
        "label": args.label, "workers": args.workers, "requests": args.requests,
        "concurrency": args.concurrency, "completed": len(ok), "failed": len(records) - len(ok),
        "wall_s": round(wall, 3), "throughput_rps": round(len(ok) / wall, 3) if wall > 0 else 0.0,
        "retried_requests": sum(1 for r in ok if r["attempts"] > 1),
        "latency": summarize_latencies(lat),
        "status_counts": dict(Counter(r["status"] for r in records)),
        "per_worker": dict(Counter(r["worker"] for r in ok)),
    }
    return {"summary": summary, "records": sorted(records, key=lambda r: r["i"])}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--images", required=True, help="thư mục ảnh (đọc đệ quy)")
    p.add_argument("--requests", type=int, default=60)
    p.add_argument("--concurrency", type=int, default=12, help="số client chạy song song")
    p.add_argument("--workers", type=int, required=True, help="số worker ĐANG chạy (chỉ để ghi nhãn kết quả)")
    p.add_argument("--label", default=None)
    p.add_argument("--branches", default="A,B,C", help="các mã chi nhánh luân phiên")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--attempts", type=int, default=2)
    p.add_argument("--out", default="results")
    p.add_argument("--host"), p.add_argument("--user"), p.add_argument("--password")
    args = p.parse_args()
    args.label = args.label or f"w{args.workers}"

    try:
        result = run_benchmark(args)
    except RpcError as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, f"bench_{args.label}.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(args.out, f"bench_{args.label}.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(result["records"][0].keys()) if result["records"] else ["i"])
        w.writeheader()
        w.writerows(result["records"])

    s = result["summary"]
    lat = s["latency"]
    print(f"\n== {s['label']} ({s['workers']} worker) ==")
    print(f"hoàn thành {s['completed']}/{s['requests']} · lỗi {s['failed']} · gửi lại {s['retried_requests']}")
    print(f"thời gian {s['wall_s']} s · thông lượng {s['throughput_rps']} req/s")
    if lat.get("count"):
        print(f"latency  p50 {lat['p50_ms']:.0f} ms · p95 {lat['p95_ms']:.0f} ms · p99 {lat['p99_ms']:.0f} ms · max {lat['max_ms']:.0f} ms")
    print(f"chia việc giữa các worker: {s['per_worker']}")
    print(f"đã lưu results/bench_{args.label}.json|csv")
    return 0 if s["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
