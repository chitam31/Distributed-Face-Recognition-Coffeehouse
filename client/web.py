"""Giao diện web cho MỘT chi nhánh: trình duyệt bật camera (hoặc chọn ảnh), máy chi nhánh chuyển ảnh qua RabbitMQ.

    python -m client.web --branch B --host 10.234.18.42          # rồi mở http://127.0.0.1:8080 và đăng nhập branch_b
    python -m client.web --no-login --branch A --user branch_a --password branch_a_pass     # kiểu cũ, không đăng nhập

Đăng nhập theo chi nhánh (mặc định, từ 12/11/2026): tài khoản trên trang chính là tài khoản RabbitMQ quyền tối thiểu
của chi nhánh (branch_a, branch_b...). Mật khẩu đúng khi và chỉ khi broker cho nối, nên máy chi nhánh KHÔNG lưu mật
khẩu nào. Chi nhánh lấy từ tên tài khoản (branch_b -> B); --branch ghim máy vào một chi nhánh, tài khoản chi nhánh khác
bị từ chối. Mỗi máy một phiên: đăng nhập mới thay phiên cũ; rảnh quá SESSION_IDLE_S thì phải đăng nhập lại.

Kiến trúc (vẫn là "thin client": nhận diện chạy hết ở worker):

    trình duyệt ──HTTP, chỉ trong máy này──▶ client.web ──RabbitMQ RPC (tài khoản chi nhánh)──▶ worker ──▶ CSDL
    (camera, giao diện)                      (nút chi nhánh)

  * Trình duyệt KHÔNG bao giờ cầm mật khẩu RabbitMQ và không nói chuyện thẳng với broker; mật khẩu chỉ nằm ở tiến
    trình này (tài khoản quyền tối thiểu của chi nhánh, xem scripts/create_branch_users.sh).
  * Mặc định chỉ nghe 127.0.0.1. Địa chỉ http://localhost hoặc http://127.0.0.1 được trình duyệt coi là "ngữ cảnh an
    toàn" nên cho dùng camera mà không cần HTTPS hay cờ trình duyệt đặc biệt. Mở bằng IP LAN thì camera sẽ bị chặn.
  * Dùng được cả khi laptop chi nhánh chạy trong WSL không có webcam: tiến trình này chạy trong WSL, còn trình duyệt
    (có camera) mở trang ở Windows; hoặc dùng nút "Chọn ảnh…" để gửi ảnh có sẵn.

Dùng chung đường RPC với CLI/GUI (rpc_client.py): cùng request_id → cùng cơ chế idempotency, retry, timeout.
pika KHÔNG an toàn khi nhiều luồng dùng chung một kết nối, nên mọi lời gọi RPC được xếp hàng qua một khoá
(một quầy phục vụ một lúc một thao tác; bài toán ở đây là nhận diện, không phải phục vụ hàng nghìn trình duyệt).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import urlsplit

import cv2
import numpy as np
from PIL import Image

from common.messages import (MessageError, build_customer_update, build_order_request, build_recognize_request,
                             validate_customer_update, validate_order, validate_recognize)
from client.imaging import prepare_jpeg
from client.presenter import STATUS_COLOR, STATUS_TEXT
from client.rpc_client import RpcError, RpcTimeout

log = logging.getLogger("client.web")

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(HERE, "web_static", "index.html")
MENU_PATH = os.path.join(HERE, "menu.json")

MAX_BODY_BYTES = 3 * 1024 * 1024        # ảnh từ canvas (<= 960 px) chỉ vài trăm KB, 3 MiB là rất rộng rãi
MAX_PIXELS = 25_000_000                 # chặn ảnh "bom giải nén" trước khi giải mã
BROWSER_MAX_SIDE = 960                  # trình duyệt co ảnh về cạnh dài tối đa này; server còn co tiếp về 640 như CLI/GUI
IDLE_RECONNECT_S = 20.0                 # kết nối RabbitMQ rảnh quá lâu thường đã bị broker đóng (heartbeat) -> làm mới
LOOPBACK_BINDS = {"127.0.0.1", "localhost", "::1"}
BRANCH_USER_RE = re.compile(r"^branch_([a-z])$")   # tài khoản do scripts/create_branch_users.sh tạo
SESSION_IDLE_S = 8 * 3600                          # một ca làm việc; rảnh lâu hơn thì đăng nhập lại
LOGIN_MAX_FAILS = 5                                # sai quá số lần này trong LOGIN_WINDOW_S ...
LOGIN_WINDOW_S = 300.0
LOGIN_LOCK_S = 60.0                                # ... thì khoá đăng nhập chừng này giây

CSP = ("default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; base-uri 'none'; "
       "form-action 'none'; frame-ancestors 'none'")


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def load_menu(path: str = MENU_PATH) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list) or not data:
        raise ValueError("menu phải là danh sách món không rỗng")
    for m in data:
        if not (isinstance(m, dict) and isinstance(m.get("name"), str) and m["name"].strip()
                and isinstance(m.get("price"), (int, float)) and not isinstance(m["price"], bool)
                and m["price"] >= 0):
            raise ValueError(f"món không hợp lệ trong menu: {m!r}")
    return data


def _obj(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise MessageError("body phải là một JSON object")
    return payload


def _opt_str(payload: Dict[str, Any], key: str) -> Optional[str]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MessageError(f"'{key}' phải là chuỗi")
    return value


class BranchWebApp:
    """Phần logic, không biết HTTP: đổi JSON của trình duyệt thành message RabbitMQ, gọi RPC, trả kết quả."""

    def __init__(self, client, branch_id: str, menu: List[Dict[str, Any]], clock: Callable[[], float] = time.monotonic):
        self.client = client
        self.branch_id = branch_id
        self.menu = menu
        self._clock = clock
        self._lock = threading.Lock()
        self._last_used = clock()

    # ------------------------------------------------------------------ RPC (xếp hàng, làm mới kết nối rảnh)
    def _rpc(self, fn: Callable[[Dict[str, Any]], Dict[str, Any]], message: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if self._clock() - self._last_used > IDLE_RECONNECT_S:
                try:
                    self.client.connect()
                except Exception as exc:                     # noqa: BLE001 - để call() tự báo lỗi/thử lại
                    log.warning("làm mới kết nối RabbitMQ không được (%s); call() sẽ thử lại", exc)
            try:
                return fn(message)
            finally:
                self._last_used = self._clock()

    # ------------------------------------------------------------------ ảnh
    @staticmethod
    def decode_image(value: Any) -> bytes:
        """data URL / base64 của ảnh bất kỳ trình duyệt đọc được -> JPEG cạnh dài <= 640 px (như CLI/GUI)."""
        if not isinstance(value, str) or not value:
            raise MessageError("thiếu 'image'")
        if value.startswith("data:"):
            head, _, value = value.partition(",")
            if ";base64" not in head:
                raise MessageError("ảnh phải là data URL base64")
        try:
            raw = base64.b64decode(value, validate=True)
        except Exception as exc:
            raise MessageError("'image' không phải base64 hợp lệ") from exc
        try:
            with Image.open(io.BytesIO(raw)) as im:
                width, height = im.size
        except Exception as exc:
            raise MessageError("không đọc được ảnh (cần JPEG/PNG/WebP...)") from exc
        if width * height > MAX_PIXELS:
            raise MessageError("ảnh quá lớn")
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise MessageError("không đọc được ảnh (cần JPEG/PNG/WebP...)")
        return prepare_jpeg(img)

    # ------------------------------------------------------------------ ba thao tác
    def recognize(self, payload: Any) -> Dict[str, Any]:
        p = _obj(payload)
        auto_enroll = p.get("auto_enroll", True)
        if not isinstance(auto_enroll, bool):
            raise MessageError("'auto_enroll' phải là true/false")
        msg = build_recognize_request(self.branch_id, self.decode_image(p.get("image")),
                                      request_id=_opt_str(p, "request_id"), auto_enroll=auto_enroll,
                                      full_name=_opt_str(p, "full_name"))
        validate_recognize(msg)                  # cùng bộ kiểm tra với worker: sai thì báo ngay, khỏi tốn một vòng RabbitMQ
        return self._rpc(self.client.recognize, msg)

    def order(self, payload: Any) -> Dict[str, Any]:
        p = _obj(payload)
        msg = build_order_request(self.branch_id, p.get("customer_id"), p.get("items"),
                                  request_id=_opt_str(p, "request_id"), note=_opt_str(p, "note"))
        validate_order(msg)
        return self._rpc(self.client.send_command, msg)

    def update_customer(self, payload: Any) -> Dict[str, Any]:
        p = _obj(payload)
        msg = build_customer_update(self.branch_id, p.get("customer_id"), full_name=_opt_str(p, "full_name"),
                                    phone=_opt_str(p, "phone"), request_id=_opt_str(p, "request_id"))
        validate_customer_update(msg)
        return self._rpc(self.client.send_command, msg)

    def config(self) -> Dict[str, Any]:
        return {"branch": self.branch_id, "menu": self.menu, "max_side": BROWSER_MAX_SIDE,
                "status_text": STATUS_TEXT, "status_color": STATUS_COLOR}


def classify_connect_error(exc: BaseException) -> "tuple[int, str]":
    """Đổi lỗi khi nối RabbitMQ lúc đăng nhập thành (mã HTTP, câu báo). Dựa vào TÊN lớp lỗi của pika để không phải
    import pika ở đây (test chạy được cả khi máy không cài pika)."""
    names = {c.__name__ for c in type(exc).__mro__}
    text = repr(exc)
    if names & {"ProbableAuthenticationError", "AuthenticationError"} or "ACCESS_REFUSED" in text:
        return 401, "sai tài khoản hoặc mật khẩu"
    if "ProbableAccessDeniedError" in names:
        return 403, "tài khoản không có quyền vào RabbitMQ (virtual host)"
    return 503, "không nối được RabbitMQ (kiểm tra ZeroTier, địa chỉ máy chủ, broker đang chạy)"


class BranchLogin:
    """Phiên đăng nhập của MỘT máy chi nhánh. make_app(user, password, branch) phải trả BranchWebApp đã nối được
    broker, hoặc ném HttpError (401 sai mật khẩu, 503 không tới được broker...)."""

    def __init__(self, make_app: Callable[[str, str, str], BranchWebApp], pinned_branch: Optional[str] = None,
                 clock: Callable[[], float] = time.monotonic, idle_s: float = SESSION_IDLE_S):
        self.make_app = make_app
        self.pinned = pinned_branch.upper() if pinned_branch else None
        self._clock = clock
        self.idle_s = idle_s
        self._lock = threading.Lock()
        self._sessions: Dict[str, Dict[str, Any]] = {}     # token -> {user, branch, app, last}; tối đa một phiên
        self._fails: List[float] = []
        self._locked_until = 0.0

    # ---------------------------------------------------------------- tiện ích
    @staticmethod
    def _close(sess: Dict[str, Any]) -> None:
        try:
            sess["app"].client.close()
        except Exception:                                    # noqa: BLE001 - đóng kết nối cũ, lỗi cũng bỏ qua
            pass

    def _get(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        """Phiên còn hạn của token (cập nhật giờ dùng), hoặc None. Phải giữ self._lock khi gọi."""
        sess = self._sessions.get(token or "")
        if sess is None:
            return None
        if self._clock() - sess["last"] > self.idle_s:
            self._close(self._sessions.pop(token))
            return None
        sess["last"] = self._clock()
        return sess

    # ---------------------------------------------------------------- thao tác
    def login(self, payload: Any) -> "tuple[str, Dict[str, Any]]":
        p = _obj(payload)
        user, password = p.get("username"), p.get("password")
        if not isinstance(user, str) or not isinstance(password, str) or not password or len(password) > 128:
            raise MessageError("cần 'username' và 'password' dạng chuỗi")
        user = user.strip().lower()
        m = BRANCH_USER_RE.match(user)
        if not m:
            raise MessageError("tài khoản chi nhánh có dạng branch_a, branch_b, ...")
        branch = m.group(1).upper()
        now = self._clock()
        with self._lock:
            if now < self._locked_until:
                raise HttpError(429, f"thử sai quá nhiều lần, chờ {int(self._locked_until - now) + 1} giây rồi thử lại")
        if self.pinned and branch != self.pinned:
            raise HttpError(403, f"máy này là chi nhánh {self.pinned}; tài khoản {user} không dùng được ở đây")
        try:
            app = self.make_app(user, password, branch)          # nối broker ngoài khoá (có thể mất vài giây)
        except HttpError as exc:
            if exc.status == 401:
                with self._lock:
                    self._fails = [t for t in self._fails if now - t < LOGIN_WINDOW_S] + [now]
                    if len(self._fails) >= LOGIN_MAX_FAILS:
                        self._locked_until, self._fails = now + LOGIN_LOCK_S, []
            raise
        token = secrets.token_urlsafe(32)
        with self._lock:
            for old in self._sessions.values():                 # một máy một phiên: phiên cũ bị thay
                self._close(old)
            self._sessions = {token: {"user": user, "branch": branch, "app": app, "last": self._clock()}}
            self._fails = []
        log.info("đăng nhập: %s (chi nhánh %s)", user, branch)
        return token, {"user": user, "branch": branch}

    def logout(self, token: Optional[str]) -> None:
        with self._lock:
            sess = self._sessions.pop(token or "", None)
        if sess:
            self._close(sess)
            log.info("đăng xuất: %s", sess["user"])

    def app_for(self, token: Optional[str]) -> BranchWebApp:
        with self._lock:
            sess = self._get(token)
        if sess is None:
            raise HttpError(401, "chưa đăng nhập hoặc phiên đã hết hạn")
        return sess["app"]

    def session_info(self, token: Optional[str]) -> Dict[str, Any]:
        with self._lock:
            sess = self._get(token)
        info = {"login_required": True, "logged_in": sess is not None, "pinned_branch": self.pinned}
        if sess:
            info.update(user=sess["user"], branch=sess["branch"])
        return info

    def close_all(self) -> None:
        with self._lock:
            sessions, self._sessions = list(self._sessions.values()), {}
        for sess in sessions:
            self._close(sess)


def _hostname(host_header: str) -> str:
    return (urlsplit("//" + host_header).hostname or "").lower()


def make_handler(app: Optional[BranchWebApp], allowed_hosts: Optional[Set[str]], auth: Optional[BranchLogin] = None):
    """allowed_hosts: tên Host được phép (chống DNS rebinding khi chỉ nghe loopback). None = không kiểm tra.
    auth: None = kiểu cũ (một app cố định, không đăng nhập); có auth thì mọi /api/* trừ session/login cần cookie phiên."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "CoffeeBranchWeb/1.0"

        def log_message(self, fmt, *args):                  # dùng logging thay vì stderr
            log.info("%s %s", self.address_string(), fmt % args)

        # ---------------------------------------------------------------- tiện ích phản hồi
        def _send(self, status: int, body: bytes, ctype: str, extra: Optional[List[tuple]] = None) -> None:
            self.send_response(status)
            for k, v in extra or ():
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: Any, extra: Optional[List[tuple]] = None) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8",
                       extra)

        # ---------------------------------------------------------------- phiên (cookie riêng theo cổng: hai web
        # chi nhánh trên cùng máy, khác cổng, không ghi đè phiên của nhau vì trình duyệt không tách cookie theo cổng)
        def _cookie_name(self) -> str:
            return f"cb_session_{self.server.server_address[1]}"

        def _token(self) -> Optional[str]:
            try:
                morsel = SimpleCookie(self.headers.get("Cookie", "")).get(self._cookie_name())
            except CookieError:
                return None
            return morsel.value if morsel else None

        def _app(self) -> BranchWebApp:
            return auth.app_for(self._token()) if auth else app

        # ---------------------------------------------------------------- kiểm tra yêu cầu
        def _guard(self, post: bool) -> None:
            host = self.headers.get("Host", "")
            if allowed_hosts is not None and _hostname(host) not in allowed_hosts:
                raise HttpError(403, "Host không được phép")
            if post:
                origin = self.headers.get("Origin")
                if origin and urlsplit(origin).netloc != host:
                    raise HttpError(403, "Origin không khớp (chặn yêu cầu từ trang web khác)")
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    raise HttpError(415, "chỉ nhận application/json")

        def _read_json(self) -> Any:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                raise HttpError(411, "thiếu Content-Length")
            if length < 0:
                raise HttpError(400, "Content-Length không hợp lệ")
            if length > MAX_BODY_BYTES:
                raise HttpError(413, f"body quá lớn (tối đa {MAX_BODY_BYTES // 1024} KiB)")
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HttpError(400, f"JSON không hợp lệ: {exc}")

        def _run(self, work: Callable[[], Any]) -> None:
            try:
                self._json(200, work())
            except HttpError as exc:
                self._json(exc.status, {"error": exc.message})
            except MessageError as exc:
                self._json(400, {"error": str(exc)})
            except RpcTimeout as exc:
                self._json(504, {"error": str(exc)})
            except RpcError as exc:
                self._json(502, {"error": str(exc)})
            except Exception:                                # noqa: BLE001
                log.exception("lỗi không lường trước")
                self._json(500, {"error": "lỗi nội bộ của máy chủ web chi nhánh (xem log)"})

        # ---------------------------------------------------------------- định tuyến
        def do_GET(self) -> None:                           # noqa: N802
            def work():
                self._guard(post=False)
                path = urlsplit(self.path).path
                if path == "/api/session":
                    if auth is None:
                        return {"login_required": False, "logged_in": True, "branch": app.branch_id}
                    return auth.session_info(self._token())
                if path == "/api/config":
                    cfg = self._app().config()
                    if auth:
                        cfg["user"] = auth.session_info(self._token()).get("user")
                    return cfg
                raise HttpError(404, "không có đường dẫn này")

            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                try:
                    self._guard(post=False)
                    with open(INDEX_PATH, "rb") as fh:
                        self._send(200, fh.read(), "text/html; charset=utf-8")
                except HttpError as exc:
                    self._json(exc.status, {"error": exc.message})
                return
            self._run(work)

        def do_POST(self) -> None:                          # noqa: N802
            routes = {"/api/recognize": "recognize", "/api/order": "order", "/api/customer": "update_customer"}
            path = urlsplit(self.path).path
            if auth is not None and path in ("/api/login", "/api/logout"):
                self._login_or_logout(path)
                return

            def work():
                self._guard(post=True)
                name = routes.get(path)
                if name is None:
                    raise HttpError(404, "không có đường dẫn này")
                target = self._app()                     # có đăng nhập: 401 nếu không có phiên, TRƯỚC khi đọc body
                return getattr(target, name)(self._read_json())

            self._run(work)

        def _login_or_logout(self, path: str) -> None:
            base = f"{self._cookie_name()}=%s; Path=/; HttpOnly; SameSite=Strict"
            try:
                self._guard(post=True)                   # cùng Origin + JSON: trang web lạ không đăng nhập hộ được
                if path == "/api/logout":
                    auth.logout(self._token())
                    self._json(200, {"ok": True}, [("Set-Cookie", (base % "") + "; Max-Age=0")])
                    return
                token, info = auth.login(self._read_json())
                self._json(200, info, [("Set-Cookie", base % token)])
            except HttpError as exc:
                self._json(exc.status, {"error": exc.message})
            except MessageError as exc:
                self._json(400, {"error": str(exc)})
            except Exception:                                # noqa: BLE001
                log.exception("lỗi không lường trước khi đăng nhập")
                self._json(500, {"error": "lỗi nội bộ của máy chủ web chi nhánh (xem log)"})

    return Handler


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Giao diện web cho một chi nhánh quán cà phê")
    p.add_argument("--branch", default=os.environ.get("BRANCH_ID"),
                   help="ghim máy này vào một chi nhánh (A/B/C/D); có đăng nhập thì chỉ tài khoản của chi nhánh đó vào được")
    p.add_argument("--host"), p.add_argument("--user"), p.add_argument("--password")   # RabbitMQ (như branch_client)
    p.add_argument("--no-login", action="store_true",
                   help="kiểu cũ: không có trang đăng nhập, dùng --user/--password cố định")
    p.add_argument("--bind", default="127.0.0.1", help="địa chỉ web nghe (mặc định chỉ máy này)")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--menu", default=MENU_PATH, help="tệp menu JSON [{name, price}]")
    p.add_argument("--timeout", type=float, default=10.0, help="timeout mỗi lần chờ reply (giây)")
    p.add_argument("--attempts", type=int, default=3, help="số lần thử (gửi lại cùng request_id)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from client.branch_client import make_settings
    from client.rpc_client import BranchRpcClient
    try:
        menu = load_menu(args.menu)
    except (OSError, ValueError) as exc:
        print(f"LỖI menu: {exc}", file=sys.stderr)
        return 2
    if args.branch and not re.fullmatch(r"[A-Za-z]", args.branch):
        print("LỖI: --branch phải là một chữ cái (A, B, C, D...)", file=sys.stderr)
        return 2
    allowed = {"localhost", "127.0.0.1", "::1"} if args.bind in LOOPBACK_BINDS else None
    app: Optional[BranchWebApp] = None
    auth: Optional[BranchLogin] = None
    if args.no_login:
        branch = (args.branch or "A").upper()
        app = BranchWebApp(BranchRpcClient(make_settings(args), branch, timeout_s=args.timeout,
                                           max_attempts=args.attempts), branch, menu)
    else:
        if args.user or args.password:
            print("Lưu ý: bỏ qua --user/--password; tài khoản chi nhánh nhập trên trang đăng nhập.", file=sys.stderr)
        base = make_settings(argparse.Namespace(host=args.host, user=None, password=None))

        def make_app(user: str, password: str, branch: str) -> BranchWebApp:
            from dataclasses import replace
            client = BranchRpcClient(replace(base, rabbit_user=user, rabbit_pass=password), branch,
                                     timeout_s=args.timeout, max_attempts=args.attempts)
            try:
                client.connect()                         # broker là nơi kiểm mật khẩu và quyền
            except Exception as exc:                     # noqa: BLE001
                client.close()
                status, msg = classify_connect_error(exc)
                log.warning("đăng nhập %s không được: %r", user, exc)
                raise HttpError(status, msg) from None
            return BranchWebApp(client, branch, menu)

        auth = BranchLogin(make_app, pinned_branch=args.branch)
    try:
        server = ThreadingHTTPServer((args.bind, args.port), make_handler(app, allowed, auth))
    except OSError as exc:
        print(f"LỖI: không nghe được {args.bind}:{args.port} ({exc}). Thử --port khác.", file=sys.stderr)
        return 2
    server.daemon_threads = True
    if auth:
        who = f"chi nhánh {auth.pinned} (đăng nhập branch_{auth.pinned.lower()})" if auth.pinned else "đăng nhập bằng tài khoản branch_x"
        print(f"Web chi nhánh: mở http://127.0.0.1:{args.port} bằng trình duyệt, {who}. Ctrl+C để dừng.")
    else:
        print(f"Chi nhánh {app.branch_id} (không đăng nhập): mở http://127.0.0.1:{args.port} (Ctrl+C để dừng)")
    if allowed is None:
        print("CẢNH BÁO: đang nghe ngoài loopback; máy khác trong mạng mở được trang này, và trình duyệt sẽ chặn camera "
              "trên địa chỉ http không phải localhost.", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if auth:
            auth.close_all()
        if app:
            app.client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
