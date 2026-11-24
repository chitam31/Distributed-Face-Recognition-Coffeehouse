# bat_ssh.ps1 - BẬT SSH tạm thời cho buổi B4 (máy Windows của Thuận hoặc Khang).
# Chạy trong PowerShell MỞ BẰNG "Run as administrator", bằng CHÍNH tài khoản Windows của em:
#   powershell -ExecutionPolicy Bypass -File .\bat_ssh.ps1 -PublicKey "ssh-ed25519 AAAA... b4-coffeehouse"
# Việc làm: cài OpenSSH Server (nếu chưa có), chạy dịch vụ sshd (khởi động THỦ CÔNG, không tự bật lại sau khi
# tắt máy), chỉ cho phép cổng 22 từ mạng ZeroTier của nhóm 10.234.18.0/24, thêm khoá công khai của Tâm.
# Không xoá gì. Sau buổi chạy tat_ssh.ps1. Các lệnh theo tài liệu OpenSSH cho Windows của Microsoft.
param(
    [Parameter(Mandatory = $true)][string]$PublicKey,
    [string]$Subnet = "10.234.18.0/24"
)
$ErrorActionPreference = "Stop"

Write-Host "== Máy: $(hostname)  |  Tài khoản đăng nhập SSH sẽ là: $env:USERNAME"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Host "LỖI: hãy mở PowerShell bằng 'Run as administrator' rồi chạy lại."; exit 1 }
if ($PublicKey -notmatch '^ssh-(ed25519|rsa) [A-Za-z0-9+/=]+ ?.*$') { Write-Host "LỖI: -PublicKey không giống một khoá công khai SSH (phải bắt đầu bằng ssh-ed25519)."; exit 1 }

Write-Host "== 1. IP ZeroTier của máy này (phải có dạng 10.234.18.x)"
$zt = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -like "10.234.18.*" }
if ($zt) { $zt | ForEach-Object { Write-Host ("   " + $_.IPAddress + "  (" + $_.InterfaceAlias + ")") } }
else { Write-Host "   CẢNH BÁO: chưa thấy IP 10.234.18.x. Mở ZeroTier One, kiểm mạng 8d1c312afa8f1e2a đang OK." }

Write-Host "== 2. OpenSSH Server"
$cap = Get-WindowsCapability -Online | Where-Object Name -like "OpenSSH.Server*"
if ($cap.State -ne "Installed") {
    Write-Host "   Đang cài (cần Internet, có thể mất vài phút)..."
    Add-WindowsCapability -Online -Name $cap.Name | Out-Null
} else { Write-Host "   Đã cài sẵn." }
Set-Service -Name sshd -StartupType Manual
Start-Service sshd
Write-Host ("   sshd: " + (Get-Service sshd).Status)

Write-Host "== 3. Tường lửa: cổng 22 chỉ nhận từ $Subnet"
$rule = Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue
if (-not $rule) {
    New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -DisplayName "OpenSSH Server (sshd)" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
}
Set-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -Enabled True -Profile Any -RemoteAddress $Subnet
$addr = (Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" | Get-NetFirewallAddressFilter).RemoteAddress
Write-Host "   Quy tắc OpenSSH-Server-In-TCP, địa chỉ được phép: $addr"

Write-Host "== 4. Khoá công khai của Tâm (đăng nhập không cần mật khẩu)"
# Tài khoản thuộc nhóm Administrators: OpenSSH cho Windows đọc khoá ở tệp chung dưới ProgramData,
# và tệp đó chỉ được cấp quyền cho Administrators (S-1-5-32-544) và SYSTEM (S-1-5-18).
$keyFile = Join-Path $env:ProgramData "ssh\administrators_authorized_keys"
$line = $PublicKey.Trim()
if ((Test-Path $keyFile) -and (Select-String -Path $keyFile -SimpleMatch $line -Quiet)) {
    Write-Host "   Khoá đã có sẵn trong $keyFile"
} else {
    Add-Content -Path $keyFile -Value $line -Encoding ascii
    Write-Host "   Đã thêm khoá vào $keyFile"
}
icacls.exe $keyFile /inheritance:r /grant "*S-1-5-32-544:F" /grant "*S-1-5-18:F" | Out-Null
Restart-Service sshd

Write-Host ""
Write-Host "XONG. Gửi Tâm hai dòng sau (KHÔNG gửi mật khẩu Windows):"
Write-Host "   Tài khoản: $env:USERNAME"
Write-Host ("   IP ZeroTier: " + (($zt | Select-Object -ExpandProperty IPAddress) -join ", "))
Write-Host "Sau buổi B4 nhớ chạy tat_ssh.ps1."
