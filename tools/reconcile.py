"""Đối chiếu và dọn dữ liệu giữa MySQL và pgvector.

Hai CSDL không có transaction phân tán, nên nếu worker chết đúng giữa "ghi MySQL" và "ghi pgvector"
(hoặc bước bù trừ cũng lỗi) có thể còn lại dữ liệu mồ côi:
  * khách trong MySQL không có embedding nào  (không bao giờ được nhận ra)
  * embedding trong pgvector không có khách MySQL (worker tự dọn khi gặp, nhưng quét chủ động sẽ sạch hơn)
Công cụ này chỉ BÁO CÁO; thêm --fix mới xoá. Có thể chạy định kỳ (cron).

Ngoài ra báo cáo các cặp khách TRÙNG (hai customer_id có embedding giống nhau >= ngưỡng) - hậu quả của phương án
"chấp nhận trùng rồi gộp sau" thay vì dùng khoá. Với cài đặt hiện tại (có khoá) danh sách này phải rỗng.

    python -m tools.reconcile            # chỉ báo cáo
    python -m tools.reconcile --fix      # xoá dữ liệu mồ côi
"""
from __future__ import annotations

import argparse
import sys

from common.config import load_settings
from tools.metrics import diff_ids
from worker.repo_mysql import MySqlRepo
from worker.repo_vector import PgVectorRepo

DUP_SQL = ("SELECT a.customer_id, b.customer_id, MAX(1 - (a.embedding <=> b.embedding)) AS sim "
           "FROM face_embeddings a JOIN face_embeddings b ON a.customer_id < b.customer_id "
           "WHERE 1 - (a.embedding <=> b.embedding) >= %s "
           "GROUP BY a.customer_id, b.customer_id ORDER BY sim DESC")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fix", action="store_true", help="xoá dữ liệu mồ côi")
    args = p.parse_args()

    s = load_settings()
    mysql, vectors = MySqlRepo(**s.mysql_kwargs), PgVectorRepo(s.pg_dsn)
    no_embedding, no_customer = diff_ids(mysql.customer_ids(), vectors.customer_ids())

    print(f"Khách MySQL không có embedding : {sorted(no_embedding) or 'không có'}")
    print(f"Embedding không có khách MySQL : {sorted(no_customer) or 'không có'}")

    import psycopg
    with psycopg.connect(s.pg_dsn) as conn:
        dups = conn.execute(DUP_SQL, (s.sim_threshold,)).fetchall()
    if dups:
        print(f"Cặp khách có thể TRÙNG (sim >= {s.sim_threshold}):")
        for a, b, sim in dups:
            print(f"  customer {a} ~ customer {b}  (sim={float(sim):.3f})  -> xem lại và gộp thủ công")
    else:
        print("Không có cặp khách trùng.")

    if args.fix:
        for cid in no_embedding:
            mysql.delete_customer(cid)
            print(f"đã xoá khách mồ côi {cid} khỏi MySQL")
        for cid in no_customer:
            vectors.delete_customer(cid)
            print(f"đã xoá embedding mồ côi của customer {cid}")
    elif no_embedding or no_customer:
        print("\nChạy lại với --fix để dọn.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
