# OpenStitching — 科研级显微拼合
$repos = @("OpenStitching/stitching", "mikedh/OpenStitching")
foreach ($r in $repos) {
    $url = "https://github.com/$r"
    try {
        $web = Invoke-WebRequest -Uri $url -TimeoutSec 10 -ErrorAction SilentlyContinue
        if ($web.StatusCode -eq 200) { Write-Host "✅ $r" }
    } catch { Write-Host "❌ $r" }
}
# 查 PyPI openstitching
try {
    $web = Invoke-WebRequest -Uri "https://pypi.org/project/openstitching/" -TimeoutSec 10 -ErrorAction SilentlyContinue
    if ($web.StatusCode -eq 200) { Write-Host "✅ pypi openstitching" }
} catch { Write-Host "❌ pypi openstitching" }
