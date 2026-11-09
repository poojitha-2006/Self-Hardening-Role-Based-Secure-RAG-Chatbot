# Public Enterprise Document Collection

This project now focuses on collecting real public enterprise documents for future RAG research. It contains 971 validated public documents from selected GitLab Handbook pages, NIST/CISA sources, and the licensed CUAD/MAUD contract datasets. The collection includes full extracted text, separate raw files, source metadata, and acquisition status.

Start with [docs/DATASET_METHODOLOGY.md](docs/DATASET_METHODOLOGY.md) for provenance, collection scope, licensing notes, validation results, and resume commands. The detailed counts and current failures are in [data/dataset_report.json](data/dataset_report.json); full text is in [data/processed/documents.jsonl](data/processed/documents.jsonl).

The original [documents/catalog.json](documents/catalog.json) remains unchanged. Legacy employee, policy, canary, attack, evaluation, and old document files remain in the workspace for compatibility but are not included in the new public-only processed corpus.