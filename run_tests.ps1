Write-Host "=== Running Dataset Pipeline Tests ===" -ForegroundColor Cyan
python -m pytest dataset_pipeline
$dsExit = $LASTEXITCODE

Write-Host "`n=== Running Secure RAG Chatbot Tests ===" -ForegroundColor Cyan
python -m pytest secure-rag
$ragExit = $LASTEXITCODE

if ($dsExit -ne 0 -or $ragExit -ne 0) {
    Write-Host "`n[FAIL] One or more test suites failed." -ForegroundColor Red
    exit 1
} else {
    Write-Host "`n[PASS] All test suites passed successfully!" -ForegroundColor Green
    exit 0
}
