<<<<<<< HEAD
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
=======
# Self-Hardening-Role-Based-Secure-RAG-Chatbot
Large Language Model (LLM)-based Retrieval-Augmented Generation (RAG) systems are increasingly being used to provide conversational access to organizational knowledge. However, deploying such systems in enterprise environments introduces significant security challenges, particularly unauthorized information access, prompt injection, indirect prompt injection, and attempts to bypass role-based access restrictions. Conventional RAG security approaches generally rely on static filters and manually defined rules, which may become ineffective when attackers use new or modified attack patterns. This project proposes a Self-Hardening Secure RAG Chatbot that combines role-based document access control with an adaptive security mechanism driven by automated red teaming.
The proposed system provides employees with a conversational interface for querying internal organizational documents while restricting information according to their assigned roles, such as Administrator, Manager, or Analyst. Documents are converted into embeddings and stored in a vector database along with role-based metadata. During retrieval, only documents authorized for the authenticated user's role are considered. The retrieved content is then passed through security checks before being provided to a locally hosted Large Language Model. Additional input and output guardrails are employed to identify malicious prompts, unauthorized information requests, prompt injection attempts, and potentially leaked restricted content.
The key contribution of the project is an automated self-hardening feedback loop. A custom red-team engine generates and executes adversarial queries against the chatbot, including predefined attacks and automatically generated or paraphrased variations. Each attack is evaluated using a canary-based success detection mechanism to determine whether the system produced an unauthorized or security-sensitive response. Successful attacks are automatically recorded in a dedicated known-attack vector store. In subsequent testing rounds, the adaptive guardrail uses this accumulated knowledge to recognize similar attack patterns and strengthen its detection process without requiring a developer to manually rewrite security rules after every attack cycle.
The complete system is implemented using freely available technologies, including Python, FastAPI, ChromaDB, locally hosted embedding models, Ollama-based open-source LLMs, and Streamlit. The system is evaluated through multiple automated red-team rounds using consistent attack datasets and metrics such as attack success rate, false-positive rate, response latency, and authorized-answer accuracy. The primary objective is to demonstrate a measurable reduction in successful attacks as the system progresses through successive red-team and adaptation cycles.
The project aims to demonstrate that combining RAG, role-based access control, multi-layer guardrails, and automated adversarial testing can provide a more adaptive security architecture than a conventional static RAG chatbot. The final outcome will be a functional role-restricted enterprise chatbot accompanied by an automated red-team framework and experimental analysis showing how the system's security posture changes over successive attack rounds. Rather than claiming absolute security, the project focuses on demonstrating measurable, reproducible improvement against a defined set of adversarial attacks and identifying the limitations and attack categories that remain difficult to mitigate.
>>>>>>> 3a4a653437c91f1fdbd0303bd870aad328532811
