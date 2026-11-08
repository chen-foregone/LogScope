$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
Set-Location -LiteralPath $repoRoot
if (-not (Test-Path -LiteralPath 'backend/app/agent_model.py')) {
    throw 'Run this script from its original LogScope location.'
}

& git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    throw 'The Git index already contains changes. Review them before running this script.'
}

$env:PYTHONPATH = 'backend'
$env:PGOPTIONS = '-c max_parallel_workers_per_gather=0'
$env:AGENT_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
$env:AGENT_ENABLED = 'false'
Remove-Item Env:RUN_BAILIAN_QUALITY -ErrorAction SilentlyContinue
Remove-Item Env:RUN_BAILIAN_LIVE -ErrorAction SilentlyContinue
Remove-Item Env:RUN_BAILIAN_INCIDENTS -ErrorAction SilentlyContinue
$testTemp = Join-Path $PSScriptRoot ('audit-temp-' + [guid]::NewGuid().ToString('N'))
$cases = @(
    'backend/tests/test_conclusions.py::test_safety_late_writes_and_completed_replays'
    'backend/tests/test_evidence.py::test_save_redacted_material_updates_real_detail_and_survives_restart'
    'backend/tests/test_evidence.py::test_identity_schema_is_strict_and_never_persists_raw_values'
    'backend/tests/test_historical_evidence.py::test_cross_round_sensitive_material_never_persists_raw_values'
)
& .venv/Scripts/python.exe -m pytest @cases -q -p no:cacheprovider --tb=short `
    "--basetemp=$testTemp" '--junitxml=.scratch/simulated-incident-edge-cases/semantic-docker-audit.xml' `
    *> .scratch/simulated-incident-edge-cases/semantic-docker-audit.txt
$testExit = $LASTEXITCODE
Get-Content .scratch/simulated-incident-edge-cases/semantic-docker-audit.txt -Tail 20
if ($testExit -ne 0) { throw 'Audit tests failed. No files have been staged or committed.' }

$files = @(
    'backend/app/agent_model.py'
    'backend/tests/test_agent_quality.py'
    'backend/tests/test_severity.py'
    'docs/agent-integration.md'
    '.scratch/simulated-incident-edge-cases/issues/02-model-semantic-followup.md'
    '.scratch/simulated-incident-edge-cases/map.md'
    '.scratch/simulated-incident-edge-cases/semantic-validation.md'
    '.scratch/simulated-incident-edge-cases/finish-semantic.ps1'
    '.scratch/simulated-incident-edge-cases/semantic-targeted-workspace.txt'
    '.scratch/simulated-incident-edge-cases/semantic-targeted-workspace.xml'
    '.scratch/simulated-incident-edge-cases/semantic-full-workspace.txt'
    '.scratch/simulated-incident-edge-cases/semantic-full-workspace.xml'
    '.scratch/simulated-incident-edge-cases/semantic-live.txt'
    '.scratch/simulated-incident-edge-cases/semantic-live.xml'
    '.scratch/simulated-incident-edge-cases/semantic-severity-fixed.txt'
    '.scratch/simulated-incident-edge-cases/semantic-severity-fixed.xml'
    '.scratch/simulated-incident-edge-cases/semantic-docker-audit.txt'
    '.scratch/simulated-incident-edge-cases/semantic-docker-audit.xml'
    '.scratch/agent-quality-followup/live/order-noise-9dbbd932.json'
    '.scratch/agent-quality-followup/live/order-multiple-495b7e33.json'
    '.scratch/agent-quality-followup/live/order-conflicting-5f535a86.json'
    '.scratch/agent-quality-followup/live/order-missing-d90af038.json'
)
& git add -- @files
if ($LASTEXITCODE -ne 0) { throw 'Git staging failed; no commit was created.' }
& git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'Staged changes need review; no commit was created.' }
& git commit -m 'Evaluate bounded Agent semantic instructions against fixed scenarios'
if ($LASTEXITCODE -ne 0) { throw 'Git commit failed. Staged files remain available for review.' }
& git log -1 --oneline
Write-Output 'Audit and commit finished. Raw model semantic failures remain documented; issue 02 is not automatically closed.'
