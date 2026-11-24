#!/usr/bin/env bash
# Đo thời gian N worker xử lý xong N request dồn vào queue (thí nghiệm co giãn / load leveling).
# Dùng:  bash scripts/measure_drain.sh <số_worker> <số_request> [ảnh]
# Ví dụ: bash scripts/measure_drain.sh 1 200 dataset/Chitam/4.jpg
#
# Cách đo (chính xác, không dựa vào số liệu hàng đợi của RabbitMQ vì nó cập nhật trễ vài giây):
#   mỗi request xử lý xong để lại 1 dòng trong MySQL recognition_logs (kèm worker và created_at),
#   nên "xong" = đã có thêm đúng N dòng log mới. Kết quả được ghi nối vào results/drain_times.csv.
# Yêu cầu: DEMO_DELAY_MS=0 trong .env khi đo (độ trễ giả sẽ làm sai số liệu).
set -euo pipefail
cd "$(dirname "$0")/.."

WORKERS="${1:?thiếu số worker (vd 1)}"
COUNT="${2:?thiếu số request (vd 200)}"
IMAGE="${3:-dataset/Chitam/4.jpg}"
TIMEOUT_S="${TIMEOUT_S:-900}"

sql() { docker compose exec -T mysql mysql -uroot -proot123 coffeehouse -N -B -e "$1" 2>/dev/null; }
ready() {   # số message đang CHỜ trong face.requests (đọc trực tiếp, không bị trễ)
  docker compose run --rm tools tools.chaos status 2>/dev/null | awk '$1=="face.requests" {print $2}'
}

if grep -Eq '^DEMO_DELAY_MS=[1-9]' .env 2>/dev/null; then
  echo "!! .env đang để DEMO_DELAY_MS>0 (độ trễ giả). Đặt DEMO_DELAY_MS=0 rồi chạy lại." >&2
  exit 1
fi

echo "== Đặt số worker = $WORKERS =="
docker compose up -d --scale worker="$WORKERS" >/dev/null 2>&1
sleep 15   # chờ worker mới nạp xong model

echo "== Chờ backlog cũ (nếu có) xử lý xong =="
until [ "$(ready)" = "0" ]; do echo "   queue còn $(ready) ..."; sleep 3; done
sleep 6    # worker xử lý nốt message đang cầm dở

base=$(sql "SELECT COALESCE(MAX(log_id),0) FROM recognition_logs")
echo "== Dồn $COUNT request (ảnh: $IMAGE); mốc log_id = $base =="
start=$(date +%s)
docker compose run --rm tools tools.chaos flood --image "$IMAGE" --count "$COUNT" --branch A

while true; do
  done_n=$(sql "SELECT COUNT(*) FROM recognition_logs WHERE log_id > $base")
  now=$(( $(date +%s) - start ))
  echo "   ${now}s : đã xử lý xong ${done_n}/${COUNT}"
  [ "${done_n:-0}" -ge "$COUNT" ] && break
  if [ "$now" -gt "$TIMEOUT_S" ]; then echo "!! quá ${TIMEOUT_S}s mà chưa đủ ${COUNT}; kiểm tra: docker compose logs worker" >&2; exit 1; fi
  sleep 2
done
wall=$(( $(date +%s) - start ))

# Số liệu từ chính nhật ký trong DB (chính xác đến mili-giây)
span=$(sql "SELECT ROUND(TIMESTAMPDIFF(MICROSECOND, MIN(created_at), MAX(created_at))/1000000, 2) FROM recognition_logs WHERE log_id > $base")
avg_ms=$(sql "SELECT ROUND(AVG(latency_ms)) FROM recognition_logs WHERE log_id > $base")
per_worker=$(sql "SELECT worker, COUNT(*) FROM recognition_logs WHERE log_id > $base GROUP BY worker ORDER BY worker" | awk '{printf "%s=%s ", $1, $2}')
rps=$(LC_ALL=C awk -v c="$COUNT" -v s="$span" 'BEGIN{ if (s>0) printf "%.2f", c/s; else print "n/a" }')

echo
echo "==> $WORKERS worker: $COUNT request xong sau ${wall}s (tính cả khởi động công cụ ~3-5s)"
echo "    khoảng cách giữa log đầu và log cuối: ${span}s  =>  ~${rps} req/s"
echo "    thời gian xử lý trung bình mỗi request: ${avg_ms} ms"
echo "    chia việc cho các worker: ${per_worker}"

mkdir -p results
[ -f results/drain_times.csv ] || echo "time,workers,requests,wall_s,db_span_s,req_per_s,avg_latency_ms,per_worker" > results/drain_times.csv
echo "$(date +%FT%T),$WORKERS,$COUNT,$wall,$span,$rps,$avg_ms,\"${per_worker% }\"" >> results/drain_times.csv
