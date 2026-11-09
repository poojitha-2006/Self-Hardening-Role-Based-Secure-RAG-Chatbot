# Public Enterprise Document Dataset

## Scope

This is a public-source document collection for future research. It contains no chatbot, backend, embeddings, or synthetic canary augmentation. Existing legacy synthetic records and downloaded files are preserved in place but excluded from the public processed corpus. `documents/catalog.json` remains the unchanged source catalog.

The collection uses the official GitLab Handbook sitemap as the primary discovery source, plus the catalog's GitLab/NIST/CISA references and the official CUAD/MAUD archives. Handbook URLs are limited to `handbook.gitlab.com`, selected from the published sitemap, checked against `robots.txt`, and fetched with a configurable delay. The sitemap exposed 4,651 unique URLs during the run; 260 additional pages were selected and processed. These pages are a representative public subset, not GitLab's whole internal knowledge base.

## Layout

- `data/raw/gitlab_handbook/`: original public HTML payloads.
- `data/raw/nist/`, `data/raw/cisa/`, and `data/raw/sec_edgar/`: source payloads where available.
- `data/raw/cuad/` and `data/raw/maud/`: official source ZIP archives and contract files extracted from them.
- `data/processed/<department>/`: readable normalized Markdown per document.
- `data/processed/documents.jsonl`: one JSON record per usable public document with full text and source metadata.
- `data/processed/document_metadata.json`: metadata sidecar for the JSONL corpus.
- `data/catalog_downloaded.json`: source catalog plus per-record acquisition status and provenance.
- `data/download_failures.json`: failed, partial, and reference-only source records.
- `data/dataset_report.json`: latest collection metrics, attribution notes, and validation results.

## Latest collection

Run date: 2026-10-18. The verified run produced 971 usable documents: 260 newly discovered Handbook pages plus catalog sources and 510 CUAD contracts and 152 MAUD agreements, deduplicated by extracted-text hash. The complete report is authoritative for exact source/topic breakdowns and raw byte totals. The existing source catalog has 68 records. No chunks are counted as documents.

Ten catalog/page fetches remain failed or incomplete because their extracted public content is under the 250-character minimum; they remain recorded for retry. Two SEC catalog entries were not requested because SEC access requires a real identifying `SEC_USER_AGENT`, which was not configured. SEC 10-K/10-Q content therefore is not included in the 971 documents. The two CUAD/MAUD repository pointers remain `reference_only`; their full-text datasets were separately obtained from the official CC BY 4.0 archives.

## Provenance and licensing

GitLab Handbook pages identify a Creative Commons Attribution-ShareAlike 4.0 license. The Atticus Project lists CUAD and MAUD under CC BY 4.0. The raw archive ZIPs, archive member names, URLs, SHA-256 hashes, source organization, title, department/topic, format, and any source-provided publication date are retained. CISA/NIST documents should be reviewed individually for third-party notices. SEC fetches must respect the SEC fair-access policy and use a truthful contact User-Agent. A publicly reachable source is not automatically permission to redistribute it; confirm source-specific terms before publishing any collected files.

Department labels group public content by topic. They are not source-provided access-control facts and do not make public content confidential or restricted. No employee records or canary tokens are included in the processed corpus.

## Reproduce and resume

Install dependencies once:

```powershell
python -m pip install -r requirements.txt
```

Small smoke test (five eligible records and no Atticus archive download):

```powershell
python scripts/fetch_data.py --limit 5 --max-handbook-pages 5 --skip-atticus
```

Resume/expand the public collection (existing verified raw/processed records are reused):

```powershell
python scripts/fetch_data.py --max-handbook-pages 250 --enable-pdf
```

To retrieve GitLab Inc. 10-K/10-Q filings, set `SEC_USER_AGENT` to your real organization and contact details, then run:

```powershell
$env:SEC_USER_AGENT = "Your Organization contact: your-real-email@example.com"
python scripts/fetch_data.py --max-handbook-pages 0 --sec-filings 5 --enable-pdf
```

Replace the example identity with truthful contact information. Without it the collector deliberately makes no SEC request and records the issue. `--max-handbook-pages 0` reuses the existing discovered pages; adjust `--max-handbook-pages` to grow the Handbook set. `--limit N` limits the catalog fetch sample; `--skip-atticus` avoids the larger archives for a smoke test. Use `--help` for timeout, delay, retry, output path, and SEC CIK options.

## Validation

The pipeline validates that every JSONL document has substantial extracted text, matching source URL metadata, an existing raw file, an existing processed file, and a matching SHA-256 of the exact serialized extracted text. Duplicate text hashes are excluded from the processed corpus. Current check result is in `data/dataset_report.json`; tests run with:

```powershell
python -m pytest -q
```

The legacy `scripts/chunk_documents.py` and `data/documents/` tree are not part of this public-source corpus and were left untouched.
