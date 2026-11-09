-- ============================================================
-- secure-rag SQLite Database Schema
-- Single source of truth for Role-Based Access Control (RBAC),
-- Document Classifications, Canaries, Chunks, and Audit Logging.
-- ============================================================

PRAGMA foreign_keys = ON;

-- ── 1. Departments ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE
);

-- ── 2. Classifications ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS classifications (
    level INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL
);

-- ── 3. Roles ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY,
    department_id INTEGER NOT NULL REFERENCES departments(id),
    name TEXT NOT NULL,
    seniority INTEGER NOT NULL CHECK (seniority BETWEEN 1 AND 5),
    is_executive INTEGER NOT NULL DEFAULT 0 CHECK (is_executive IN (0, 1))
);

-- ── 4. Employees ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY,
    employee_id TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role_id TEXT NOT NULL REFERENCES roles(id),
    department_id INTEGER NOT NULL REFERENCES departments(id),
    seniority INTEGER NOT NULL CHECK (seniority BETWEEN 1 AND 5),
    is_executive INTEGER NOT NULL DEFAULT 0 CHECK (is_executive IN (0, 1))
);

-- ── 5. Documents ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    doc_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT,
    license TEXT,
    owner_department_id INTEGER NOT NULL REFERENCES departments(id),
    classification_level INTEGER NOT NULL REFERENCES classifications(level),
    exec_readable INTEGER NOT NULL DEFAULT 0 CHECK (exec_readable IN (0, 1)),
    content_hash TEXT,
    is_simulated_classification INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- ── 6. Explicit Grants (Overrides) ──────────────────────────
CREATE TABLE IF NOT EXISTS doc_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    subject_type TEXT NOT NULL CHECK (subject_type IN ('employee', 'role', 'department')),
    subject_id TEXT NOT NULL,
    effect TEXT NOT NULL CHECK (effect IN ('ALLOW', 'DENY')),
    reason TEXT NOT NULL
);

-- ── 7. Document Chunks ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id TEXT NOT NULL UNIQUE,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    doc_key TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    char_length INTEGER NOT NULL,
    is_fixture INTEGER NOT NULL DEFAULT 0,
    fixture_marker TEXT,
    classification_level INTEGER NOT NULL REFERENCES classifications(level),
    department_id INTEGER NOT NULL REFERENCES departments(id)
);

-- ── 8. Canary Tokens ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS canaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    doc_key TEXT NOT NULL,
    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id),
    department_id INTEGER NOT NULL REFERENCES departments(id),
    classification_level INTEGER NOT NULL REFERENCES classifications(level),
    created_at TEXT NOT NULL
);

-- ── 9. Audit Log ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    user_id INTEGER,
    username TEXT NOT NULL,
    department_id INTEGER,
    role_id TEXT,
    query_text TEXT NOT NULL,
    allowed_doc_count INTEGER NOT NULL,
    retrieved_chunk_count INTEGER NOT NULL,
    decision TEXT NOT NULL,
    guardrail_triggers TEXT,
    blocked_reason TEXT
);

-- ── 10. Red-Team Runs ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS redteam_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    split TEXT NOT NULL,
    model TEXT NOT NULL,
    num_attacks INTEGER NOT NULL,
    num_success INTEGER NOT NULL,
    asr REAL NOT NULL,
    avg_latency_ms REAL NOT NULL
);

-- ── 11. Red-Team Results ────────────────────────────────────
CREATE TABLE IF NOT EXISTS redteam_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES redteam_runs(id),
    attack_id TEXT NOT NULL,
    category TEXT NOT NULL,
    user_id INTEGER,
    username TEXT NOT NULL,
    role_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    response TEXT,
    is_blocked INTEGER NOT NULL,
    is_success INTEGER NOT NULL,
    canary_leaked TEXT,
    system_prompt_leaked INTEGER NOT NULL,
    marker_leaked INTEGER NOT NULL,
    risk_score REAL NOT NULL
);

-- ── 12. Known Attacks Memory Store ──────────────────────────
CREATE TABLE IF NOT EXISTS known_attacks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attack_id TEXT NOT NULL UNIQUE,
    prompt TEXT NOT NULL,
    category TEXT NOT NULL,
    embedding_id TEXT,
    added_at TEXT NOT NULL,
    source_split TEXT NOT NULL
);

-- ── 13. Evaluation Metrics ──────────────────────────────────
CREATE TABLE IF NOT EXISTS eval_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_num INTEGER NOT NULL,
    mode TEXT NOT NULL,
    seed INTEGER NOT NULL,
    asr REAL NOT NULL,
    overblocking_rate REAL NOT NULL,
    authorized_accuracy REAL NOT NULL,
    p95_latency_ms REAL NOT NULL,
    created_at TEXT NOT NULL
);

-- ── SINGLE SOURCE OF TRUTH ACCESS VIEW ──────────────────────
-- Access rules:
-- 1. Explicit DENY always wins
-- 2. Explicit ALLOW overrides defaults (if no DENY)
-- 3. Public (0) & Internal (1) are readable by all authenticated employees
-- 4. Confidential (2) requires same department with seniority >= 2 OR executive
-- 5. Restricted (3) requires same department with seniority >= 3 OR executive (if exec_readable)
CREATE VIEW IF NOT EXISTS v_access AS
SELECT
    e.id AS employee_id,
    e.username,
    e.employee_id AS emp_code,
    e.role_id,
    e.department_id AS employee_department_id,
    e.seniority,
    e.is_executive,
    d.id AS document_id,
    d.doc_key,
    d.title AS document_title,
    d.owner_department_id,
    d.classification_level,
    d.exec_readable,
    CASE
        -- 1. Explicit DENY grant always wins
        WHEN EXISTS (
            SELECT 1 FROM doc_grants g
            WHERE g.document_id = d.id
              AND g.effect = 'DENY'
              AND (
                  (g.subject_type = 'employee' AND g.subject_id = CAST(e.id AS TEXT)) OR
                  (g.subject_type = 'role' AND g.subject_id = e.role_id) OR
                  (g.subject_type = 'department' AND g.subject_id = CAST(e.department_id AS TEXT))
              )
        ) THEN 0

        -- 2. Explicit ALLOW grant (if no deny)
        WHEN EXISTS (
            SELECT 1 FROM doc_grants g
            WHERE g.document_id = d.id
              AND g.effect = 'ALLOW'
              AND (
                  (g.subject_type = 'employee' AND g.subject_id = CAST(e.id AS TEXT)) OR
                  (g.subject_type = 'role' AND g.subject_id = e.role_id) OR
                  (g.subject_type = 'department' AND g.subject_id = CAST(e.department_id AS TEXT))
              )
        ) THEN 1

        -- 3. Level 0 (Public) & Level 1 (Internal): accessible by all authenticated employees
        WHEN d.classification_level <= 1 THEN 1

        -- 4. Level 2 (Confidential): same department with seniority >= 2 OR executive
        WHEN d.classification_level = 2 THEN
            CASE
                WHEN e.is_executive = 1 THEN 1
                WHEN e.department_id = d.owner_department_id AND e.seniority >= 2 THEN 1
                ELSE 0
            END

        -- 5. Level 3 (Restricted): same department with seniority >= 3 OR executive (if exec_readable = 1)
        WHEN d.classification_level = 3 THEN
            CASE
                WHEN e.is_executive = 1 AND d.exec_readable = 1 THEN 1
                WHEN e.department_id = d.owner_department_id AND e.seniority >= 3 THEN 1
                ELSE 0
            END

        ELSE 0
    END AS has_access
FROM employees e
CROSS JOIN documents d;
