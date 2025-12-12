# PowerShell script to find or list Task Scheduler tasks related to music library sync
# Run this in PowerShell to see if the task exists

Write-Host "Searching for Music Library Sync tray tasks in Task Scheduler..." -ForegroundColor Cyan
Write-Host ""

# Search for tasks with "music" or "tray" or "library" in the name
$tasks = Get-ScheduledTask | Where-Object {
    $_.TaskName -like "*music*" -or 
    $_.TaskName -like "*tray*" -or 
    $_.TaskName -like "*library*" -or
    $_.TaskName -like "*sync*"
}

if ($tasks) {
    Write-Host "Found matching tasks:" -ForegroundColor Green
    Write-Host ""
    foreach ($task in $tasks) {
        Write-Host "Task Name: $($task.TaskName)" -ForegroundColor Yellow
        Write-Host "  State: $($task.State)"
        Write-Host "  Path: $($task.TaskPath)"
        
        $taskInfo = Get-ScheduledTaskInfo -TaskName $task.TaskName -TaskPath $task.TaskPath
        Write-Host "  Last Run: $($taskInfo.LastRunTime)"
        Write-Host "  Next Run: $($taskInfo.NextRunTime)"
        
        $taskDef = Get-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath
        Write-Host "  Action: $($taskDef.Actions.Execute) $($taskDef.Actions.Arguments)"
        Write-Host ""
    }
} else {
    Write-Host "No matching tasks found." -ForegroundColor Red
    Write-Host ""
    Write-Host "All tasks in Task Scheduler:" -ForegroundColor Cyan
    $allTasks = Get-ScheduledTask | Select-Object TaskName, State, TaskPath | Sort-Object TaskName
    $allTasks | Format-Table -AutoSize
}

Write-Host ""
Write-Host "To open Task Scheduler GUI:" -ForegroundColor Cyan
Write-Host "  Press Win+R, type 'taskschd.msc', press Enter"
Write-Host ""
Write-Host "Or search for 'Task Scheduler' in Start menu"
Write-Host ""

