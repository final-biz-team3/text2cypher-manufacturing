param([string]$LokiUrl = "http://127.0.0.1:3100")

$ErrorActionPreference = "Stop"
$anchor = [DateTimeOffset]::UtcNow
$total = 0

# 현재부터 72시간 전까지 3시간 간격으로 실제 코드 기반 39개 이벤트 세트를 배치한다.
for ($slot = 0; $slot -lt 24; $slot++) {
    $baseTime = $anchor.AddHours(-($slot * 3))
    $prefix = "three-day-$($baseTime.ToString('MMdd-HHmm'))"
    & "$PSScriptRoot/seed_demo_logs.ps1" -LokiUrl $LokiUrl -BaseTime $baseTime -RequestPrefix $prefix | Out-Null
    $total += 39
}

Write-Output "Seeded $total code-aligned demo log events across the last 72 hours."
