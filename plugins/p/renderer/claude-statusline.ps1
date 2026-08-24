# Claude Code statusLine renderer for the aligned-v1 profile.
# Line 1: model + effort | cwd | git branch | context remaining.
# Line 2: five-hour + weekly + model-scoped weekly quota remaining.

$ErrorActionPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}

$inputJson = [Console]::In.ReadToEnd()
$data = $inputJson | ConvertFrom-Json
$model = $data.model.display_name
$cwd = $data.workspace.current_dir
if ([string]::IsNullOrEmpty($cwd)) { $cwd = $data.cwd }

$homeDir = $HOME
if ($cwd -and $homeDir -and $cwd.StartsWith($homeDir, [System.StringComparison]::OrdinalIgnoreCase)) {
    $cwd = '~' + $cwd.Substring($homeDir.Length)
}

$branch = $data.workspace.git_branch
if (-not $branch -and $cwd -and (Test-Path -LiteralPath $cwd)) {
    Push-Location -LiteralPath $cwd
    $b = git --no-optional-locks rev-parse --abbrev-ref HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $b) { $branch = $b.Trim() }
    Pop-Location
}

$esc = [char]27
$dim = "$esc[2m"
$reset = "$esc[0m"
$cyan = "$esc[2;36m"
$yellow = "$esc[2;33m"
$magenta = "$esc[35m"
$sep = "$dim|$reset"

function New-Bar([double]$left) {
    $slots = 10
    $bounded = [math]::Max(0, [math]::Min(100, $left))
    $filled = [int][math]::Round($bounded / 100 * $slots)
    $bar = [string]::new([char]0x2588, $filled) + [string]::new([char]0x2591, $slots - $filled)
    $used = 100 - $bounded
    $color = if ($used -ge 80) { "$esc[31m" } elseif ($used -ge 60) { "$esc[33m" } else { "$esc[32m" }
    return "$color$bar$reset"
}

$parts = New-Object System.Collections.Generic.List[string]
if ($model) { $parts.Add("$cyan$model$reset") }
$effort = $data.effort.level
if ($effort) { $parts.Add("$dim" + 'eff' + "$reset $magenta$effort$reset") }
if ($cwd) { $parts.Add("$dim$cwd$reset") }
if ($branch) { $parts.Add("$yellow$branch$reset") }

$remaining = $data.context_window.remaining_percentage
if ($null -ne $remaining) {
    $left = [math]::Max(0, [math]::Min(100, [double]$remaining))
    $tokens = ''
    $size = $data.context_window.context_window_size
    $used = $data.context_window.total_input_tokens
    if ($size -and $null -ne $used) {
        $fmt = { param($n) if ($n -ge 1000000) { '{0:0.#}M' -f ($n / 1000000) } else { "$([int][math]::Round($n/1000))k" } }
        $tokens = " $dim$(& $fmt $used)/$(& $fmt $size)$reset"
    }
    $parts.Add("$(New-Bar $left) " + ("{0:N0}% left" -f $left) + $tokens)
}
$profileLabel = 'p:?'
$profileHelper = Join-Path $PSScriptRoot 'skill-profile-label.py'
if (Test-Path -LiteralPath $profileHelper) {
    try {
        $profileResult = $null
        if (Get-Command python3 -ErrorAction SilentlyContinue) {
            $profileResult = $inputJson | & python3 $profileHelper 2>$null
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            $profileResult = $inputJson | & python $profileHelper 2>$null
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            $profileResult = $inputJson | & py -3 $profileHelper 2>$null
        }
        $candidate = ($profileResult | Out-String).Trim()
        if ($candidate -in @('p:h', 'p:w', 'p:?')) { $profileLabel = $candidate }
    } catch {}
}
$parts.Add("$dim$profileLabel$reset")
if ($parts.Count -gt 0) { Write-Host ($parts -join " $sep ") }

$quotaParts = New-Object System.Collections.Generic.List[string]
foreach ($window in @(
    @{ label = '5h'; value = $data.rate_limits.five_hour.used_percentage },
    @{ label = 'wk'; value = $data.rate_limits.seven_day.used_percentage }
)) {
    if ($null -ne $window.value) {
        $used = [math]::Max(0, [math]::Min(100, [double]$window.value))
        $left = 100 - $used
        $quotaParts.Add("$dim$($window.label)$reset $(New-Bar $left) " + ("{0:N0}% left" -f $left))
    }
}

# The model-scoped weekly window is absent from statusline stdin. Read the
# local credential only for the request header, never print or persist it, and
# cache only the returned label and percentage. Fresh success caches for 60s;
# attempts throttle to 30s; a last good value may display for up to 15 minutes.
$cacheRoot = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { [System.IO.Path]::GetTempPath() }
$cacheDir = Join-Path $cacheRoot 'claude-statusline'
$cachePath = Join-Path $cacheDir 'usage-cache.json'
$attemptPath = Join-Path $cacheDir 'usage-attempt.txt'
$nowMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$cache = $null
if (Test-Path -LiteralPath $cachePath) { $cache = Get-Content -LiteralPath $cachePath -Raw | ConvertFrom-Json }
$lastAttempt = 0
try { if (Test-Path -LiteralPath $attemptPath) { $lastAttempt = [long](Get-Content -LiteralPath $attemptPath -Raw) } } catch {}

function Get-ClaudeCredential {
    if ($IsMacOS -and (Get-Command security -ErrorAction SilentlyContinue)) {
        try {
            $raw = & security find-generic-password -s 'Claude Code-credentials' -w 2>$null
            if ($LASTEXITCODE -eq 0 -and $raw) { return (($raw | Out-String) | ConvertFrom-Json) }
        } catch {}
    }
    $profileRoot = if ($env:USERPROFILE) { $env:USERPROFILE } elseif ($HOME) { $HOME } else { $null }
    if (-not $profileRoot) { return $null }
    try {
        $credentialPath = Join-Path $profileRoot '.claude/.credentials.json'
        return (Get-Content -LiteralPath $credentialPath -Raw -ErrorAction Stop | ConvertFrom-Json)
    } catch { return $null }
}

if ((-not $cache -or ($nowMs - $cache.at) -gt 60000) -and ($nowMs - $lastAttempt) -gt 30000) {
    try {
        if (-not (Test-Path -LiteralPath $cacheDir)) { New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null }
        "$nowMs" | Out-File -FilePath $attemptPath -Encoding ascii
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $credential = Get-ClaudeCredential
        if ($credential.claudeAiOauth.accessToken -and $credential.claudeAiOauth.expiresAt -gt $nowMs) {
            $headers = @{ 'Authorization' = "Bearer $($credential.claudeAiOauth.accessToken)"; 'anthropic-beta' = 'oauth-2025-04-20' }
            $usage = Invoke-RestMethod -Uri 'https://api.anthropic.com/api/oauth/usage' -Headers $headers -Method Get -TimeoutSec 3 -ErrorAction Stop
            $scoped = @($usage.limits | Where-Object { $_.kind -eq 'weekly_scoped' -and $null -ne $_.percent })
            $fresh = @{ at = $nowMs; label = ''; percent = $null }
            if ($scoped.Count -gt 0) {
                $fresh.label = "$($scoped[0].scope.model.display_name)".ToLower()
                $fresh.percent = [double]$scoped[0].percent
            }
            $temporary = "$cachePath.tmp"
            ($fresh | ConvertTo-Json -Compress) | Out-File -FilePath $temporary -Encoding utf8 -ErrorAction Stop
            Move-Item -LiteralPath $temporary -Destination $cachePath -Force -ErrorAction Stop
            $cache = Get-Content -LiteralPath $cachePath -Raw | ConvertFrom-Json
        }
    } catch {}
}
if ($cache -and $cache.label -and $null -ne $cache.percent -and ($nowMs - $cache.at) -le 900000) {
    $used = [math]::Max(0, [math]::Min(100, [double]$cache.percent))
    $left = 100 - $used
    $quotaParts.Add("$dim$($cache.label)$reset $(New-Bar $left) " + ("{0:N0}% left" -f $left))
}

if ($quotaParts.Count -gt 0) { Write-Host ($quotaParts -join " $sep ") }
