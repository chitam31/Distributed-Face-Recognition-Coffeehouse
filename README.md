# Hệ thống nhận diện khuôn mặt phân tán cho chuỗi quán cà phê nhiều chi nhánh

CS401V – Distributed Systems · Lab Assignment 2 · Tân Tạo University · hạn nộp 30/11/2026

Nhóm: Lê Chí Tâm (2302220), Triệu Minh Thuận (2302059), Nguyễn Hạo Khang (2302319). Thư mục này là mã nguồn đồ án; báo cáo cuối nằm ở thư mục `1. BAO CAO` bên cạnh. Repo GitHub: https://github.com/chitam31/Distributed-Face-Recognition-Coffeehouse

Mỗi chi nhánh chụp ảnh khách bằng một ứng dụng client và gửi lên máy chủ qua **RabbitMQ**. Một nhóm **worker** nhận diện khuôn mặt bằng **InsightFace (SCRFD + ArcFace) trên ONNX Runtime**, so khớp vector trong **PostgreSQL + pgvector**, rồi tra hồ sơ và đơn gần nhất trong **MySQL**. Khách quen → trả đơn gần nhất để quán xác nhận; khách mới → báo cho chi nhánh và lưu lại cho lần sau.

Sơ đồ kiến trúc: Hình 1 và Hình 2 trong báo cáo.

## Điểm nhấn phân tán

| Vấn đề | Cách xử lý trong code | Nơi xem |
|---|---|---|
| Mở rộng | Nhiều worker cùng đọc `face.requests` (competing consumers), `prefetch=1`; `--scale worker=N` | `worker/worker.py`, `docker-compose.yml` |
| Quá tải | Queue bền vững hấp thụ đợt request dồn dập, client chỉ chờ lâu hơn chứ không lỗi | `tools/chaos.py flood` |
| Worker chết | Ack thủ công **sau khi đã reply**; worker chết → RabbitMQ giao lại cho worker khác | `worker/handler.py` |
| Mạng/client lỗi | Client timeout 10s rồi gửi lại **cùng `request_id`**; worker tra `recognition_logs` → idempotent | `client/rpc_client.py`, `worker/service.py` |
| Nhất quán 2 CSDL | Không có transaction phân tán → ghi MySQL rồi pgvector, lỗi thì **bù trừ (saga)**; `tools/reconcile.py` dọn dữ liệu mồ côi | `worker/service.py` |
| Đồng thời | Hai chi nhánh gặp cùng một người mới → `pg_advisory_xact_lock` + kiểm tra lại trong khoá | `worker/repo_vector.py` |
| Message hỏng / lỗi kéo dài | Dead-letter queue (`face.requests.dlq`, `orders.dlq`) | `common/rabbit.py` |
| Bảo mật | Tài khoản riêng mỗi chi nhánh (chỉ publish), TLS (amqps), quyền thư mục ảnh | `scripts/create_branch_users.sh`, `scripts/gen_certs.sh` |
| Không đồng nhất | Giao thức AMQP + JSON: client có thể viết bằng ngôn ngữ khác worker | `common/messages.py` |

Báo cáo (số liệu thật, phân tích, sơ đồ): `CS401V_Report_Final_Project_Distributed_System_EN.pdf` (tiếng Anh) và `CS401V_Report_Final_Project_Distributed_System_VI.pdf` (tiếng Việt), trong thư mục `1. BAO CAO`.

## Cấu trúc thư mục

```
coffeehouse-face/
├── docker-compose.yml        RabbitMQ + PostgreSQL(pgvector) + MySQL + worker (scale được)
├── docker-compose.tls.yml    lớp phủ bật amqps (TLS)
├── docker-compose.admin.yml  trang quản trị chỉ đọc (một container, chỉ 127.0.0.1:8090)
├── sql/                      mysql_init.sql, pg_init.sql
├── common/                   config.py, messages.py (hợp đồng message), rabbit.py (topology), errors.py
├── worker/                   worker.py, handler.py, service.py, face_engine.py, repo_mysql.py,
│                             repo_vector.py, photo_store.py, Dockerfile
├── admin/                    web.py + web_static/ (trang quản trị chỉ đọc của máy server)
├── client/                   branch_client.py (CLI), gui.py (Tkinter + webcam), web.py + web_static/ (giao diện web, có đăng nhập),
│                             rpc_client.py, imaging.py, presenter.py, menu.json
├── tools/                    enroll.py, benchmark.py, eval_threshold.py, reconcile.py, chaos.py,
│                             init_topology.py, make_charts.py, metrics.py
├── scripts/                  quickstart.sh, create_branch_users.sh/.ps1, gen_certs.sh, demo_kill_worker.sh, admin_web.sh,
│                             measure_drain.sh
├── tests/                    129 unit test (repo giả: không cần broker/DB/model)
└── docker/rabbitmq/          tls.conf (cấu hình amqps cho lớp phủ TLS)
(dataset/ ảnh khuôn mặt không nộp: dữ liệu sinh trắc học; tự tạo dataset/<tên_người>/*.jpg khi chạy công cụ đo)
```

## 1. Chạy phía server (một máy, cần Docker + Docker Compose v2)

```bash
cp .env.example .env                 # tuỳ chọn
scripts/quickstart.sh 2              # dựng toàn bộ + 2 worker + tài khoản chi nhánh A, B, C  (Windows: chạy trong WSL2 hoặc Git Bash; tài khoản chi nhánh: scripts/create_branch_users.ps1)
docker compose logs -f worker        # lần đầu worker nạp model nên mất ~10–30s
```

Hoặc thủ công:

```bash
docker compose up -d --build --scale worker=3
scripts/create_branch_users.sh A B C
```

* RabbitMQ UI: http://localhost:15672 (mặc định `coffee_admin` / `coffee_admin_123`).
* Đổi số worker khi đang chạy: `docker compose up -d --scale worker=1` (hoặc 2, 3).
* Đo mở rộng: `bash scripts/measure_drain.sh <số_worker> 200` (kết quả của nhóm: Bảng VIII và Hình 7 trong báo cáo). Trên **một máy**, mỗi worker mặc định dùng hết nhân CPU (ONNX Runtime) nên thêm worker không tăng thông lượng; đặt `ORT_THREADS=2` trong `.env` (số worker × ORT_THREADS ≤ số nhân) rồi `docker compose up -d --scale worker=N` để thấy thông lượng tăng.
* Mặc định cổng **5672** (AMQP) và **15672** (UI) chỉ mở trên chính máy server (`127.0.0.1`). Để laptop chi nhánh ở máy khác nối vào, đặt `RABBIT_BIND_IP=<IP mạng riêng của server>` trong `.env` (vd. IP ZeroTier `10.234.18.42`; không dùng `0.0.0.0`) rồi `docker compose up -d`. Compose chỉ publish cổng 5672 trên IP đó, nên các mạng khác mà máy server đang tham gia không nối được. Mạng trường chặn kết nối giữa các máy → dùng hotspot điện thoại hoặc ZeroTier/Tailscale. Kiểm tra: `ss -ltn | grep -E ':(5672|15672)'`.

> **Lưu ý build:** `docker compose build worker` cần Internet để cài `insightface` và tải model `buffalo_l` (~280 MB) vào image, lần đầu mất vài phút.

## 2. Nạp dữ liệu ban đầu và đo đạc (tuỳ chọn nhưng nên làm)

Các công cụ chạy **ngay trong container** (đã có sẵn model và thư viện), nên máy em không cần cài `insightface`. Đặt ảnh vào `dataset/<tên_người>/*.jpg` rồi:

```bash
docker compose run --rm tools tools.enroll --dataset dataset --per-person 2           # enroll 1–2 ảnh mỗi người
docker compose run --rm tools tools.eval_threshold --dataset dataset --gallery-per-person 2 --unknown-people 3
docker compose run --rm tools tools.benchmark --images dataset --requests 60 --concurrency 12 --workers 3
docker compose run --rm tools tools.reconcile                                          # kiểm tra dữ liệu mồ côi
pip install matplotlib && python -m tools.make_charts                                  # (trên máy host) sinh biểu đồ
```

Lưu ý: `tools.enroll` đăng ký **mọi** thư mục trong `dataset/`, kể cả thư mục `zz_*` (người lạ dùng để đo nhận nhầm). Nếu cần giữ người lạ cho buổi demo, đừng chạy `tools.enroll` trên cả `dataset/` mà chỉ đăng ký từng người qua client. Phân tích theo cặp (chỉ đọc cache embedding, chạy sau `eval_threshold`): `docker compose run --rm -T --entrypoint python tools - < tools/pair_analysis.py`.

Kết quả ghi vào thư mục `results/` trên máy em. Linux/macOS: nếu báo lỗi quyền ghi, chạy `chmod a+rwx results` một lần.
Ngưỡng chọn được: đặt `SIM_THRESHOLD=<giá trị>` trong `.env` rồi `docker compose up -d`.

## 3. Chạy client chi nhánh (trên laptop có webcam)

```bash
pip install -r client/requirements.txt
export BRANCH_ID=A RABBIT_HOST=<IP-server> RABBIT_USER=branch_a RABBIT_PASS=branch_a_pass
python -m client.gui --branch A                # giao diện: webcam + nhận diện + nhập đơn
# hoặc dòng lệnh:
python -m client.branch_client --branch A recognize --image dataset/nguyen_an/1.jpg --name "Nguyễn An"
python -m client.branch_client --branch A camera
```

Windows PowerShell: `$env:RABBIT_HOST="192.168.1.10"` thay cho `export`.

### Giao diện web của chi nhánh (không cần Tkinter; dùng được khi laptop chạy WSL không có webcam)

```bash
python -m client.web --branch B --host <IP-server>      # rồi mở http://127.0.0.1:8080 và đăng nhập branch_b
```

Trang hỏi tài khoản chi nhánh (`branch_a`, `branch_b`...): mật khẩu đúng khi và chỉ khi RabbitMQ cho nối, nên máy chi nhánh
không lưu mật khẩu nào; `--branch B` ghim máy vào chi nhánh B (tài khoản chi nhánh khác bị từ chối); một máy một phiên, rảnh
8 giờ thì phải đăng nhập lại. Kiểu cũ không đăng nhập: thêm `--no-login --user branch_b --password branch_b_pass`.
Trình duyệt bật camera (hoặc "Chọn ảnh…"), tiến trình `client.web` đổi ảnh thành message RabbitMQ bằng đúng `BranchRpcClient`
của CLI/GUI, nên idempotency/retry/timeout giống hệt. Mật khẩu RabbitMQ chỉ nằm ở tiến trình này, trình duyệt không giữ.
Mặc định chỉ nghe `127.0.0.1` (địa chỉ này được trình duyệt coi là an toàn nên cho dùng camera không cần HTTPS). Trang hiển thị
khách quen, đơn gần nhất, **món hay gọi** (tối đa 3 món gọi nhiều nhất của khách), menu, giỏ hàng, và nhật ký thao tác có cột
**worker** đã xử lý. Chạy trong WSL thì mở trang ở trình duyệt Windows (trình duyệt có webcam).

### Trang quản trị chỉ đọc (máy server)

```bash
echo 'ADMIN_PASSWORD=<ít nhất 10 ký tự>' >> .env     # hoặc nhập bằng read -s để mật khẩu không hiện trên màn hình
bash scripts/admin_web.sh up                           # rồi mở http://127.0.0.1:8090 trên máy server
```

Một container riêng (cùng image, mã gắn chỉ đọc, không build lại, worker không bị tạo lại), chỉ publish `127.0.0.1:8090`.
Hiện trực tiếp: yêu cầu theo chi nhánh và theo worker (số lượng, kết quả, p50/p95 ms), biểu đồ theo thời gian, độ sâu hàng
đợi, kết nối đang mở cùng tài khoản đã xác thực, nhật ký và đơn gần nhất (chỉ mã khách), và nút đối chiếu MySQL ↔ pgvector.
Phiên MySQL và PostgreSQL đặt chế độ chỉ đọc; không có đường dẫn ghi nào. Xem từ máy khác qua
đường hầm SSH: `ssh -L 8090:127.0.0.1:8090 <user>@<máy server>` rồi mở `http://localhost:8090`.

## 4. Kiểm thử tự động

```bash
python -m unittest discover -s tests -t . -v
```

129 test chạy trong vài giây, không cần broker, CSDL hay model. Phần chính: idempotency, khoá chống trùng khách (có test đối chứng khi bỏ khoá), saga bù trừ, quy tắc ack/retry/DLQ (kể cả reply phải đi trước ack), validate message, lọc ảnh mờ, tính percentile/quét ngưỡng, xếp hạng "món hay gọi", lớp HTTP của giao diện web (kiểm tra đầu vào, ánh xạ lỗi, từ chối yêu cầu từ trang khác), đăng nhập chi nhánh (ghim chi nhánh, khoá tạm, hết hạn phiên), trang quản trị (gom số liệu, chỉ SELECT, phiên chỉ đọc, lỗi cục bộ từng nguồn).

## 5. Bảo mật (tuỳ chọn, nên bật khi demo trên mạng thật)

```bash
scripts/gen_certs.sh <IP-server>
docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d --scale worker=3
# client: RABBIT_TLS=true RABBIT_PORT=5671 RABBIT_CA_FILE=/đường/dẫn/ca.pem
```

Mỗi chi nhánh một tài khoản chỉ có quyền publish vào exchange `coffee.requests` và cấu hình/đọc các queue có tên `reply.*` (queue trả lời là exclusive nên chi nhánh khác không đọc được; mẫu tên chưa tách riêng từng chi nhánh) — xem `scripts/create_branch_users.sh`.

## Xử lý sự cố thường gặp

| Triệu chứng | Nguyên nhân / cách xử lý |
|---|---|
| Client: `RabbitMQ từ chối (404)` | Chưa có worker nào khai báo topology → chạy `docker compose run --rm tools tools.init_topology` hoặc đợi worker khởi động |
| Client: `RabbitMQ từ chối (403)` | Sai tài khoản; dùng user tạo bởi `create_branch_users.sh` |
| Client nối từ máy khác bị từ chối | Dùng user `guest` (chỉ cho phép localhost), firewall chặn cổng 5672, hoặc chưa đặt `RABBIT_BIND_IP` trong `.env` (mặc định cổng chỉ mở trên localhost) |
| `PRECONDITION_FAILED` khi worker khởi động | Đổi `QUEUE_TYPE` khi queue đã tồn tại → `docker compose down -v` rồi dựng lại |
| Mọi ảnh trả `NO_FACE` | Ảnh quá nhỏ/mờ; hạ `MIN_BLUR_VAR` (mặc định 30) hoặc `MIN_FACE_PX` (60) |
| Nhận nhầm người / bỏ sót nhiều | Chạy `tools.eval_threshold` và chỉnh `SIM_THRESHOLD` |
| Build image dừng ở bước kiểm tra (`numpy bi nang len`, `ImportError`) | Xung đột phiên bản thư viện; gửi 30 dòng cuối log build, thử `docker compose build --no-cache worker` |
| Worker khởi động chậm | Đang nạp model; xem `docker compose logs worker` |
| Scale mà chỉ thấy 1 worker xử lý | Chưa đủ request đồng thời; dùng `tools.benchmark --concurrency 12` hoặc `tools.chaos flood` |

## Phạm vi đã kiểm chứng và giới hạn

* **Đã chạy thật trên máy lab (Docker Compose: RabbitMQ 3.13, MySQL 8, PostgreSQL 16/pgvector, 3 worker InsightFace):** nhận diện khách quen / khách mới / không thấy mặt; đơn hàng liên chi nhánh; một chi nhánh trên laptop riêng qua mạng ZeroTier; giao diện Tkinter (chi nhánh A và B, mở ảnh, cập nhật khách, webcam thật trên máy lab); giao diện web có đăng nhập theo chi nhánh (chi nhánh D từ laptop ngày 14/11, chi nhánh A trên máy lab ngày 16/11); trang quản trị chỉ đọc qua đường hầm SSH (14/11: phiên MySQL 8.0.46 từ chối lệnh ghi với lỗi 1792; với chi nhánh D, sai mật khẩu và tài khoản `branch_a` đều bị từ chối); benchmark và đo dồn tải với 1–6 worker; giết worker giữa chừng (giao lại + phát lại theo `request_id`, không trùng log); dead-letter queue; đối soát hai CSDL; quét ngưỡng trên 11 người / 77 ảnh.
* **Unit test:** 129 test đạt (chạy lại ngày 16/11/2026 và trên chính bộ mã nộp ngày 24/11/2026), không cần broker, CSDL hay model. Trước khi lên máy lab, giao diện web và trang quản trị còn được thử với Chrome thật trong môi trường thử.
* **Số liệu và bằng chứng:** báo cáo cuối tiếng Anh và tiếng Việt (thư mục `1. BAO CAO`), các bảng và hình ở Mục VII.
* **Chưa kiểm chứng trên môi trường thật:** TLS (`docker-compose.tls.yml`, cổng 5671) trên mạng thật, mọi lần chạy dùng AMQP thường trong mạng ZeroTier; worker chạy trên nhiều máy (mọi worker hiện cùng một máy); bốn chi nhánh trên bốn máy cùng gửi một lúc (`scripts/b4/` trong repo GitHub đã chuẩn bị, chưa có kết quả); giao diện Tkinter/webcam trên laptop WSL (WSL không có webcam); chưa đo lại hiệu năng sau khi worker thêm truy vấn "món hay gọi".
* Ngưỡng đã chọn: `SIM_THRESHOLD=0.46` (đặt trong `.env`; giá trị mặc định trong mã vẫn là 0.45, kết quả giống hệt trên bộ dữ liệu này). Độ chính xác thực tế còn phụ thuộc camera, ánh sáng và việc enroll nhiều ảnh (xem mục VII-B của báo cáo).
* Giấy phép: model pretrained của InsightFace (`buffalo_l`) chỉ dành cho nghiên cứu phi thương mại.
* Khuôn mặt là dữ liệu sinh trắc học nhạy cảm: chỉ thu ảnh khi có sự đồng ý của người chụp, hạn chế quyền truy cập thư mục ảnh.
