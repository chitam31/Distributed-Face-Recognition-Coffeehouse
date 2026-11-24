#!/usr/bin/env bash
# Demo "worker chết giữa chừng": giết worker đúng lúc nó đã ghi DB nhưng chưa reply/ack,
# rồi xem request vẫn được xử lý (RabbitMQ giao lại) mà không tạo bản ghi trùng.
#
# Chuẩn bị (DEMO_DELAY_MS=4000 trong .env, 3 worker):
#   sed -i 's/^DEMO_DELAY_MS=.*/DEMO_DELAY_MS=4000/' .env && docker compose up -d --no-deps --force-recreate --scale worker=3 worker
# Chạy trong MỘT terminal (đã `source ~/.venv/bin/activate`):
#   python -m client.branch_client --branch A --host 127.0.0.1 --user branch_a --password branch_a_pass \
#     recognize --image dataset/Chitam/02.jpg > /tmp/kill_demo_client.txt 2>&1 &
#   bash scripts/demo_kill_worker.sh
#   wait; cat /tmp/kill_demo_client.txt
# Script tự CHỜ (tối đa 30s) tới khi có worker đang "dừng giả", nên không cần canh giây.
set -euo pipefail
cd "$(dirname "$0")/.."

delay_ms=$(grep -E '^DEMO_DELAY_MS=' .env 2>/dev/null | tail -1 | cut -d= -f2 || true)
delay_ms=${delay_ms:-0}
if [ "$delay_ms" -le 0 ] 2>/dev/null; then
  echo "!! DEMO_DELAY_MS=0 nên worker không có cửa sổ để giết. Đặt DEMO_DELAY_MS=4000 và chạy lại 'docker compose up -d --scale worker=3'." >&2
  exit 1
fi
win=$(( delay_ms / 1000 - 1 )); [ "$win" -lt 1 ] && win=1     # chỉ chọn worker vừa mới bắt đầu dừng

echo "Đang chờ một worker bước vào cửa sổ xử lý (DEMO_DELAY_MS=${delay_ms}) ..."
for _ in $(seq 1 100); do
  for id in $(docker compose ps -q worker); do
    line=$(docker logs --since "${win}s" "$id" 2>&1 | grep "DEMO: dừng" | tail -1 || true)
    if [ -n "$line" ]; then
      name=$(docker inspect -f '{{.Name}}' "$id" | sed 's#^/##')
      rid=$(printf '%s' "$line" | sed -n 's/.*request=\([^)]*\).*/\1/p')
      echo "Phát hiện $name đang giữ request_id=$rid (DB đã ghi, CHƯA reply/ack)."
      docker kill "$id" >/dev/null
      echo ">>> ĐÃ GIẾT $name. Message chưa được ack nên RabbitMQ sẽ giao lại cho worker còn lại."
      echo "Khởi động lại worker đã chết:  docker compose up -d --scale worker=3"
      exit 0
    fi
  done
  sleep 0.3
done
echo "Không thấy worker nào đang xử lý sau 30s. Client đã gửi request chưa? (nhớ chạy client ở nền trước)" >&2
exit 1
