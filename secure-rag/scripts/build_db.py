"""
secure-rag/scripts/build_db.py
==============================
Initializes the SQLite database (db/rag_access.db) with:
  1. Complete schema and v_access single-source-of-truth view.
  2. 12 enterprise departments (Executive, HR/People, Engineering, etc.).
  3. 4 classifications (Public 0, Internal 1, Confidential 2, Restricted 3).
  4. 48 roles and 48 employees reconciled from dataset_pipeline/data/employees.json.
  5. 386 curated documents from dataset_pipeline/data/curated/documents.jsonl,
     classified using source ownership and document-sensitivity rules.
  6. Demonstration grants (ALLOW and DENY) to verify policy overrides.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.rag.classification import classify_document as classify_curated_document

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
DB_DIR = ROOT / "db"
DB_PATH = DB_DIR / "rag_access.db"
SCHEMA_PATH = DB_DIR / "schema.sql"
CURATED_DOCS_PATH = WORKSPACE_ROOT / "dataset_pipeline" / "data" / "curated" / "documents.jsonl"
EMPLOYEES_PATH = WORKSPACE_ROOT / "dataset_pipeline" / "data" / "employees.json"


def hash_password(password: str, salt: bytes = b"secure_rag_salt_2026") -> str:
    """Hash password using PBKDF2-HMAC-SHA256 (compatible with standard libraries)."""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return f"pbkdf2:sha256:100000${salt.hex()}${dk.hex()}"


DEPARTMENTS = [
    (1, "Executive", "executive"),
    (2, "HR/People", "hr_people"),
    (3, "Engineering", "engineering"),
    (4, "Product", "product"),
    (5, "AI/Data", "ai_data"),
    (6, "Cybersecurity/IT", "cybersecurity_it"),
    (7, "Finance", "finance"),
    (8, "Legal", "legal"),
    (9, "Sales", "sales"),
    (10, "Marketing", "marketing"),
    (11, "Customer Support", "customer_support"),
    (12, "Procurement/Operations", "procurement_operations"),
]

DEPT_NAME_TO_ID = {name: dept_id for dept_id, name, _ in DEPARTMENTS}
# Aliases for dataset matching
DEPT_NAME_TO_ID["People / HR"] = 2
DEPT_NAME_TO_ID["AI / Data"] = 5
DEPT_NAME_TO_ID["Cybersecurity"] = 6
DEPT_NAME_TO_ID["Operations / IT"] = 12

CLASSIFICATIONS = [
    (0, "Public", "Freely accessible to all employees and external public."),
    (1, "Internal", "Accessible to all authenticated enterprise employees."),
    (2, "Confidential", "Departmental sensitive; requires same department (seniority >= 2) or executive."),
    (3, "Restricted", "Crown-jewel assets; requires same department (seniority >= 3) or executive (if exec_readable)."),
]

# 48 Roles across 12 departments: 3-4 roles per dept, seniority 1-5, is_executive
ROLES = [
    # 1. Executive
    ("CEO", 1, "Chief Executive Officer", 5, 1),
    ("COO", 1, "Chief Operating Officer", 5, 1),
    ("CTO", 1, "Chief Technology Officer", 5, 1),
    ("CFO", 1, "Chief Financial Officer", 5, 1),
    # 2. HR/People
    ("HR_ASSOCIATE", 2, "HR Associate", 1, 0),
    ("RECRUITER", 2, "Recruiter", 2, 0),
    ("HR_MANAGER", 2, "HR Manager", 4, 0),
    ("CHRO", 2, "Chief Human Resources Officer", 5, 1),
    # 3. Engineering
    ("JUNIOR_DEVELOPER", 3, "Junior Developer", 1, 0),
    ("SENIOR_DEVELOPER", 3, "Senior Developer", 3, 0),
    ("TECH_LEAD", 3, "Tech Lead", 3, 0),
    ("ENGINEERING_MANAGER", 3, "Engineering Manager", 4, 0),
    # 4. Product
    ("ASSOCIATE_PM", 4, "Associate Product Manager", 1, 0),
    ("PRODUCT_MANAGER", 4, "Product Manager", 2, 0),
    ("SENIOR_PM", 4, "Senior Product Manager", 3, 0),
    ("DIRECTOR_PRODUCT", 4, "Director of Product", 4, 0),
    # 5. AI/Data
    ("DATA_ANALYST", 5, "Data Analyst", 2, 0),
    ("DATA_SCIENTIST", 5, "Data Scientist", 3, 0),
    ("ML_ENGINEER", 5, "Machine Learning Engineer", 3, 0),
    ("AI_LEAD", 5, "AI Lead", 4, 0),
    # 6. Cybersecurity/IT
    ("SECURITY_ANALYST", 6, "Security Analyst", 2, 0),
    ("SECURITY_ENGINEER", 6, "Security Engineer", 3, 0),
    ("SOC_MANAGER", 6, "SOC Manager", 4, 0),
    ("CISO", 6, "Chief Information Security Officer", 5, 1),
    # 7. Finance
    ("ACCOUNTANT", 7, "Accountant", 2, 0),
    ("FINANCIAL_ANALYST", 7, "Financial Analyst", 2, 0),
    ("SR_FINANCIAL_ANALYST", 7, "Senior Financial Analyst", 3, 0),
    ("FINANCE_MANAGER", 7, "Finance Manager", 4, 0),
    # 8. Legal
    ("LEGAL_ASSOCIATE", 8, "Legal Associate", 1, 0),
    ("CONTRACT_SPECIALIST", 8, "Contract Specialist", 2, 0),
    ("CONTRACT_MANAGER", 8, "Contract Manager", 4, 0),
    ("GENERAL_COUNSEL", 8, "General Counsel", 5, 1),
    # 9. Sales
    ("SALES_REP", 9, "Sales Representative", 1, 0),
    ("ACCOUNT_EXECUTIVE", 9, "Account Executive", 2, 0),
    ("SR_ACCOUNT_EXECUTIVE", 9, "Senior Account Executive", 3, 0),
    ("SALES_MANAGER", 9, "Sales Manager", 4, 0),
    # 10. Marketing
    ("MARKETING_ASSOCIATE", 10, "Marketing Associate", 1, 0),
    ("CONTENT_STRATEGIST", 10, "Content Strategist", 2, 0),
    ("CAMPAIGN_MANAGER", 10, "Campaign Manager", 4, 0),
    ("MARKETING_DIRECTOR", 10, "Marketing Director", 4, 0),
    # 11. Customer Support
    ("SUPPORT_SPECIALIST", 11, "Support Specialist", 1, 0),
    ("SR_SUPPORT_SPECIALIST", 11, "Senior Support Specialist", 2, 0),
    ("SUPPORT_LEAD", 11, "Support Lead", 3, 0),
    ("SUPPORT_MANAGER", 11, "Support Manager", 4, 0),
    # 12. Procurement/Operations
    ("IT_SUPPORT", 12, "IT Support Specialist", 1, 0),
    ("PROCUREMENT_SPECIALIST", 12, "Procurement Specialist", 2, 0),
    ("SYSTEM_ADMINISTRATOR", 12, "System Administrator", 3, 0),
    ("IT_MANAGER", 12, "IT Manager", 4, 0),
]

ROLE_MAP = {r[0]: r for r in ROLES}

# 48 Fictional Employees: 1 employee per role
EMPLOYEES = [
    # Executive
    ("EMP-0001", "alice_ceo", "Alice Vance", "CEO"),
    ("EMP-0002", "david_coo", "David Chen", "COO"),
    ("EMP-0003", "elena_cto", "Elena Rostova", "CTO"),
    ("EMP-0004", "marcus_cfo", "Marcus Sterling", "CFO"),
    # HR/People
    ("EMP-0005", "hannah_hr", "Hannah Abbott", "HR_ASSOCIATE"),
    ("EMP-0006", "rachel_recruiter", "Rachel Green", "RECRUITER"),
    ("EMP-0007", "hector_hr_mgr", "Hector Salazar", "HR_MANAGER"),
    ("EMP-0008", "clara_chro", "Clara Oswald", "CHRO"),
    # Engineering
    ("EMP-0009", "jack_dev_jr", "Jack Harper", "JUNIOR_DEVELOPER"),
    ("EMP-0010", "sam_dev_sr", "Samantha Carter", "SENIOR_DEVELOPER"),
    ("EMP-0011", "tara_lead", "Tara Maclay", "TECH_LEAD"),
    ("EMP-0012", "eric_eng_mgr", "Eric Foreman", "ENGINEERING_MANAGER"),
    # Product
    ("EMP-0036", "peter_pm_jr", "Peter Parker", "ASSOCIATE_PM"),
    ("EMP-0037", "paula_pm", "Paula Brooks", "PRODUCT_MANAGER"),
    ("EMP-0038", "priya_pm_sr", "Priya Sharma", "SENIOR_PM"),
    ("EMP-0039", "dan_prod_dir", "Daniel Jackson", "DIRECTOR_PRODUCT"),
    # AI/Data
    ("EMP-0013", "diana_data", "Diana Prince", "DATA_ANALYST"),
    ("EMP-0014", "dan_data_sci", "Daniel Rand", "DATA_SCIENTIST"),
    ("EMP-0015", "maya_ml", "Maya Lopez", "ML_ENGINEER"),
    ("EMP-0016", "alan_ai_lead", "Alan Turing-Smith", "AI_LEAD"),
    # Cybersecurity/IT
    ("EMP-0017", "sarah_sec_analyst", "Sarah Connor", "SECURITY_ANALYST"),
    ("EMP-0018", "steve_sec_eng", "Steve Rogers", "SECURITY_ENGINEER"),
    ("EMP-0019", "sean_soc_mgr", "Sean Archer", "SOC_MANAGER"),
    ("EMP-0020", "chloe_ciso", "Chloe Decker", "CISO"),
    # Finance
    ("EMP-0021", "frank_accountant", "Frank Castle", "ACCOUNTANT"),
    ("EMP-0022", "fiona_fin_analyst", "Fiona Gallagher", "FINANCIAL_ANALYST"),
    ("EMP-0040", "felix_sr_fin", "Felix Leiter", "SR_FINANCIAL_ANALYST"),
    ("EMP-0023", "farah_fin_mgr", "Farah Nazir", "FINANCE_MANAGER"),
    # Legal
    ("EMP-0024", "leo_legal_assoc", "Leo Fitz", "LEGAL_ASSOCIATE"),
    ("EMP-0041", "lara_contract", "Lara Croft", "CONTRACT_SPECIALIST"),
    ("EMP-0025", "carlos_contract_mgr", "Carlos Olivera", "CONTRACT_MANAGER"),
    ("EMP-0026", "grace_gc", "Grace Van Pelt", "GENERAL_COUNSEL"),
    # Sales
    ("EMP-0027", "sara_sdr", "Sara Lance", "SALES_REP"),
    ("EMP-0028", "adam_ae", "Adam Jensen", "ACCOUNT_EXECUTIVE"),
    ("EMP-0042", "samuel_sr_ae", "Samuel Drake", "SR_ACCOUNT_EXECUTIVE"),
    ("EMP-0029", "sophia_sales_mgr", "Sophia Hess", "SALES_MANAGER"),
    # Marketing
    ("EMP-0030", "milo_mkt_assoc", "Milo Thatch", "MARKETING_ASSOCIATE"),
    ("EMP-0043", "claire_content", "Claire Bennet", "CONTENT_STRATEGIST"),
    ("EMP-0031", "colin_campaign_mgr", "Colin Robinson", "CAMPAIGN_MANAGER"),
    ("EMP-0032", "mona_mkt_dir", "Mona Sax", "MARKETING_DIRECTOR"),
    # Customer Support
    ("EMP-0044", "cody_support", "Cody Martin", "SUPPORT_SPECIALIST"),
    ("EMP-0045", "tina_sr_support", "Tina Cohen-Chang", "SR_SUPPORT_SPECIALIST"),
    ("EMP-0046", "leo_support_lead", "Leo Valdez", "SUPPORT_LEAD"),
    ("EMP-0047", "maya_support_mgr", "Maya Lin", "SUPPORT_MANAGER"),
    # Procurement/Operations
    ("EMP-0033", "oscar_it_support", "Oscar Martinez", "IT_SUPPORT"),
    ("EMP-0048", "piper_procure", "Piper Halliwell", "PROCUREMENT_SPECIALIST"),
    ("EMP-0034", "simon_sysadmin", "Simon Tam", "SYSTEM_ADMINISTRATOR"),
    ("EMP-0035", "ian_ops_mgr", "Ian Malcolm", "IT_MANAGER"),
]


def classify_document(doc: Dict[str, Any]) -> Tuple[int, int]:
    """
    Classify document into (classification_level, exec_readable).
    Levels: 0=Public, 1=Internal, 2=Confidential, 3=Restricted.
    """
    result = classify_curated_document(doc)
    level = {"Public": 0, "Internal": 1, "Confidential": 2, "Restricted": 3}[
        result["classification"]
    ]
    return level, int(result["exec_readable"])


def build_database() -> None:
    print(f"Building SQLite database at: {DB_PATH}")
    DB_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    # Apply Schema
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())

    # Insert Departments
    conn.executemany("INSERT INTO departments (id, name, slug) VALUES (?, ?, ?);", DEPARTMENTS)

    # Insert Classifications
    conn.executemany("INSERT INTO classifications (level, name, description) VALUES (?, ?, ?);", CLASSIFICATIONS)

    # Insert Roles
    role_tuples = [(r[0], r[1], r[2], r[3], r[4]) for r in ROLES]
    conn.executemany("INSERT INTO roles (id, department_id, name, seniority, is_executive) VALUES (?, ?, ?, ?, ?);", role_tuples)

    # Insert Employees
    default_pw_hash = hash_password("password")
    employee_tuples = []
    for emp_id, username, full_name, role_id in EMPLOYEES:
        role_info = ROLE_MAP[role_id]
        dept_id = role_info[1]
        seniority = role_info[3]
        is_exec = role_info[4]
        employee_tuples.append((
            emp_id, username, full_name, default_pw_hash, role_id, dept_id, seniority, is_exec
        ))
    conn.executemany("""
        INSERT INTO employees (employee_id, username, full_name, password_hash, role_id, department_id, seniority, is_executive)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
    """, employee_tuples)

    # Load Curated Documents
    if not CURATED_DOCS_PATH.is_file():
        raise FileNotFoundError(f"Curated documents not found at: {CURATED_DOCS_PATH}")

    with open(CURATED_DOCS_PATH, encoding="utf-8") as f:
        doc_rows = [json.loads(line) for line in f if line.strip()]

    print(f"Loaded {len(doc_rows)} curated documents from {CURATED_DOCS_PATH}")

    now_iso = datetime.now(timezone.utc).isoformat()
    doc_tuples = []
    dist = {0: 0, 1: 0, 2: 0, 3: 0}

    for doc in doc_rows:
        doc_key = doc["document_id"]
        meta = doc.get("metadata", {})
        title = meta.get("title", doc_key)
        source = meta.get("source", "Unknown")
        url = meta.get("source_url")
        license_info = meta.get("license_or_usage")
        classification_data = classify_curated_document(doc)
        dept_name = classification_data["owner_department"]
        dept_id = DEPT_NAME_TO_ID.get(dept_name, 3) # default Engineering
        content_hash = meta.get("content_hash", "")

        level, exec_readable = classify_document(doc)
        dist[level] += 1

        doc_tuples.append((
            doc_key, title, source, url, license_info, dept_id, level, exec_readable, content_hash, 1, now_iso
        ))

    conn.executemany("""
        INSERT INTO documents (doc_key, title, source, url, license, owner_department_id, classification_level, exec_readable, content_hash, is_simulated_classification, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, doc_tuples)

    # Insert Demonstration Grants
    # 1. ALLOW grant: Grant Legal Contract Manager (EMP-0025, role CONTRACT_MANAGER) access to a Finance document
    fin_doc = conn.execute("SELECT id, doc_key FROM documents WHERE owner_department_id = 7 LIMIT 1;").fetchone()
    if fin_doc:
        conn.execute("""
            INSERT INTO doc_grants (document_id, subject_type, subject_id, effect, reason)
            VALUES (?, 'role', 'CONTRACT_MANAGER', 'ALLOW', 'Cross-departmental legal audit of finance document');
        """, (fin_doc[0],))

    # 2. DENY grant: Explicitly deny Senior Developer (EMP-0010, role SENIOR_DEVELOPER) access to a specific Engineering doc
    eng_doc = conn.execute("SELECT id, doc_key FROM documents WHERE owner_department_id = 3 LIMIT 1;").fetchone()
    if eng_doc:
        conn.execute("""
            INSERT INTO doc_grants (document_id, subject_type, subject_id, effect, reason)
            VALUES (?, 'role', 'SENIOR_DEVELOPER', 'DENY', 'Explicit security quarantine / project reassignment restriction');
        """, (eng_doc[0],))

    conn.commit()

    # Verify counts
    emp_count = conn.execute("SELECT COUNT(*) FROM employees;").fetchone()[0]
    doc_count = conn.execute("SELECT COUNT(*) FROM documents;").fetchone()[0]
    grant_count = conn.execute("SELECT COUNT(*) FROM doc_grants;").fetchone()[0]
    v_access_count = conn.execute("SELECT COUNT(*) FROM v_access;").fetchone()[0]

    print("\n=== Database Build Complete ===")
    print(f"Employees: {emp_count}")
    print(f"Documents: {doc_count}")
    print(f"Grants: {grant_count}")
    print(f"v_access user-document pairs: {v_access_count}")
    print("\nSimulated Document Classification Distribution:")
    total_docs = len(doc_rows)
    for lvl, name, desc in CLASSIFICATIONS:
        count = dist[lvl]
        pct = (count / total_docs) * 100
        print(f"  Level {lvl} ({name:12}): {count:3d} docs ({pct:5.1f}%) — {desc}")

    conn.close()


if __name__ == "__main__":
    build_database()
