#!/usr/bin/env bash
# Bật / tắt trang quản trị chỉ đọc trên MÁY LAB (chạy trong thư mục dự án).
#   bash scripts/admin_web.sh up       # cần ADMIN_PASSWORD trong .env (ít nhất 10 ký tự)
#   bash scripts/admin_web.sh status   # container + cổng (phải chỉ thấy 127.0.0.1:8090)
#   bash scripts/admin_web.sh logs
#   bash scripts/admin_web.sh down     # chỉ dừng và xoá container admin; mysql/postgres/rabbitmq/worker không bị đụng
# Xem từ laptop: ssh -N -L 8090:127.0.0.1:8090 labsit@10.234.18.42   (trong WSL), rồi mở http://localhost:8090
set -euo pipefail
cd "$(dirname "$0")/.."
DC=(docker compose -f docker-compose.yml -f docker-compose.admin.yml)
PORT=$(sed -n 's/^ADMIN_PORT=\([0-9]*\).*/\1/p' .env 2>/dev/null | tail -1)
PORT=${PORT:-8090}

case "${1:-}" in
  up)
    if ! grep -qE '^ADMIN_PASSWORD=[^[:space:]#]{10,}' .env 2>/dev/null; then
      echo "LỖI: chưa có dòng ADMIN_PASSWORD=... (ít nhất 10 ký tự) trong .env" >&2
      exit 2
    fi
    "${DC[@]}" up -d --no-deps admin
    sleep 2
    "${DC[@]}" ps admin
    echo "Mở http://127.0.0.1:${PORT} trên máy lab (hoặc qua đường hầm SSH)."
    ;;
  status)
    "${DC[@]}" ps admin
    ss -ltn | grep -E ":${PORT}\b" || echo "(không có tiến trình nào nghe cổng ${PORT})"
    ;;
  logs)
    "${DC[@]}" logs --tail 50 admin
    ;;
  down)
    "${DC[@]}" stop admin
    "${DC[@]}" rm -f admin
    ;;
  *)
    echo "Dùng: bash scripts/admin_web.sh up|status|logs|down" >&2
    exit 2
    ;;
esac
