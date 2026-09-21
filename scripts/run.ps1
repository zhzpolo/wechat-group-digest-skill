$skillRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $skillRoot
$env:PYTHONPATH = Join-Path $skillRoot 'scripts'
& '.\.venv\Scripts\python.exe' -X utf8 -m wechat_digest @args
exit $LASTEXITCODE
