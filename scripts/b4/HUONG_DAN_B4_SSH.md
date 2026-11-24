# Hướng dẫn B4 qua SSH (Tâm điều khiển từ laptop WSL, chi nhánh D)

Bộ tệp: `b4_chay.py` (chạy 15 yêu cầu, dùng chung cho cả bốn máy), `b4_ssh.sh` + `b4.conf.mau` (điều phối, chạy trên laptop WSL của Tâm), `bat_ssh.ps1` và `tat_ssh.ps1` (Thuận, Khang chạy trên máy Windows).

**Mức kiểm chứng (Thầy ghi thật):** `b4_chay.py` đã chạy thử trong sandbox của Thầy với client giả (vòng lặp, ghi tệp, từ chối ghi đè) và với client thật khi không có broker (lỗi được ghi nguyên văn, mỗi lần khoảng 3,1 giây). `b4_ssh.sh` đã chạy trọn lệnh `chay` trong mô phỏng (ssh và docker giả) và qua `shellcheck`. **Chưa chạy** trên máy thật: SSH vào Windows, cú pháp lệnh `cmd` của Windows, và hai tệp `.ps1` (sandbox không có PowerShell). Lần chạy đầu trên máy thật là lần kiểm chứng.

## Điều kiện trước khi bắt đầu
- Thuận và Khang **đồng ý** và tự chạy `bat_ssh.ps1` trên máy mình.
- WSL trên laptop của Tâm chạy được (ZeroTier của Tâm nằm trong WSL; PowerShell không thấy mạng `10.234.x.x`).
- Cả bốn máy: ZeroTier mạng `8d1c312afa8f1e2a` đang `OK`.
- Máy Thuận và Khang: thư mục `coffeehouse-client` (có `client\`, `common\`, `.venv\`) như buổi 05/11. Lệnh `kiemtra` sẽ báo nếu thiếu.
- Lúc chạy B4, hai máy Windows phải **mở, cắm sạc, không ngủ**.

## Bước 1. Tâm tạo khoá SSH riêng cho buổi B4 (laptop, TRONG WSL)
```bash
# LAPTOP, TRONG WSL. Phải ra: Whitehathacker / ctam31 / Linux
hostname; whoami; uname -s
ssh-keygen -t ed25519 -f ~/.ssh/id_b4 -C b4-coffeehouse -N ""
cat ~/.ssh/id_b4.pub
```
Dòng in ra bắt đầu bằng `ssh-ed25519 ... b4-coffeehouse` là **khoá công khai**: gửi dòng đó cho Thuận và Khang. **Không** gửi tệp `~/.ssh/id_b4` (khoá bí mật) cho ai. Khoá không đặt mật khẩu để bốn máy chạy song song không bị hỏi; vì vậy gỡ nó sau buổi (Bước 8).

## Bước 2. Tâm đưa khoá lên máy lab (laptop, TRONG WSL)
```bash
# LAPTOP, TRONG WSL
hostname; whoami; uname -s
ssh-copy-id -i ~/.ssh/id_b4.pub labsit@10.234.18.42
```
Nhập mật khẩu của `labsit` một lần. Kiểm tra (phải in `LabSit25` mà không hỏi mật khẩu):
```bash
# LAPTOP, TRONG WSL
ssh -i ~/.ssh/id_b4 -o IdentitiesOnly=yes -o BatchMode=yes labsit@10.234.18.42 hostname
```

## Bước 3. Thuận và Khang bật SSH (máy Windows của từng bạn)
Mở PowerShell bằng **Run as administrator**, bằng **chính tài khoản Windows của mình**, `cd` tới thư mục chứa `bat_ssh.ps1`, rồi chạy (dán khoá công khai Tâm gửi vào giữa hai dấu nháy):
```powershell
# MÁY THUẬN / KHANG, PowerShell (Administrator)
hostname
powershell -ExecutionPolicy Bypass -File .\bat_ssh.ps1 -PublicKey "ssh-ed25519 AAAA...dòng Tâm gửi... b4-coffeehouse"
```
Script cài OpenSSH Server nếu chưa có (cần Internet, vài phút), chỉ mở cổng 22 cho `10.234.18.0/24`, đặt dịch vụ ở chế độ khởi động thủ công, và thêm khoá của Tâm. Cuối cùng nó in **Tài khoản** và **IP ZeroTier**: gửi hai dòng đó cho Tâm. Không gửi mật khẩu Windows.

## Bước 4. Tâm chuẩn bị bộ tệp và kiểm tra (laptop, TRONG WSL)
Giải nén bộ tệp vào `~/b4` (tệp tải về trên Windows nằm ở `/mnt/c/Users/DellOS/Downloads/`), rồi:
```bash
# LAPTOP, TRONG WSL
hostname; whoami; uname -s
cd ~/b4 && cp -n b4.conf.mau b4.conf && nano b4.conf
```
Trong `b4.conf`, thay `ĐIỀN_TAI_KHOAN_THUAN` và `ĐIỀN_TAI_KHOAN_KHANG` bằng tài khoản hai bạn gửi; nếu IP ZeroTier của hai bạn khác `.129` và `.61` thì sửa luôn. Lưu (Ctrl+O, Enter, Ctrl+X). Rồi:
```bash
# LAPTOP, TRONG WSL
cd ~/b4 && bash b4_ssh.sh kiemtra
```
Mong đợi: in tên ba máy, các dòng `co client`, `co venv`, và mốc `7 9` / `9`. Lần SSH đầu tới mỗi máy, khoá máy chủ được ghi nhận tự động.

## Bước 5. Chép tệp và so mã client
```bash
# LAPTOP, TRONG WSL
cd ~/b4 && bash b4_ssh.sh chep
```
Lệnh này chép `b4_chay.py` sang bốn máy và chép **ảnh gốc từ dataset trên lab** (không qua Zalo) sang máy B, C, D, rồi so md5 mã client với máy lab. Có dòng `KHÁC lab` thì **dừng**, dán cho Thầy.

## Bước 6. Giai đoạn 0: mỗi máy một yêu cầu
```bash
# LAPTOP, TRONG WSL
cd ~/b4 && bash b4_ssh.sh thu
```
Chỉ sang Bước 7 khi cả bốn khối A, B, C, D đều có `[KHÁCH QUEN]`.

## Bước 7. B4
Báo Thuận và Khang (Zalo) là sắp chạy, máy đừng ngủ. Rồi:
```bash
# LAPTOP, TRONG WSL
cd ~/b4 && bash b4_ssh.sh chay
```
Lệnh tự làm: ghi mốc `log_id`, cho bốn máy chạy 15 yêu cầu **cùng lúc**, trong lúc đó ghi danh sách kết nối RabbitMQ (`b4_connections.txt`, bằng chứng dạng chữ cho bốn tài khoản nối từ bốn địa chỉ), rồi tổng kết theo chi nhánh, theo worker, theo trạng thái, mốc dữ liệu sau, `reconcile`, và tính cửa sổ thời gian chung. Kết quả ở `~/b4_ketqua_<ngày_giờ>/` và bản sao trong `results/` của lab. **Dán nguyên văn** cho Thầy, đừng sửa số. Máy nào lỗi: lỗi nằm trong tệp `b4_X.txt` của máy đó, đó cũng là kết quả.

Nếu lệnh bị ngắt sau khi bốn máy đã chạy: `bash b4_ssh.sh tongket <số trong base.txt> <thư mục kết quả>`.

## Bước 8. Sau buổi (chỉ khi Thầy đã xác nhận đủ bằng chứng)
Mỗi khối là một việc riêng; làm sau khi đã dán kết quả cho Thầy.

Thuận, Khang tắt SSH và gỡ khoá của Tâm:
```powershell
# MÁY THUẬN / KHANG, PowerShell (Administrator)
hostname
powershell -ExecutionPolicy Bypass -File .\tat_ssh.ps1 -XoaKhoaTam
```
Thuận, Khang xoá ảnh B4 trên máy mình (ảnh khuôn mặt, đã hứa xoá):
```powershell
# MÁY THUẬN / KHANG, PowerShell (thư mục coffeehouse-client)
hostname
Remove-Item .\demo_photos\b4_*.jpg
```
Tâm gỡ khoá B4 trên lab rồi xoá cặp khoá trên laptop (hai khối riêng):
```bash
# LAPTOP, TRONG WSL: gỡ dòng khoá b4-coffeehouse khỏi máy lab
ssh -i ~/.ssh/id_b4 -o IdentitiesOnly=yes labsit@10.234.18.42 "sed -i '/b4-coffeehouse/d' ~/.ssh/authorized_keys && grep -c b4-coffeehouse ~/.ssh/authorized_keys || true"
```
```bash
# LAPTOP, TRONG WSL: xoá cặp khoá B4
hostname; whoami
rm ~/.ssh/id_b4 ~/.ssh/id_b4.pub
```
