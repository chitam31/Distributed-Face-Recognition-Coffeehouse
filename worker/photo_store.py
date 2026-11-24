"""Lưu ảnh khách thành file; CSDL chỉ giữ đường dẫn tương đối.

Ảnh khuôn mặt là dữ liệu sinh trắc học nhạy cảm nên thư mục được tạo với quyền 0750,
file 0640 (chỉ user chạy worker và nhóm đọc được).
Với nhiều worker trên nhiều máy, thư mục này phải là ổ dùng chung (NFS) hoặc thay bằng
object storage (MinIO/S3); PhotoStore là điểm duy nhất cần thay.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

_SAFE = re.compile(r"[^A-Za-z0-9_\-]")


class PhotoStore:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, mode=0o750, exist_ok=True)

    def save(self, customer_id: int, jpeg: bytes, branch_id: str, when: Optional[datetime] = None) -> str:
        """Ghi ảnh, trả về đường dẫn tương đối (lưu vào customer_photos.file_path)."""
        when = when or datetime.now(timezone.utc)
        folder = str(int(customer_id))
        directory = os.path.join(self.root, folder)
        os.makedirs(directory, mode=0o750, exist_ok=True)
        name = f"{when.strftime('%Y%m%dT%H%M%S')}_{_SAFE.sub('', branch_id)}_{uuid.uuid4().hex[:8]}.jpg"
        rel = os.path.join(folder, name)
        tmp = os.path.join(directory, "." + name + ".tmp")
        with open(tmp, "wb") as fh:                      # ghi tạm rồi đổi tên: không để lại file dở dang
            fh.write(jpeg)
        os.chmod(tmp, 0o640)
        os.replace(tmp, os.path.join(self.root, rel))
        return rel

    def delete(self, rel_path: Optional[str]) -> None:
        if not rel_path:
            return
        full = os.path.abspath(os.path.join(self.root, rel_path))
        if not full.startswith(self.root + os.sep):      # chặn path traversal
            return
        try:
            os.remove(full)
            parent = os.path.dirname(full)
            if parent != self.root and not os.listdir(parent):
                os.rmdir(parent)
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def full_path(self, rel_path: str) -> str:
        return os.path.join(self.root, rel_path)
