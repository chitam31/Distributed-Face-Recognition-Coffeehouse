"""Vẽ các hình cho báo cáo IEEE (bản tiếng Anh và tiếng Việt) với kích thước vừa MỘT CỘT (~3,45 inch).

    python docs/ieee/make_ieee_figures.py          # cần matplotlib; ghi vào docs/ieee/figs/{en,vi}/

Số liệu trong Fig. scaling và Fig. benchmark là số đo thật (results/drain_times*.csv, results/bench_*.json),
được ghi lại ở dưới để hình luôn khớp với bảng trong báo cáo.
"""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.ticker import FuncFormatter

HERE = os.path.dirname(os.path.abspath(__file__))
plt.rcParams.update({"font.family": "Liberation Sans", "font.size": 6.5, "axes.linewidth": 0.6,
                     "pdf.fonttype": 42})
W = 3.45                       # chiều rộng một cột (inch)
BLUE, ORANGE, GRAY = "#2a78d6", "#eb6834", "#7a7a7a"

# ---- số đo thật -------------------------------------------------------------------------------------------
BENCH = {1: (3.45, 3456, 3519), 2: (6.34, 1854, 1937), 4: (8.26, 1416, 1571)}      # workers: (req/s, p50, p95)
DRAIN_DEFAULT = {1: 6.72, 2: 6.50, 3: 5.49}                                          # mặc định ONNX Runtime
DRAIN_TUNED = {1: 3.48, 2: 6.16, 4: 8.36, 6: 9.06}                                   # ORT_THREADS=2

T = {
    "en": {
        "client": "Branch\nclient", "broker": "RabbitMQ", "worker": "Worker", "pg": "PostgreSQL\n(pgvector)", "my": "MySQL",
        "w1": "Worker 1", "w2": "Worker 2",
        "branch": "Branch {}", "branch_sub": "web / GUI / CLI",
        "mq_title": "RabbitMQ broker", "mq_l1": "exchange coffee.requests",
        "mq_l2": "queues face.requests · orders", "mq_l3": "reply.<branch>.<uuid>  ·  DLX / DLQ",
        "pool": "Stateless workers (docker compose --scale worker=N)", "wk": "Worker {}", "wk_sub": "InsightFace + ONNX",
        "pgdb": "PostgreSQL + pgvector\nface vectors", "mydb": "MySQL 8\ncustomers, orders, logs", "ph": "Photo store\nphoto files",
        "e_rw": "read / write", "data": "Shared data: every worker reads and writes all three stores",
        "e_req": "request", "e_rep": "reply", "e_cc": "competing consumers\nprefetch = 1", "e_ra": "reply, ack",
        "e_vec": "vector →\ncustomer_id", "e_prof": "profile, orders,\nlogs", "e_ph": "photos",
        "A_title": "Recognition flow (A: known customer, B: new customer)",
        "A": ["1 publish (id=R)", "2 deliver", "3 lookup log(R): not found", "4 detect + embed", "5 nearest vector → id, similarity"],
        "alt1": "alt [similarity ≥ τ]: known customer (A)", "A2": "6 profile + last order",
        "alt2": "[else]: new customer (B)",
        "B": ["6 advisory lock", "7 search again (inside lock)", "8 INSERT customer, save photo", "9 INSERT embedding, COMMIT (unlock)"],
        "comp": "error in 8–9: delete customer + photo (compensate)",
        "tail": ["10 write log(R)", "11 reply → reply queue", "12 deliver reply", "13 ack"],
        "C_title": "Worker crash and idempotent redelivery",
        "C": ["1 publish (id=R)", "2 deliver (unacked)", "3 process + write log(R)", "crash before\nreply + ack",
              "4 connection lost:\nR back in queue", "5 redeliver", "6 log(R) found → stored result", "7 reply (duplicate = true)",
              "8 deliver reply", "9 ack"],
        "S_title": "Throughput vs. number of workers", "S_x": "Workers", "S_y": "Throughput (req/s)",
        "S_def": "default ONNX threads", "S_tun": "2 ONNX threads / worker", "S_ideal": "ideal linear (3.48 × N)",
        "B_thr": "Throughput (req/s)", "B_lat": "End-to-end latency (s)", "B_x": "Workers", "p50": "p50", "p95": "p95",
        "dec": ".", "L_x": "Request index (submission order)", "L_y": "End-to-end latency (s)", "L_w1": "{} worker", "L_wn": "{} workers", "Q_x": "Time since the burst arrived (s)", "Q_y": "Messages waiting in face.requests",
    },
    "vi": {
        "client": "Client\nchi nhánh", "broker": "RabbitMQ", "worker": "Worker", "pg": "PostgreSQL\n(pgvector)", "my": "MySQL",
        "w1": "Worker 1", "w2": "Worker 2",
        "branch": "Chi nhánh {}", "branch_sub": "web / GUI / CLI",
        "mq_title": "Broker RabbitMQ", "mq_l1": "exchange coffee.requests",
        "mq_l2": "queue face.requests · orders", "mq_l3": "reply.<chi nhánh>.<uuid>  ·  DLX / DLQ",
        "pool": "Worker phi trạng thái (docker compose --scale worker=N)", "wk": "Worker {}", "wk_sub": "InsightFace + ONNX",
        "pgdb": "PostgreSQL + pgvector\nvector khuôn mặt", "mydb": "MySQL 8\nkhách, đơn, nhật ký", "ph": "Kho ảnh\nfile ảnh",
        "e_rw": "đọc / ghi", "data": "Dữ liệu dùng chung: worker nào cũng đọc, ghi cả ba kho",
        "e_req": "request", "e_rep": "reply", "e_cc": "competing consumers\nprefetch = 1", "e_ra": "reply, ack",
        "e_vec": "vector →\ncustomer_id", "e_prof": "hồ sơ, đơn,\nnhật ký", "e_ph": "ảnh",
        "A_title": "Luồng nhận diện (A: khách quen, B: khách mới)",
        "A": ["1 publish (id=R)", "2 giao message", "3 tra log(R): chưa có", "4 phát hiện + embed", "5 vector gần nhất → id, độ tương tự"],
        "alt1": "alt [độ tương tự ≥ τ]: khách quen (A)", "A2": "6 hồ sơ + đơn gần nhất",
        "alt2": "[ngược lại]: khách mới (B)",
        "B": ["6 khoá advisory", "7 tìm lại (trong khoá)", "8 INSERT khách, lưu ảnh", "9 INSERT embedding, COMMIT (mở khoá)"],
        "comp": "lỗi ở bước 8–9: xoá khách + ảnh (bù trừ)",
        "tail": ["10 ghi log(R)", "11 reply → queue trả lời", "12 giao reply", "13 ack"],
        "C_title": "Worker chết giữa chừng và giao lại idempotent",
        "C": ["1 publish (id=R)", "2 giao (chưa ack)", "3 xử lý + ghi log(R)", "chết trước\nreply + ack",
              "4 mất kết nối:\nR trở lại queue", "5 giao lại", "6 thấy log(R) → kết quả cũ", "7 reply (duplicate = true)",
              "8 giao reply", "9 ack"],
        "S_title": "Thông lượng theo số worker", "S_x": "Số worker", "S_y": "Thông lượng (req/s)",
        "S_def": "luồng ONNX mặc định", "S_tun": "2 luồng ONNX / worker", "S_ideal": "tuyến tính lý tưởng (3,48 × N)",
        "B_thr": "Thông lượng (req/s)", "B_lat": "Latency đầu-cuối (s)", "B_x": "Số worker", "p50": "p50", "p95": "p95",
        "dec": ",", "L_x": "Thứ tự gửi của request", "L_y": "Latency đầu-cuối (s)", "L_w1": "{} worker", "L_wn": "{} worker", "Q_x": "Thời gian từ lúc đợt dồn tới (s)", "Q_y": "Message chờ trong face.requests",
    },
}


def box(ax, x, y, w, h, text, fc, ec="#444444", fs=6.5, lw=0.7, style="round,pad=0.01,rounding_size=0.04", bold=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=style, fc=fc, ec=ec, lw=lw))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, linespacing=1.15,
            fontweight="bold" if bold else "normal")


def arrow(ax, p, q, color="#333333", ls="-", lw=0.8, style="-|>", ms=5):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=ms, color=color, lw=lw, ls=ls,
                                 shrinkA=0, shrinkB=0))


# ---------------------------------------------------------------------------------------------- architecture
def fig_architecture(lang: str) -> None:
    """Bốn chi nhánh -> broker -> nhóm worker -> tầng dữ liệu dùng chung. Mũi tên xuống tầng dữ liệu đi ra từ KHUNG
    nhóm worker (đặt ở khe giữa các worker), không từ một worker cụ thể: worker nào cũng đọc, ghi cả ba kho."""
    t = T[lang]
    H = 3.80
    fig = plt.figure(figsize=(W, H)); ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    # ---- bốn chi nhánh
    gap = 0.06
    bw4 = (W - 0.08 - 3 * gap) / 4
    xs4 = [0.04 + i * (bw4 + gap) for i in range(4)]
    for x, n in zip(xs4, "ABCD"):
        box(ax, x, 3.25, bw4, 0.52, t["branch"].format(n) + "\n" + t["branch_sub"], "#e3f0fc", fs=5.9)
        cx = x + bw4 / 2
        arrow(ax, (cx - 0.08, 3.25), (cx - 0.08, 2.92), color=BLUE)
        arrow(ax, (cx + 0.08, 2.92), (cx + 0.08, 3.25), color="#2e7d32", ls="--", lw=0.7)
    cxa = xs4[0] + bw4 / 2
    ax.text(cxa - 0.12, 3.085, t["e_req"], fontsize=5.4, color=BLUE, ha="right", va="center")
    ax.text(cxa + 0.12, 3.085, t["e_rep"], fontsize=5.4, color="#2e7d32", ha="left", va="center")
    # ---- broker
    box(ax, 0.04, 2.26, W - 0.08, 0.66, "", "#fff1de", style="round,pad=0.01,rounding_size=0.03")
    ax.text(W / 2, 2.82, t["mq_title"], ha="center", va="center", fontsize=6.8, fontweight="bold")
    ax.text(W / 2, 2.66, t["mq_l1"], ha="center", va="center", fontsize=6.2)
    ax.text(W / 2, 2.53, t["mq_l2"], ha="center", va="center", fontsize=6.2)
    ax.text(W / 2, 2.40, t["mq_l3"], ha="center", va="center", fontsize=6.2)
    # ---- nhóm worker
    ax.add_patch(FancyBboxPatch((0.04, 1.24), W - 0.08, 0.82, boxstyle="round,pad=0.01,rounding_size=0.03",
                                fc="#f6fbf6", ec="#888888", lw=0.6, ls="--"))
    ax.text(W / 2, 1.33, t["pool"], ha="center", va="center", fontsize=5.8, color="#3a6b3a")
    bw3 = 0.98
    xs3 = [0.04, 1.235, 2.43]
    for x, n in zip(xs3, ["1", "2", "N"]):
        box(ax, x + 0.03, 1.44, bw3 - 0.06, 0.46, t["wk"].format(n) + "\n" + t["wk_sub"], "#e4f4e6")
        cx = x + bw3 / 2
        arrow(ax, (cx - 0.12, 2.26), (cx - 0.12, 1.90), color="#333333")                       # giao request
        arrow(ax, (cx + 0.12, 1.90), (cx + 0.12, 2.26), color="#2e7d32", ls="--", lw=0.7)     # reply + ack
    ax.text(0.74, 2.16, t["e_cc"], fontsize=5.6, ha="left", va="center", color="#333333", linespacing=1.05)
    ax.text(xs3[1] + bw3 / 2 + 0.17, 2.16, t["e_ra"], fontsize=5.6, ha="left", va="center", color="#2e7d32")
    # ---- mũi tên hai chiều từ KHUNG nhóm worker xuống tầng dữ liệu, đặt ở khe giữa các worker
    for gx in (xs3[0] + bw3 + 0.08, xs3[1] + bw3 + 0.08):
        arrow(ax, (gx, 1.24), (gx, 1.00), color="#333333", style="<|-|>")
    ax.text(W / 2, 1.12, t["e_rw"], ha="center", va="center", fontsize=5.6, color="#222222")
    # ---- tầng dữ liệu dùng chung
    ax.add_patch(FancyBboxPatch((0.04, 0.02), W - 0.08, 0.98, boxstyle="round,pad=0.01,rounding_size=0.03",
                                fc="#faf8fd", ec="#888888", lw=0.6, ls="--"))
    ax.text(W / 2, 0.905, t["data"], ha="center", va="center", fontsize=5.8, color="#4a3a6b")
    sw = 1.0
    sg = (W - 0.08 - 3 * sw) / 4
    for i, (key, fc) in enumerate([("pgdb", "#ece6f6"), ("mydb", "#ece6f6"), ("ph", "#fffbe0")]):
        box(ax, 0.04 + sg + i * (sw + sg), 0.10, sw, 0.66, t[key], fc, fs=5.9)
    fig.savefig(os.path.join(HERE, "figs", lang, "architecture.png"), dpi=300, bbox_inches="tight", pad_inches=0.02,
                facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------------------------- sequences
def lifelines(ax, actors, xs, top, bottom):
    for a, x in zip(actors, xs):
        box(ax, x - 0.29, top, 0.58, 0.27, a, "#e3f0fc", fs=6.2, style="round,pad=0.005,rounding_size=0.03")
        ax.plot([x, x], [bottom, top], color="#9aa7b0", lw=0.6, ls=(0, (3, 2)), zorder=0)


def seq_flow(lang: str) -> None:
    t = T[lang]
    actors = [t["client"], t["broker"], t["worker"], t["pg"], t["my"]]
    xs = [0.36, 1.02, 1.74, 2.5, 3.14]
    rh = 0.185
    rows = len(t["A"]) + 1 + 1 + 1 + len(t["B"]) + 1 + len(t["tail"])
    H = 0.62 + rh * rows
    fig = plt.figure(figsize=(W, H)); ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    ax.text(W / 2, H - 0.08, t["A_title"], ha="center", va="center", fontsize=6.6, fontweight="bold")
    lifelines(ax, actors, xs, H - 0.48, 0.05)
    y = H - 0.48 - 0.15
    C, MQ, WK, PG, MY = xs

    def msg(x1, x2, text, color="#1a237e", ls="-"):
        nonlocal y
        arrow(ax, (x1, y), (x2, y), color=color, ls=ls, lw=0.7, ms=4.5)
        ax.text((x1 + x2) / 2, y + 0.022, text, ha="center", va="bottom", fontsize=5.7, color=color,
                bbox=dict(boxstyle="square,pad=0.03", fc="white", ec="none"))
        y -= rh

    a = t["A"]
    msg(C, MQ, a[0])
    msg(MQ, WK, a[1])
    msg(WK, MY, a[2])
    ax.text(WK, y + 0.01, a[3], ha="center", va="center", fontsize=5.7,
            bbox=dict(boxstyle="round,pad=0.12", fc="#fff8e1", ec="#f9a825", lw=0.5)); y -= rh
    msg(WK, PG, a[4])
    # alt fragment: known customer / new customer, then the common tail (log, reply, ack)
    ytop = y + rh * 0.55
    ax.text(0.08, y + 0.02, t["alt1"], fontsize=5.7, style="italic", color="#555555", va="center", ha="left")
    y -= rh * 0.9
    msg(WK, MY, t["A2"])
    ysep = y + rh * 0.55
    ax.plot([0.04, W - 0.04], [ysep, ysep], color="#9e9e9e", lw=0.6, ls="--")
    ax.text(0.08, y + 0.02, t["alt2"], fontsize=5.7, style="italic", color="#555555", va="center", ha="left")
    y -= rh * 0.9
    b = t["B"]
    msg(WK, PG, b[0])
    msg(WK, PG, b[1])
    msg(WK, MY, b[2])
    msg(WK, PG, b[3])
    ax.text(WK, y + 0.0, t["comp"], ha="center", va="center", fontsize=5.6, color="#b71c1c",
            bbox=dict(boxstyle="round,pad=0.1", fc="#fff5f5", ec="#b71c1c", lw=0.5)); y -= rh
    ybot = y + rh * 0.55
    ax.add_patch(plt.Rectangle((0.04, ybot), W - 0.08, ytop - ybot, fill=False, ec="#9e9e9e", lw=0.6, ls="--"))
    y -= rh * 0.1
    tl = t["tail"]
    msg(WK, MY, tl[0])
    msg(WK, MQ, tl[1], color="#1b5e20", ls="--")      # reply vào queue trả lời của client trên broker
    msg(MQ, C, tl[2], color="#1b5e20", ls="--")       # broker giao reply cho client
    msg(WK, MQ, tl[3])
    fig.savefig(os.path.join(HERE, "figs", lang, "seq_flow.png"), dpi=300, bbox_inches="tight", pad_inches=0.02,
                facecolor="white")
    plt.close(fig)


def seq_crash(lang: str) -> None:
    t = T[lang]
    actors = [t["client"], t["broker"], t["w1"], t["w2"], t["my"]]
    xs = [0.36, 1.05, 1.72, 2.38, 3.1]
    rh = 0.19
    n = len(t["C"])
    H = 0.62 + rh * (n + 0.4)
    fig = plt.figure(figsize=(W, H)); ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    ax.text(W / 2, H - 0.08, t["C_title"], ha="center", va="center", fontsize=6.6, fontweight="bold")
    top = H - 0.48
    C, MQ, W1, W2, MY = xs
    # lifelines; Worker 1 ends at the crash
    y_crash = top - 0.15 - rh * 3
    for a, x in zip(actors, xs):
        box(ax, x - 0.29, top, 0.58, 0.27, a, "#e3f0fc", fs=6.2, style="round,pad=0.005,rounding_size=0.03")
        ax.plot([x, x], [y_crash + 0.02 if x == W1 else 0.05, top], color="#9aa7b0", lw=0.6, ls=(0, (3, 2)), zorder=0)
    y = top - 0.15
    c = t["C"]

    def msg(x1, x2, text, color="#1a237e", ls="-"):
        nonlocal y
        arrow(ax, (x1, y), (x2, y), color=color, ls=ls, lw=0.7, ms=4.5)
        ax.text((x1 + x2) / 2, y + 0.022, text, ha="center", va="bottom", fontsize=5.7, color=color,
                bbox=dict(boxstyle="square,pad=0.03", fc="white", ec="none"))
        y -= rh

    msg(C, MQ, c[0])
    msg(MQ, W1, c[1])
    msg(W1, MY, c[2])
    ax.text(W1, y + 0.0, "\u00d7", ha="center", va="center", fontsize=9, color="#b71c1c", fontweight="bold")
    ax.text(W1 + 0.1, y + 0.0, c[3], ha="left", va="center", fontsize=5.8, color="#b71c1c", fontweight="bold",
            linespacing=1.05); y -= rh * 1.15
    # the BROKER notices the closed connection and requeues the unacknowledged message
    ax.text(MQ, y + 0.0, c[4], ha="center", va="center", fontsize=5.6, color="#b71c1c", linespacing=1.05,
            bbox=dict(boxstyle="round,pad=0.2", fc="#fff5f5", ec="#b71c1c", lw=0.5, ls="--")); y -= rh * 1.2
    msg(MQ, W2, c[5])
    msg(W2, MY, c[6])
    msg(W2, MQ, c[7], color="#1b5e20", ls="--")       # reply vào queue trả lời trên broker
    msg(MQ, C, c[8], color="#1b5e20", ls="--")        # broker giao reply cho client
    msg(W2, MQ, c[9])
    fig.savefig(os.path.join(HERE, "figs", lang, "seq_crash.png"), dpi=300, bbox_inches="tight", pad_inches=0.02,
                facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------------------------- charts
def fmt_factory(lang):
    dec = T[lang]["dec"]
    return lambda v, pos=None: (f"{v:g}").replace(".", dec)


def fig_scaling(lang: str) -> None:
    t = T[lang]
    f = fmt_factory(lang)
    fig, ax = plt.subplots(figsize=(W, 2.15))
    ws = [1, 2, 3, 4, 5, 6]
    ax.plot(ws, [DRAIN_TUNED[1] * w for w in ws], ls=(0, (4, 3)), color=GRAY, lw=0.8, label=t["S_ideal"])
    xd, yd = list(DRAIN_DEFAULT), list(DRAIN_DEFAULT.values())
    xt, yt = list(DRAIN_TUNED), list(DRAIN_TUNED.values())
    ax.plot(xt, yt, "o-", color=BLUE, lw=1.3, ms=3.8, label=t["S_tun"])
    ax.plot(xd, yd, "s-", color=ORANGE, lw=1.3, ms=3.6, label=t["S_def"])
    for x, y in zip(xt, yt):
        ax.annotate(f"{y:.2f}".replace(".", T[lang]["dec"]), (x, y), textcoords="offset points", xytext=(0, -9), ha="center", fontsize=6, color=BLUE)
    for x, y in zip(xd, yd):
        ax.annotate(f"{y:.2f}".replace(".", T[lang]["dec"]), (x, y), textcoords="offset points", xytext=(0, 4.5), ha="center", fontsize=6, color=ORANGE)
    ax.set_xlabel(t["S_x"]); ax.set_ylabel(t["S_y"])
    ax.set_xticks(ws); ax.set_ylim(0, 22); ax.set_xlim(0.7, 6.3)
    ax.yaxis.set_major_formatter(FuncFormatter(f))
    ax.grid(alpha=0.25, lw=0.4)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(loc="upper left", frameon=False, fontsize=6, handlelength=2.2)
    fig.tight_layout(pad=0.3)
    fig.savefig(os.path.join(HERE, "figs", lang, "scaling.png"), dpi=300, facecolor="white")
    plt.close(fig)


def fig_bench(lang: str) -> None:
    t = T[lang]
    f = fmt_factory(lang)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(W, 1.75))
    ws = list(BENCH)
    a1.bar(ws, [BENCH[w][0] for w in ws], color=BLUE, width=0.6)
    for w in ws:
        a1.text(w, BENCH[w][0] + 0.15, f(BENCH[w][0]), ha="center", va="bottom", fontsize=6)
    a1.set_xlabel(t["B_x"]); a1.set_title(t["B_thr"], fontsize=6.5)
    a1.set_xticks(ws); a1.set_ylim(0, 10); a1.set_xlim(0.4, 4.6)
    a2.plot(ws, [BENCH[w][1] / 1000 for w in ws], "o-", color=BLUE, lw=1.2, ms=3.5, label=t["p50"])
    a2.plot(ws, [BENCH[w][2] / 1000 for w in ws], "s--", color=ORANGE, lw=1.2, ms=3.3, label=t["p95"])
    a2.set_xlabel(t["B_x"]); a2.set_title(t["B_lat"], fontsize=6.5)
    a2.set_xticks(ws); a2.set_ylim(0, 4.2); a2.set_xlim(0.4, 4.6)
    a2.legend(frameon=False, fontsize=6, loc="upper right")
    for a in (a1, a2):
        a.grid(axis="y", alpha=0.25, lw=0.4)
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
        a.yaxis.set_major_formatter(FuncFormatter(f))
    fig.tight_layout(pad=0.4, w_pad=1.0)
    fig.savefig(os.path.join(HERE, "figs", lang, "bench.png"), dpi=300, facecolor="white")
    plt.close(fig)


def fig_latency(lang: str) -> None:
    """Latency of EVERY request of the benchmark (results/bench_w{1,2,4}.csv), in submission order."""
    import csv
    t = T[lang]
    f = fmt_factory(lang)
    root = os.path.abspath(os.path.join(HERE, "..", ".."))
    fig, ax = plt.subplots(figsize=(W, 1.9))
    style = {1: (ORANGE, "s", "-"), 2: ("#2e9d5b", "^", "-"), 4: (BLUE, "o", "-")}
    for w in (1, 2, 4):
        with open(os.path.join(root, "results", f"bench_w{w}.csv"), encoding="utf-8") as fh:
            rows = sorted(csv.DictReader(fh), key=lambda r: int(r["i"]))
        xs = [int(r["i"]) + 1 for r in rows]
        ys = [float(r["latency_ms"]) / 1000 for r in rows]
        col, mk, ls = style[w]
        ax.plot(xs, ys, ls, marker=mk, color=col, lw=0.7, ms=1.7, label=(t["L_w1"] if w == 1 else t["L_wn"]).format(w))
    ax.set_xlabel(t["L_x"]); ax.set_ylabel(t["L_y"])
    ax.set_xlim(0, 101); ax.set_ylim(0, 4.2)
    ax.yaxis.set_major_formatter(FuncFormatter(f))
    ax.grid(alpha=0.25, lw=0.4)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(loc="lower right", frameon=False, fontsize=6, handlelength=2.0)
    fig.tight_layout(pad=0.3)
    fig.savefig(os.path.join(HERE, "figs", lang, "latency_req.png"), dpi=300, facecolor="white")
    plt.close(fig)


def fig_queue(lang: str) -> None:
    """Queue depth over time during a burst, from results/queue_depth_w*.csv (tools.chaos watch). Skipped if absent."""
    import csv
    import glob
    import re
    root = os.path.abspath(os.path.join(HERE, "..", ".."))
    files = sorted(glob.glob(os.path.join(root, "results", "queue_depth_w*.csv")),
                   key=lambda f: int(re.search(r"_w(\d+)", f).group(1)))
    if not files:
        return
    t = T[lang]
    f = fmt_factory(lang)
    fig, ax = plt.subplots(figsize=(W, 1.9))
    colors = [ORANGE, BLUE, "#2e9d5b", GRAY]
    for k, path in enumerate(files):
        n = int(re.search(r"_w(\d+)", path).group(1))
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        xs = [float(r["t_s"]) for r in rows]
        ys = [int(r["ready"]) for r in rows]
        i0 = next((i for i, y in enumerate(ys) if y > 0), 0)          # start the axis where the burst arrives
        t0 = xs[max(i0 - 1, 0)]
        ax.plot([x - t0 for x in xs[max(i0 - 1, 0):]], ys[max(i0 - 1, 0):], "-", color=colors[k % len(colors)], lw=1.1,
                label=(t["L_w1"] if n == 1 else t["L_wn"]).format(n))
    ax.set_xlabel(t["Q_x"]); ax.set_ylabel(t["Q_y"])
    ax.set_xlim(left=0); ax.set_ylim(bottom=0)
    ax.yaxis.set_major_formatter(FuncFormatter(f))
    ax.grid(alpha=0.25, lw=0.4)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.legend(loc="upper right", frameon=False, fontsize=6, handlelength=2.0)
    fig.tight_layout(pad=0.3)
    fig.savefig(os.path.join(HERE, "figs", lang, "queue_depth.png"), dpi=300, facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------------------------------------- RabbitMQ topology
MQ_T = {
    "en": {
        "pa": "(a) Request–reply path",
        "client": "Branch client A–D · its own RabbitMQ account\nwrite: only coffee.requests · configure/read: only reply.* queues",
        "pub": "publish (persistent, confirmed)\nrk = face.requests | orders\nreply_to · correlation_id",
        "cons": "consume the reply,\nmatch correlation_id",
        "ex": "coffee.requests\ndirect exchange · durable",
        "bind": "binding = queue name",
        "qf": "face.requests\nquorum · durable\ndelivery-limit 3",
        "qo": "orders\nquorum · durable\ndelivery-limit 3",
        "dx": "default exchange “”\nrk = reply_to",
        "rq": "reply.<branch>.<uuid>\nexclusive · auto-delete",
        "deliver": "deliver", "reply": "reply",
        "wk": "Worker pool 1…N: competing consumers on both queues\nprefetch = 1 · ack only after the reply is published",
        "pb": "(b) Dead-lettering: a worker rejects with requeue = false, or delivery count > 3",
        "f": "face.requests", "o": "orders",
        "dlx": "coffee.dlx\ndirect · rk = DLQ name",
        "fd": "face.requests.dlq\nclassic · durable", "od": "orders.dlq\nclassic · durable",
    },
    "vi": {
        "pa": "(a) Đường yêu cầu – trả lời",
        "client": "Client chi nhánh A–D · tài khoản RabbitMQ riêng\nghi: chỉ coffee.requests · tạo/đọc: chỉ queue reply.*",
        "pub": "publish (bền vững, có xác nhận)\nrk = face.requests | orders\nreply_to · correlation_id",
        "cons": "nhận reply,\nkhớp correlation_id",
        "ex": "coffee.requests\ndirect exchange · bền vững",
        "bind": "binding = tên queue",
        "qf": "face.requests\nquorum · bền vững\ndelivery-limit 3",
        "qo": "orders\nquorum · bền vững\ndelivery-limit 3",
        "dx": "default exchange “”\nrk = reply_to",
        "rq": "reply.<chi nhánh>.<uuid>\nexclusive · auto-delete",
        "deliver": "giao", "reply": "reply",
        "wk": "Nhóm worker 1…N: consumer cạnh tranh trên cả hai queue\nprefetch = 1 · chỉ ack sau khi đã publish reply",
        "pb": "(b) Dead-letter: worker từ chối với requeue = false, hoặc giao quá 3 lần",
        "f": "face.requests", "o": "orders",
        "dlx": "coffee.dlx\ndirect · rk = tên DLQ",
        "fd": "face.requests.dlq\nclassic · bền vững", "od": "orders.dlq\nclassic · bền vững",
    },
}


def fig_mq(lang: str) -> None:
    """Topology RabbitMQ đúng như common/rabbit.py: (a) đường yêu cầu – trả lời, (b) dead-letter từ queue qua DLX."""
    t = MQ_T[lang]
    H = 3.97
    GREEN, RED, ORANGE, GREY = "#2e7d32", "#b71c1c", "#fff1de", "#f1f1f1"
    fig = plt.figure(figsize=(W, H)); ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    xl, xr = 1.145, 2.905                                     # tâm cột trái (yêu cầu) và cột phải (trả lời)
    ax.text(0.04, 3.885, t["pa"], fontsize=5.4, ha="left", va="center", fontweight="bold")
    box(ax, 0.04, 3.30, W - 0.08, 0.50, t["client"], "#e3f0fc", fs=5.3)
    arrow(ax, (xl, 3.30), (xl, 2.98), color=BLUE)
    ax.text(xl - 0.05, 3.14, t["pub"], fontsize=4.6, ha="right", va="center", color=BLUE, linespacing=1.1)
    arrow(ax, (xr, 2.98), (xr, 3.30), color=GREEN, ls="--", lw=0.7)
    ax.text(xr - 0.05, 3.14, t["cons"], fontsize=4.6, ha="right", va="center", color=GREEN, linespacing=1.1)
    box(ax, xl - 0.55, 2.62, 1.10, 0.36, t["ex"], ORANGE, fs=5.2)
    box(ax, 2.40, 2.62, 1.01, 0.36, t["rq"], "#e8f5e9", fs=5.0)
    arrow(ax, (xl - 0.20, 2.62), (0.55, 2.34))
    arrow(ax, (xl + 0.20, 2.62), (1.70, 2.34))
    ax.text(xl, 2.475, t["bind"], fontsize=4.5, ha="center", va="center", color="#444444")
    box(ax, 0.04, 1.90, 1.02, 0.44, t["qf"], GREY, fs=5.0)
    box(ax, 1.19, 1.90, 1.02, 0.44, t["qo"], GREY, fs=5.0)
    box(ax, 2.40, 1.90, 1.01, 0.44, t["dx"], "#fafafa", fs=5.0)
    arrow(ax, (xr, 2.34), (xr, 2.62), color=GREEN, ls="--", lw=0.7)
    for x in (0.55, 1.70):
        arrow(ax, (x, 1.90), (x, 1.56))
    ax.text(0.60, 1.73, t["deliver"], fontsize=4.8, ha="left", va="center")
    arrow(ax, (xr, 1.56), (xr, 1.90), color=GREEN, ls="--", lw=0.7)
    ax.text(xr - 0.05, 1.73, t["reply"], fontsize=4.8, ha="right", va="center", color=GREEN)
    box(ax, 0.04, 1.12, W - 0.08, 0.44, t["wk"], "#e4f4e6", fs=5.3)
    ax.plot([0.04, W - 0.04], [1.03, 1.03], color="#aaaaaa", lw=0.5, ls="--")
    # ---- (b) dead-letter: từ queue (không phải từ worker) qua coffee.dlx tới DLQ tương ứng
    ax.text(0.04, 0.905, t["pb"], fontsize=5.0, ha="left", va="center", fontweight="bold")
    box(ax, 0.04, 0.45, 0.85, 0.30, t["f"], GREY, fs=5.0)
    box(ax, 0.04, 0.08, 0.85, 0.30, t["o"], GREY, fs=5.0)
    box(ax, 1.30, 0.235, 0.85, 0.36, t["dlx"], ORANGE, fs=5.0)
    box(ax, 2.56, 0.45, 0.85, 0.30, t["fd"], "#fdecea", fs=4.8)
    box(ax, 2.56, 0.08, 0.85, 0.30, t["od"], "#fdecea", fs=4.8)
    for (p, q) in (((0.89, 0.60), (1.30, 0.47)), ((0.89, 0.23), (1.30, 0.36)),
                   ((2.15, 0.47), (2.56, 0.60)), ((2.15, 0.36), (2.56, 0.23))):
        arrow(ax, p, q, color=RED, ls="--", lw=0.7)
    fig.savefig(os.path.join(HERE, "figs", lang, "rabbitmq_topology.png"), dpi=300, bbox_inches="tight",
                pad_inches=0.02, facecolor="white")
    plt.close(fig)


def main() -> None:
    for lang in ("en", "vi"):
        os.makedirs(os.path.join(HERE, "figs", lang), exist_ok=True)
        fig_architecture(lang)
        fig_mq(lang)
        seq_flow(lang)
        seq_crash(lang)
        fig_scaling(lang)
        fig_bench(lang)
        fig_latency(lang)
        fig_queue(lang)
        print("ok", lang)


if __name__ == "__main__":
    main()
