# PowerShell script to create Task Scheduler task for Music Library Sync Tray
# Run this in PowerShell as Administrator

$taskName = "Music Library Sync Tray"
$taskPath = "\"

Write-Host "Creating Task Scheduler task: $taskName" -ForegroundColor Cyan
Write-Host ""

# Check if task already exists
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "Task already exists! Removing old task..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

# Task action - run the start script
$action = New-ScheduledTaskAction -Execute "C:\Users\docha\iCloudDrive\scripts\start_tray_windows.bat"

# Task trigger - at logon
$trigger = New-ScheduledTaskTrigger -AtLogOn

# Task principal - run as current user, highest privileges
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

# Task settings
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable:$false `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

# Task description
$description = "Music Library Sync System Tray Launcher - Starts automatically on login"

# Register the task
try {
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description $description `
        -Force
    
    Write-Host "Task created successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Task Details:" -ForegroundColor Cyan
    Write-Host "  Name: $taskName"
    Write-Host "  Action: $($action.Execute) $($action.Arguments)"
    Write-Host "  Trigger: At Log On"
    Write-Host "  User: $env:USERNAME"
    Write-Host ""
    Write-Host "The tray launcher will start automatically when you log in." -ForegroundColor Green
    Write-Host ""
    Write-Host "To test immediately, run:" -ForegroundColor Yellow
    Write-Host "  Start-ScheduledTask -TaskName '$taskName'"
    Write-Host ""
    Write-Host "Or open Task Scheduler and run it manually."
    
} catch {
    Write-Host "Error creating task: $_" -ForegroundColor Red
    Write-Host ""
    Write-Host "Make sure you're running PowerShell as Administrator!" -ForegroundColor Yellow
    Write-Host "Right-click PowerShell -> Run as Administrator"
}

