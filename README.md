# Secure RAG Chatbot and Public-Text Dataset Pipeline

This repository contains two related components:

- `secure-rag/`: a FastAPI and Streamlit retrieval-augmented chatbot with JWT authentication, simulated role/department access policy, ChromaDB retrieval, guardrails, and red-team/evaluation tools.
- `dataset_pipeline/`: scripts for collecting, processing, curating, and validating real public enterprise documents and building evaluation datasets.

The chatbot's department and classification policy is simulated for this project. The curated corpus consists of real public text; public-source documents are not confidential company records. Separate fictional chatbot fixtures are used to exercise simulated access controls and canary checks.

## Current verified state

- The curated corpus contains **386 documents** in `dataset_pipeline/data/curated/documents.jsonl`. The separate processed corpus contains **971 documents**.
- The dataset pipeline's current attack manifest reports **60 handwritten training attacks, 0 validation attacks, and 0 test attacks**. The five listed external attack sources were not attempted because network access was not authorized; this manifest has no source marked `unavailable`.
- The chatbot's separate `secure-rag/redteam/attacks.json` contains **65 attacks**: 46 train and 19 held-out test.
- From `secure-rag/`, `python -m app.rag.ingest --force` was run successfully. It read the curated JSONL plus 21 simulated chatbot fixture documents, indexed **407 documents and 1,987 chunks**, and found all **10 of 10** registered chatbot canaries in ingested chunks.
- Live API checks with the installed Ollama `llama3.2:latest` model confirmed that an Engineering Analyst did not receive HR-confidential sources while the HR Manager received the relevant HR source. Five injection probes were blocked with no restricted source or canary returned.
- The latest combined test run passed **20 dataset tests and 16 chatbot tests**. Two FastAPI `on_event` deprecation warnings remain.
- The final 10-attack/10-benign smoke run (seed 42) measured **0% ASR, 0% FPR, 80% answer accuracy** in each of baseline, static, and adaptive rounds. Mean latency across attack and benign requests was **7,191.2 ms**, **9,712.9 ms**, and **8,988.0 ms**, respectively. These small-sample results are not statistically conclusive.
- The smoke run wrote JSON/CSV results, five plots, and a Markdown report under `secure-rag/eval/results/`. Exact held-out attack text matches in the known-attacks store after the run: **0**.

## Setup

Use Python 3.12+ and install the repository dependencies from the root:

```powershell
python -m pip install -r requirements.txt
```

Ollama is used for text generation; embeddings use ChromaDB's local MiniLM embedding function by default. Pull the model configured in `secure-rag/config.yaml` if it is not already installed:

```powershell
ollama pull llama3.2:latest
```

Copy `.env.example` to `.env` only if you need local overrides. Do not commit `.env` or production credentials.

## Tests

Run both test suites from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
```

The runner invokes `python -m pytest` for both subprojects. This avoids relying on the `pytest.exe` launcher, which may be blocked by Windows application-control policy. Run an individual suite with:

```powershell
python -m pytest dataset_pipeline
python -m pytest secure-rag
```

## Build the chatbot index

From `secure-rag/`, ingest the simulated chatbot fixtures and the curated public corpus:

```powershell
python -m app.rag.ingest --force
```

The chatbot ingestion source is `dataset_pipeline/data/curated/documents.jsonl`, **not** `dataset_pipeline/data/processed/documents.jsonl`. `--force` recreates the chatbot's ChromaDB document collection before indexing.

## Run the application

Open two PowerShell terminals from `secure-rag/`.

Terminal 1 — FastAPI:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Terminal 2 — Streamlit:

```powershell
python -m streamlit run app/ui.py
```

The API health endpoint is `http://127.0.0.1:8000/health`; the UI is normally served at `http://localhost:8501`. Demo logins use the seeded fictional users and password `password`; do not reuse these demo credentials in production.

## Smoke experiment

With the index built and Ollama running, execute from `secure-rag/`:

```powershell
python -m eval.run_experiment --smoke --seed 42
```

The smoke mode samples 10 attacks from the chatbot's held-out test split and 10 benign queries, runs baseline/static/adaptive rounds, and checks that exact held-out attack text is not in the known-attacks store. It writes JSON and CSV results, plots, and a Markdown report under `secure-rag/eval/results/` by default. This is a small wiring check, **not** the full experiment.

The full evaluation is intentionally not documented as validated here. Do not infer full-experiment quality from smoke results.

## Dataset pipeline commands

From `dataset_pipeline/`, run the existing scripts as needed:

```powershell
python scripts/curate_corpus.py
python redteam/build_attack_dataset.py
python scripts/validate_datasets.py
```

The public corpus methodology, source inventory, licensing notes, and current acquisition status are documented under `dataset_pipeline/docs/`. Raw and processed dataset inputs should be treated as source data and are not modified by chatbot ingestion.

## Known limitations

- The dataset-pipeline attack manifest currently has an empty validation and test split. Its 60 handwritten examples are training-only; external sources were not downloaded.
- The separate chatbot test split has 19 attacks, so any smoke estimate is small and not statistically conclusive.
- Access control uses simulated roles, departments, and document classifications. It is not a production identity provider or real company authorization policy.
- The curated public corpus is deliberately bounded during chatbot ingestion (up to five chunks per document); it is not a complete index of all processed text.
- The current persistent known-attacks collection contains legacy entries that may not have `source_split` metadata. New experiment seeding labels training entries and prevents held-out test text from being learned, but legacy provenance is incomplete.
- The Streamlit dashboard contains demonstration-oriented values and should not be treated as a source of measured evaluation metrics unless those are populated from a completed experiment.
- Prompt-injection defenses are layered mitigations, not a guarantee against novel attacks. Review failures and false positives before drawing security conclusions.
- Streamlit's health endpoint and initial page load were checked. Interactive login/chat UI verification was not completed; the live API login/chat path was exercised separately.
- Production deployment requires a strong secret key, secure user/password management, restricted CORS, operational logging, and a reviewed access policy.
