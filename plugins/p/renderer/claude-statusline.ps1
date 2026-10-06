# Claude Code statusLine renderer for the aligned-v1 profile on Windows.
# Line 1: model | effort | cwd | git branch | context left | profile label.
# Line 2: five-hour, weekly and model-scoped weekly quota left.
# Meets the same output contract as claude-statusline.py; that contract is
# tests/fixtures/statusline-contract.json. The render path reads stdin, the
# local usage cache and git only. The usage refresh runs detached in
# claude-statusline.py --update-cache; this script never reads a credential.

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}

$esc = [char]27
$Reset = "$esc[0m"; $Dim = "$esc[2m"; $Cyan = "$esc[2;36m"; $Yellow = "$esc[2;33m"
$Magenta = "$esc[35m"; $Red = "$esc[31m"; $Amber = "$esc[33m"; $Green = "$esc[32m"
$Block = [string][char]0x2588; $Shade = [string][char]0x2591
$Invariant = [System.Globalization.CultureInfo]::InvariantCulture

function Get-Field($obj, [string]$name) {
    if ($obj -is [System.Management.Automation.PSCustomObject]) {
        $property = $obj.PSObject.Properties[$name]
        if ($property) {
            $value = $property.Value
            if ($value -is [array]) { return ,$value }
            return $value
        }
    }
    return $null
}

function Get-Text($value) { if ($value -is [string]) { return $value.Trim() } return '' }

function Get-Num($value) {
    if ($null -eq $value -or $value -is [bool]) { return $null }
    if ($value -is [int] -or $value -is [long] -or $value -is [double] -or $value -is [decimal] -or $value -is [single]) {
        $number = [double]$value
        if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) { return $null }
        return $number
    }
    return $null
}

function Limit-Percent([double]$value) { return [math]::Max(0.0, [math]::Min(100.0, $value)) }
function Format-Percent([double]$left) { return ([long][math]::Floor($left)).ToString($Invariant) + '% left' }
function Format-Bar([double]$left) {
    $filled = [int][math]::Floor($left / 10 + 0.5)
    return ($Block * $filled) + ($Shade * (10 - $filled))
}
function Get-Tone([double]$left) {
    $used = 100 - $left
    if ($used -ge 80) { return $Red } elseif ($used -ge 60) { return $Amber } else { return $Green }
}
function Format-Tokens([double]$count) {
    if ($count -ge 1000000) {
        $tenths = [long][math]::Floor($count / 100000 + 0.5)
        $whole = [long][math]::Floor($tenths / 10); $part = $tenths % 10
        if ($part -eq 0) { return $whole.ToString($Invariant) + 'M' }
        return $whole.ToString($Invariant) + '.' + $part.ToString($Invariant) + 'M'
    }
    return ([long][math]::Floor($count / 1000 + 0.5)).ToString($Invariant) + 'k'
}
function Get-ShortCwd([string]$cwd, [string]$homeDir, [bool]$windows) {
    if (-not $cwd -or -not $homeDir) { return $cwd }
    $trimmed = $homeDir.TrimEnd('/', '\')
    if ($trimmed) { $homeDir = $trimmed }
    $probe = $cwd; $base = $homeDir
    if ($windows) { $probe = $cwd.ToLowerInvariant(); $base = $homeDir.ToLowerInvariant() }
    if ($probe -ceq $base) { return '~' }
    if ($probe.StartsWith($base, [System.StringComparison]::Ordinal) -and ('/\'.IndexOf($probe[$base.Length]) -ge 0)) {
        return '~' + $cwd.Substring($homeDir.Length)
    }
    return $cwd
}

function Paint([string]$code, [string]$text) { return $code + $text + $Reset }
function New-Segment([string]$key, [string]$plain, [string]$painted) { return @{ key = $key; plain = $plain; painted = $painted } }
function New-Gauge([string]$key, [string]$label, [double]$left, [string]$tokens) {
    $bar = Format-Bar $left; $pct = Format-Percent $left
    $head = ''; $paintedHead = ''; $suffix = ''; $paintedSuffix = ''
    if ($label) { $head = $label + ' '; $paintedHead = (Paint $Dim $label) + ' ' }
    if ($tokens) { $suffix = ' ' + $tokens; $paintedSuffix = ' ' + (Paint $Dim $tokens) }
    return New-Segment $key ($head + $bar + ' ' + $pct + $suffix) ($paintedHead + (Get-Tone $left) + $bar + $Reset + ' ' + $pct + $paintedSuffix)
}

function Join-Line($segments) {
    return (@($segments | ForEach-Object { $_.painted })) -join (' ' + (Paint $Dim '|') + ' ')
}

function Find-Python {
    foreach ($candidate in @(@('python3'), @('python'), @('py', '-3'))) {
        $command = Get-Command $candidate[0] -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) { return ,(@($command.Source) + @($candidate | Select-Object -Skip 1)) }
    }
    return $null
}

function Start-Refresh($python) {
    $helper = Join-Path $PSScriptRoot 'claude-statusline.py'
    if (-not $python -or -not (Test-Path -LiteralPath $helper)) { return }
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $python[0]
    $info.Arguments = ((@($python | Select-Object -Skip 1) + @('"' + $helper + '"', '--update-cache')) -join ' ')
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    [void][System.Diagnostics.Process]::Start($info)
}

function Get-ProfileLabel($python, [string]$inputJson) {
    $helper = Join-Path $PSScriptRoot 'skill-profile-label.py'
    if (-not $python -or -not (Test-Path -LiteralPath $helper)) { return 'p:?' }
    $arguments = @($python | Select-Object -Skip 1) + @($helper)
    $result = ($inputJson | & $python[0] @arguments 2>$null | Out-String).Trim()
    if ($result -in @('p:h', 'p:w', 'p:?')) { return $result }
    return 'p:?'
}

function Get-CacheDir {
    if ($env:LOCALAPPDATA) { return Join-Path $env:LOCALAPPDATA 'claude-statusline' }
    if ($env:XDG_CACHE_HOME) { return Join-Path $env:XDG_CACHE_HOME 'claude-statusline' }
    return Join-Path ([System.IO.Path]::GetTempPath()) 'claude-statusline'
}

function Get-ScopedState($python) {
    $dir = Get-CacheDir
    $nowMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    $cache = $null
    try { $cache = Get-Content -LiteralPath (Join-Path $dir 'usage-cache.json') -Raw -Encoding UTF8 | ConvertFrom-Json } catch {}
    $at = Get-Num (Get-Field $cache 'at')
    $label = Get-Text (Get-Field $cache 'label')
    $percent = Get-Num (Get-Field $cache 'percent')
    $state = @('unavailable', $label)
    if ($null -ne $at -and ($nowMs - $at) -le 900000) {
        if ($label -and $null -ne $percent) { $state = @('gauge', $label, $percent) }
        elseif (-not $label) { $state = $null }
    }
    if ($null -eq $at -or ($nowMs - $at) -gt 60000) {
        $attemptPath = Join-Path $dir 'usage-attempt.txt'
        $last = 0
        try { $last = [long]((Get-Content -LiteralPath $attemptPath -Raw).Trim()) } catch {}
        if (($nowMs - $last) -gt 30000) {
            try {
                if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
                [System.IO.File]::WriteAllText($attemptPath, [string]$nowMs)
                Start-Refresh $python
            } catch {}
        }
    }
    return ,$state
}

function Get-GitBranch([string]$cwd) {
    try {
        if (-not $cwd -or -not (Test-Path -LiteralPath $cwd -PathType Container)) { return '' }
        $branch = & git -C $cwd --no-optional-locks rev-parse --abbrev-ref HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and $branch) { return ([string]$branch).Trim() }
    } catch {}
    return ''
}

function Get-Lines([string]$inputJson) {
    $data = $null
    if ($inputJson.Trim()) { try { $data = $inputJson | ConvertFrom-Json } catch { $data = $null } }
    $python = Find-Python
    $workspace = Get-Field $data 'workspace'
    $context = Get-Field $data 'context_window'
    $windows = ($PSVersionTable.PSEdition -eq 'Desktop') -or [bool]$IsWindows
    $homeDir = $env:HOME
    if (-not $homeDir) { $homeDir = $env:USERPROFILE }

    $line1 = New-Object System.Collections.ArrayList
    $model = Get-Text (Get-Field (Get-Field $data 'model') 'display_name')
    if ($model) { [void]$line1.Add((New-Segment 'model' $model (Paint $Cyan $model))) }
    $effort = Get-Text (Get-Field (Get-Field $data 'effort') 'level')
    if ($effort) { [void]$line1.Add((New-Segment 'effort' ('eff ' + $effort) ((Paint $Dim 'eff') + ' ' + (Paint $Magenta $effort)))) }
    $rawCwd = Get-Text (Get-Field $workspace 'current_dir')
    if (-not $rawCwd) { $rawCwd = Get-Text (Get-Field $data 'cwd') }
    $cwd = Get-ShortCwd $rawCwd $homeDir $windows
    if ($cwd) { [void]$line1.Add((New-Segment 'cwd' $cwd (Paint $Dim $cwd))) }
    $branch = Get-Text (Get-Field $workspace 'git_branch')
    if (-not $branch) { $branch = Get-GitBranch $rawCwd }
    if ($branch) { [void]$line1.Add((New-Segment 'branch' $branch (Paint $Yellow $branch))) }
    $remaining = Get-Num (Get-Field $context 'remaining_percentage')
    if ($null -ne $remaining) {
        $size = Get-Num (Get-Field $context 'context_window_size')
        $used = Get-Num (Get-Field $context 'total_input_tokens')
        $tokens = ''
        if ($null -ne $size -and $size -gt 0 -and $null -ne $used -and $used -ge 0) {
            $tokens = (Format-Tokens $used) + '/' + (Format-Tokens $size)
        }
        [void]$line1.Add((New-Gauge 'context' '' (Limit-Percent $remaining) $tokens))
    }
    $label = Get-ProfileLabel $python $inputJson
    [void]$line1.Add((New-Segment 'profile' $label (Paint $Dim $label)))

    $line2 = New-Object System.Collections.ArrayList
    $limits = Get-Field $data 'rate_limits'
    foreach ($window in @(@('5h', 'five_hour'), @('wk', 'seven_day'))) {
        $usedPercent = Get-Num (Get-Field (Get-Field $limits $window[1]) 'used_percentage')
        if ($null -ne $usedPercent) { [void]$line2.Add((New-Gauge $window[0] $window[0] (100 - (Limit-Percent $usedPercent)) '')) }
    }
    $hasLimits = ($limits -is [System.Management.Automation.PSCustomObject]) -and @($limits.PSObject.Properties).Count -gt 0
    if ($hasLimits) {
        $scoped = Get-ScopedState $python
        if ($line2.Count -gt 0 -and $null -ne $scoped) {
            if ($scoped[0] -eq 'gauge') {
                [void]$line2.Add((New-Gauge 'scoped' $scoped[1] (100 - (Limit-Percent $scoped[2])) ''))
            } else {
                $text = $scoped[1]
                if (-not $text) { $text = 'scoped' }
                $text = $text + ' --'
                [void]$line2.Add((New-Segment 'scoped' $text (Paint $Dim $text)))
            }
        }
    }

    $lines = @(Join-Line $line1)
    if ($line2.Count -gt 0) { $lines += Join-Line $line2 }
    return $lines
}

$inputJson = ''
try { $inputJson = [Console]::In.ReadToEnd() } catch {}
try { $output = Get-Lines $inputJson } catch { $output = @('p:?') }
foreach ($line in $output) { [Console]::Out.WriteLine($line) }
exit 0
