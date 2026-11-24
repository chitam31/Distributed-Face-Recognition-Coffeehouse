#!/usr/bin/env bash
# b4_ssh.sh - điều phối B4 qua SSH từ laptop WSL của Tâm (chi nhánh D). Đọc HUONG_DAN_B4_SSH.md trước.
#
#   bash b4_ssh.sh may        kiểm máy đang chạy là WSL (Linux) và có khoá SSH
#   bash b4_ssh.sh kiemtra    SSH không mật khẩu tới lab, B, C; kiểm thư mục và venv từng máy
#   bash b4_ssh.sh chep       chép b4_chay.py và ảnh (từ dataset trên lab) sang các máy; so md5 mã client
#   bash b4_ssh.sh thu        mỗi máy gửi 1 yêu cầu, LẦN LƯỢT (giai đoạn 0)
#   bash b4_ssh.sh chay       B4: ghi mốc log_id, bốn máy cùng chạy COUNT yêu cầu, rồi tổng kết
#   bash b4_ssh.sh tongket <base> <thư_mục_kết_quả>   chạy lại phần tổng kết (khi lệnh chay bị ngắt)
#
# Không lệnh nào xoá dữ liệu. Kết quả để ở ~/b4_ketqua_<ngày_giờ>/ trên laptop và bản sao trong results/ của lab.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
CONF="${B4_CONF:-$HERE/b4.conf}"
[ -f "$CONF" ] || { echo "Thiếu $CONF: chép b4.conf.mau thành b4.conf rồi điền tài khoản."; exit 2; }
# shellcheck source=/dev/null
. "$CONF"
if grep -q "ĐIỀN" <<<"$B $C"; then echo "LỖI: b4.conf còn chữ ĐIỀN (chưa điền tài khoản Windows của Thuận, Khang)."; exit 2; fi

SSHO=(-i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
s()   { ssh "${SSHO[@]}" "$@"; }
cpy() { scp -q "${SSHO[@]}" "$@"; }
sql() { s "$LAB" "cd $LAB_DIR && docker compose exec -T mysql mysql --default-character-set=utf8mb4 -uroot -proot123 coffeehouse -N -B -e \"$1\" < /dev/null 2>/dev/null"; }

cmd_may() {
  echo "== máy chạy điều phối: $(hostname) / $(whoami) / $(uname -s)   (phải là Whitehathacker / ctam31 / Linux)"
  [ "$(uname -s)" = "Linux" ] || { echo "LỖI: không phải WSL/Linux."; return 1; }
  [ -f "$KEY" ] && echo "   có khoá $KEY" || { echo "LỖI: chưa có khoá $KEY (bước 1 của hướng dẫn)."; return 1; }
}

cmd_kiemtra() {
  cmd_may || return 1
  echo "== lab ($LAB)"
  s "$LAB" "hostname; cd $LAB_DIR && ls -d client common >/dev/null && echo '   có client/ common/'; test -x $LAB_PY && echo '   có venv client'; test -f $IMG_A && echo '   có ảnh $IMG_A'" || echo "   LỖI: không SSH được vào lab bằng khoá (xem bước 2 của hướng dẫn)."
  for X in B C; do
    H=${!X}; DIR_VAR="${X}_DIR"; DIR=${!DIR_VAR}
    echo "== chi nhánh $X ($H)"
    s "$H" "hostname && cd $DIR && (if exist client\\ (echo    co client) else (echo    THIEU client)) && (if exist .venv\\Scripts\\python.exe (echo    co venv) else (echo    THIEU venv))" \
      || echo "   LỖI: không SSH được vào $H bằng khoá (bạn đã chạy bat_ssh.ps1 chưa? tài khoản đúng chưa?)."
  done
  echo "== chi nhánh D (laptop này)"
  [ -d "$D_DIR/client" ] && echo "   có $D_DIR/client" || echo "   THIẾU $D_DIR/client"
  [ -x "$D_PY" ] && echo "   có venv $D_PY" || echo "   THIẾU venv $D_PY"
  echo "== mốc dữ liệu trên lab (mong đợi 7 khách, mã lớn nhất 9, 9 đơn)"
  sql "SELECT COUNT(*), MAX(customer_id) FROM customers; SELECT COUNT(*) FROM orders"
}

cmd_chep() {
  cmd_may || return 1
  echo "== chép b4_chay.py"
  s "$LAB" "mkdir -p $LAB_DIR/scripts/b4" && cpy "$HERE/b4_chay.py" "$LAB:$LAB_DIR/scripts/b4/b4_chay.py" && echo "   lab: xong"
  cpy "$HERE/b4_chay.py" "$B:$B_DIR/b4_chay.py" && echo "   B: xong"
  cpy "$HERE/b4_chay.py" "$C:$C_DIR/b4_chay.py" && echo "   C: xong"
  cp "$HERE/b4_chay.py" "$D_DIR/b4_chay.py" && echo "   D: xong"
  echo "== chép ảnh gốc từ dataset trên lab (không qua Zalo, không bị nén)"
  s "$B" "cd $B_DIR && if not exist demo_photos mkdir demo_photos"
  s "$C" "cd $C_DIR && if not exist demo_photos mkdir demo_photos"
  cpy -3 "$LAB:$LAB_DIR/$SRC_B" "$B:$B_DIR/demo_photos/b4_B.jpg" && echo "   B: $SRC_B -> demo_photos\\b4_B.jpg"
  cpy -3 "$LAB:$LAB_DIR/$SRC_C" "$C:$C_DIR/demo_photos/b4_C.jpg" && echo "   C: $SRC_C -> demo_photos\\b4_C.jpg"
  cpy "$LAB:$LAB_DIR/$SRC_D" "$D_DIR/b4_D.jpg" && echo "   D: $SRC_D -> b4_D.jpg"
  echo "== so md5 mã client với lab (mọi dòng phải giống cột lab)"
  local T; T=$(mktemp -d)
  s "$LAB" "cd $LAB_DIR && $LAB_PY scripts/b4/b4_chay.py --md5" > "$T/A" 2>&1
  s "$B" "cd $B_DIR && .venv\\Scripts\\python.exe b4_chay.py --md5" 2>&1 | tr -d '\r' > "$T/B"
  s "$C" "cd $C_DIR && .venv\\Scripts\\python.exe b4_chay.py --md5" 2>&1 | tr -d '\r' > "$T/C"
  (cd "$D_DIR" && "$D_PY" b4_chay.py --md5) > "$T/D" 2>&1
  for X in B C D; do
    if diff -q "$T/A" "$T/$X" >/dev/null; then echo "   $X: khớp lab"; else echo "   $X: KHÁC lab (dán khối dưới cho Thầy, chưa chạy B4)"; diff "$T/A" "$T/$X" | sed 's/^/      /'; fi
  done
  echo "   (tệp so sánh tạm ở $T, trong /tmp)"
}

run_A() { s "$LAB" "cd $LAB_DIR && $LAB_PY scripts/b4/b4_chay.py --branch A --host 127.0.0.1 --user branch_a --password branch_a_pass --image $IMG_A --count $1 --out $2"; }
run_B() { s "$B" "cd $B_DIR && .venv\\Scripts\\python.exe b4_chay.py --branch B --host 10.234.18.42 --user branch_b --password branch_b_pass --image demo_photos\\b4_B.jpg --count $1 --out $2" | tr -d '\r'; }
run_C() { s "$C" "cd $C_DIR && .venv\\Scripts\\python.exe b4_chay.py --branch C --host 10.234.18.42 --user branch_c --password branch_c_pass --image demo_photos\\b4_C.jpg --count $1 --out $2" | tr -d '\r'; }
run_D() { (cd "$D_DIR" && "$D_PY" b4_chay.py --branch D --host 10.234.18.42 --user branch_d --password branch_d_pass --image b4_D.jpg --count "$1" --out "$2"); }

cmd_thu() {
  cmd_may || return 1
  local st; st=$(date +%Y%m%d_%H%M%S)
  for X in A B C D; do
    echo "================ chi nhánh $X (1 yêu cầu)"
    case $X in A) run_A 1 "results/thu_A_$st.txt";; B) run_B 1 "thu_B_$st.txt";; C) run_C 1 "thu_C_$st.txt";; D) run_D 1 "thu_D_$st.txt";; esac
  done
  echo "Chỉ chạy 'chay' khi cả bốn khối trên đều có [KHÁCH QUEN]."
}

cmd_tongket() {
  local base=$1 out=$2 st; st=$(basename "$out" | sed 's/^b4_ketqua_//')
  echo "== tổng kết từ log_id > $base"
  sql "SELECT branch_id, COUNT(*), MIN(created_at), MAX(created_at) FROM recognition_logs WHERE log_id > $base GROUP BY branch_id ORDER BY branch_id" | tee "$out/b4_by_branch.txt"
  sql "SELECT worker, COUNT(*) FROM recognition_logs WHERE log_id > $base GROUP BY worker ORDER BY worker" | tee "$out/b4_workers.txt"
  sql "SELECT status, COUNT(*) FROM recognition_logs WHERE log_id > $base GROUP BY status" | tee "$out/b4_status.txt"
  sql "SELECT log_id, branch_id, worker, status, customer_id, created_at FROM recognition_logs WHERE log_id > $base ORDER BY log_id" > "$out/b4_rows.txt"
  echo "   (b4_rows.txt: $(wc -l < "$out/b4_rows.txt") dòng)"
  sql "SELECT COUNT(*), MAX(customer_id) FROM customers; SELECT COUNT(*) FROM orders" | tee "$out/b4_moc_sau.txt"
  s "$LAB" "cd $LAB_DIR && docker compose run --rm -T tools tools.reconcile < /dev/null 2>/dev/null" | tee "$out/b4_reconcile.txt"
  python3 - "$out/b4_by_branch.txt" "$COUNT" <<'EOF' | tee "$out/b4_nhanxet.txt"
import sys
rows = [l.rstrip("\n").split("\t") for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
n = int(sys.argv[2])
for b, c, lo, hi in rows:
    print(f"   chi nhánh {b}: {c} dòng (mong đợi {n}), từ {lo} đến {hi}")
if len(rows) >= 2:
    start, end = max(r[2] for r in rows), min(r[3] for r in rows)
    print(f"   cửa sổ chung của {len(rows)} chi nhánh: " + (f"CÓ, từ {start} đến {end} (giờ UTC của MySQL)" if start < end else "KHÔNG chồng nhau"))
EOF
  for f in b4_by_branch b4_workers b4_status b4_rows b4_moc_sau b4_reconcile b4_nhanxet; do
    s "$LAB" "cat > $LAB_DIR/results/${f}_$st.txt" < "$out/$f.txt"
  done
  echo "== Đã lưu ở $out và results/*_$st.txt trên lab. Dán các tệp này cho Thầy."
}

cmd_chay() {
  cmd_may || return 1
  local st out base; st=$(date +%Y%m%d_%H%M%S); out="$HOME/b4_ketqua_$st"; mkdir -p "$out"
  base=$(sql "SELECT COALESCE(MAX(log_id),0) FROM recognition_logs" | tr -d '[:space:]')
  [[ "$base" =~ ^[0-9]+$ ]] || { echo "LỖI: không đọc được mốc log_id trên lab ('$base'). Dừng, chưa gửi yêu cầu nào."; return 1; }
  echo "$base" > "$out/base.txt"
  echo "== mốc base=$base. Bốn máy bắt đầu NGAY BÂY GIỜ ($(date -u +%T) UTC)."
  # Bằng chứng dạng chữ cho "bốn máy cùng nối": mỗi khoảng 1,5 giây ghi danh sách kết nối RabbitMQ trên lab.
  ( while [ ! -f "$out/.xong" ]; do
      echo "--- $(date -u +%T) UTC"
      s "$LAB" "cd $LAB_DIR && docker compose exec -T rabbitmq rabbitmqctl list_connections user peer_host peer_port state < /dev/null 2>/dev/null"
      sleep 1.5
    done ) > "$out/b4_connections.txt" 2>&1 & pS=$!
  run_A "$COUNT" "results/b4_lab_A_$st.txt" > "$out/b4_A.txt" 2>&1 & pA=$!
  run_B "$COUNT" "b4_thuan_$st.txt"         > "$out/b4_B.txt" 2>&1 & pB=$!
  run_C "$COUNT" "b4_khang_$st.txt"         > "$out/b4_C.txt" 2>&1 & pC=$!
  run_D "$COUNT" "b4_tam_D_$st.txt"         > "$out/b4_D.txt" 2>&1 & pD=$!
  wait $pA; eA=$?; wait $pB; eB=$?; wait $pC; eC=$?; wait $pD; eD=$?
  touch "$out/.xong"; wait $pS
  s "$LAB" "cat > $LAB_DIR/results/b4_connections_$st.txt" < "$out/b4_connections.txt"
  echo "   các tài khoản thấy trong danh sách kết nối: $(grep -oE '^branch_[a-d]' "$out/b4_connections.txt" | sort -u | tr '\n' ' ')"
  echo "== bốn máy đã xong (mã thoát ssh/lệnh: A=$eA B=$eB C=$eC D=$eD)"
  for X in A B C D; do echo "   $X: $(tail -n 1 "$out/b4_$X.txt")"; done
  cmd_tongket "$base" "$out"
}

case "${1:-}" in
  may) cmd_may;; kiemtra) cmd_kiemtra;; chep) cmd_chep;; thu) cmd_thu;; chay) cmd_chay;;
  tongket) [ $# -eq 3 ] || { echo "dùng: bash b4_ssh.sh tongket <base> <thư_mục_kết_quả>"; exit 2; }; cmd_tongket "$2" "$3";;
  *) sed -n '2,10p' "$0"; exit 2;;
esac
