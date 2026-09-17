# End-to-end check of the PR-05 session cycle through the real CLI.
#
# Not a test suite: this exercises the commands a learner actually types, so a
# wiring mistake in cli.py shows up here rather than in unit tests that call the
# core directly.
#
# Filters use ASCII markers (`::`) rather than Russian words. Windows
# PowerShell 5.1 reads a BOM-less script as ANSI, which mangles Cyrillic
# literals inside the script itself -- a filter for "состояние" silently matches
# nothing and the check looks like it passed. The CLI's own output stays
# Russian; only the pattern that selects lines is ASCII.
$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
$repo = "C:\Users\nonhumanbox\Documents\deepseek_harness\botai\repo"
$tmp = Join-Path $env:TEMP ("botai-flow-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$script:failures = 0

Push-Location $repo
New-Item -ItemType Directory -Path (Join-Path $tmp "courses") -Force | Out-Null
Copy-Item -Recurse examples\minimal-course (Join-Path $tmp "courses\minimal-diff")

function Invoke-Cli {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    python scripts/cli.py @Args --root $tmp
}

function Step {
    param([string]$Title, [scriptblock]$Body)
    Write-Output ""
    Write-Output "### $Title"
    $out = & $Body
    # Print the "  key : value" lines the CLI emits, which is the evidence.
    $out | Select-String -Pattern ":\s" -SimpleMatch:$false | ForEach-Object { $_.Line }
}

function Assert {
    param([string]$Name, [bool]$Condition, [string]$Detail = "")
    if ($Condition) { Write-Output "  [ok]   $Name" }
    else { Write-Output "  [FAIL] $Name  $Detail"; $script:failures++ }
}

Write-Output "workspace: $tmp"

Step "1. accept the course" { Invoke-Cli course-accept --course minimal-diff }
Step "2. consent --dry-run" { Invoke-Cli consent-set --course minimal-diff --purpose learning_storage --dry-run }

Write-Output ""
Write-Output "### 3. session-start BEFORE consent"
$before = Invoke-Cli session-start --course minimal-diff
$before | Select-String ":\s" | ForEach-Object { $_.Line }
Assert "consent gate holds" (($before -join "`n") -match "CONSENT_PENDING")

Step "4. record consent" {
    Invoke-Cli consent-set --course minimal-diff --purpose learning_storage `
        --purpose model_processing --provider deepseek
}

Write-Output ""
Write-Output "### 5. session-start AFTER consent"
$out = Invoke-Cli session-start --course minimal-diff
$out | Select-String ":\s" | ForEach-Object { $_.Line }
$sid = ($out | Select-String "session_id\s+:\s+([0-9a-f-]{36})").Matches.Groups[1].Value
Assert "session id parsed" ($sid.Length -eq 36) "got '$sid'"

Step "6. session-next" { Invoke-Cli session-next --course minimal-diff --session $sid }

Step "7a. choose goal" {
    Invoke-Cli session-goal --course minimal-diff --session $sid `
        --objective explain-diff --assignment practice-diff
}
Step "7b. confirm goal" {
    Invoke-Cli session-goal --course minimal-diff --session $sid `
        --objective explain-diff --assignment practice-diff --confirm
}

Write-Output ""
Write-Output "### 8. record an attempt"
$attemptFile = Join-Path $tmp "attempt.txt"
Set-Content -Path $attemptFile -Value "git diff compares the working tree with the index" -Encoding UTF8
$out2 = Invoke-Cli session-attempt --course minimal-diff --session $sid --attempt-file $attemptFile
$out2 | Select-String ":\s" | ForEach-Object { $_.Line }
$aid = ($out2 | Select-String "attempt_id\s+:\s+([0-9a-f-]{36})").Matches.Groups[1].Value
Assert "attempt id parsed" ($aid.Length -eq 36) "got '$aid'"

Write-Output ""
Write-Output "### 9. check claimed pass with NO evidence (must be refused)"
$bad = Invoke-Cli session-check --course minimal-diff --session $sid --attempt $aid `
    --check-kind explain --verdict pass --criterion k1=pass
$bad | Select-String "::\s|отказ" | ForEach-Object { $_.Line }
Assert "pass without evidence refused" (($bad -join "`n") -match "без доказательства|отказ")

Step "10. check with evidence -> practising" {
    Invoke-Cli session-check --course minimal-diff --session $sid --attempt $aid `
        --check-kind explain --verdict pass --criterion "k1=pass:$aid"
}

Write-Output ""
Write-Output "### 11. second, different check -> demonstrated"
$attemptFile2 = Join-Path $tmp "attempt2.txt"
Set-Content -Path $attemptFile2 -Value "after git add the change is in the index" -Encoding UTF8
$out3 = Invoke-Cli session-attempt --course minimal-diff --session $sid --attempt-file $attemptFile2
$a2 = ($out3 | Select-String "attempt_id\s+:\s+([0-9a-f-]{36})").Matches.Groups[1].Value
$final = Invoke-Cli session-check --course minimal-diff --session $sid --attempt $a2 `
    --check-kind transfer --verdict pass --criterion "k2=pass:$a2"
$final | Select-String ":\s" | ForEach-Object { $_.Line }
Assert "mastery reached" (($final -join "`n") -match "demonstrated")
Assert "provisional framing shown" (($final -join "`n") -match "provisional|forming|\u0444\u043e\u0440\u043c")

Step "12a. pause" { Invoke-Cli session-pause --course minimal-diff --session $sid --target PAUSED }

Write-Output ""
Write-Output "### 12b. illegal --target (must be refused by argparse)"
$badResume = Invoke-Cli session-pause --course minimal-diff --session $sid --target DIAGNOSIS 2>&1
$badResume | Select-String "invalid choice|usage|отказ" | ForEach-Object { $_.Line } | Select-Object -First 1
Assert "illegal --target refused" (($badResume -join "`n") -match "invalid choice|usage|отказ")

Write-Output ""
Write-Output "### 12c. resume returns to the state it stopped in"
$resumed = Invoke-Cli session-pause --course minimal-diff --session $sid --resume UNDERSTANDING_CHECK
$resumed | Select-String "::\s|отказ" | ForEach-Object { $_.Line }
Assert "resumed into the saved state" (($resumed -join "`n") -match "UNDERSTANDING_CHECK")

Write-Output ""
Write-Output "### 12d. resume naming a DIFFERENT state (must be refused)"
$wrongResume = Invoke-Cli session-pause --course minimal-diff --session $sid --resume DIAGNOSIS 2>&1
$wrongResume | Select-String "отказ|DIAGNOSIS|resume" | ForEach-Object { $_.Line } | Select-Object -First 2
Assert "mismatched resume refused" (($wrongResume -join "`n") -match "RESUME_STATE_MISMATCH|usage|отказ")

Step "13. progress projection" { Invoke-Cli progress --course minimal-diff }

Write-Output ""
Write-Output "### 14. progress --json"
$json = (Invoke-Cli progress --course minimal-diff --json) | Out-String
try {
    $doc = $json | ConvertFrom-Json
    Assert "json parses" $true
    Assert "course id present" ($doc.course_id -eq "minimal-diff") "got '$($doc.course_id)'"
    Assert "one objective demonstrated" ($doc.counts.demonstrated -eq 1) "got $($doc.counts.demonstrated)"
} catch {
    Assert "json parses" $false $_
}

Write-Output ""
Write-Output "### 15. restart persistence (fresh process reads the same state)"
$again = Invoke-Cli session-next --course minimal-diff --session $sid
$again | Select-String ":\s" | ForEach-Object { $_.Line }
Assert "state survives a new process" (($again -join "`n") -match "UNDERSTANDING_CHECK|REFLECTION|PAUSED")

Pop-Location
Write-Output ""
if ($script:failures -eq 0) { Write-Output "FLOW OK" } else { Write-Output "FLOW FAILURES: $script:failures" }
Write-Output "workspace left at: $tmp"
