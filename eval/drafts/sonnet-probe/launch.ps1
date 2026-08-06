# eval/drafts/sonnet-probe/launch.ps1
# Three tabs, three slots each - matching how the Opus baseline for these nine
# was produced. Nine at once is legal per the skill but changes the contention
# profile (one MCP stdio process, one GPU) against the runs we compare to.
$bash = "C:\Program Files\Git\bin\bash.exe"
$repo = "C:\horizon-scout"
$wtArgs = @()
foreach ($g in @("sql", "vec", "hyb")) {
    if ($wtArgs.Count -gt 0) { $wtArgs += ';' }
    $wtArgs += @(
        'new-tab', '-d', $repo, '--title', "sonnet-$g",
        $bash, '-i', '-l', "$repo\eval\drafts\sonnet-probe\$g\run.sh"
    )
}
wt @wtArgs
Write-Host "launched 3 tabs. verify by process, not exit code:"
Write-Host "  Get-CimInstance Win32_Process | Where-Object CommandLine -match 'question-orchestrator' | Select-Object ProcessId,CommandLine"
