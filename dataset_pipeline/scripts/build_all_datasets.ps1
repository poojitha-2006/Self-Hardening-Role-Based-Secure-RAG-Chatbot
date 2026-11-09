param(
    [switch]$AllowAttackNetwork,
    [switch]$AllowEmbeddingModelDownload,
    [string]$ClassificationMap,
    [string]$OllamaModel = "llama3.2:3b"
)

$ErrorActionPreference = "Stop"

python scripts/curate_corpus.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$attackArgs = @()
if ($AllowAttackNetwork) { $attackArgs += "--allow-network" }
if ($AllowEmbeddingModelDownload) { $attackArgs += "--allow-model-download" }
python redteam/build_attack_dataset.py @attackArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$benignArgs = @("--model", $OllamaModel)
if ($ClassificationMap) { $benignArgs += @("--classification-map", $ClassificationMap) }
python eval/build_benign_set.py @benignArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python scripts/validate_datasets.py
exit $LASTEXITCODE