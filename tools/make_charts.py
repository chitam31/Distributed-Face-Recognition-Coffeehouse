"""Vẽ biểu đồ cho báo cáo từ kết quả thực nghiệm thật trong results/.

    python -m tools.make_charts            # cần: pip install matplotlib

Sinh (nếu có dữ liệu tương ứng):
  docs/figures/throughput_vs_workers.png   - thông lượng khi chạy 1, 2, 3 worker
  docs/figures/latency_vs_workers.png      - latency p50/p95 theo số worker
  docs/figures/scaling_threads.png         - thông lượng theo số worker: mặc định so với ORT_THREADS giới hạn
                                             (từ results/drain_times_default_threads.csv và results/drain_times.csv
                                              do scripts/measure_drain.sh ghi)
  docs/figures/threshold_sweep.png         - tỉ lệ nhận đúng / nhận nhầm theo ngưỡng
  docs/figures/results_tables.md           - bảng Markdown để dán vào báo cáo
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sys

OUT = os.path.join("docs", "figures")


def load_benchmarks():
    runs = []
    for path in sorted(glob.glob(os.path.join("results", "bench_*.json"))):
        with open(path, encoding="utf-8") as fh:
            runs.append(json.load(fh)["summary"])
    return sorted(runs, key=lambda r: (r["workers"], r["label"]))


def load_drain(path: str) -> dict:
    """Đọc CSV do scripts/measure_drain.sh ghi: {số worker: (req/s, latency TB ms)}; trùng số worker lấy dòng cuối."""
    out: dict = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                out[int(row["workers"])] = (float(row["req_per_s"]), float(row["avg_latency_ms"]))
            except (KeyError, ValueError):
                continue                      # bỏ dòng hỏng (vd số thập phân dùng dấu phẩy do locale)
    return dict(sorted(out.items()))


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(OUT, exist_ok=True)
    tables = []
    runs = load_benchmarks()

    if runs:
        w = [r["workers"] for r in runs]
        fig, ax = plt.subplots(figsize=(5.5, 3.6))
        ax.bar([str(x) for x in w], [r["throughput_rps"] for r in runs], color="#1565c0")
        for i, r in enumerate(runs):
            ax.text(i, r["throughput_rps"], f"{r['throughput_rps']:.2f}", ha="center", va="bottom", fontsize=9)
        ax.set_xlabel("Số worker"); ax.set_ylabel("Thông lượng (request/giây)")
        ax.set_title("Thông lượng theo số worker"); ax.grid(axis="y", alpha=.3)
        fig.tight_layout(); fig.savefig(os.path.join(OUT, "throughput_vs_workers.png"), dpi=160); plt.close(fig)

        fig, ax = plt.subplots(figsize=(5.5, 3.6))
        ax.plot(w, [r["latency"]["p50_ms"] for r in runs], "o-", label="p50", color="#1b7f3b")
        ax.plot(w, [r["latency"]["p95_ms"] for r in runs], "s-", label="p95", color="#c77700")
        ax.set_xlabel("Số worker"); ax.set_ylabel("Latency đầu-cuối (ms)"); ax.set_xticks(w)
        ax.set_title("Latency theo số worker"); ax.legend(); ax.grid(alpha=.3)
        fig.tight_layout(); fig.savefig(os.path.join(OUT, "latency_vs_workers.png"), dpi=160); plt.close(fig)

        tables.append("| Số worker | Yêu cầu | Hoàn thành | Thời gian (s) | Throughput (req/s) | p50 (ms) | p95 (ms) | Chia việc giữa worker |")
        tables.append("|---|---|---|---|---|---|---|---|")
        for r in runs:
            lat = r["latency"]
            tables.append(f"| {r['workers']} | {r['requests']} | {r['completed']} | {r['wall_s']} | {r['throughput_rps']} | "
                          f"{lat.get('p50_ms', float('nan')):.0f} | {lat.get('p95_ms', float('nan')):.0f} | {r['per_worker']} |")
        tables.append("")

    tuned = load_drain(os.path.join("results", "drain_times.csv"))
    default = load_drain(os.path.join("results", "drain_times_default_threads.csv"))
    if tuned or default:
        ink, muted, grid = "#0b0b0b", "#52514e", "#e3e2de"             # chữ dùng màu mực, không dùng màu của chuỗi
        series = [("Giới hạn luồng ONNX (ORT_THREADS=2)", tuned, "#2a78d6"),
                  ("Mặc định (mỗi worker dùng hết nhân CPU)", default, "#eb6834")]
        fig, ax = plt.subplots(figsize=(6.4, 3.9))
        xs = sorted(set(tuned) | set(default))
        if 1 in tuned:                                                   # đường tham chiếu: tăng tuyến tính lý tưởng
            ax.plot(xs, [tuned[1][0] * x for x in xs], ls="--", lw=1.2, color="#8a8984",
                    label="Tuyến tính lý tưởng (từ 1 worker đã giới hạn luồng)")
        for name, data, color in series:
            if not data:
                continue
            ax.plot(list(data), [v[0] for v in data.values()], "-o", lw=2, ms=7, mec="white", mew=1.4,
                    color=color, label=name)
            dy = -15 if color == "#2a78d6" else 8                       # nhãn xanh nằm dưới, nhãn cam nằm trên: không chồng nhau
            for x, (rps, _) in data.items():
                ax.annotate(f"{rps:.1f}", (x, rps), textcoords="offset points", xytext=(0, dy), ha="center",
                            fontsize=8, color=ink)
        ax.set_xticks(xs)
        ax.set_xlabel("Số worker", color=muted); ax.set_ylabel("Thông lượng (request/giây)", color=muted)
        ax.set_title("Thông lượng theo số worker trên một máy (6 nhân / 12 luồng)", fontsize=10, color=ink, loc="left")
        ax.grid(axis="y", color=grid, lw=.8); ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color("#c9c8c3")
        ax.tick_params(colors=muted, labelsize=8)
        ax.set_ylim(bottom=0)
        ax.legend(fontsize=7.5, frameon=False, labelcolor=ink, loc="upper left")
        fig.tight_layout(); fig.savefig(os.path.join(OUT, "scaling_threads.png"), dpi=160); plt.close(fig)

        tables.append("| Số worker | Cấu hình luồng ONNX | Throughput (req/s) | Latency TB mỗi request (ms) | Tăng so với 1 worker | Hiệu suất |")
        tables.append("|---|---|---|---|---|---|")
        for name, data, _ in series:
            base = data.get(1, (None, None))[0]
            for x, (rps, lat) in data.items():
                gain = f"{rps / base:.2f}×" if base else "–"
                eff = f"{rps / base / x:.0%}" if base else "–"
                tables.append(f"| {x} | {name} | {rps:.2f} | {lat:.0f} | {gain} | {eff} |")
        tables.append("")

    thr_path = os.path.join("results", "threshold_eval.json")
    if os.path.exists(thr_path):
        with open(thr_path, encoding="utf-8") as fh:
            ev = json.load(fh)
        rows = ev["rows"]
        t = [r["threshold"] for r in rows]
        fig, ax = plt.subplots(figsize=(6.2, 3.8))
        ax.plot(t, [r["correct_rate"] * 100 for r in rows], "o-", label="Nhận đúng người", color="#1b7f3b")
        ax.plot(t, [r["frr"] * 100 for r in rows], "s-", label="Bỏ sót (FRR)", color="#c77700")
        ax.plot(t, [r["wrong_person_rate"] * 100 for r in rows], "^-", label="Nhầm sang người khác", color="#b71c1c")
        if any(r["far"] == r["far"] for r in rows):
            ax.plot(t, [r["far"] * 100 for r in rows], "d--", label="Người lạ bị nhận nhầm (FAR)", color="#6a1b9a")
        if ev.get("best"):
            ax.axvline(ev["best"]["threshold"], color="gray", ls=":", label=f"Chọn = {ev['best']['threshold']:.2f}")
        ax.set_xlabel("Ngưỡng cosine"); ax.set_ylabel("%"); ax.set_title("Quét ngưỡng nhận diện")
        ax.legend(fontsize=8); ax.grid(alpha=.3)
        fig.tight_layout(); fig.savefig(os.path.join(OUT, "threshold_sweep.png"), dpi=160); plt.close(fig)

        tables.append("| Ngưỡng | Nhận đúng | Bỏ sót (FRR) | Nhầm sang người khác | FAR người lạ | Quyết định đúng |")
        tables.append("|---|---|---|---|---|---|")
        for r in rows:
            tables.append(f"| {r['threshold']:.2f} | {r['correct_rate']:.1%} | {r['frr']:.1%} | {r['wrong_person_rate']:.1%} | "
                          f"{r['far']:.1%} | {r['decision_acc']:.1%} |")
        tables.append("")

    if not tables:
        print("Chưa có dữ liệu trong results/. Chạy tools.benchmark và tools.eval_threshold trước.", file=sys.stderr)
        return 1
    with open(os.path.join(OUT, "results_tables.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(tables))
    print(f"đã ghi biểu đồ và bảng vào {OUT}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
