# Dataset Inventory and Methodology

## 1. Overview & Project Role

This directory (`dataset_pipeline/`) contains the data engineering pipeline for the **Self-Hardening Secure RAG Chatbot** project. Its sole responsibility is to collect, curate, normalize, and validate datasets for:
- RAG document retrieval and chunk indexing
- Red-team prompt injection and jailbreak evaluation (train/validation/test splits)
- Benign question answering and over-blocking evaluation

No chatbot logic, guardrail filters, APIs, or UI components are implemented here.

---

## 2. Real vs. Simulated Provenance

To maintain research integrity and prevent data contamination, all dataset components are explicitly demarcated as either **Real** (publicly acquired) or **Simulated** (synthetic artifacts created for security benchmarking):

| Component | Status | Source / Description | License / Usage |
| :--- | :--- | :--- | :--- |
| **GitLab Handbook** | **REAL** | Extracted from `handbook.gitlab.com` sitemap; real public company policies, handbook entries, and job descriptions across departments. | CC BY-SA 4.0 |
| **CUAD Contracts** | **REAL** | Contract Understanding Atticus Dataset from The Atticus Project; real commercial agreements and contract clauses. Contract types derived from titles. | CC BY 4.0 |
| **MAUD Agreements** | **REAL** | Merger Agreement Understanding Dataset from The Atticus Project; real public merger & acquisition agreements. | CC BY 4.0 |
| **NIST / CISA Docs** | **REAL** | Public security reference frameworks and guidance (e.g., NIST CSF, CISA guides). | Public Domain / US Gov Work |
| **Public Attacks** | **REAL** | Remote attack datasets from Hugging Face: Tensor Trust, Gandalf (Lakera), deepset/prompt-injections, HackAPrompt, and BIPIA. | Source licenses checked at download (Apache 2.0, MIT, CC BY 4.0) |
| **Handwritten Attacks** | **SIMULATED** | 60 hand-crafted attack prompts specifically targeting synthetic canary tokens and fictional role boundaries. | Original project test fixture |
| **Canaries & Tokens** | **SIMULATED** | Fictional tokens (`CANARY-HR-7F3A91C2`, `CANARY-FIN-2D8B64E1`, `CANARY-LEGAL-5A0C73D9`, `CANARY-SEC-9E4B1F60`, `CANARY-EXEC-3C6A82B5`) to detect data exfiltration. | Synthetic test artifact |
| **Roles & Policies** | **SIMULATED** | Fictional organizational structure, employee IDs (`EMP-XXXX`), and role-based access control rules (`data/role_policy.yaml`, `data/employees.json`). | Synthetic test artifact |
| **Document Classifications**| **SIMULATED** | Security classification labels (`Public`, `Internal`, `Confidential`, `Restricted`) assigned to documents for access control testing. | Synthetic test artifact |
| **Benign QA Queries** | **SYNTHETIC (from REAL)** | Generated from real curated chunks using local Ollama (`llama3.2`), strictly verified against literal text substrings. | Derived from real text |

---

## 3. Curated Document Corpus (`data/curated/documents.jsonl`)

The curated corpus is created deterministically by `scripts/curate_corpus.py` (seed `20261018`):
1. **Deduplication & Length Filter**: Drops texts shorter than 500 characters (5 documents dropped) and removes duplicate content hashes (2 documents dropped).
2. **Canonicalization**: Merges split GitLab source labels (`"GitLab"` and `"GitLab Handbook"`) into a single canonical source: `"GitLab Handbook"`.
3. **Legal Contract Sampling**: Caps Legal contracts at 80 (50 CUAD + 30 MAUD) via seeded stratified sampling based on agreement type derived from title metadata. All 30 GitLab Legal Handbook pages are preserved.
4. **Length Cap**: Any curated document exceeding 60,000 characters is capped at 60,000 characters with `truncated: true` and original hash recorded in metadata. Raw storage under `data/raw/` remains untouched.

### Exact Document & Chunk Counts (from `curation_report.json`)
- **Total Curated Documents**: 386 (exceeds the 350-document minimum floor)
- **Total Characters**: 6,436,547
- **Estimated Chunks** (900 chars, 120 overlap): 9,072 chunks
- **Truncated Documents**: 15 documents

#### Breakdown by Topic:
| Topic | Document Count | Total Characters | Est. Chunks | Status / Note |
| :--- | :--- | :--- | :--- | :--- |
| **Legal** | 110 | 3,365,712 | 4,685 | 50 CUAD + 30 MAUD + 30 Handbook |
| **Cybersecurity/IT** | 32 | 473,083 | 669 | Meets target (>= 30) |
| **Finance** | 29 | 362,568 | 511 | Handbook-based (SEC skipped) |
| **HR/People** | 28 | 393,313 | 557 | Handbook-based |
| **Marketing** | 28 | 338,819 | 480 | Handbook-based |
| **Sales** | 26 | 325,183 | 461 | Handbook-based |
| **Executive** | 25 | 293,763 | 417 | Handbook-based |
| **Engineering** | 24 | 293,892 | 417 | Handbook-based |
| **Product** | 22 | 196,739 | 280 | Handbook-based |
| **AI/Data** | 21 | 185,419 | 264 | Handbook-based |
| **Customer Support** | 21 | 179,301 | 253 | Handbook-based |
| **Procurement/Operations** | 20 | 128,755 | 182 | Handbook-based |

#### Breakdown by Source:
- **GitLab Handbook**: 303 documents
- **CUAD**: 50 documents
- **MAUD**: 30 documents
- **NIST**: 2 documents
- **CISA**: 1 document

### Limitations & Shortfalls:
- **Legal Skew**: Legal represents 28.5% (110 / 386) of the curated corpus, above the 20% target. Because all non-Legal documents (276) were retained, lowering Legal below 20% without discarding valid non-Legal data requires expanding non-Legal sources.
- **Topics Below Target of 30**: 10 non-Legal topics have 20–29 documents. To expand them via additional GitLab Handbook crawling, run:
  ```powershell
  python scripts/fetch_data.py --max-handbook-pages 250 --enable-pdf
  ```
  *(Network authorization required before running).*
- **SEC Filings**: `SEC_USER_AGENT` was not set; therefore, SEC EDGAR fetching was skipped, and `"Finance is Handbook-based; SEC filings not included"` was recorded.

---

## 4. Attack Dataset Splits (`data/attacks/`)

### Splitting by Source Rationale
Attack splits are partitioned strictly **by source** rather than randomly:
- **TRAIN**: `handwritten`, `Tensor Trust`, `Gandalf (Lakera)`
- **VALIDATION**: `deepset/prompt-injections`
- **TEST**: `HackAPrompt`, `BIPIA` (indirect prompt injection)

**Why Source Partitioning?**
Random splitting of attack prompts leads to severe data leakage and superficial memorization of specific prompt injection phrasings. Partitioning by source evaluates **true out-of-distribution generalization**: guardrails and defense mechanisms trained on Gandalf or Tensor Trust must defend against completely novel, unseen attack strategies (such as indirect injections from BIPIA or obfuscations from HackAPrompt).

### Attack Schema
Every row across all splits adheres to:
```json
{
  "id": "string",
  "source": "string",
  "split": "train | validation | test",
  "category": "direct_prompt_injection | jailbreak_roleplay | role_escalation | system_prompt_secret_extraction | indirect_injection | exfiltration_obfuscation",
  "text": "string",
  "language": "en",
  "license": "string"
}
```

### Attack Counts & Leakage Check
- **Handwritten Attacks**: 60 total in `train.jsonl` (10 per category).
- **Leakage Check**: Cosine similarity against training set using `sentence-transformers/all-MiniLM-L6-v2` at threshold `0.95`. Status: `completed` (0 cross-split near duplicates).
- **Remote Sources Status**: In offline runs without `--allow-network`, remote downloads are recorded as `not_attempted_authorization_required` in `manifest.json`.

---

## 5. Benign and Evaluation Query Set (`data/benign/`)

Produced by `eval/build_benign_set.py` to evaluate response accuracy and guardrail over-blocking:
1. **Benign QA Queries (~150)**:
   - Generated from curated Public and Internal document chunks across all 12 topics.
   - Powered by local Ollama model (`llama3.2:3b`, temperature 0).
   - **Strict Grounding Criterion**: Each generated answer span and its keywords must appear literally and verbatim as a substring of the source chunk. If not, the candidate is discarded.
2. **Over-Blocking Tests (20)**:
   - Harmless, answerable user queries containing sensitive keywords (e.g. phishing awareness, password policies, encryption, breach protocols, incident response) where `should_be_refused = false`.
3. **Unauthorized-Access Probes (30)**:
   - Ordinary questions whose answers exist only in higher-classification documents (e.g. Confidential or Restricted), where `should_be_refused = true`.
   - Deferred cleanly via a hook that takes `--classification-map <path>` once simulated higher-classification documents are defined.

---

## 6. Pipeline Reproduction

To reproduce all datasets locally in sequence:

```powershell
# Run entire data pipeline offline
./scripts/build_all_datasets.ps1
```

Or run step-by-step:
```powershell
# 1. Curate document corpus
python scripts/curate_corpus.py

# 2. Build attack splits (with local embedding leakage check)
python redteam/build_attack_dataset.py

# 3. Generate benign query set using local Ollama
python eval/build_benign_set.py --target 150

# 4. Validate all dataset files, schemas, and reports
python scripts/validate_datasets.py
```

### Running Tests
Execute the dataset test suite with pytest:
```powershell
python -m pytest dataset_pipeline
```