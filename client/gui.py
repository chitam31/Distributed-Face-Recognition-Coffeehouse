"""Ứng dụng chi nhánh (mô phỏng app di động trên laptop): webcam + nhận diện + nhập đơn.

    python -m client.gui --branch A

Luồng dùng:
  1. "Chụp & nhận diện" (hoặc "Mở ảnh...") -> gửi lên server qua RabbitMQ.
  2. KHÁCH QUEN: hiện đơn gần nhất để xác nhận. KHÁCH MỚI: nhập tên/SĐT (tuỳ chọn) rồi "Cập nhật khách".
  3. Chọn món -> "Thêm vào đơn" -> "Lưu đơn".

Mọi lời gọi mạng chạy ở thread nền để cửa sổ không bị treo trong lúc chờ worker.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

import cv2
from PIL import Image, ImageTk

from common.config import load_settings
from common.messages import Status, build_customer_update, build_order_request, build_recognize_request
from client.imaging import Camera, load_image, prepare_jpeg
from client.presenter import STATUS_COLOR, describe, money
from client.rpc_client import BranchRpcClient, RpcError

MENU_PATH = os.path.join(os.path.dirname(__file__), "menu.json")
PREVIEW_W, PREVIEW_H = 480, 360


class BranchApp(tk.Tk):
    def __init__(self, branch_id: str, camera_index: int = 0):
        super().__init__()
        self.branch_id = branch_id
        self.title(f"Quán cà phê - Chi nhánh {branch_id}")
        self.geometry("1020x700")
        self.client = BranchRpcClient(load_settings(), branch_id)
        self.events: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self.busy = False
        self.customer: Optional[Dict[str, Any]] = None
        self.cart: List[Dict[str, Any]] = []
        self.last_frame = None
        self.menu = self._load_menu()
        self.camera = Camera(camera_index)
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(33, self._tick_camera)
        self.after(100, self._drain_events)

    # ------------------------------------------------------------------ giao diện
    def _load_menu(self) -> List[Dict[str, Any]]:
        try:
            with open(MENU_PATH, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return [{"name": "Cà phê sữa đá", "price": 29000}]

    def _build_ui(self) -> None:
        left = ttk.Frame(self, padding=8)
        right = ttk.Frame(self, padding=8)
        left.pack(side=tk.LEFT, fill=tk.Y)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.preview = ttk.Label(left, text="(không có webcam)", anchor="center")
        self.preview.pack()
        bar = ttk.Frame(left)
        bar.pack(fill=tk.X, pady=6)
        self.btn_capture = ttk.Button(bar, text="Chụp & nhận diện", command=self.on_capture)
        self.btn_capture.pack(side=tk.LEFT, padx=2)
        self.btn_open = ttk.Button(bar, text="Mở ảnh...", command=self.on_open_image)
        self.btn_open.pack(side=tk.LEFT, padx=2)
        self.auto_enroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Tự đăng ký khách mới", variable=self.auto_enroll).pack(side=tk.LEFT, padx=6)

        self.status_lbl = tk.Label(left, text="Sẵn sàng", font=("Segoe UI", 15, "bold"), fg="#444", anchor="w")
        self.status_lbl.pack(fill=tk.X, pady=(8, 2))
        self.result_txt = tk.Text(left, width=58, height=14, wrap="word", state="disabled", font=("Segoe UI", 10))
        self.result_txt.pack(fill=tk.BOTH, expand=True)

        box = ttk.LabelFrame(right, text="Khách hàng", padding=8)
        box.pack(fill=tk.X)
        self.lbl_customer = ttk.Label(box, text="(chưa chọn khách)")
        self.lbl_customer.grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(box, text="Tên").grid(row=1, column=0, sticky="w")
        self.var_name = tk.StringVar()
        ttk.Entry(box, textvariable=self.var_name, width=22).grid(row=1, column=1, padx=4, pady=2)
        ttk.Label(box, text="SĐT").grid(row=1, column=2, sticky="w")
        self.var_phone = tk.StringVar()
        ttk.Entry(box, textvariable=self.var_phone, width=16).grid(row=1, column=3, padx=4)
        self.btn_update = ttk.Button(box, text="Cập nhật khách", command=self.on_update_customer, state="disabled")
        self.btn_update.grid(row=2, column=0, columnspan=2, sticky="w", pady=4)

        order = ttk.LabelFrame(right, text="Đơn hàng", padding=8)
        order.pack(fill=tk.BOTH, expand=True, pady=8)
        row = ttk.Frame(order)
        row.pack(fill=tk.X)
        self.var_item = tk.StringVar(value=self.menu[0]["name"])
        ttk.Combobox(row, textvariable=self.var_item, values=[m["name"] for m in self.menu], state="readonly",
                     width=22).pack(side=tk.LEFT)
        self.var_qty = tk.IntVar(value=1)
        ttk.Spinbox(row, from_=1, to=20, textvariable=self.var_qty, width=4).pack(side=tk.LEFT, padx=4)
        self.var_opt = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_opt, width=16).pack(side=tk.LEFT)
        ttk.Label(row, text="(ít đá, 50% đường...)").pack(side=tk.LEFT, padx=4)
        ttk.Button(order, text="Thêm vào đơn", command=self.on_add_item).pack(anchor="w", pady=4)

        self.cart_list = tk.Listbox(order, height=8)
        self.cart_list.pack(fill=tk.BOTH, expand=True)
        foot = ttk.Frame(order)
        foot.pack(fill=tk.X, pady=4)
        self.lbl_total = ttk.Label(foot, text="Tổng: 0đ", font=("Segoe UI", 11, "bold"))
        self.lbl_total.pack(side=tk.LEFT)
        ttk.Button(foot, text="Xoá món chọn", command=self.on_remove_item).pack(side=tk.RIGHT, padx=2)
        self.btn_save = ttk.Button(foot, text="Lưu đơn", command=self.on_save_order, state="disabled")
        self.btn_save.pack(side=tk.RIGHT, padx=2)

    # ------------------------------------------------------------------ webcam
    def _tick_camera(self) -> None:
        if self.camera.is_open():
            frame = self.camera.read()
            if frame is not None:
                self.last_frame = frame
                rgb = cv2.cvtColor(cv2.resize(frame, (PREVIEW_W, PREVIEW_H)), cv2.COLOR_BGR2RGB)
                self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
                self.preview.configure(image=self._photo, text="")
        self.after(33, self._tick_camera)

    # ------------------------------------------------------------------ chạy tác vụ mạng ở thread nền
    def _run_async(self, work: Callable[[], Dict[str, Any]], done: Callable[[Dict[str, Any]], None]) -> None:
        if self.busy:
            return
        self._set_busy(True)

        def target():
            try:
                result = work()
                self.events.put(lambda: done(result))
            except (RpcError, ValueError) as exc:
                self.events.put(lambda: self._show_error(str(exc)))
            except Exception as exc:                                  # noqa: BLE001
                logging.exception("lỗi không lường trước")
                self.events.put(lambda: self._show_error(f"lỗi không lường trước: {exc}"))

        threading.Thread(target=target, daemon=True).start()

    def _drain_events(self) -> None:
        try:
            while True:
                self.events.get_nowait()()
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.btn_capture, self.btn_open):
            b.configure(state=state)
        if busy:
            self.status_lbl.configure(text="Đang gửi lên server...", fg="#444")
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        enabled = "normal" if (self.customer and not self.busy) else "disabled"
        self.btn_update.configure(state=enabled)
        self.btn_save.configure(state="normal" if (self.customer and self.cart and not self.busy) else "disabled")

    # ------------------------------------------------------------------ nhận diện
    def on_capture(self) -> None:
        if self.last_frame is None:
            messagebox.showwarning("Webcam", "Chưa có hình từ webcam. Dùng 'Mở ảnh...' để chọn file.")
            return
        self._recognize(self.last_frame.copy())

    def on_open_image(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Ảnh", "*.jpg *.jpeg *.png *.bmp")])
        if path:
            try:
                self._recognize(load_image(path))
            except ValueError as exc:
                messagebox.showerror("Ảnh", str(exc))

    def _recognize(self, frame) -> None:
        msg = build_recognize_request(self.branch_id, prepare_jpeg(frame), auto_enroll=self.auto_enroll.get(),
                                      full_name=self.var_name.get().strip() or None)
        self._run_async(lambda: self.client.recognize(msg), self._on_recognized)

    def _on_recognized(self, resp: Dict[str, Any]) -> None:
        self._set_busy(False)
        status = resp.get("status")
        self.status_lbl.configure(text=status or "?", fg=STATUS_COLOR.get(status, "#444"))
        self._write_result(describe(resp))
        cust = resp.get("customer")
        if status in (Status.RECOGNIZED, Status.NEW_CUSTOMER) and cust:
            self.customer = cust
            self.cart.clear()
            self._refresh_cart()
            self.lbl_customer.configure(text=f"Khách #{cust['customer_id']} · {cust.get('full_name') or '(chưa có tên)'}")
            self.var_name.set(cust.get("full_name") or "")
            self.var_phone.set(cust.get("phone") or "")
        self._refresh_buttons()

    # ------------------------------------------------------------------ hồ sơ khách và đơn
    def on_update_customer(self) -> None:
        if not self.customer:
            return
        name, phone = self.var_name.get().strip() or None, self.var_phone.get().strip() or None
        if not (name or phone):
            messagebox.showinfo("Cập nhật", "Nhập tên hoặc số điện thoại.")
            return
        msg = build_customer_update(self.branch_id, self.customer["customer_id"], full_name=name, phone=phone)
        self._run_async(lambda: self.client.send_command(msg), self._on_generic_done)

    def on_add_item(self) -> None:
        item = next((m for m in self.menu if m["name"] == self.var_item.get()), None)
        if item is None:
            return
        try:
            qty = max(1, int(self.var_qty.get()))
        except (tk.TclError, ValueError):
            qty = 1
        self.cart.append({"item_name": item["name"], "quantity": qty, "unit_price": float(item["price"]),
                          "options": self.var_opt.get().strip() or None})
        self._refresh_cart()

    def on_remove_item(self) -> None:
        sel = self.cart_list.curselection()
        if sel:
            self.cart.pop(sel[0])
            self._refresh_cart()

    def _refresh_cart(self) -> None:
        self.cart_list.delete(0, tk.END)
        total = 0.0
        for it in self.cart:
            opt = f" ({it['options']})" if it["options"] else ""
            self.cart_list.insert(tk.END, f"{it['quantity']} x {it['item_name']}{opt} - {money(it['quantity'] * it['unit_price'])}")
            total += it["quantity"] * it["unit_price"]
        self.lbl_total.configure(text=f"Tổng: {money(total)}")
        self._refresh_buttons()

    def on_save_order(self) -> None:
        if not (self.customer and self.cart):
            return
        msg = build_order_request(self.branch_id, self.customer["customer_id"], list(self.cart))
        self._run_async(lambda: self.client.send_command(msg), self._on_order_saved)

    def _on_order_saved(self, resp: Dict[str, Any]) -> None:
        self._on_generic_done(resp)
        if resp.get("status") == Status.ORDER_SAVED:
            self.cart.clear()
            self._refresh_cart()

    def _on_generic_done(self, resp: Dict[str, Any]) -> None:
        self._set_busy(False)
        status = resp.get("status")
        self.status_lbl.configure(text=status or "?", fg=STATUS_COLOR.get(status, "#444"))
        self._write_result(describe(resp))
        if status == Status.CUSTOMER_UPDATED and resp.get("customer"):
            self.customer = resp["customer"]
            self.lbl_customer.configure(text=f"Khách #{self.customer['customer_id']} · {self.customer.get('full_name') or '(chưa có tên)'}")

    # ------------------------------------------------------------------ tiện ích
    def _write_result(self, lines: List[str]) -> None:
        self.result_txt.configure(state="normal")
        self.result_txt.delete("1.0", tk.END)
        self.result_txt.insert(tk.END, "\n".join(lines))
        self.result_txt.configure(state="disabled")

    def _show_error(self, text: str) -> None:
        self._set_busy(False)
        self.status_lbl.configure(text="LỖI", fg=STATUS_COLOR[Status.ERROR])
        self._write_result([text])

    def _on_close(self) -> None:
        self.camera.release()
        self.client.close()
        self.destroy()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--branch", default=os.environ.get("BRANCH_ID", "A"))
    p.add_argument("--camera", type=int, default=0)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO)
    BranchApp(args.branch, args.camera).mainloop()


if __name__ == "__main__":
    main()
