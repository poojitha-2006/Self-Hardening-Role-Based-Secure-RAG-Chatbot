import json
from collections import Counter
from pathlib import Path

import pytest
import yaml
from requests import Response

from scripts.chunk_documents import employee_can_read, export_chunks, split_text
from scripts.curate_corpus import (
    deduplicate_and_filter,
    sample_legal,
    source_group,
    truncate_row,
    validate_source_row,
)
from redteam.build_attack_dataset import (
    REMOTE_SOURCES,
    direct_request_rows,
    handwritten_rows,
    legacy_handwritten_rows,
    load_public_sources,
)
from eval.build_benign_set import build_overblocking, build_unauthorized_probes, validate_answer_span
from scripts.fetch_data import (
    PublicSession,
    department_folder,
    discover_catalog,
    extract_html,
    load_catalog,
    process_synthetic,
    save_content,
    serialize_jsonl,
)

ROOT = Path(__file__).resolve().parents[1]


def load_json(relative_path: str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def test_original_catalog_is_discovered_and_unchanged_shape() -> None:
    path = discover_catalog(None)
    catalog = load_catalog(path)
    assert path == ROOT / "documents/catalog.json"
    assert len(catalog["records"]) == 68
    assert sum(bool(record.get("url")) for record in catalog["records"]) == 63
    assert sum(record.get("record_type") == "synthetic_test_record" for record in catalog["records"]) == 5


def test_source_folders_separate_external_datasets_and_synthetic_fixtures() -> None:
    assert department_folder({"record_type": "dataset_reference"}) == "external_datasets"
    assert department_folder({"department": "People / HR"}) == "people_hr"


def test_html_extraction_prefers_main_and_removes_non_content() -> None:
    response = Response()
    response._content = b"<html><body><nav>Navigation filler</nav><main><h1>Policy</h1><p>" + b"Useful policy text. " * 10 + b"</p><script>ignored script</script></main></body></html>"
    text = extract_html(response)
    assert "Useful policy text" in text
    assert "Navigation filler" not in text
    assert "ignored script" not in text


def test_output_path_and_metadata_are_preserved(tmp_path: Path) -> None:
    record = {
        "document_id": "SIM-HR-001", "title": "Example", "department": "People / HR",
        "source": "Synthetic fixture", "url": None, "classification": "Confidential",
        "record_type": "synthetic_test_record",
    }
    metadata = {"document_id": "SIM-HR-001", "classification": "Confidential", "is_synthetic": True}
    path, digest = save_content(record, metadata, "Fictional content " * 10, tmp_path)
    saved = Path(path)
    assert saved.is_absolute()
    assert saved.is_file()
    assert len(digest) == 64
    assert "\"is_synthetic\": true" in saved.read_text(encoding="utf-8")


def test_synthetic_fixture_is_instrumented_and_not_misrepresented_as_download() -> None:
    catalog = load_catalog(ROOT / "documents/catalog.json")
    record = next(item for item in catalog["records"] if item["document_id"] == "SIM-HR-001")
    policy = yaml.safe_load((ROOT / "data/role_policy.yaml").read_text(encoding="utf-8"))
    canaries = {item["document_id"]: item["token"] for item in load_json("data/canaries.json")["tokens"]}
    result = process_synthetic(record, policy, ROOT / "data/documents", canaries)
    path = ROOT / result.content_path
    assert result.status == "synthetic_fixture"
    assert path.parent.name == "synthetic_fixtures"
    assert canaries[record["document_id"]] in path.read_text(encoding="utf-8")


def test_role_policy_blocks_cross_department_and_seniority_escalation() -> None:
    employees = load_json("data/employees.json")["employees"]
    by_id = {employee["employee_id"]: employee for employee in employees}
    catalog = load_json("documents/catalog.json")["records"]
    by_doc = {record["document_id"]: record for record in catalog}
    policy = yaml.safe_load((ROOT / "data/role_policy.yaml").read_text(encoding="utf-8"))
    assert employee_can_read(by_doc["SIM-HR-001"], by_id["EMP-0007"], policy)
    assert not employee_can_read(by_doc["SIM-HR-001"], by_id["EMP-0003"], policy)
    assert not employee_can_read(by_doc["SIM-FIN-001"], by_id["EMP-0010"], policy)
    assert employee_can_read(by_doc["GL-008"], by_id["EMP-0010"], policy)


def test_chunk_export_excludes_unauthorized_text_before_indexing(tmp_path: Path) -> None:
    employees = load_json("data/employees.json")
    policy = yaml.safe_load((ROOT / "data/role_policy.yaml").read_text(encoding="utf-8"))
    restricted_path = tmp_path / "hr.md"
    restricted_path.write_text("FICTIONAL HR-ONLY TEXT " * 12, encoding="utf-8")
    public_path = tmp_path / "public.md"
    public_path.write_text("Public handbook text " * 12, encoding="utf-8")
    processed = {"records": [
        {"document_id": "SIM-HR-001", "classification": "Confidential", "download_status": "synthetic_fixture",
         "content_path": str(restricted_path), "department": "People / HR", "record_type": "synthetic_test_record"},
        {"document_id": "GL-008", "classification": "Public", "download_status": "downloaded",
         "content_path": str(public_path), "department": "Engineering", "record_type": "public_reference"},
        {"document_id": "BAD-001", "classification": "Public", "download_status": "failed",
         "content_path": str(restricted_path), "department": "People / HR", "record_type": "public_reference"},
    ]}
    engineer_chunks = export_chunks("EMP-0010", processed, employees, policy, chunk_size=100, overlap=10)
    hr_chunks = export_chunks("EMP-0007", processed, employees, policy, chunk_size=100, overlap=10)
    assert {chunk["document_id"] for chunk in engineer_chunks} == {"GL-008"}
    assert {chunk["document_id"] for chunk in hr_chunks} == {"GL-008", "SIM-HR-001"}
    assert all(chunk["document_id"] != "BAD-001" for chunk in engineer_chunks + hr_chunks)


def test_chunks_are_nonempty_and_catalog_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    chunks = split_text("one two three four five six", chunk_size=10, overlap=2)
    assert all(chunk.strip() for chunk in chunks)
    assert "" not in split_text(" \n ", chunk_size=10, overlap=0)
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps({"records": [
        {"document_id": "DUP-1"}, {"document_id": "DUP-1"}
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate document_id"):
        load_catalog(catalog_path)


def test_retry_after_parses_seconds_and_invalid_values() -> None:
    assert PublicSession._retry_after_seconds("3") == 3
    assert PublicSession._retry_after_seconds("not-a-date") is None


def test_dataset_reference_is_never_treated_as_downloadable_content() -> None:
    record = next(item for item in load_json("documents/catalog.json")["records"] if item["document_id"] == "GL-062")
    assert record["record_type"] == "dataset_reference"
    assert record["local_content"] is False
    assert record["url"].endswith("/cuad")


def test_jsonl_escapes_unicode_line_separators_without_changing_text() -> None:
    original = "alpha\u0085beta\u2028gamma\u2029delta"
    line = serialize_jsonl({"text": original})
    assert len(line.splitlines()) == 1
    assert json.loads(line)["text"] == original


def _curation_row(tmp_path: Path, document_id: str, topic: str, source: str, text: str) -> dict:
    raw = tmp_path / f"{document_id}.raw"
    processed = tmp_path / f"{document_id}.md"
    raw.write_text("raw source", encoding="utf-8")
    processed.write_text(text, encoding="utf-8")
    return {
        "document_id": document_id,
        "text": text,
        "metadata": {
            "document_id": document_id,
            "title": document_id,
            "source_organization": "The Atticus Project" if source in {"CUAD", "MAUD"} else source,
            "source_url": f"https://example.test/{document_id}",
            "department": topic,
            "license_or_usage": "Test fixture license",
            "classification": "Public",
            "content_hash": __import__("hashlib").sha256(text.encode("utf-8")).hexdigest(),
            "raw_path": str(raw),
            "processed_text_path": str(processed),
        },
    }


def test_curator_drops_short_and_duplicate_text_and_merges_gitlab_sources(tmp_path: Path) -> None:
    text = "A valid public policy passage. " * 20
    first = _curation_row(tmp_path, "GL-A", "Engineering and software development", "GitLab", text)
    duplicate = _curation_row(tmp_path, "GL-B", "Engineering and software development", "GitLab Handbook", text)
    short = _curation_row(tmp_path, "GL-SHORT", "Engineering and software development", "GitLab", "short")
    selected, counts = deduplicate_and_filter([first, duplicate, short])
    assert [row["document_id"] for row in selected] == ["GL-A"]
    assert counts["duplicate_dropped"] == 1
    assert counts["short_dropped"] == 1
    assert selected[0]["metadata"]["source"] == "GitLab Handbook"


def test_curator_legal_sampling_is_seeded_and_keeps_legal_handbook(tmp_path: Path) -> None:
    text = "A licensed contract clause. " * 20
    rows = []
    for index in range(60):
        row = _curation_row(tmp_path, f"CUAD-{index}", "Legal, contracts and compliance", "CUAD", text + str(index))
        row["metadata"]["title"] = (f"Merger Agreement {index}" if index < 10 else
                                      f"Services Agreement {index}" if index < 20 else f"Other Contract {index}")
        rows.append(row)
    rows += [_curation_row(tmp_path, f"MAUD-{index}", "Legal, contracts and compliance", "MAUD", text + f"m{index}")
             for index in range(35)]
    rows.append(_curation_row(tmp_path, "GL-LEGAL", "Legal, contracts and compliance", "GitLab Handbook", text + "handbook"))
    selected, counts = sample_legal(rows, seed=17)
    selected_again, _ = sample_legal(rows, seed=17)
    assert len(selected) == 81
    assert counts == {"legal_handbook_kept": 1, "cuad_available": 60, "cuad_kept": 50,
                      "cuad_type_strata": {"merger_acquisition": 10, "other_or_unspecified": 30,
                                           "services_consulting": 10}, "maud_available": 35,
                      "maud_kept": 30, "maud_type_strata": {"merger_agreement_dataset": 30}}
    assert [row["document_id"] for row in selected] == [row["document_id"] for row in selected_again]
    assert any(row["document_id"] == "GL-LEGAL" for row in selected)


def test_curator_truncates_only_the_curated_copy_and_sets_flag(tmp_path: Path) -> None:
    raw_text = "x" * 60_100
    row = _curation_row(tmp_path, "DOC-LONG", "Engineering and software development", "GitLab", raw_text)
    raw_path = Path(row["metadata"]["raw_path"])
    curated = truncate_row(row)
    assert len(curated["text"]) == 60_000
    assert curated["truncated"] is True
    assert curated["metadata"]["truncated"] is True
    assert curated["metadata"]["original_content_hash"] == row["metadata"]["content_hash"]
    assert raw_path.read_text(encoding="utf-8") == "raw source"  # The fixture's raw file is unchanged.


def test_curated_source_validator_checks_hash_and_source_paths(tmp_path: Path) -> None:
    row = _curation_row(tmp_path, "DOC-VALID", "Finance and accounting", "GitLab", "Finance guidance. " * 35)
    assert validate_source_row(row, root=tmp_path) == []
    broken = {**row, "metadata": {**row["metadata"], "raw_path": str(tmp_path / "missing.raw")}}
    assert any("raw_path does not exist" in error for error in validate_source_row(broken, root=tmp_path))
    bad_hash = {**row, "metadata": {**row["metadata"], "content_hash": "wrong"}}
    assert any("content_hash mismatch" in error for error in validate_source_row(bad_hash, root=tmp_path))


def test_attack_handwritten_set_is_synthetic_and_source_split_is_fixed() -> None:
    rows = handwritten_rows()
    assert len(rows) == 90
    assert {row["source"] for row in rows} == {"handwritten"}
    assert dict(Counter(row["split"] for row in rows)) == {"train": 75, "test": 15}
    assert {row["category"] for row in rows} == {
        "direct_prompt_injection", "jailbreak_roleplay", "role_escalation",
        "system_prompt_secret_extraction", "indirect_injection", "exfiltration_obfuscation",
        "direct_request",
    }
    expected_splits = {"Tensor Trust": "train", "Gandalf (Lakera)": "train",
                       "deepset/prompt-injections": "validation", "HackAPrompt": "test", "BIPIA": "test"}
    assert {source: config["split"] for source, config in REMOTE_SOURCES.items()} == expected_splits


def test_direct_request_attacks_cover_secret_facts_without_split_wording_leakage() -> None:
    rows = direct_request_rows()
    train = {row["text"] for row in rows if row["split"] == "train"}
    test = {row["text"] for row in rows if row["split"] == "test"}

    assert len(rows) == 30
    assert len(train) == len(test) == 15
    assert train.isdisjoint(test)
    assert all(row["category"] == "direct_request" for row in rows)
    assert all(row["target_role"] == "Analyst" for row in rows)
    assert all(not any(term in row["text"].casefold() for term in ("ignore", "jailbreak", "override"))
               for row in rows)


def test_legacy_handwritten_attacks_remain_a_separate_source() -> None:
    rows = legacy_handwritten_rows()
    assert len(rows) == 65
    assert {row["source"] for row in rows} == {"legacy_handwritten"}
    assert dict(Counter(row["split"] for row in rows)) == {"train": 46, "test": 19}
    assert all(row["license"] == "Original handwritten attacks from this project" for row in rows)


def test_attack_remote_sources_are_not_touched_without_authorization() -> None:
    manifest: dict = {"sources": {}}
    assert load_public_sources(False, manifest) == []
    assert set(manifest["sources"]) == set(REMOTE_SOURCES)
    assert all(item["status"] == "not_attempted_authorization_required" for item in manifest["sources"].values())


def test_benign_answer_span_requires_literal_source_text() -> None:
    source = "Security awareness training explains how to identify phishing messages."
    assert validate_answer_span({"question": "What does the training explain?",
                                 "answer_span": "how to identify phishing messages"}, source) == (
                                     "What does the training explain?", ["identify", "phishing", "messages"])
    assert validate_answer_span({"question": "What is explained?", "answer_span": "Users should click links."}, source) is None


def test_overblocking_queries_are_grounded_and_answerable() -> None:
    chunks = [{"document_id": "PUBLIC-1", "topic": "Cybersecurity/IT", "chunk_index": 2,
               "start_char": 10, "end_char": 130,
               "text": "Employees should report phishing messages to the security team. Security awareness is updated each year.",
               "metadata": {"classification": "Public"}}]
    rows = build_overblocking(chunks)
    assert rows
    assert all(row["should_be_refused"] is False for row in rows)
    assert all(row["answer_span"] in chunks[0]["text"] for row in rows)
    assert all(row["case_type"] == "over_blocking_test" for row in rows)


def test_unauthorized_probe_generator_defers_until_mapping_exists(tmp_path: Path) -> None:
    rows, note = build_unauthorized_probes(None)
    assert rows == []
    assert "deferred" in note
    missing_rows, missing_note = build_unauthorized_probes(tmp_path / "not-yet-created.json")
    assert missing_rows == []
    assert "not found" in missing_note
