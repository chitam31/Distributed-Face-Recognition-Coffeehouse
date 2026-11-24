"""Client dòng lệnh của chi nhánh (dùng để kiểm thử nhanh và viết kịch bản demo).

Ví dụ (đặt RABBIT_HOST / RABBIT_USER / RABBIT_PASS trước, hoặc truyền --host ...):

    python -m client.branch_client --branch A recognize --image dataset/an/1.jpg --name "Nguyễn An"
    python -m client.branch_client --branch B recognize --image dataset/an/2.jpg
    python -m client.branch_client --branch B order --customer-id 1 --item "Cà phê sữa đá:2:29000:ít đá"
    python -m client.branch_client --branch A update --customer-id 1 --name "Nguyễn An" --phone 0901234567
    python -m client.branch_client --branch A camera          # webcam: SPACE = chụp, Q = thoát

Giao diện đồ hoạ: python -m client.gui --branch A
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List

from common.config import Settings, load_settings
from common.messages import build_customer_update, build_order_request, build_recognize_request
from client.imaging import Camera, load_jpeg_for_sending, prepare_jpeg
from client.presenter import describe
from client.rpc_client import BranchRpcClient, RpcError


def parse_item(text: str) -> Dict[str, Any]:
    """'Tên món:số lượng:đơn giá[:tuỳ chọn]'  ->  dict món."""
    parts = text.split(":")
    if len(parts) < 3:
        raise argparse.ArgumentTypeError("món phải có dạng 'Tên:SL:Giá[:Tuỳ chọn]'")
    try:
        return {"item_name": parts[0].strip(), "quantity": int(parts[1]), "unit_price": float(parts[2]),
                "options": ":".join(parts[3:]).strip() or None}
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"số lượng/giá không hợp lệ trong '{text}'") from exc


def make_settings(args: argparse.Namespace) -> Settings:
    s = load_settings()
    overrides = {k: v for k, v in (("rabbit_host", args.host), ("rabbit_user", args.user),
                                   ("rabbit_pass", args.password)) if v}
    if overrides:
        from dataclasses import replace
        s = replace(s, **overrides)
    return s


def print_response(resp: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    else:
        print("\n".join(describe(resp)))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Client chi nhánh quán cà phê")
    p.add_argument("--branch", default=os.environ.get("BRANCH_ID", "A"), help="mã chi nhánh (A/B/C...)")
    p.add_argument("--host"), p.add_argument("--user"), p.add_argument("--password")
    p.add_argument("--timeout", type=float, default=10.0, help="timeout mỗi lần chờ reply (giây)")
    p.add_argument("--attempts", type=int, default=3, help="số lần thử (gửi lại cùng request_id)")
    p.add_argument("--json", action="store_true", help="in JSON thô")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("recognize", help="gửi một ảnh để nhận diện")
    r.add_argument("--image", required=True)
    r.add_argument("--name", help="tên khách (dùng khi đây là khách mới)")
    r.add_argument("--no-enroll", action="store_true", help="không tự đăng ký khách mới")
    r.add_argument("--request-id", help="ép request_id (để thử idempotency)")

    sub.add_parser("camera", help="chụp bằng webcam")

    o = sub.add_parser("order", help="lưu đơn hàng")
    o.add_argument("--customer-id", type=int, required=True)
    o.add_argument("--item", type=parse_item, action="append", required=True)
    o.add_argument("--note")
    o.add_argument("--request-id")

    u = sub.add_parser("update", help="cập nhật tên / số điện thoại khách")
    u.add_argument("--customer-id", type=int, required=True)
    u.add_argument("--name")
    u.add_argument("--phone")
    return p


def run_camera(client: BranchRpcClient, args) -> int:
    import cv2
    cam = Camera()
    if not cam.is_open():
        print("Không mở được webcam", file=sys.stderr)
        return 2
    print("SPACE = chụp và nhận diện · Q = thoát")
    try:
        while True:
            frame = cam.read()
            if frame is None:
                continue
            cv2.imshow(f"Chi nhanh {args.branch}", frame)
            key = cv2.waitKey(30) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == 32:
                msg = build_recognize_request(args.branch, prepare_jpeg(frame))
                try:
                    print("\n".join(describe(client.recognize(msg))), "\n")
                except RpcError as exc:
                    print(f"LỖI: {exc}\n")
    finally:
        cam.release()
        cv2.destroyAllWindows()
    return 0


def main(argv: List[str] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    client = BranchRpcClient(make_settings(args), args.branch, timeout_s=args.timeout, max_attempts=args.attempts)
    try:
        if args.cmd == "recognize":
            msg = build_recognize_request(args.branch, load_jpeg_for_sending(args.image),
                                          auto_enroll=not args.no_enroll, full_name=args.name,
                                          request_id=args.request_id)
            resp = client.recognize(msg)
        elif args.cmd == "order":
            resp = client.send_command(build_order_request(args.branch, args.customer_id, args.item,
                                                           note=args.note, request_id=args.request_id))
        elif args.cmd == "update":
            resp = client.send_command(build_customer_update(args.branch, args.customer_id,
                                                             full_name=args.name, phone=args.phone))
        else:
            return run_camera(client, args)
        print_response(resp, args.json)
        return 0 if resp.get("status") != "ERROR" else 1
    except (RpcError, ValueError) as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
