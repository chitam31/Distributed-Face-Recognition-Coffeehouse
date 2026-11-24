#!/usr/bin/env bash
# Tạo tài khoản RabbitMQ riêng cho từng chi nhánh với quyền TỐI THIỂU (least privilege):
#   - ghi (publish) CHỈ vào exchange coffee.requests  (từ đó tới queue face.requests / orders)
#   - tự tạo + đọc queue trả lời của chính mình: tên bắt đầu bằng "reply."
#   - KHÔNG đọc được face.requests và không tạo/xoá queue nào ngoài tên "reply.*"
#     (mẫu ^reply\. chưa tách riêng từng chi nhánh; queue trả lời là exclusive nên chi nhánh khác vẫn không đọc được)
# Dùng:  scripts/create_branch_users.sh            (mặc định A B C)
#        scripts/create_branch_users.sh A B C D    (thêm chi nhánh)
set -euo pipefail
cd "$(dirname "$0")/.."
BRANCHES=("${@:-A B C}")
read -r -a BRANCHES <<< "${BRANCHES[*]}"

for b in "${BRANCHES[@]}"; do
  user="branch_$(echo "$b" | tr 'A-Z' 'a-z')"
  pass="${user}_pass"                       # ĐỔI mật khẩu này khi triển khai thật
  docker compose exec -T rabbitmq rabbitmqctl delete_user "$user" >/dev/null 2>&1 || true
  docker compose exec -T rabbitmq rabbitmqctl add_user "$user" "$pass"
  # configure, write, read (regex trên tên tài nguyên)
  docker compose exec -T rabbitmq rabbitmqctl set_permissions -p / "$user" '^reply\.' '^coffee\.requests$' '^reply\.'
  echo "chi nhánh $b -> user=$user pass=$pass"
done
echo
echo "Kiểm tra:  docker compose exec rabbitmq rabbitmqctl list_permissions -p /"
