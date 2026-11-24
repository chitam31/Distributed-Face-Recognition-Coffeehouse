#!/usr/bin/env python3
"""B4: chạy N lần `branch_client recognize --no-enroll` và ghi kết quả có giờ UTC.

Một tệp dùng chung cho cả bốn máy (Windows, WSL, Linux), chỉ cần Python 3 và thư viện chuẩn.
Phải chạy TỪ thư mục có `client/` và `common/` (thư mục mã client), bằng Python của venv client:

    Windows:  .venv\\Scripts\\python.exe b4_chay.py --branch B --host 10.234.18.42 --user branch_b --password branch_b_pass --image demo_photos\\b4_B.jpg --out b4_thuan.txt
    WSL/Linux: .venv/bin/python b4_chay.py --branch D ... --out b4_tam_D.txt
    Chỉ in md5 các tệp client để so với máy lab:  python b4_chay.py --md5

Nguyên tắc: không ghi đè tệp kết quả đã có (dừng và báo lỗi); lỗi giữa chừng vẫn chạy tiếp và
được ghi nguyên văn vào tệp, vì lỗi cũng là dữ liệu thật. Không xoá, không sửa gì khác trên máy.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import platform
import socket
import subprocess
import sys
import time

FILES = [
    "client/branch_client.py", "client/rpc_client.py", "client/imaging.py", "client/presenter.py",
    "common/config.py", "common/messages.py", "common/rabbit.py", "common/errors.py",
]


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def md5_report() -> int:
    for rel in FILES:
        if os.path.isfile(rel):
            with open(rel, "rb") as f:
                print(f"{hashlib.md5(f.read()).hexdigest()}  {rel}")
        else:
            print(f"{'THIEU':32}  {rel}")
    return 0


def main(argv=None) -> int:
    try:   # tránh lỗi mã hoá khi in tiếng Việt qua SSH trên Windows
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    p = argparse.ArgumentParser(description="B4: chạy N lần nhận diện --no-enroll, ghi giờ UTC")
    p.add_argument("--md5", action="store_true", help="chỉ in md5 các tệp client rồi thoát")
    p.add_argument("--branch")
    p.add_argument("--host")
    p.add_argument("--user")
    p.add_argument("--password")
    p.add_argument("--image")
    p.add_argument("--count", type=int, default=15)
    p.add_argument("--sleep", type=float, default=1.0, help="nghỉ giữa hai lần (giây)")
    p.add_argument("--out", help="tệp kết quả (không được tồn tại sẵn)")
    a = p.parse_args(argv)

    if not (os.path.isdir("client") and os.path.isdir("common")):
        print(f"LỖI: thư mục hiện tại ({os.getcwd()}) không có client/ và common/. Hãy cd vào thư mục mã client.")
        return 3
    if a.md5:
        return md5_report()
    for name in ("branch", "host", "user", "password", "image", "out"):
        if not getattr(a, name):
            print(f"LỖI: thiếu --{name}")
            return 3
    if not os.path.isfile(a.image):
        print(f"LỖI: không thấy ảnh {a.image}")
        return 3
    if os.path.exists(a.out):
        print(f"LỖI: {a.out} đã tồn tại; không ghi đè bằng chứng cũ. Đặt tên --out khác.")
        return 4

    cmd = [sys.executable, "-m", "client.branch_client", "--branch", a.branch, "--host", a.host,
           "--user", a.user, "--password", a.password, "recognize", "--no-enroll", "--image", a.image]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    ok_rc = recognized = 0
    with open(a.out, "w", encoding="utf-8", newline="\n") as out:
        def emit(line: str) -> None:
            print(line, flush=True)
            out.write(line + "\n")
            out.flush()

        emit(f"# máy {socket.gethostname()} | {platform.system()} {platform.release()} | "
             f"python {platform.python_version()} | chi nhánh {a.branch} | ảnh {a.image} | "
             f"{a.count} lần | bắt đầu {utc_now().isoformat(timespec='milliseconds')}")
        for i in range(1, a.count + 1):
            t0 = time.monotonic()
            emit(f"== lần {i}, {utc_now().strftime('%H:%M:%S.%f')[:-3]} UTC")
            try:
                r = subprocess.run(cmd, capture_output=True, env=env, timeout=60)
                text = (r.stdout + r.stderr).decode("utf-8", errors="replace").rstrip()
                rc = r.returncode
            except subprocess.TimeoutExpired:
                text, rc = "LỖI: quá 60 giây không xong (b4_chay dừng lần này)", -1
            for line in text.splitlines():
                emit(line)
            ms = int((time.monotonic() - t0) * 1000)
            emit(f"-- mã thoát {rc}, {ms} ms")
            ok_rc += rc == 0
            recognized += "[KHÁCH QUEN]" in text
            if i < a.count:
                time.sleep(a.sleep)
        emit(f"# xong {a.count} lần | mã thoát 0: {ok_rc} | có [KHÁCH QUEN]: {recognized} | "
             f"kết thúc {utc_now().isoformat(timespec='milliseconds')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
