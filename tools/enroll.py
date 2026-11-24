"""Đăng ký khách từ thư mục ảnh (đường offline, không qua RabbitMQ) - dùng để nạp dữ liệu ban đầu
và kiểm chứng pipeline nhận diện ở tuần 2.

    python -m tools.enroll --dataset dataset --per-person 2 --branch A

dataset/<tên_người>/<ảnh>.jpg  ->  mỗi thư mục là một khách (full_name = tên thư mục).
Chạy từ máy host nên cần PG_HOST/MYSQL_HOST=localhost (docker-compose đã publish cổng 5432, 3306)
và PHOTO_DIR trỏ tới thư mục ảnh (mặc định ./data/photos).

Chạy lại không tạo khách trùng: người nào đã nhận ra được trong pgvector thì bị bỏ qua.
"""
from __future__ import annotations

import argparse
import sys

from client.imaging import load_image, prepare_jpeg
from common.config import load_settings
from tools.eval_threshold import list_people
from worker.worker import build_service


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", required=True)
    p.add_argument("--per-person", type=int, default=2, help="số ảnh đầu tiên của mỗi người dùng để enroll")
    p.add_argument("--branch", default="A")
    args = p.parse_args()

    settings = load_settings()
    service = build_service(settings, "enroll-tool")
    service.engine.warm_up()

    people = list_people(args.dataset)
    if not people:
        print("Không thấy ảnh nào trong dataset", file=sys.stderr)
        return 2

    created = skipped = 0
    for name, files in people.items():
        jpegs = [prepare_jpeg(load_image(f)) for f in files[: args.per_person]]
        # đã có người này chưa?
        existing = None
        for jpeg in jpegs:
            analysis = service.engine.analyze(jpeg)
            if analysis.status is None:
                hits = service.vectors.search(analysis.embedding, 1)
                if hits and hits[0][1] >= settings.sim_threshold:
                    existing = hits[0]
                break
        if existing:
            print(f"= {name}: đã có (customer_id={existing[0]}, sim={existing[1]:.3f}) -> bỏ qua")
            skipped += 1
            continue
        result = service.enroll_person(name, jpegs, args.branch)
        if result["customer_id"] is None:
            print(f"! {name}: không ảnh nào dùng được {result['skipped']}")
            continue
        print(f"+ {name}: customer_id={result['customer_id']}, {result['added']} embedding"
              + (f", bỏ qua {len(result['skipped'])} ảnh" if result["skipped"] else ""))
        created += 1
    print(f"xong: tạo {created}, bỏ qua {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
