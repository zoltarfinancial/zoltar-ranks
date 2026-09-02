# Serve the research console locally, with the SS12 review board writable.
#
# This replaces the old `python -m http.server` wrapper. That served the page
# read-only, which was fine while the console only reported - but SS12 is the
# one section the three parties WRITE to, and a static server gives Andrew no
# way to answer from the page. dashboard/serve.py adds exactly one write route
# (POST /api/review) and binds to 127.0.0.1 only.
#
# Opening dashboard/index.html straight from Explorer still works: the page
# reads data/results/*.js beside the JSON, so it renders live either way. What
# file:// cannot do is post a reply - there the page falls back to handing you
# the JSON line and a Copy button.
#
#   .\dashboard\serve.ps1
#   .\dashboard\serve.ps1 -Port 9000
param([int]$Port = 8787)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
& $python "dashboard\serve.py" --port $Port
