"""Gộp hai khách thật ra là MỘT người (kết quả của `tools.reconcile`: "Cặp khách có thể TRÙNG").

    python -m tools.merge_customers --keep 6 --drop 3            # CHỈ XEM TRƯỚC, không ghi gì
    python -m tools.merge_customers --keep 6 --drop 3 --apply    # ghi thật

Khi --apply: chuyển ảnh, đơn hàng (MySQL) và embedding (pgvector) của khách --drop sang khách --keep;
bổ sung tên / số điện thoại nếu khách --keep còn trống; rồi xoá khách --drop.
`recognition_logs` KHÔNG bị sửa (đó là lịch sử, bảng này cố ý không có khoá ngoại tới khách).
Tệp ảnh trong volume không đổi chỗ (cột file_path vẫn trỏ thư mục cũ, vẫn đọc được).

Hai CSDL khác nhau nên không có giao dịch chung. Thứ tự: mở giao dịch PostgreSQL (chưa commit) -> giao dịch
MySQL (commit) -> commit PostgreSQL. MySQL lỗi thì PostgreSQL tự rollback. Chỉ khi commit PostgreSQL (bước
cuối) lỗi thì hai bên lệch nhau: chạy `tools.reconcile` để thấy và xử lý.
Công cụ này chỉ quyết định theo hai id em đưa; việc "đó có đúng là cùng một người không" là việc của người.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List, Optional


def plan(keep: Dict[str, Any], drop: Dict[str, Any], n_keep_emb: int, n_drop_emb: int, cap: int) -> Dict[str, Any]:
    """Tính kết quả gộp (thuần tuý, không chạm CSDL; có unit test)."""
    warnings: List[str] = []
    name = keep.get("full_name") or drop.get("full_name")
    phone = keep.get("phone") or drop.get("phone")
    if keep.get("full_name") and drop.get("full_name") and keep["full_name"] != drop["full_name"]:
        warnings.append(f"hai khách có tên khác nhau ({keep['full_name']!r} và {drop['full_name']!r}); giữ tên của khách --keep")
    if keep.get("phone") and drop.get("phone") and keep["phone"] != drop["phone"]:
        warnings.append("hai khách có số điện thoại khác nhau; giữ số của khách --keep")
    seen = [v for v in (keep.get("last_seen_at"), drop.get("last_seen_at")) if v is not None]
    emb_after = n_keep_emb + n_drop_emb
    if emb_after > cap:
        warnings.append(f"sau gộp có {emb_after} embedding, vượt mức tối đa {cap} cho mỗi khách "
                        "(mức này chỉ chặn việc THÊM tự động về sau; gộp vẫn chạy được)")
    return {"full_name": name, "phone": phone, "last_seen_at": max(seen) if seen else None,
            "embeddings_after": emb_after, "warnings": warnings,
            "set_name": name != keep.get("full_name"), "set_phone": phone != keep.get("phone"),
            "set_last_seen": bool(seen) and max(seen) != keep.get("last_seen_at")}


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--keep", type=int, required=True, help="id khách giữ lại")
    p.add_argument("--drop", type=int, required=True, help="id khách sẽ được gộp vào rồi xoá")
    p.add_argument("--apply", action="store_true", help="ghi thật (mặc định chỉ xem trước)")
    args = p.parse_args(argv)
    if args.keep == args.drop:
        print("--keep và --drop phải khác nhau", file=sys.stderr)
        return 2

    import psycopg
    from common.config import load_settings
    from worker.repo_mysql import MySqlRepo, parse_iso_utc

    s = load_settings()
    mysql = MySqlRepo(**s.mysql_kwargs)
    keep, drop = mysql.get_customer(args.keep), mysql.get_customer(args.drop)
    for cid, row in ((args.keep, keep), (args.drop, drop)):
        if row is None:
            print(f"Không có khách #{cid} trong MySQL", file=sys.stderr)
            return 2

    def counts(cid: int) -> Dict[str, int]:
        with mysql._tx() as cur:
            out = {}
            for key, table in (("ảnh", "customer_photos"), ("đơn", "orders")):
                cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE customer_id = %s", (cid,))
                out[key] = int(cur.fetchone()["n"])
            cur.execute("SELECT COUNT(*) AS n FROM recognition_logs WHERE customer_id = %s", (cid,))
            out["dòng log (không sửa)"] = int(cur.fetchone()["n"])
        with psycopg.connect(s.pg_dsn) as conn:
            out["embedding"] = int(conn.execute("SELECT COUNT(*) FROM face_embeddings WHERE customer_id = %s", (cid,)).fetchone()[0])
        return out

    ck, cd = counts(args.keep), counts(args.drop)
    res = plan(keep, drop, ck["embedding"], cd["embedding"], s.max_embeddings_per_customer)
    print(f"GIỮ   #{args.keep}: tên={keep['full_name']!r} chi nhánh đầu={keep['first_branch_id']} tạo={keep['created_at']}  {ck}")
    print(f"GỘP   #{args.drop}: tên={drop['full_name']!r} chi nhánh đầu={drop['first_branch_id']} tạo={drop['created_at']}  {cd}")
    print(f"Sau gộp, khách #{args.keep}: tên={res['full_name']!r} điện thoại={res['phone']!r} embedding={res['embeddings_after']}"
          f" ảnh={ck['ảnh'] + cd['ảnh']} đơn={ck['đơn'] + cd['đơn']}; khách #{args.drop} bị xoá.")
    for w in res["warnings"]:
        print("CẢNH BÁO:", w)
    if not args.apply:
        print("\nCHƯA ghi gì. Thêm --apply để ghi thật.")
        return 0

    with psycopg.connect(s.pg_dsn) as conn:                    # commit khi thoát khối, rollback nếu có lỗi
        conn.execute("UPDATE face_embeddings SET customer_id = %s WHERE customer_id = %s", (args.keep, args.drop))
        with mysql._tx() as cur:
            cur.execute("UPDATE customer_photos SET customer_id = %s WHERE customer_id = %s", (args.keep, args.drop))
            cur.execute("UPDATE orders SET customer_id = %s WHERE customer_id = %s", (args.keep, args.drop))
            sets, vals = [], []
            if res["set_name"]:
                sets.append("full_name = %s"); vals.append(res["full_name"])
            if res["set_phone"]:
                sets.append("phone = %s"); vals.append(res["phone"])
            if res["set_last_seen"]:
                sets.append("last_seen_at = %s"); vals.append(parse_iso_utc(res["last_seen_at"]))   # get_customer trả chuỗi ISO
            if sets:
                cur.execute(f"UPDATE customers SET {', '.join(sets)} WHERE customer_id = %s", vals + [args.keep])
            cur.execute("DELETE FROM customers WHERE customer_id = %s", (args.drop,))
    after = counts(args.keep)
    print(f"\nĐÃ gộp. Khách #{args.keep} giờ có: {after}. Chạy `python -m tools.reconcile` để kiểm (phải sạch).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
