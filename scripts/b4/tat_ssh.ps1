# tat_ssh.ps1 - TẮT SSH sau buổi B4 (máy Windows của Thuận hoặc Khang).
# Chạy trong PowerShell "Run as administrator":
#   powershell -ExecutionPolicy Bypass -File .\tat_ssh.ps1                 (dừng sshd, tắt quy tắc tường lửa)
#   powershell -ExecutionPolicy Bypass -File .\tat_ssh.ps1 -XoaKhoaTam     (thêm: gỡ dòng khoá có chữ b4-coffeehouse)
# Không gỡ OpenSSH Server khỏi Windows; chỉ dừng và vô hiệu hoá nó.
param([switch]$XoaKhoaTam)
$ErrorActionPreference = "Stop"

Write-Host "== Máy: $(hostname)"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Host "LỖI: hãy mở PowerShell bằng 'Run as administrator' rồi chạy lại."; exit 1 }

if (Get-Service sshd -ErrorAction SilentlyContinue) {
    Stop-Service sshd
    Set-Service -Name sshd -StartupType Disabled
    Write-Host ("   sshd: " + (Get-Service sshd).Status + ", khởi động: " + (Get-Service sshd).StartType)
} else { Write-Host "   Máy này không có dịch vụ sshd." }

if (Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue) {
    Disable-NetFirewallRule -Name "OpenSSH-Server-In-TCP"
    Write-Host "   Đã tắt quy tắc tường lửa OpenSSH-Server-In-TCP."
}

if ($XoaKhoaTam) {
    $keyFile = Join-Path $env:ProgramData "ssh\administrators_authorized_keys"
    if (Test-Path $keyFile) {
        $keep = @(Get-Content $keyFile | Where-Object { $_ -notmatch "b4-coffeehouse" })
        Set-Content -Path $keyFile -Value $keep -Encoding ascii
        Write-Host "   Đã gỡ khoá b4-coffeehouse; còn $($keep.Count) dòng khoá khác trong $keyFile."
    }
} else {
    Write-Host "   (Khoá của Tâm vẫn còn nhưng sshd đã tắt. Muốn gỡ: chạy lại với -XoaKhoaTam.)"
}
Write-Host "XONG."
