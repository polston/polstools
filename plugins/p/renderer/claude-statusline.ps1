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
$Block = [string][char]0x2588; $Shade = [string][char]0x2591; $Ellipsis = [string][char]0x2026
$Invariant = [System.Globalization.CultureInfo]::InvariantCulture
$IsWin = ($PSVersionTable.PSEdition -eq 'Desktop') -or [bool]$IsWindows

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

# C0 controls, DEL and C1 controls never reach the terminal from outside input.
function Remove-Controls([string]$text) { return [regex]::Replace($text, '[\u0000-\u001F\u007F-\u009F]', '') }
function Get-Text($value) { if ($value -is [string]) { return (Remove-Controls $value).Trim() } return '' }

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
function Get-Width([string]$text) { return $text.Length - ([regex]::Matches($text, '[\uDC00-\uDFFF]')).Count }
function Limit-Text([string]$text, [int]$points) {
    $builder = New-Object System.Text.StringBuilder; $count = 0
    for ($i = 0; $i -lt $text.Length -and $count -lt $points; $i++) {
        [void]$builder.Append($text[$i])
        if ([char]::IsHighSurrogate($text[$i]) -and $i + 1 -lt $text.Length) { $i++; [void]$builder.Append($text[$i]) }
        $count++
    }
    return $builder.ToString()
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
    return @{
        key = $key; label = $label; left = $left; tokens = $tokens
        plain = $head + $bar + ' ' + $pct + $suffix
        painted = $paintedHead + (Get-Tone $left) + $bar + $Reset + ' ' + $pct + $paintedSuffix
        compactPlain = $head + $pct + $suffix
        compactPainted = $paintedHead + $pct + $paintedSuffix
    }
}

function Get-LineWidth($segments) { return Get-Width ((@($segments | ForEach-Object { $_.plain })) -join ' | ') }

function Invoke-Step($segments, [string]$kind, [string]$key) {
    $updated = New-Object System.Collections.ArrayList
    foreach ($segment in $segments) {
        if ($kind -eq 'drop' -and $segment.key -eq $key) { continue }
        if ($kind -eq 'tokens' -and $segment.tokens) {
            $segment = New-Gauge $segment.key $segment.label $segment.left ''
        } elseif ($kind -eq 'bars' -and $segment.ContainsKey('compactPlain')) {
            $segment = @{ key = $segment.key; label = $segment.label; left = $segment.left; tokens = $segment.tokens
                plain = $segment.compactPlain; painted = $segment.compactPainted
                compactPlain = $segment.compactPlain; compactPainted = $segment.compactPainted }
        } elseif ($kind -eq 'basename' -and $segment.key -eq 'cwd') {
            $name = ($segment.plain.TrimEnd('/', '\') -replace '\\', '/').Split('/')[-1]
            if (-not $name) { $name = $segment.plain }
            $segment = New-Segment 'cwd' $name (Paint $Dim $name)
        }
        [void]$updated.Add($segment)
    }
    return ,$updated
}

$Line1Steps = @(@('tokens', ''), @('drop', 'effort'), @('bars', ''), @('basename', ''), @('drop', 'branch'), @('drop', 'cwd'), @('drop', 'model'))
$Line2Steps = @(@('bars', ''), @('drop', 'scoped'), @('drop', 'wk'))

function Format-Line($segments, $steps, $columns) {
    if ($columns) {
        foreach ($step in $steps) {
            if ((Get-LineWidth $segments) -le $columns) { break }
            $segments = Invoke-Step $segments $step[0] $step[1]
        }
        $plain = (@($segments | ForEach-Object { $_.plain })) -join ' | '
        if ((Get-Width $plain) -gt $columns) {
            if ($columns -gt 1) { return (Limit-Text $plain ($columns - 1)) + $Ellipsis }
            return Limit-Text $plain $columns
        }
    }
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

function Get-UserId {
    try {
        $uid = (& id -u 2>$null | Out-String).Trim()
        if ($uid -match '^[0-9]+$') { return $uid }
    } catch {}
    return $null
}

# Same location rule as claude-statusline.py. On POSIX the shared temp
# directory gets a per-user name; on Windows the temp location is per user.
function Get-CacheDir {
    if ($env:LOCALAPPDATA) { return Join-Path $env:LOCALAPPDATA 'claude-statusline' }
    if ($env:XDG_CACHE_HOME) { return Join-Path $env:XDG_CACHE_HOME 'claude-statusline' }
    $name = 'claude-statusline'
    if (-not $IsWin) {
        $uid = Get-UserId
        if (-not $uid) { return $null }
        $name = $name + '-' + $uid
    }
    return Join-Path ([System.IO.Path]::GetTempPath()) $name
}

function Get-LinkItem([string]$path) {
    return Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
}

function Test-Reparse($item) {
    return [bool]($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
}

# POSIX only: a directory owned by this user with no group or world access.
# ls reports a symbolic link with a leading l, so a link never passes.
function Test-PrivateDirectory([string]$dir) {
    $uid = Get-UserId
    if (-not $uid) { return $false }
    try {
        $env:LC_ALL = 'C'
        $fields = (& ls -ldn -- $dir 2>$null | Out-String).Trim() -split '\s+'
    } catch { return $false }
    if ($fields.Count -lt 3 -or $fields[0].Length -lt 10) { return $false }
    if ($fields[0][0] -ne 'd' -or $fields[0].Substring(4, 6) -ne '------') { return $false }
    return ($fields[2] -ceq $uid)
}

# The cache directory, or $null when it cannot be trusted. It must be a real
# directory, not a link. On POSIX it must also be owned by this user and
# closed to group and world; one that fails is left untouched.
function Get-TrustedCacheDir([bool]$create) {
    $dir = Get-CacheDir
    if (-not $dir) { return $null }
    try {
        if ($create -and -not (Get-LinkItem $dir)) {
            if ($IsWin) {
                New-Item -ItemType Directory -Path $dir -Force | Out-Null
            } else {
                [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($dir)) | Out-Null
                & mkdir -m 700 -- $dir 2>$null
            }
        }
        $item = Get-LinkItem $dir
        if (-not $item -or -not $item.PSIsContainer -or (Test-Reparse $item)) { return $null }
        if (-not $IsWin -and -not (Test-PrivateDirectory $dir)) { return $null }
        return $dir
    } catch { return $null }
}

# True when path is absent or a plain file; a link or directory is refused.
function Test-PlainFileOrAbsent([string]$path) {
    $item = Get-LinkItem $path
    if (-not $item) { return $true }
    return (-not $item.PSIsContainer) -and (-not (Test-Reparse $item))
}

function Get-ScopedState($python) {
    $dir = Get-TrustedCacheDir $true
    if (-not $dir) { return $null }
    $nowMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    $pinned = 0L
    if ([long]::TryParse([string]$env:P_STATUSLINE_NOW_MS, [ref]$pinned)) { $nowMs = $pinned }
    $cache = $null
    $cachePath = Join-Path $dir 'usage-cache.json'
    if (Test-PlainFileOrAbsent $cachePath) {
        try { $cache = Get-Content -LiteralPath $cachePath -Raw -Encoding UTF8 | ConvertFrom-Json } catch {}
    }
    $at = Get-Num (Get-Field $cache 'at')
    $label = Get-Text (Get-Field $cache 'label')
    $percent = Get-Num (Get-Field $cache 'percent')
    $state = @('unavailable', $label)
    if ($null -ne $at -and ($nowMs - $at) -ge 0 -and ($nowMs - $at) -le 900000) {
        if ($label -and $null -ne $percent) { $state = @('gauge', $label, $percent) }
        elseif (-not $label) { $state = $null }
    }
    if (-not $env:P_STATUSLINE_NO_REFRESH -and ($null -eq $at -or ($nowMs - $at) -lt 0 -or ($nowMs - $at) -gt 60000)) {
        $attemptPath = Join-Path $dir 'usage-attempt.txt'
        if (Test-PlainFileOrAbsent $attemptPath) {
            $last = 0
            try { $last = [long]((Get-Content -LiteralPath $attemptPath -Raw).Trim()) } catch {}
            if (($nowMs - $last) -gt 30000 -or ($nowMs - $last) -lt 0) {
                try {
                    [System.IO.File]::WriteAllText($attemptPath, [string]$nowMs)
                    Start-Refresh $python
                } catch {}
            }
        }
    }
    return ,$state
}

function Get-GitBranch([string]$cwd) {
    try {
        if (-not $cwd -or -not (Test-Path -LiteralPath $cwd -PathType Container)) { return '' }
        $branch = & git -C $cwd --no-optional-locks rev-parse --abbrev-ref HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and $branch) { return (Remove-Controls ([string]$branch)).Trim() }
    } catch {}
    return ''
}

function Get-Lines([string]$inputJson) {
    $data = $null
    if ($inputJson.Trim()) { try { $data = $inputJson | ConvertFrom-Json } catch { $data = $null } }
    $python = Find-Python
    $workspace = Get-Field $data 'workspace'
    $context = Get-Field $data 'context_window'
    $windows = $IsWin
    $homeDir = Remove-Controls ([string]$env:HOME)
    if (-not $homeDir) { $homeDir = Remove-Controls ([string]$env:USERPROFILE) }

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

    $columns = 0
    if (-not [int]::TryParse([string]$env:COLUMNS, [ref]$columns) -or $columns -lt 1) { $columns = 0 }
    $lines = @(Format-Line $line1 $Line1Steps $columns)
    if ($line2.Count -gt 0) { $lines += Format-Line $line2 $Line2Steps $columns }
    return $lines
}

$inputJson = ''
try { $inputJson = [Console]::In.ReadToEnd() } catch {}
try { $output = Get-Lines $inputJson } catch { $output = @('p:?') }
foreach ($line in $output) { [Console]::Out.WriteLine($line) }
exit 0
