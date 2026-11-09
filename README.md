# Secure RAG & Enterprise Dataset Platform

A unified, production-grade repository combining:
1. **`secure-rag/`**: A Self-Hardening Secure RAG Chatbot featuring multi-layer guardrails (Input Guard, RBAC Vector Retrieval, Context Guard, Adaptive Store, and Output Guard).
2. **`dataset_pipeline/`**: A public enterprise document collection and curation pipeline (971 validated documents from GitLab Handbook, NIST, CISA, CUAD, and MAUD) with attack and benign benchmark builders.

---

## 1. Unified Directory Structure

```text
majorproject/
├── README.md                             # Unified system documentation (this file)
├── requirements.txt                      # Consolidated dependencies (chatbot + dataset pipeline)
├── .env.example                          # Safe environment configuration template
├── run_tests.ps1                         # PowerShell test runner for both subprojects
│
├── secure-rag/                           # Self-Hardening Secure RAG Chatbot
│   ├── pytest.ini                        # Scoped pytest configuration for secure-rag
│   ├── requirements.txt                  # Chatbot-specific pinned dependencies
│   ├── config.yaml                       # Application configuration (LLM, RAG, ChromaDB, Guardrails)
│   ├── .env.example                      # Chatbot-specific environment template
│   ├── app/                              # Chatbot application source
│   │   ├── auth.py                       # JWT authentication, bcrypt passwords, role verification
│   │   ├── config.py                     # Centralized settings via pydantic-settings
│   │   ├── main.py                       # FastAPI application & REST endpoints
│   │   ├── schemas.py                    # Pydantic request & response schemas
│   │   ├── guardrails/                   # Guardrail layers
│   │   │   ├── input_guard.py            # Static prompt injection & obfuscation detector
│   │   │   ├── context_guard.py          # Indirect injection scanner for retrieved context
│   │   │   ├── output_guard.py           # Canary leakage & restricted content filter
│   │   │   └── adaptive.py               # Self-hardening nearest-neighbor attack memory
│   │   ├── rag/                          # Retrieval-Augmented Generation engine
│   │   │   ├── embedder.py               # SentenceTransformers / Ollama embeddings
│   │   │   ├── ingest.py                 # Document chunking, frontmatter parsing, & embedding
│   │   │   ├── llm.py                    # Ollama client interface
│   │   │   ├── pipeline.py               # Full RAG execution pipeline
│   │   │   └── retriever.py              # RBAC-filtered vector retrieval
│   │   └── store/                        # Storage abstraction layers
│   │       ├── attack_store.py           # ChromaDB collection for known attack signatures
│   │       └── vector_store.py           # ChromaDB collection for RBAC document chunks
│   ├── data/                             # Chatbot runtime data
│   │   ├── canaries.json                 # Chatbot canary mapping (canary token -> minimum role)
│   │   ├── docs/                         # Seed RBAC markdown documents
│   │   │   ├── admin/                    # Administrator documents (salary bands, M&A, credentials)
│   │   │   ├── manager/                  # Manager documents (budgets, reviews, negotiations)
│   │   │   ├── analyst/                  # Analyst documents (handbook, policies, roadmap)
│   │   │   └── fixtures/                 # Injection test fixtures
│   │   └── chroma_db/                    # Local persistent vector database
│   ├── eval/                             # Chatbot evaluation & experiment harness
│   │   ├── metrics.py                    # ASR, overblocking, precision, recall, F1 metrics
│   │   ├── plots.py                      # Matplotlib & seaborn experiment plots
│   │   ├── run_experiment.py             # Multi-round self-hardening evaluation runner
│   │   └── datasets/
│   │       └── benign_queries.json       # Benchmark benign queries per role
│   ├── redteam/                          # Live red-teaming execution suite
│   │   ├── attacks.json                  # 65 adversarial prompt injection attacks
│   │   ├── canary.py                     # Canary leak detection & normalized matching
│   │   ├── mutator.py                    # Adversarial mutation engine (base64, leet, wraps)
│   │   ├── report.py                     # Red-team campaign report generator
│   │   └── runner.py                     # Execution harness against running API
│   └── tests/                            # Chatbot test suite
│       └── test_data_integrity.py        # Static asset, schema, canary, & config validation
│
└── dataset_pipeline/                     # Public Enterprise Document Collection & Curation
    ├── pytest.ini                        # Scoped pytest configuration for dataset_pipeline
    ├── requirements.txt                  # Dataset pipeline dependencies (requests, bs4, pypdf, pyyaml)
    ├── README.md                         # Dataset collection methodology overview
    ├── docs/                             # In-depth dataset documentation
    │   ├── DATASETS.md                   # Dataset inventory
    │   ├── DATASET_METHODOLOGY.md        # Provenance, licensing, and reproducibility guide
    │   └── DATA_METHODOLOGY.md           # Processing and filtering specifications
    ├── documents/
    │   └── catalog.json                  # Original document source catalog (68 records)
    ├── scripts/                          # Dataset curation and collection scripts
    │   ├── fetch_data.py                 # Crawler for GitLab Handbook, NIST, CISA, and SEC EDGAR
    │   ├── curate_corpus.py              # Filter, deduplicate, and sample corpus
    │   ├── chunk_documents.py            # Role-based document chunker
    │   ├── validate_datasets.py          # Integrity validation for curated data
    │   └── build_all_datasets.ps1        # Full pipeline automation script
    ├── redteam/
    │   └── build_attack_dataset.py       # Offline attack split builder (train, val, test)
    ├── eval/
    │   └── build_benign_set.py           # Benign QA and refusal probe generator
    ├── evaluation/                       # Legacy evaluation query sets
    │   ├── authorized_questions.json
    │   └── unauthorized_access_tests.json
    ├── attacks/                          # Legacy raw attack datasets
    │   ├── bipia_test.json
    │   └── tensor_trust_training.json
    ├── tests/
    │   └── test_dataset_pipeline.py      # Unit & integration tests for pipeline (20 tests)
    └── data/                             # Dataset storage (preserves internal relative paths)
        ├── raw/                          # [READ-ONLY] Original scraped HTML, PDF, and TXT payloads
        ├── processed/                    # 971 normalized markdown documents by topic
        ├── curated/                      # Curated documents.jsonl and curation_report.json
        ├── attacks/                      # Generated attack dataset splits and manifest
        ├── benign/                       # Generated benign queries and report
        ├── canaries.json                 # Synthetic fixture canary schema
        ├── employees.json                # Employee access control database
        └── role_policy.yaml              # Department and role access rules
```

---

## 2. Dataset Paths & Shared Data Layout

| Purpose | Location | Description |
| :--- | :--- | :--- |
| **Chatbot RBAC Corpus** | `secure-rag/data/docs/` | 21 Markdown files with YAML frontmatter (`min_role: Analyst/Manager/Administrator`). |
| **Chatbot Canaries** | `secure-rag/data/canaries.json` | Active canary mapping (`token -> minimum role`). |
| **Curated Public Corpus** | `dataset_pipeline/data/curated/documents.jsonl` | 387 bounded, deduplicated enterprise documents. |
| **Processed Public Docs** | `dataset_pipeline/data/processed/` | 971 Markdown documents across 12 business topics. |
| **Raw Scraped Sources** | `dataset_pipeline/data/raw/` | Archival, read-only HTML, PDF, and contract texts. |
| **Dataset Canaries** | `dataset_pipeline/data/canaries.json` | Synthetic fixture tokens for dataset integrity checks. |
| **Attack Benchmarks** | `dataset_pipeline/data/attacks/` | Offline train (`300`), validation (`100`), test (`300`) splits. |
| **Chatbot Attack Library**| `secure-rag/redteam/attacks.json` | 65 targeted prompt injections for live red-teaming. |

---

## 3. Installation & Environment Setup

### Option A: Unified Environment (Recommended)
From the project root:
```powershell
python -m pip install -r requirements.txt
```

### Option B: Separate Environments
To install dependencies for each component individually:

**Chatbot only (`secure-rag`):**
```powershell
cd secure-rag
python -m pip install -r requirements.txt
cd ..
```

**Dataset Pipeline only (`dataset_pipeline`):**
```powershell
cd dataset_pipeline
python -m pip install -r requirements.txt
cd ..
```

### Environment Configuration
Copy `.env.example` to `.env` in the root or in `secure-rag/`:
```powershell
Copy-Item .env.example .env
```
Ensure passwords and API keys are configured securely. Never commit `.env` to source control.

---

## 4. Commands to Run Tests

### Run All Tests Across Both Projects
From the project root:
```powershell
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
```

### Run Tests Individually
- **Dataset Pipeline tests (20 tests):**
  ```powershell
  pytest dataset_pipeline
  ```
- **Chatbot data integrity & configuration tests (7 tests):**
  ```powershell
  pytest secure-rag
  ```

---

## 5. Commands to Run the Dataset Pipeline

All pipeline commands are executed from `dataset_pipeline/`:

```powershell
cd dataset_pipeline
```

1. **Curate Corpus (Deduplicate & Filter):**
   ```powershell
   python scripts/curate_corpus.py
   ```
2. **Build Attack Dataset (Offline handwritten & remote splits):**
   ```powershell
   python redteam/build_attack_dataset.py
   ```
3. **Build Benign Query Set (Generates grounded QA pairs):**
   ```powershell
   python eval/build_benign_set.py --model llama3.2:latest
   ```
4. **Validate Dataset Quality & Hashes:**
   ```powershell
   python scripts/validate_datasets.py
   ```
5. **Or Run the Complete Automated Pipeline:**
   ```powershell
   .\scripts\build_all_datasets.ps1
   ```

---

## 6. Commands to Start and Test the Chatbot

All chatbot commands are executed from `secure-rag/`:

```powershell
cd secure-rag
```

1. **Ingest Documents into ChromaDB:**
   Embeds all documents in `data/docs/` with RBAC metadata into the local vector store:
   ```powershell
   python -m app.rag.ingest --force
   ```

2. **Start the FastAPI Backend:**
   Starts the chatbot API on `http://localhost:8000`:
   ```powershell
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

3. **Start the Streamlit UI (Optional):**
   ```powershell
   streamlit run app/ui.py
   ```

4. **Execute Red-Team Attack Campaign:**
   Launches 65 adversarial attacks against the running chatbot server:
   ```powershell
   python -m redteam.runner --split test --report-format markdown
   ```

5. **Run Multi-Round Evaluation Experiment:**
   Executes baseline, static, and adaptive rounds with metric computations:
   ```powershell
   python eval/run_experiment.py
   ```
