[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgsForPython
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PyScript = Join-Path $ScriptDir "godot_translation_import.py"

$PythonCmd = Get-Command "python" -ErrorAction SilentlyContinue
if (-not $PythonCmd) {
    $PythonCmd = Get-Command "python3" -ErrorAction SilentlyContinue
}

if (-not $PythonCmd) {
    Write-Error "python/python3 not found. Install Python 3 first."
    exit 1
}

$ArgsToPass = @()
if (-not $ArgsForPython -or $ArgsForPython.Count -eq 0) {
    $ArgsToPass += "merge"
}
elseif ($ArgsForPython[0] -ne "merge" -and $ArgsForPython[0] -ne "audit") {
    $ArgsToPass += "merge"
    $ArgsToPass += $ArgsForPython
}
else {
    $ArgsToPass += $ArgsForPython
}

& $PythonCmd.Source $PyScript @ArgsToPass
exit $LASTEXITCODE
