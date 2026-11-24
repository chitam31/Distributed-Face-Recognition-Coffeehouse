# Bản PowerShell của create_branch_users.sh (dành cho Windows). Chạy từ thư mục gốc dự án:
#   .\scripts\create_branch_users.ps1            (mặc định A B C)
#   .\scripts\create_branch_users.ps1 -Branches A,B,C,D
param([string[]]$Branches = @('A', 'B', 'C'))
Set-Location (Join-Path $PSScriptRoot '..')
foreach ($b in $Branches) {
    $user = 'branch_' + $b.ToLower()
    $pass = $user + '_pass'                  # ĐỔI mật khẩu này khi triển khai thật
    docker compose exec -T rabbitmq rabbitmqctl delete_user $user 2>&1 | Out-Null
    docker compose exec -T rabbitmq rabbitmqctl add_user $user $pass
    # configure, write, read (regex trên tên tài nguyên); dùng nháy đơn để PowerShell không diễn giải ký tự
    docker compose exec -T rabbitmq rabbitmqctl set_permissions -p / $user '^reply\.' '^coffee\.requests$' '^reply\.'
    Write-Host "chi nhanh $b -> user=$user pass=$pass"
}
Write-Host ''
Write-Host 'Kiem tra:  docker compose exec rabbitmq rabbitmqctl list_permissions -p /'
