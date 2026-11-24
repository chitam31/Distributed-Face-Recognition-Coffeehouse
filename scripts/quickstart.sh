#!/usr/bin/env bash
# Dựng toàn bộ phía server rồi tạo tài khoản chi nhánh. Cần Docker + Docker Compose v2.
#   scripts/quickstart.sh [số_worker]      (mặc định 2)
set -euo pipefail
cd "$(dirname "$0")/.."
N="${1:-2}"
docker compose up -d --build --scale worker="$N"
echo "Chờ RabbitMQ..."
until docker compose exec -T rabbitmq rabbitmq-diagnostics -q ping >/dev/null 2>&1; do sleep 2; done
scripts/create_branch_users.sh A B C
echo
echo "Sẵn sàng. RabbitMQ UI: http://localhost:15672  (coffee_admin / coffee_admin_123)"
echo "Xem log worker:  docker compose logs -f worker"
