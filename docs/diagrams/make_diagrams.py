"""Sinh toàn bộ sơ đồ cho báo cáo (PNG) từ mã nguồn, để sơ đồ luôn khớp với thiết kế.

    python docs/diagrams/make_diagrams.py        # cần graphviz (lệnh `dot`) và matplotlib

Kết quả: docs/diagrams/*.png
"""
from __future__ import annotations

import os
import subprocess
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = "DejaVu Sans"


# ====================================================================== graphviz
def run_dot(name: str, source: str) -> None:
    dot_path = os.path.join(HERE, f"{name}.dot")
    png_path = os.path.join(HERE, f"{name}.png")
    with open(dot_path, "w", encoding="utf-8") as fh:
        fh.write(source)
    subprocess.run(["dot", "-Tpng", "-Gdpi=170", dot_path, "-o", png_path], check=True)
    os.remove(dot_path)
    print("ok", png_path)


ARCH = f"""
digraph G {{
  rankdir=TB; nodesep=0.5; ranksep=0.7; bgcolor="white";
  node [fontname="{FONT}", fontsize=11, shape=box, style="rounded,filled", fillcolor="#f5f5f5", color="#555555"];
  edge [fontname="{FONT}", fontsize=10, color="#444444"];

  subgraph cluster_branch {{ label="Chi nhánh (client = ứng dụng di động mô phỏng bằng laptop + webcam)"; fontname="{FONT}"; fontsize=11; style=dashed; color="#999999";
    A [label="Chi nhánh A\\nlaptop + webcam", fillcolor="#e3f2fd"];
    B [label="Chi nhánh B\\nlaptop + webcam", fillcolor="#e3f2fd"];
    C [label="Chi nhánh C\\nlaptop + webcam", fillcolor="#e3f2fd"];
  }}

  subgraph cluster_server {{ label="Máy chủ (Docker Compose)"; fontname="{FONT}"; fontsize=11; style=dashed; color="#999999";
    MQ [label="RabbitMQ\\nexchange coffee.requests\\nqueue face.requests · orders\\nreply.<chi nhánh>.<uuid>\\nDLQ", fillcolor="#fff3e0", shape=box3d];
    W1 [label="Worker 1\\nInsightFace + ONNX Runtime", fillcolor="#e8f5e9"];
    W2 [label="Worker 2\\nInsightFace + ONNX Runtime", fillcolor="#e8f5e9"];
    W3 [label="Worker N\\nInsightFace + ONNX Runtime", fillcolor="#e8f5e9"];
    PG [label="PostgreSQL + pgvector\\nface_embeddings (HNSW, cosine)", shape=cylinder, fillcolor="#ede7f6"];
    MY [label="MySQL 8\\nkhách · ảnh · đơn hàng · nhật ký", shape=cylinder, fillcolor="#ede7f6"];
    FS [label="Thư mục ảnh\\n(volume dùng chung)", shape=folder, fillcolor="#fffde7"];
  }}

  A -> MQ [label="request"]; B -> MQ; C -> MQ;
  MQ -> A [label="reply", style=dashed, constraint=false];
  MQ -> W1 [label="competing\\nconsumers\\nprefetch=1"]; MQ -> W2; MQ -> W3;
  W1 -> PG [label="vector → customer_id"]; W2 -> PG; W3 -> PG;
  W1 -> MY [label="customer_id → hồ sơ, đơn"]; W2 -> MY; W3 -> MY;
  W1 -> FS; W2 -> FS; W3 -> FS;
}}
"""

TOPOLOGY = f"""
digraph G {{
  rankdir=TB; nodesep=0.5; ranksep=0.75; bgcolor="white";
  node [fontname="{FONT}", fontsize=11, style="filled", color="#555555"];
  edge [fontname="{FONT}", fontsize=10, color="#444444"];

  P [label="Client chi nhánh\\n(quyền: chỉ publish)", shape=box, style="rounded,filled", fillcolor="#e3f2fd"];
  EX [label="coffee.requests\\n(direct, durable)", shape=hexagon, fillcolor="#fff3e0"];
  QF [label="face.requests\\n(quorum, durable)", shape=box, style="filled", fillcolor="#fff8e1"];
  QO [label="orders\\n(quorum, durable)", shape=box, style="filled", fillcolor="#fff8e1"];
  W [label="Worker × N\\nack thủ công · prefetch=1", shape=box, style="rounded,filled", fillcolor="#e8f5e9"];
  DLX [label="coffee.dlx\\n(direct)", shape=hexagon, fillcolor="#ffebee"];
  DF [label="face.requests.dlq", shape=box, style="filled", fillcolor="#ffebee"];
  DO [label="orders.dlq", shape=box, style="filled", fillcolor="#ffebee"];
  RQ [label="reply.<chi nhánh>.<uuid>\\n(exclusive, auto-delete)", shape=box, style="filled", fillcolor="#e1f5fe"];

  P -> EX [label="publish\\ncorrelation_id, reply_to"];
  EX -> QF [label="rk=face.requests"]; EX -> QO [label="rk=orders"];
  QF -> W; QO -> W;
  W -> RQ [label="reply (default exchange)\\nrouting_key = reply_to"];
  RQ -> P [style=dashed, label="correlation_id"];
  QF -> DLX [style=dotted, label="reject / hết lượt giao"]; QO -> DLX [style=dotted];
  DLX -> DF; DLX -> DO;
}}
"""

ERD = f"""
digraph G {{
  rankdir=LR; nodesep=0.5; ranksep=0.8; bgcolor="white";
  node [fontname="{FONT}", fontsize=10, shape=plain];
  edge [fontname="{FONT}", fontsize=9, color="#555555", arrowhead=crow, arrowtail=none, dir=both, arrowtail=tee];

  branches [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4"><tr><td bgcolor="#d1c4e9"><b>branches</b></td></tr>
    <tr><td align="left">branch_id PK</td></tr><tr><td align="left">name</td></tr><tr><td align="left">address</td></tr></table>>];
  customers [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4"><tr><td bgcolor="#bbdefb"><b>customers</b></td></tr>
    <tr><td align="left">customer_id PK</td></tr><tr><td align="left">full_name NULL</td></tr><tr><td align="left">phone NULL</td></tr>
    <tr><td align="left">first_branch_id FK</td></tr><tr><td align="left">created_at · last_seen_at</td></tr></table>>];
  photos [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4"><tr><td bgcolor="#bbdefb"><b>customer_photos</b></td></tr>
    <tr><td align="left">photo_id PK</td></tr><tr><td align="left">customer_id FK</td></tr><tr><td align="left">file_path</td></tr>
    <tr><td align="left">branch_id FK · captured_at</td></tr></table>>];
  orders [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4"><tr><td bgcolor="#c8e6c9"><b>orders</b></td></tr>
    <tr><td align="left">order_id PK</td></tr><tr><td align="left">request_id UNIQUE</td></tr><tr><td align="left">customer_id FK · branch_id FK</td></tr>
    <tr><td align="left">ordered_at · total_amount</td></tr><tr><td align="left" bgcolor="#f1f8e9">INDEX (customer_id, ordered_at DESC)</td></tr></table>>];
  items [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4"><tr><td bgcolor="#c8e6c9"><b>order_items</b></td></tr>
    <tr><td align="left">item_id PK</td></tr><tr><td align="left">order_id FK</td></tr><tr><td align="left">item_name · quantity</td></tr>
    <tr><td align="left">unit_price · options</td></tr></table>>];
  logs [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4"><tr><td bgcolor="#ffe0b2"><b>recognition_logs</b></td></tr>
    <tr><td align="left">log_id PK</td></tr><tr><td align="left" bgcolor="#fff3e0">request_id UNIQUE</td></tr><tr><td align="left">branch_id · customer_id NULL</td></tr>
    <tr><td align="left">status · similarity</td></tr><tr><td align="left">latency_ms · worker · created_at</td></tr></table>>];
  emb [label=<<table border="0" cellborder="1" cellspacing="0" cellpadding="4"><tr><td bgcolor="#e1bee7"><b>face_embeddings</b> (PostgreSQL)</td></tr>
    <tr><td align="left">embedding_id PK</td></tr><tr><td align="left" bgcolor="#f3e5f5">customer_id (khoá logic, không FK)</td></tr>
    <tr><td align="left">embedding vector(512)</td></tr><tr><td align="left">INDEX HNSW (cosine)</td></tr></table>>];

  branches -> customers; branches -> orders; branches -> photos;
  customers -> photos; customers -> orders; orders -> items;
  customers -> emb [style=dashed, label="customer_id\\n(giữa 2 CSDL)", arrowhead=none, arrowtail=none, dir=none];
  customers -> logs [style=dotted, arrowhead=none, dir=none, label="không FK"];
}}
"""


# ====================================================================== sequence diagrams (matplotlib)
def sequence(name: str, title: str, actors, steps, width=11.0):
    """steps: ('msg', src, dst, text[, style]) | ('note', actor, text) | ('sep', text)
    style: 'solid' (mặc định) | 'dashed' (trả lời) | 'x' (sự cố)"""
    n = len(actors)
    xs = {a: (i + 0.5) * (width / n) for i, a in enumerate(actors)}
    rows = len(steps)
    height = 1.6 + 0.62 * rows
    fig, ax = plt.subplots(figsize=(width, height))
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis("off")
    top = height - 0.9
    ax.text(width / 2, height - 0.25, title, ha="center", va="center", fontsize=12, fontweight="bold")
    for a, x in xs.items():
        ax.add_patch(FancyBboxPatch((x - 0.85, top), 1.7, 0.5, boxstyle="round,pad=0.02", fc="#e3f2fd", ec="#37474f"))
        ax.text(x, top + 0.25, a, ha="center", va="center", fontsize=8.5)
        ax.plot([x, x], [0.3, top], color="#90a4ae", lw=1, ls=(0, (4, 3)), zorder=0)
    y = top - 0.5
    for step in steps:
        kind = step[0]
        if kind == "msg":
            _, s, d, text = step[:4]
            style = step[4] if len(step) > 4 else "solid"
            x1, x2 = xs[s], xs[d]
            color = {"solid": "#1a237e", "dashed": "#1b5e20", "x": "#b71c1c"}[style]
            ls = "--" if style == "dashed" else "-"
            if s == d:
                ax.annotate("", xy=(x1, y - 0.2), xytext=(x1 + 0.0, y + 0.05),
                            arrowprops=dict(arrowstyle="->", color=color, connectionstyle="arc3,rad=-1.6", lw=1.2))
                ax.text(x1 + 0.35, y - 0.08, text, ha="left", va="center", fontsize=8, color=color)
            else:
                ax.annotate("", xy=(x2, y), xytext=(x1, y), arrowprops=dict(arrowstyle="->", color=color, lw=1.3, ls=ls))
                ax.text((x1 + x2) / 2, y + 0.1, text, ha="center", va="bottom", fontsize=8, color=color)
        elif kind == "note":
            _, a, text = step
            ax.text(xs[a], y, text, ha="center", va="center", fontsize=8,
                    bbox=dict(boxstyle="round,pad=0.3", fc="#fff8e1", ec="#f9a825"))
        elif kind == "sep":
            ax.plot([0.2, width - 0.2], [y + 0.28, y + 0.28], color="#bdbdbd", lw=0.8)
            ax.text(0.25, y + 0.08, step[1], fontsize=8, color="#616161", style="italic", va="center")
        y -= 0.62
    fig.savefig(os.path.join(HERE, f"{name}.png"), dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("ok", os.path.join(HERE, f"{name}.png"))


def build_sequences():
    C, MQ, W, PG, MY = "Client chi nhánh", "RabbitMQ", "Worker", "PostgreSQL\n+ pgvector", "MySQL"
    sequence("seq_flow_a", "Luồng A - khách quen được nhận ra", [C, MQ, W, PG, MY], [
        ("msg", C, MQ, "publish face.requests\n(correlation_id, reply_to, ảnh JPEG)"),
        ("msg", MQ, W, "giao message (prefetch=1)"),
        ("msg", W, MY, "tra recognition_logs theo request_id"),
        ("note", W, "SCRFD detect + căn chỉnh\nArcFace 512-d, chuẩn hoá L2"),
        ("msg", W, PG, "tìm embedding gần nhất (cosine)"),
        ("msg", PG, W, "customer_id, similarity ≥ ngưỡng", "dashed"),
        ("msg", W, MY, "lấy hồ sơ + đơn gần nhất; ghi log"),
        ("msg", W, C, "reply RECOGNIZED (qua reply_to)", "dashed"),
        ("msg", W, MQ, "ack message gốc (SAU khi đã reply)"),
    ])
    sequence("seq_flow_b", "Luồng B - khách mới (khoá + saga)", [C, MQ, W, PG, MY], [
        ("msg", MQ, W, "giao face.requests"),
        ("msg", W, PG, "tìm gần nhất: dưới ngưỡng"),
        ("msg", W, PG, "BEGIN; pg_advisory_xact_lock"),
        ("msg", W, PG, "tìm lại trong khoá (double-check)"),
        ("msg", W, MY, "1) INSERT customers + ảnh"),
        ("msg", W, PG, "2) INSERT embedding; COMMIT (nhả khoá)"),
        ("note", W, "Lỗi ở bước 2 → bù trừ:\nxoá customer + file ảnh (saga)"),
        ("msg", W, MY, "ghi recognition_logs"),
        ("msg", W, C, "reply NEW_CUSTOMER + customer_id", "dashed"),
        ("msg", W, MQ, "ack"),
    ])
    sequence("seq_flow_c", "Luồng C - lưu đơn hàng", [C, MQ, W, MY], [
        ("msg", C, MQ, "publish order.create → queue orders"),
        ("msg", MQ, W, "giao message"),
        ("msg", W, MY, "BEGIN; INSERT orders (request_id UNIQUE)\n+ order_items; COMMIT"),
        ("note", W, "Trùng request_id → trả lại đơn cũ,\nkhông tạo đơn thứ hai"),
        ("msg", W, C, "reply ORDER_SAVED + order_id", "dashed"),
        ("msg", W, MQ, "ack"),
    ])
    sequence("seq_worker_crash", "Worker chết giữa chừng: giao lại + idempotent", [C, MQ, "Worker 1", "Worker 2", MY], [
        ("msg", C, MQ, "publish request (request_id = R)"),
        ("msg", MQ, "Worker 1", "giao message (chưa ack)"),
        ("msg", "Worker 1", MY, "ghi khách + log (request_id = R)"),
        ("msg", "Worker 1", "Worker 1", "CHẾT (kill)", "x"),
        ("note", MQ, "Phát hiện kết nối đứt\n→ trả message về hàng đợi"),
        ("msg", MQ, "Worker 2", "giao lại cùng message"),
        ("msg", "Worker 2", MY, "log request_id = R đã có"),
        ("msg", "Worker 2", C, "reply kết quả đã lưu (duplicate=true)", "dashed"),
        ("msg", "Worker 2", MQ, "ack"),
    ])
    sequence("seq_client_retry", "Client timeout rồi gửi lại cùng request_id", [C, MQ, W, MY], [
        ("msg", C, MQ, "publish (request_id = R)"),
        ("msg", MQ, W, "giao message"),
        ("note", C, "10 giây không có reply"),
        ("msg", C, MQ, "gửi lại, CÙNG request_id = R"),
        ("msg", W, MY, "lần 1 ghi xong log R"),
        ("msg", W, C, "reply lần 1 (client không còn nhận)", "x"),
        ("msg", MQ, W, "giao bản gửi lại"),
        ("msg", W, MY, "tra log: R đã xử lý"),
        ("msg", W, C, "reply kết quả đã lưu; không tạo khách trùng", "dashed"),
    ])


def main() -> int:
    run_dot("architecture", ARCH)
    run_dot("topology", TOPOLOGY)
    run_dot("erd", ERD)
    build_sequences()
    return 0


if __name__ == "__main__":
    sys.exit(main())
