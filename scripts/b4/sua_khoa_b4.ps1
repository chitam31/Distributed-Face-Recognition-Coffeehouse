# sua_khoa_b4.ps1 - Sửa để SSH trên máy này nhận khoá B4 của Tâm, rồi in thông tin kiểm tra.
# Chạy trong PowerShell (Run as administrator), ở thư mục có cả tệp này và tệp id_b4.pub Tâm gửi:
#   powershell -ExecutionPolicy Bypass -File .\sua_khoa_b4.ps1
# Chỉ đụng tới: tệp khoá chung C:\ProgramData\ssh\administrators_authorized_keys (thay dòng b4-coffeehouse)
# và khởi động lại dịch vụ sshd. Gỡ sau buổi: tat_ssh.ps1 -XoaKhoaTam (như cũ).

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host "== Máy: $(hostname)  |  Tài khoản: $env:USERNAME  |  Admin: $isAdmin"
if (-not $isAdmin) { Write-Host "LỖI: hãy mở PowerShell bằng 'Run as administrator' rồi chạy lại."; exit 1 }

$pub = Get-ChildItem -Path $PSScriptRoot -Filter "id_b4*.pub" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $pub) { Write-Host "LỖI: không thấy tệp id_b4.pub nằm cùng thư mục với script ($PSScriptRoot)."; exit 1 }
$key = (Get-Content -Path $pub.FullName -Raw).Trim()
if ($key -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+ b4-coffeehouse$') { Write-Host "LỖI: $($pub.Name) không phải một dòng 'ssh-ed25519 ... b4-coffeehouse'."; exit 1 }

$kg = Join-Path $env:WINDIR "System32\OpenSSH\ssh-keygen.exe"
$adminFile = Join-Path $env:ProgramData "ssh\administrators_authorized_keys"

Write-Host "== 1. Vân tay khoá trong $($pub.Name) (phải trùng với laptop của Tâm)"
if (Test-Path $kg) { & $kg -lf $pub.FullName } else { Write-Host "   (không có ssh-keygen.exe, bỏ qua)" }

Write-Host "== 2. Tài khoản có trong nhóm Administrators (S-1-5-32-544)?"
whoami /groups | Select-String "S-1-5-32-544" | ForEach-Object { "   " + $_.Line.Trim() }

Write-Host "== 3. Tệp khoá chung TRƯỚC khi sửa"
if (Test-Path $adminFile) {
    if (Test-Path $kg) { & $kg -lf $adminFile }
    icacls.exe $adminFile | ForEach-Object { "   " + $_ }
} else { Write-Host "   (chưa có tệp $adminFile)" }

Write-Host "== 4. Ghi lại khoá (lấy nguyên từ tệp, không qua dán tay) và đặt lại quyền"
$keep = @()
if (Test-Path $adminFile) { $keep = @(Get-Content -Path $adminFile | Where-Object { $_.Trim() -ne "" -and $_ -notmatch "b4-coffeehouse" }) }
$lines = @($keep) + @($key)
[IO.File]::WriteAllText($adminFile, (($lines -join "`r`n") + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
icacls.exe $adminFile /setowner "*S-1-5-32-544" | Out-Null
icacls.exe $adminFile /inheritance:r /grant "*S-1-5-32-544:F" /grant "*S-1-5-18:F" | Out-Null
Write-Host "   Số dòng khoá trong tệp: $($lines.Count)"
if (Test-Path $kg) { & $kg -lf $adminFile }
icacls.exe $adminFile | ForEach-Object { "   " + $_ }

Write-Host "== 5. Cấu hình sshd (Match / AuthorizedKeysFile / PubkeyAuthentication)"
$cfg = Join-Path $env:ProgramData "ssh\sshd_config"
Select-String -Path $cfg -Pattern '^\s*#?\s*(Match|AuthorizedKeysFile|PubkeyAuthentication|StrictModes)' | ForEach-Object { "   " + $_.Line }

Write-Host "== 6. Nhật ký OpenSSH (10 dòng mới nhất)"
Get-WinEvent -LogName "OpenSSH/Operational" -MaxEvents 10 -ErrorAction SilentlyContinue | ForEach-Object { "   " + $_.TimeCreated.ToString("HH:mm:ss") + "  " + ($_.Message -replace "\s+", " ") }

Write-Host "== 7. Khởi động lại sshd"
Restart-Service sshd
Write-Host ("   sshd: " + (Get-Service sshd).Status)
Write-Host ""
Write-Host "XONG. Chụp toàn bộ màn hình này gửi Tâm."
