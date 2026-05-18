$ErrorActionPreference = "Stop"

# Get the directory where the script is located
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir = Split-Path -Parent $ScriptDir
$PythonScript = Join-Path -Path $ScriptDir -ChildPath "heartbeat.py"

Write-Host "Setting up Qdrant Cloud Heartbeat task..."
Write-Host "Project Directory: $ProjectDir"
Write-Host "Python Script: $PythonScript"

# Define task parameters
$TaskName = "QdrantCloudHeartbeat"
$TaskDescription = "Pings Qdrant Cloud to prevent idle suspension"

# Create action: Run python script in project directory
$Action = New-ScheduledTaskAction -Execute "python" -Argument "`"$PythonScript`"" -WorkingDirectory $ProjectDir

# Create trigger: Run every 3 days at 3:00 AM
$Trigger = New-ScheduledTaskTrigger -Daily -DaysInterval 3 -At 3:00AM

# Register the task
Register-ScheduledTask -Action $Action -Trigger $Trigger -TaskName $TaskName -Description $TaskDescription

Write-Host "Scheduled task '$TaskName' registered successfully!"
Write-Host "It will run every 3 days at 3:00 AM to keep the Qdrant Cloud cluster active."
