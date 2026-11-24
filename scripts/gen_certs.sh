#!/usr/bin/env bash
# Sinh CA riêng + chứng chỉ máy chủ RabbitMQ (tự ký, dùng cho đồ án/demo).
#   scripts/gen_certs.sh 192.168.1.10          # IP (hoặc hostname) mà các laptop dùng để nối tới server
# Kết quả trong ./certs : ca.pem (đưa cho client), server.pem + server.key (cho RabbitMQ)
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${1:-localhost}"
mkdir -p certs && cd certs

if [[ "$HOST" =~ ^[0-9.]+$ ]]; then SAN="IP:${HOST},DNS:localhost,IP:127.0.0.1"; else SAN="DNS:${HOST},DNS:localhost,IP:127.0.0.1"; fi

openssl genrsa -out ca.key 4096 2>/dev/null
openssl req -x509 -new -nodes -key ca.key -sha256 -days 365 -subj "/CN=CoffeeHouse Demo CA" -out ca.pem

openssl genrsa -out server.key 2048 2>/dev/null
openssl req -new -key server.key -subj "/CN=${HOST}" -out server.csr
printf "subjectAltName=%s\nextendedKeyUsage=serverAuth\n" "$SAN" > server.ext
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out server.pem -days 365 -sha256 -extfile server.ext 2>/dev/null

chmod 644 ca.pem server.pem server.key   # demo: container RabbitMQ (uid 999) phải đọc được key; môi trường thật dùng secret manager
rm -f server.csr server.ext ca.srl
echo "Đã tạo chứng chỉ cho ${HOST} trong ./certs"
echo "  - Server: docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d"
echo "  - Client: RABBIT_TLS=true RABBIT_PORT=5671 RABBIT_CA_FILE=<đường dẫn>/ca.pem  (copy ca.pem sang laptop)"
