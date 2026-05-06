# Non-disturbing health check for the wet_run_counted process.
$proc = Get-Process -Id 25080 -ErrorAction SilentlyContinue
if ($null -eq $proc) {
    Write-Host "Process 25080 not running anymore (may have completed)"
    exit
}
$elapsed = (Get-Date) - $proc.StartTime
$cpuPerHour = $proc.CPU / $elapsed.TotalHours
Write-Host "running:    $([Math]::Round($elapsed.TotalMinutes, 1)) min elapsed"
Write-Host "cpu time:   $([Math]::Round($proc.CPU, 1))s ($([Math]::Round($cpuPerHour, 1))s/hr -- network-bound is ~10s/hr)"
Write-Host "memory:     $([Math]::Round($proc.WorkingSet / 1MB, 1)) MB"
Write-Host "handles:    $($proc.Handles)"
