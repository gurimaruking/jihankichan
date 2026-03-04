# robosta-discord へのセットアップスクリプト
$password = "robosta0"
$user = "karu"
$host_name = "robosta-discord"

# SSH公開鍵を転送
$pubkey = Get-Content "$env:USERPROFILE\.ssh\id_rsa.pub"

# plink/plinkでパスワード認証
Write-Host "SSH鍵を転送中..."
echo y | plink -ssh -pw $password $user@$host_name "mkdir -p ~/.ssh && echo '$pubkey' >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"

Write-Host "完了"
