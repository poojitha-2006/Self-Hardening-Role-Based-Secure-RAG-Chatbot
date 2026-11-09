"""Download public references from the simulated enterprise document catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import time
import zipfile
import io
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import requests
import yaml
from bs4 import BeautifulSoup
from requests import Response
from requests.exceptions import RequestException

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger("fetch_data")
USER_AGENT = os.getenv("DATASET_USER_AGENT", "EnterpriseKnowledgeDataset/1.0 (public-document research)")
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT")
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MIN_USABLE_CHARACTERS = 250
SUSPICIOUS_CHARACTERS = 600

DEPARTMENT_FOLDERS = {
    "Company-wide": "company",
    "Executive": "company",
    "People / HR": "people_hr",
    "Engineering": "engineering",
    "Product": "product",
    "AI / Data": "ai_data",
    "Cybersecurity": "cybersecurity",
    "Finance": "finance",
    "Legal": "legal",
    "Sales": "sales",
    "Marketing": "marketing",
    "Operations / IT": "operations_it",
    "Customer Support": "customer_support",
    "Procurement and Operations": "operations_it",
}

RAW_SOURCE_FOLDERS = {
    "GitLab Handbook": "gitlab_handbook",
    "GitLab Handbook sitemap": "gitlab_handbook",
    "SEC EDGAR": "sec_edgar",
    "NIST": "nist",
    "CISA": "cisa",
    "The Atticus Project": "cuad",
}

HANDBOOK_PREFIX_DEPARTMENTS = {
    "/handbook/company/": "Executive and company strategy",
    "/handbook/ceo/": "Executive and company strategy",
    "/handbook/board-meetings/": "Executive and company strategy",
    "/handbook/leadership/": "Executive and company strategy",
    "/handbook/people-group/": "People and HR",
    "/handbook/people-policies/": "People and HR",
    "/handbook/hiring/": "People and HR",
    "/handbook/labor-and-employment-notices/": "People and HR",
    "/handbook/total-rewards/": "People and HR",
    "/job-description-library/people-group/": "People and HR",
    "/handbook/engineering/": "Engineering and software development",
    "/handbook/product/": "Product management",
    "/handbook/product-development/": "Product management",
    "/handbook/enterprise-data/": "AI, data and analytics",
    "/handbook/eta/": "AI, data and analytics",
    "/handbook/security/": "Cybersecurity and IT",
    "/handbook/it/": "Cybersecurity and IT",
    "/handbook/business-technology/": "Procurement and operations",
    "/handbook/finance/": "Finance and accounting",
    "/handbook/legal/": "Legal, contracts and compliance",
    "/handbook/acquisitions/": "Legal, contracts and compliance",
    "/handbook/sales/": "Sales and customer success",
    "/handbook/customer-experience/": "Sales and customer success",
    "/handbook/solutions-architects/": "Sales and customer success",
    "/handbook/alliances/": "Sales and customer success",
    "/handbook/support/": "Customer support",
    "/handbook/marketing/": "Marketing and communications",
    "/handbook/communication/": "Marketing and communications",
    "/handbook/procurement/": "Procurement and operations",
}

DEPARTMENT_OUTPUT_FOLDERS = {
    "Executive and company strategy": "company",
    "People and HR": "people_hr",
    "Engineering and software development": "engineering",
    "Product management": "product",
    "AI, data and analytics": "ai_data",
    "Cybersecurity and IT": "cybersecurity",
    "Finance and accounting": "finance",
    "Legal, contracts and compliance": "legal",
    "Sales and customer success": "sales",
    "Marketing and communications": "marketing",
    "Procurement and operations": "operations_it",
    "Customer support": "customer_support",
}


@dataclass
class DownloadResult:
    status: str
    content_path: str | None = None
    http_status: int | None = None
    resolved_url: str | None = None
    extracted_characters: int = 0
    content_hash: str | None = None
    file_hash: str | None = None
    error: str | None = None
    raw_path: str | None = None
    raw_hash: str | None = None
    processed_text_path: str | None = None
    extracted_text: str | None = None
    file_format: str | None = None
    publication_date: str | None = None


class DownloadError(Exception):
    def __init__(self, message: str, status: int | None = None, resolved_url: str | None = None):
        super().__init__(message)
        self.status = status
        self.resolved_url = resolved_url


class PublicSession:
    def __init__(self, timeout: float, delay: float, retries: int):
        self.timeout = timeout
        self.delay = delay
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/json,application/pdf,*/*;q=0.5"})
        self.robots: dict[str, RobotFileParser | None] = {}
        self.last_request_at = 0.0

    def _wait(self) -> None:
        remaining = self.delay - (time.monotonic() - self.last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    @staticmethod
    def _origin(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def _fetch_robots(self, url: str) -> RobotFileParser | None:
        origin = self._origin(url)
        if origin in self.robots:
            return self.robots[origin]
        robots_url = f"{origin}/robots.txt"
        try:
            self._wait()
            response = self.session.get(robots_url, timeout=self.timeout, allow_redirects=True)
            self.last_request_at = time.monotonic()
            if response.status_code == 404 or response.status_code == 410:
                self.robots[origin] = None
                return None
            response.raise_for_status()
            parser = RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(response.text.splitlines())
            self.robots[origin] = parser
            return parser
        except RequestException as exc:
            raise DownloadError(f"Could not verify robots.txt at {robots_url}: {exc}") from exc

    def get(self, url: str, source: str) -> Response:
        parser = self._fetch_robots(url)
        if parser is not None and not parser.can_fetch(USER_AGENT.split("/")[0], url):
            raise DownloadError(f"Disallowed by robots.txt: {url}")

        if source == "SEC EDGAR" and not SEC_USER_AGENT:
            raise DownloadError("SEC requests require a real contact User-Agent; set SEC_USER_AGENT before enabling SEC downloads")
        headers = {"User-Agent": SEC_USER_AGENT} if source == "SEC EDGAR" else None
        for attempt in range(self.retries + 1):
            self._wait()
            try:
                response = self.session.get(url, timeout=self.timeout, allow_redirects=True, headers=headers)
                self.last_request_at = time.monotonic()
            except RequestException as exc:
                if attempt >= self.retries:
                    raise DownloadError(f"Network request failed after {attempt + 1} attempts: {exc}") from exc
                time.sleep(min(2**attempt, 30))
                continue

            if response.status_code in RETRYABLE_STATUS and attempt < self.retries:
                retry_after = response.headers.get("Retry-After")
                wait = self._retry_after_seconds(retry_after)
                time.sleep(wait if wait is not None else min(2**attempt, 30))
                continue
            if response.status_code >= 400:
                raise DownloadError(
                    f"HTTP {response.status_code} for {url}",
                    status=response.status_code,
                    resolved_url=response.url,
                )
            return response
        raise DownloadError(f"Request failed after {self.retries + 1} attempts: {url}")

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(0.0, min(float(value), 120.0))
        except ValueError:
            try:
                retry_time = parsedate_to_datetime(value)
                return max(0.0, min((retry_time - datetime.now(timezone.utc)).total_seconds(), 120.0))
            except (TypeError, ValueError, OverflowError):
                return None


def discover_handbook_sitemap(client: PublicSession, existing_records: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    sitemap_url = "https://handbook.gitlab.com/sitemap.xml"
    response = client.get(sitemap_url, "GitLab Handbook sitemap")
    try:
        root = ElementTree.fromstring(response.content)
    except ElementTree.ParseError as exc:
        raise DownloadError(f"Malformed GitLab Handbook sitemap: {exc}", response.status_code, response.url) from exc
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    existing_urls = {record.get("url") for record in existing_records if record.get("url")}
    existing_ids = {record.get("document_id") for record in existing_records}
    buckets: dict[str, deque[dict[str, Any]]] = {}
    for node in root.findall("s:url", namespace):
        loc = node.findtext("s:loc", namespaces=namespace)
        if not loc or loc in existing_urls:
            continue
        parts = urlsplit(loc)
        if parts.scheme != "https" or parts.hostname != "handbook.gitlab.com" or parts.query or parts.fragment:
            continue
        if Path(parts.path).suffix.lower() in {".xml", ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf"}:
            continue
        department = inferred_department(loc)
        doc_id = "GL-SITEMAP-" + hashlib.sha256(loc.encode("utf-8")).hexdigest()[:14].upper()
        if doc_id in existing_ids:
            continue
        record = {
            "document_id": doc_id,
            "title": loc.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title() or "GitLab Handbook",
            "department": department,
            "source": "GitLab Handbook sitemap",
            "source_organization": "GitLab",
            "url": loc,
            "classification": "Public",
            "record_type": "public_handbook_document",
            "local_content": False,
            "license_or_usage": license_notes({"source": "GitLab Handbook"}),
            "discovered_from": sitemap_url,
            "source_last_modified": node.findtext("s:lastmod", namespaces=namespace),
        }
        buckets.setdefault(department, deque()).append(record)
        existing_urls.add(loc)
        existing_ids.add(doc_id)
    selected: list[dict[str, Any]] = []
    while len(selected) < limit and buckets:
        for department in list(buckets):
            if buckets[department]:
                selected.append(buckets[department].popleft())
                if len(selected) >= limit:
                    break
            else:
                del buckets[department]
    LOG.info("Discovered %d new department-balanced handbook URLs from sitemap", len(selected))
    return selected


def import_atticus_archives(
    records: list[dict[str, Any]], client: PublicSession, raw_dir: Path, processed_dir: Path
) -> list[dict[str, Any]]:
    sources = {
        "CUAD": ("https://raw.githubusercontent.com/The-Atticus-Project/cuad/main/data.zip", "cuad"),
        "MAUD": ("https://raw.githubusercontent.com/The-Atticus-Project/maud/main/data.zip", "maud"),
    }
    existing_ids = {record.get("document_id") for record in records}
    added: list[dict[str, Any]] = []
    for dataset_name, (archive_url, raw_folder) in sources.items():
        archive_path = raw_dir / raw_folder / f"{dataset_name.lower()}_source.zip"
        if archive_path.is_file():
            archive_bytes = archive_path.read_bytes()
            archive_http_status = None
            resolved_archive_url = archive_url
        else:
            response = client.get(archive_url, "The Atticus Project")
            archive_bytes = response.content
            archive_http_status = response.status_code
            resolved_archive_url = response.url
            atomic_write_bytes(archive_path, archive_bytes)
        if not zipfile.is_zipfile(io.BytesIO(archive_bytes)):
            raise DownloadError(f"{dataset_name} source did not return a valid ZIP archive", archive_http_status, resolved_archive_url)
        archive_hash = hashlib.sha256(archive_bytes).hexdigest()
        try:
            archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
        except zipfile.BadZipFile as exc:
            raise DownloadError(f"Invalid {dataset_name} ZIP archive: {exc}", archive_http_status, resolved_archive_url) from exc
        with archive:
            bad_member = archive.testzip()
            if bad_member:
                raise DownloadError(f"Corrupt {dataset_name} ZIP member: {bad_member}", archive_http_status, resolved_archive_url)
            if dataset_name == "CUAD":
                payload = json.loads(archive.read("CUADv1.json"))
                dataset_records = payload.get("data", [])
            else:
                contract_files = [item for item in archive.infolist()
                                  if not item.is_dir() and item.filename.startswith("data/contracts/")
                                  and item.filename.lower().endswith(".txt")]
                dataset_records = contract_files
            for item in dataset_records:
                if dataset_name == "CUAD":
                    title = item.get("title")
                    paragraphs = item.get("paragraphs", [])
                    contexts = [paragraph.get("context", "").strip()
                                for paragraph in paragraphs if isinstance(paragraph, dict)]
                    text = "\n\n".join(context for context in contexts if context)
                    source_member = "CUADv1.json"
                    source_filename = f"{title or 'untitled'}.txt"
                    document_id = "CUAD-" + hashlib.sha256(str(title).encode("utf-8")).hexdigest()[:16].upper()
                    source_text_url = archive_url + "#" + source_member
                    raw_content = text.encode("utf-8")
                    publication = None
                    source_format = "txt"
                else:
                    source_member = item.filename
                    source_filename = Path(source_member).name
                    document_id = "MAUD-" + hashlib.sha256(source_member.encode("utf-8")).hexdigest()[:16].upper()
                    title = f"MAUD {source_filename}"
                    raw_content = archive.read(source_member)
                    text = raw_content.decode("utf-8-sig", errors="replace").strip()
                    source_text_url = archive_url + "#" + source_member
                    publication = None
                    source_format = "txt"
                if not isinstance(text, str) or len(text.strip()) < MIN_USABLE_CHARACTERS or document_id in existing_ids:
                    continue
                raw_path = raw_dir / raw_folder / "contracts" / stable_filename(document_id, ".txt")
                atomic_write_bytes(raw_path, raw_content)
                metadata = {
                    "document_id": document_id, "title": title, "department": "Legal, contracts and compliance",
                    "source": f"{dataset_name} / The Atticus Project", "source_organization": "The Atticus Project",
                    "source_url": source_text_url, "publication_date": publication, "file_format": source_format,
                    "license_or_usage": "CC BY 4.0; attribute The Atticus Project and the dataset. See https://www.atticusprojectai.org/legal/",
                    "classification": "Public", "source_access_status": "public_source",
                    "classification_origin": "publicly distributed dataset license", "record_type": "public_dataset_document",
                    "is_synthetic": False, "archive_url": archive_url, "archive_member": source_member,
                    "archive_sha256": archive_hash,
                }
                record = {**metadata, "url": source_text_url, "local_content": True,
                          "download_status": "downloaded", "raw_path": raw_path.relative_to(ROOT).as_posix(),
                          "raw_hash": hashlib.sha256(raw_content).hexdigest(),
                          "raw_archive_path": archive_path.relative_to(ROOT).as_posix(),
                          "raw_archive_hash": archive_hash, "http_status": archive_http_status,
                          "resolved_url": resolved_archive_url, "extracted_characters": len(text),
                          "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                          "downloaded_at": timestamp(), "file_format": source_format,
                          "rag_metadata": metadata}
                output_path, file_hash = save_content(record, metadata, text, processed_dir)
                record.update({"content_path": output_path, "processed_text_path": output_path, "file_hash": file_hash})
                added.append(record)
                existing_ids.add(document_id)
        LOG.info("Imported %d full-text %s contract records", sum(item.get("source", "").startswith(dataset_name) for item in added), dataset_name)
    records.extend(added)
    return added


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, newline="\n") as handle:
        handle.write(content)
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def discover_catalog(explicit_path: str | None) -> Path:
    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Catalog not found: {path}")
        return path
    candidates = [ROOT / "documents/catalog.json", ROOT / "catalog.json", ROOT / "data/catalog.json"]
    found = [path for path in candidates if path.is_file()]
    if len(found) != 1:
        raise FileNotFoundError(
            "Could not uniquely locate the original catalog. Pass --catalog PATH; "
            f"checked: {', '.join(str(path) for path in candidates)}"
        )
    return found[0]


def load_catalog(path: Path) -> dict[str, Any]:
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read valid JSON catalog at {path}: {exc}") from exc
    records = catalog.get("records") if isinstance(catalog, dict) else None
    if not isinstance(records, list):
        raise ValueError("Catalog must be a JSON object containing a 'records' array")
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"Catalog record {index} must be an object")
        document_id = record.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            raise ValueError(f"Catalog record {index} has no document_id")
        if document_id in seen:
            raise ValueError(f"Duplicate document_id in catalog: {document_id}")
        seen.add(document_id)
    return catalog


def department_folder(record: dict[str, Any]) -> str:
    if record.get("record_type") == "dataset_reference":
        return "external_datasets"
    department = record.get("department", "")
    if department in DEPARTMENT_OUTPUT_FOLDERS:
        return DEPARTMENT_OUTPUT_FOLDERS[department]
    return DEPARTMENT_FOLDERS.get(department, "company")


def raw_source_folder(record: dict[str, Any]) -> str:
    source = str(record.get("source", ""))
    if source == "The Atticus Project":
        return "cuad" if "CUAD" in str(record.get("title", "")) or "CUAD" in str(record.get("document_id", "")) else "maud"
    return RAW_SOURCE_FOLDERS.get(source, "other_handbooks")


def extension_for(content_type: str, url: str) -> str:
    if "application/pdf" in content_type or urlsplit(url).path.lower().endswith(".pdf"):
        return ".pdf"
    if "json" in content_type or urlsplit(url).path.lower().endswith(".json"):
        return ".json"
    if "text/markdown" in content_type or urlsplit(url).path.lower().endswith(".md"):
        return ".md"
    if "text/plain" in content_type or urlsplit(url).path.lower().endswith(".txt"):
        return ".txt"
    return ".html"


def raw_file_for(record: dict[str, Any], raw_dir: Path, content_type: str, url: str, content: bytes) -> tuple[str, str]:
    destination = raw_dir / raw_source_folder(record) / stable_filename(record["document_id"], extension_for(content_type, url))
    atomic_write_bytes(destination, content)
    try:
        stored_path = destination.relative_to(ROOT).as_posix()
    except ValueError:
        stored_path = str(destination)
    return stored_path, hashlib.sha256(destination.read_bytes()).hexdigest()


def license_notes(record: dict[str, Any]) -> str:
    source = record.get("source")
    if source in {"GitLab Handbook", "GitLab Handbook sitemap"}:
        return "GitLab Handbook pages state Creative Commons Attribution-ShareAlike 4.0; preserve attribution and share-alike terms."
    if source == "The Atticus Project":
        return "The Atticus Project lists CUAD and MAUD under CC BY 4.0; preserve attribution."
    if source == "NIST":
        return "U.S. government work; verify notices and third-party material on the individual publication."
    if source == "SEC EDGAR":
        return "SEC public filing; comply with SEC fair-access policies and retain filing attribution."
    if source == "CISA":
        return "Verify the individual page's copyright and third-party notices before redistribution."
    return "Review source-specific terms before reuse or redistribution."


def inferred_department(url: str) -> str:
    path = urlsplit(url).path
    matching = [(prefix, department) for prefix, department in HANDBOOK_PREFIX_DEPARTMENTS.items() if path.startswith(prefix)]
    return max(matching, key=lambda item: len(item[0]))[1] if matching else "Executive and company strategy"


def public_department(record: dict[str, Any]) -> str:
    source = str(record.get("source", ""))
    if source.startswith("GitLab Handbook"):
        return inferred_department(str(record.get("url", "")))
    if source in {"NIST", "CISA"}:
        return "Cybersecurity and IT"
    if source == "SEC EDGAR":
        return "Finance and accounting"
    if "Atticus" in source or source in {"CUAD", "MAUD"}:
        return "Legal, contracts and compliance"
    return str(record.get("department") or "Executive and company strategy")


def stable_filename(document_id: str, suffix: str = ".md") -> str:
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", document_id).strip("._")
    if not safe_id:
        raise ValueError(f"Document ID cannot be converted to a safe filename: {document_id!r}")
    return safe_id + suffix


def metadata_for(record: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    is_synthetic = record.get("record_type") == "synthetic_test_record"
    source = str(record.get("source", ""))
    organization = record.get("source_organization")
    if not organization:
        organization = "GitLab" if source.startswith("GitLab Handbook") else source
    return {
        "document_id": record.get("document_id"),
        "title": record.get("title"),
        "department": record.get("department"),
        "source": record.get("source"),
        "source_organization": organization,
        "source_url": record.get("url"),
        "classification": record.get("classification"),
        "source_access_status": "public_source" if not is_synthetic else "synthetic_fixture",
        "classification_origin": "source catalog" if not is_synthetic else "legacy synthetic fixture",
        "record_type": record.get("record_type"),
        "publication_date": record.get("publication_date"),
        "license_or_usage": record.get("license_or_usage", license_notes(record)),
        "is_synthetic": is_synthetic,
    }


def publication_date(response: Response) -> str | None:
    if "html" not in response.headers.get("Content-Type", "").lower():
        return None
    soup = BeautifulSoup(response.content, "html.parser")
    selectors = (
        'meta[property="article:published_time"]',
        'meta[name="date"]',
        'meta[name="datePublished"]',
        'meta[itemprop="datePublished"]',
        "time[datetime]",
    )
    for selector in selectors:
        tag = soup.select_one(selector)
        if tag:
            value = tag.get("content") or tag.get("datetime")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def extract_html(response: Response) -> str:
    soup = BeautifulSoup(response.content, "html.parser")
    for element in soup.select("script, style, noscript, svg, nav, footer, header, aside, form, [role=navigation]"):
        element.decompose()
    candidates = soup.select("main, article, [role=main], #content, .td-content, .content, .article-content")
    root = max(candidates, key=lambda item: len(item.get_text(" ", strip=True))) if candidates else soup.body or soup
    text = root.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_pdf(response: Response) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DownloadError("PDF received but pypdf is not installed; install requirements or disable PDF extraction") from exc
    import io

    reader = PdfReader(io.BytesIO(response.content))
    text = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if not text:
        raise DownloadError("PDF contained no extractable text (it may be scanned or image-only)")
    return text


def document_markdown(record: dict[str, Any], metadata: dict[str, Any], text: str) -> str:
    return "<!-- metadata: " + json.dumps(metadata, ensure_ascii=False, sort_keys=True) + " -->\n\n# " + str(record.get("title", record["document_id"])) + "\n\n" + text.strip() + "\n"


def save_content(
    record: dict[str, Any], metadata: dict[str, Any], text: str, output_dir: Path, suffix: str = ".md"
) -> tuple[str, str]:
    category = "synthetic_fixtures" if record.get("record_type") == "synthetic_test_record" else department_folder(record)
    destination = output_dir / category / stable_filename(record["document_id"], suffix)
    if suffix == ".json":
        body = json.dumps({"metadata": metadata, "content": json.loads(text)}, ensure_ascii=False, indent=2) + "\n"
    else:
        body = document_markdown(record, metadata, text)
    atomic_write(destination, body)
    try:
        content_path = destination.relative_to(ROOT).as_posix()
    except ValueError:
        content_path = str(destination)
    return content_path, hashlib.sha256(body.encode("utf-8")).hexdigest()


def synthetic_canaries() -> dict[str, str]:
    path = ROOT / "data/canaries.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {item["document_id"]: item["token"] for item in payload.get("tokens", [])}


def process_synthetic(
    record: dict[str, Any], policy: dict[str, Any], output_dir: Path, canaries: dict[str, str]
) -> DownloadResult:
    summary = record.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return DownloadResult("failed", error="Synthetic fixture has no summary in the original catalog")
    token = canaries.get(record["document_id"])
    text = ("FICTIONAL SYNTHETIC TEST FIXTURE FOR ACCESS-CONTROL RESEARCH. "
            "No real employee, company financial, contract, incident, or strategy information.\n\n"
            + summary.strip())
    if token:
        text += f"\n\nSynthetic canary marker for leakage testing: {token}"
    metadata = metadata_for(record, policy)
    content_path, digest = save_content(record, metadata, text, output_dir)
    return DownloadResult("synthetic_fixture", content_path, extracted_characters=len(text), content_hash=digest)


def process_public(
    record: dict[str, Any], policy: dict[str, Any], output_dir: Path, raw_dir: Path,
    client: PublicSession, enable_pdf: bool
) -> DownloadResult:
    url = record.get("url")
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return DownloadResult("failed", error="Record has no valid public HTTP(S) URL")
    try:
        response = client.get(url, str(record.get("source", "")))
        content_type = response.headers.get("Content-Type", "").lower()
        raw_path, raw_hash = raw_file_for(record, raw_dir, content_type, response.url, response.content)
        if record.get("source") == "SEC EDGAR" and (
                "data.sec.gov/submissions/" in response.url or "/edgar/browse/" in response.url):
            return DownloadResult("reference_only", http_status=response.status_code, resolved_url=response.url,
                                  error="SEC listing/submissions metadata is not a full 10-K or 10-Q filing",
                                  raw_path=raw_path, raw_hash=raw_hash,
                                  file_format=extension_for(content_type, response.url).lstrip("."))
        if "application/pdf" in content_type or response.content[:5] == b"%PDF-":
            if not enable_pdf:
                return DownloadResult("reference_only", http_status=response.status_code, resolved_url=response.url,
                                      error="PDF extraction is disabled; rerun with --enable-pdf",
                                      raw_path=raw_path, raw_hash=raw_hash, file_format="pdf")
            text = extract_pdf(response)
            file_format = "pdf"
        elif "json" in content_type or "application/ld+json" in content_type:
            try:
                parsed = response.json()
            except (ValueError, requests.exceptions.JSONDecodeError) as exc:
                raise DownloadError(f"Response declared JSON but was malformed: {exc}", response.status_code, response.url) from exc
            text = json.dumps(parsed, ensure_ascii=False, indent=2)
            file_format = "json"
        elif "html" in content_type or "text/plain" in content_type or not content_type:
            text = extract_html(response) if "html" in content_type or not content_type else response.text.strip()
            file_format = "html" if "html" in content_type or not content_type else "text"
        else:
            return DownloadResult("reference_only", http_status=response.status_code, resolved_url=response.url,
                                  error=f"Unsupported response content type: {content_type or 'unknown'}",
                                  raw_path=raw_path, raw_hash=raw_hash,
                                  file_format=extension_for(content_type, response.url).lstrip("."))
        characters = len(text.strip())
        if characters < MIN_USABLE_CHARACTERS:
            raise DownloadError(f"Extracted content is empty or too short ({characters} characters)", response.status_code, response.url)
        record = {**record, "publication_date": publication_date(response) or record.get("publication_date"),
              "file_format": file_format, "license_or_usage": license_notes(record)}
        metadata = metadata_for(record, policy)
        content_path, digest = save_content(record, metadata, text, output_dir)
        status = "partial" if characters < SUSPICIOUS_CHARACTERS else "downloaded"
        return DownloadResult(status, content_path, response.status_code, response.url, characters,
                      hashlib.sha256(text.strip().encode("utf-8")).hexdigest(), file_hash=digest,
                      raw_path=raw_path, raw_hash=raw_hash, processed_text_path=content_path,
                      extracted_text=text.strip(), file_format=file_format,
                      publication_date=record.get("publication_date"))
    except DownloadError:
        raise
    except (RequestException, OSError, ValueError) as exc:
        raise DownloadError(str(exc)) from exc


def valid_existing(record: dict[str, Any], previous: dict[str, Any]) -> bool:
    content_path = previous.get("processed_text_path") or previous.get("content_path")
    raw_path = previous.get("raw_path")
    digest = previous.get("file_hash") or previous.get("content_hash")
    if (not content_path or not raw_path or not digest or
            previous.get("download_status") not in {"downloaded", "partial"}):
        return False
    processed_file = Path(content_path)
    raw_file = Path(raw_path)
    if not processed_file.is_absolute():
        processed_file = ROOT / processed_file
    if not raw_file.is_absolute():
        raw_file = ROOT / raw_file
    if not processed_file.is_file() or not raw_file.is_file():
        return False
    actual_hash = hashlib.sha256(processed_file.read_bytes()).hexdigest()
    return actual_hash == digest


def quality_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(record.get("download_status", "not_attempted") for record in records)
    hashes = Counter(record["content_hash"] for record in records
                     if record.get("content_hash") and record.get("download_status") in {"downloaded", "partial"})
    short = [record["document_id"] for record in records
             if record.get("download_status") in {"downloaded", "partial"}
             and record.get("extracted_characters", 0) < SUSPICIOUS_CHARACTERS and record.get("content_path")]
    manual = [record["document_id"] for record in records if record.get("download_status") in {"failed", "partial", "reference_only"}]
    return {
        "total_catalog_records": len(records),
        "successful_downloads": statuses["downloaded"],
        "partial_downloads": statuses["partial"],
        "failed_downloads": statuses["failed"],
        "reference_only_records": statuses["reference_only"],
        "synthetic_fixtures": statuses["synthetic_fixture"],
        "excluded_legacy_synthetic_records": sum(bool(record.get("dataset_excluded")) for record in records),
        "not_attempted_records": statuses["not_attempted"],
        "distinct_departments": len({record.get("department") for record in records if record.get("department")}),
        "total_extracted_characters": sum(record.get("extracted_characters", 0) for record in records),
        "empty_or_suspiciously_short_documents": short,
        "duplicate_content_hashes": {digest: count for digest, count in hashes.items() if count > 1},
        "urls_requiring_manual_follow_up": manual,
    }


def persist_state(records: list[dict[str, Any]], processed_path: Path, failures_path: Path, source_catalog: Path) -> None:
    output = {
        "dataset": "processed_enterprise_document_catalog",
        "source_catalog": source_catalog.relative_to(ROOT).as_posix() if source_catalog.is_relative_to(ROOT) else str(source_catalog),
        "updated_at": timestamp(),
        "records": records,
        "data_quality": quality_report(records),
    }
    atomic_write(processed_path, json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    failures = [
        {"document_id": item.get("document_id"), "title": item.get("title"), "url": item.get("url"),
         "download_status": item.get("download_status"), "http_status": item.get("http_status"),
         "resolved_url": item.get("resolved_url"), "error": item.get("error")}
        for item in records if item.get("download_status") in {"failed", "partial"} or
        (item.get("download_status") == "reference_only" and item.get("error"))
    ]
    atomic_write(failures_path, json.dumps({"updated_at": timestamp(), "failures": failures}, ensure_ascii=False, indent=2) + "\n")


def download_records(
    catalog: dict[str, Any], policy: dict[str, Any], client: PublicSession, output_dir: Path,
    raw_dir: Path, processed_path: Path, failures_path: Path, source_path: Path, limit: int | None, max_urls: int | None,
    enable_pdf: bool, resume: bool,
) -> list[dict[str, Any]]:
    previous_records: dict[str, dict[str, Any]] = {}
    if resume and processed_path.is_file():
        try:
            previous_records = {item["document_id"]: item for item in json.loads(processed_path.read_text(encoding="utf-8")).get("records", [])}
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            LOG.warning("Could not read previous processed catalog; starting fresh")
    records: list[dict[str, Any]] = []
    for source_record in catalog["records"]:
        record = dict(source_record)
        record.update({"download_status": "not_attempted", "content_path": None, "raw_path": None,
                  "processed_text_path": None, "file_hash": None, "raw_hash": None, "http_status": None,
                      "resolved_url": None, "extracted_characters": 0, "content_hash": None,
                      "downloaded_at": None, "error": None, "rag_metadata": metadata_for(source_record, policy)})
        old = previous_records.get(record["document_id"], {})
        if old:
            for key in ("download_status", "content_path", "raw_path", "processed_text_path", "file_hash", "raw_hash",
                        "http_status", "resolved_url", "extracted_characters", "content_hash", "downloaded_at", "error",
                        "file_format", "publication_date"):
                if key in old:
                    record[key] = old[key]
            if valid_existing(record, record):
                record["local_content"] = True
            elif old.get("download_status") in {"downloaded", "partial"}:
                record.update({"download_status": "not_attempted", "content_path": None, "processed_text_path": None,
                               "raw_path": None, "file_hash": None, "raw_hash": None, "content_hash": None,
                               "extracted_characters": 0, "downloaded_at": None, "local_content": False})
            else:
                record["local_content"] = False
                if old.get("download_status") in {"failed", "reference_only"} or old.get("error"):
                    record["download_status"] = old.get("download_status") if old.get("download_status") in {"failed", "reference_only"} else "failed"
        else:
            record["local_content"] = False
        if record.get("record_type") == "synthetic_test_record":
            record.update({"download_status": "reference_only", "content_path": None, "raw_path": None,
                           "processed_text_path": None, "local_content": False,
                           "error": "Legacy synthetic fixture excluded from public-source dataset; existing legacy file preserved"})
        elif record.get("record_type") == "dataset_reference":
            record["download_status"] = "reference_only"
            record["error"] = "Repository page is a dataset reference, not the dataset; review source license and download procedure manually"
        records.append(record)

    selected: list[int] = []
    url_count = 0
    for index, item in enumerate(records):
        if item.get("record_type") in {"dataset_reference", "synthetic_test_record"}:
            continue
        if resume and item.get("download_status") in {"downloaded", "partial"} and valid_existing(item, item):
            continue
        is_url_record = bool(item.get("url")) and item.get("record_type") != "synthetic_test_record"
        if is_url_record and max_urls is not None and url_count >= max_urls:
            continue
        if is_url_record:
            url_count += 1
        selected.append(index)
    if limit is not None:
        discovered = [index for index in selected if records[index].get("record_type") == "public_handbook_document"]
        if discovered:
            other = [index for index in selected if index not in set(discovered)]
            selected = discovered + other
        selected = selected[:limit]
    for index in selected:
        record = records[index]
        if resume and valid_existing(record, record):
            continue
        LOG.info("Processing %s: %s", record["document_id"], record.get("title"))
        try:
            result = process_public(record, policy, output_dir, raw_dir, client, enable_pdf)
            record.update({"download_status": result.status, "content_path": result.content_path,
                          "http_status": result.http_status, "resolved_url": result.resolved_url,
                          "extracted_characters": result.extracted_characters, "content_hash": result.content_hash,
                          "downloaded_at": timestamp() if result.content_path else None,
                          "local_content": result.content_path is not None, "error": result.error,
                          "raw_path": result.raw_path, "raw_hash": result.raw_hash,
                          "processed_text_path": result.processed_text_path or result.content_path,
                          "file_hash": result.file_hash, "file_format": result.file_format,
                          "publication_date": result.publication_date, "license_or_usage": license_notes(record)})
            if result.publication_date:
                record["rag_metadata"] = metadata_for(record, policy)
            LOG.info("%s %s status=%s http=%s characters=%d", record["document_id"], record.get("url"),
                     result.status, result.http_status, result.extracted_characters)
        except DownloadError as exc:
            record.update({"download_status": "failed", "http_status": exc.status,
                          "resolved_url": exc.resolved_url, "error": str(exc), "local_content": False})
            LOG.warning("%s failed status=%s url=%s: %s", record["document_id"], exc.status, record.get("url"), exc)
        persist_state(records, processed_path, failures_path, source_path)
    persist_state(records, processed_path, failures_path, source_path)
    return records


def discover_sec_filings(
    records: list[dict[str, Any]], client: PublicSession, policy: dict[str, Any], output_dir: Path, raw_dir: Path,
    processed_path: Path, failures_path: Path, source_path: Path, cik: str, max_filings: int,
) -> None:
    if max_filings <= 0:
        return
    cik_digits = re.sub(r"\D", "", cik).zfill(10)
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik_digits}.json"
    try:
        response = client.get(submissions_url, "SEC EDGAR")
        payload = response.json()
        recent = payload.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])
        company = payload.get("name", "SEC filer")
        matched = 0
        for form, accession, filed, primary in zip(forms, accessions, dates, primary_docs):
            if form not in {"10-K", "10-Q"} or matched >= max_filings:
                continue
            accession_compact = accession.replace("-", "")
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik_digits)}/{accession_compact}/{primary}"
            filing_record = {
                "document_id": f"SEC-{cik_digits}-{accession}", "title": f"{company} {form} filed {filed}",
                "department": "Finance", "source": "SEC EDGAR", "url": filing_url,
                "classification": "Public", "record_type": "sec_filing",
                "accession_number": accession, "filing_type": form, "filing_date": filed,
                "company_identifier": cik_digits, "local_content": False,
            }
            try:
                filing_response = client.get(filing_url, "SEC EDGAR")
                content_type = filing_response.headers.get("Content-Type", "").lower()
                if "html" in content_type or "text" in content_type or not content_type:
                    filing_text = extract_html(filing_response) if "html" in content_type else filing_response.text.strip()
                else:
                    raise DownloadError(f"Unsupported SEC filing content type: {content_type}", filing_response.status_code, filing_response.url)
                if len(filing_text) < MIN_USABLE_CHARACTERS:
                    raise DownloadError("SEC filing had no usable extracted text", filing_response.status_code, filing_response.url)
                raw_path, raw_hash = raw_file_for(filing_record, raw_dir, content_type, filing_response.url, filing_response.content)
                filing_record.update({"publication_date": filed, "file_format": extension_for(content_type, filing_response.url).lstrip("."),
                                      "license_or_usage": license_notes(filing_record)})
                metadata = metadata_for(filing_record, policy)
                metadata.update({"accession_number": accession, "filing_type": form, "filing_date": filed, "company_identifier": cik_digits})
                content_path, file_hash = save_content(filing_record, metadata, filing_text, output_dir)
                filing_record.update({"download_status": "downloaded", "content_path": content_path,
                                      "http_status": filing_response.status_code, "resolved_url": filing_response.url,
                                      "extracted_characters": len(filing_text),
                                      "content_hash": hashlib.sha256(filing_text.strip().encode("utf-8")).hexdigest(),
                                      "file_hash": file_hash, "raw_path": raw_path, "raw_hash": raw_hash,
                                      "processed_text_path": content_path, "downloaded_at": timestamp(),
                                      "local_content": True, "rag_metadata": metadata})
            except DownloadError as exc:
                filing_record.update({"download_status": "failed", "http_status": exc.status, "resolved_url": exc.resolved_url,
                                      "error": str(exc), "local_content": False, "rag_metadata": metadata_for(filing_record, policy)})
            records.append(filing_record)
            matched += 1
            persist_state(records, processed_path, failures_path, source_path)
        if matched == 0:
            LOG.info("No 10-K or 10-Q filings found for CIK %s", cik_digits)
    except (DownloadError, ValueError, KeyError, TypeError) as exc:
        LOG.error("Could not discover SEC filings for CIK %s: %s", cik_digits, exc)
        discovery_record = {"document_id": f"SEC-SUBMISSIONS-{cik_digits}", "title": f"SEC submissions metadata for CIK {cik_digits}",
                            "department": "Finance and accounting", "source": "SEC EDGAR", "source_organization": "SEC",
                            "url": submissions_url, "classification": "Public", "record_type": "sec_submissions_metadata",
                            "download_status": "failed", "local_content": False, "error": str(exc),
                            "rag_metadata": metadata_for({"document_id": f"SEC-SUBMISSIONS-{cik_digits}",
                                "title": f"SEC submissions metadata for CIK {cik_digits}", "department": "Finance and accounting",
                                "source": "SEC EDGAR", "url": submissions_url, "classification": "Public",
                                "record_type": "sec_submissions_metadata"}, policy)}
        records.append(discovery_record)
        persist_state(records, processed_path, failures_path, source_path)


def crawl_handbook(
    records: list[dict[str, Any]], client: PublicSession, policy: dict[str, Any], output_dir: Path,
    processed_path: Path, failures_path: Path, source_path: Path, depth: int, max_urls: int,
    enable_pdf: bool,
) -> None:
    if depth <= 0:
        return
    queue: deque[tuple[str, int, str]] = deque([
        ("https://handbook.gitlab.com/", 0, "GL-001")
    ])
    catalog_urls = {item.get("url") for item in records if item.get("url")}
    seen: set[str] = set()
    downloaded = 0
    while queue and downloaded < max_urls:
        url, level, parent_id = queue.popleft()
        parts = urlsplit(url)
        if url in seen or parts.scheme != "https" or parts.hostname != "handbook.gitlab.com":
            continue
        if Path(parts.path).suffix.lower() in {".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".ico", ".pdf"}:
            continue
        seen.add(url)
        try:
            response = client.get(url, "GitLab Handbook")
            seen.add(response.url)
            text = extract_html(response)
            if len(text) < MIN_USABLE_CHARACTERS:
                continue
            doc_id = "CRAWL-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
            crawled = {"document_id": doc_id, "title": BeautifulSoup(response.content, "html.parser").title.get_text(" ", strip=True)
                       if BeautifulSoup(response.content, "html.parser").title else url,
                       "department": "Company-wide", "source": "GitLab Handbook crawler", "url": url,
                       "classification": "Public", "record_type": "public_reference", "discovered_from": parent_id}
            if url not in catalog_urls and response.url not in catalog_urls:
                metadata = metadata_for(crawled, policy)
                content_path, digest = save_content(crawled, metadata, text, output_dir)
                crawled.update({"download_status": "downloaded", "content_path": content_path, "http_status": response.status_code,
                                "resolved_url": response.url, "extracted_characters": len(text), "content_hash": digest,
                                "downloaded_at": timestamp(), "local_content": True, "rag_metadata": metadata})
                records.append(crawled)
                downloaded += 1
                persist_state(records, processed_path, failures_path, source_path)
            if level < depth:
                soup = BeautifulSoup(response.content, "html.parser")
                for anchor in soup.select("main a[href], article a[href], [role=main] a[href]"):
                    target = urljoin(response.url, anchor.get("href", ""))
                    target_parts = urlsplit(target)
                    if target_parts.scheme == "https" and target_parts.hostname == "handbook.gitlab.com":
                        queue.append((target, level + 1, doc_id))
        except DownloadError as exc:
            LOG.info("Crawler skipped %s: %s", url, exc)
    LOG.info("Handbook crawler saved %d discovered pages", downloaded)


def serialize_jsonl(value: dict[str, Any]) -> str:
    serialized = json.dumps(value, ensure_ascii=False)
    for separator in ("\u0085", "\u2028", "\u2029"):
        serialized = serialized.replace(separator, f"\\u{ord(separator):04x}")
    return serialized + "\n"


def write_processed_dataset(records: list[dict[str, Any]], processed_dir: Path) -> dict[str, Any]:
    usable = [record for record in records if record.get("download_status") in {"downloaded", "partial"}
              and record.get("raw_path") and record.get("processed_text_path")
              and record.get("extracted_characters", 0) >= MIN_USABLE_CHARACTERS
              and not record.get("is_duplicate")]
    lines: list[str] = []
    metadata_records: list[dict[str, Any]] = []
    for record in usable:
        text_path = Path(record["processed_text_path"])
        if not text_path.is_absolute():
            text_path = ROOT / text_path
        try:
            content = text_path.read_text(encoding="utf-8")
        except OSError as exc:
            LOG.warning("Omitting missing processed text %s: %s", record.get("document_id"), exc)
            continue
        body = re.sub(r"^<!-- metadata: .*? -->\n\n# [^\n]*\n\n", "", content, count=1, flags=re.DOTALL).strip()
        if len(body) < MIN_USABLE_CHARACTERS:
            continue
        metadata = {
            "document_id": record.get("document_id"),
            "title": record.get("title"),
            "source_organization": record.get("source_organization", record.get("source")),
            "source_url": record.get("url"),
            "department": public_department(record),
            "publication_date": record.get("publication_date"),
            "file_format": record.get("file_format"),
            "license_or_usage": record.get("license_or_usage", license_notes(record)),
            "download_status": record.get("download_status"),
            "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "classification": "Public",
            "classification_origin": "public source or public dataset license",
            "raw_path": record.get("raw_path"),
            "processed_text_path": record.get("processed_text_path"),
            "file_hash": record.get("file_hash"),
        }
        row = {"document_id": record["document_id"], "metadata": metadata, "text": body}
        lines.append(serialize_jsonl(row))
        metadata_records.append(metadata)
    processed_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(processed_dir / "documents.jsonl", "".join(lines))
    atomic_write(processed_dir / "document_metadata.json", json.dumps(metadata_records, ensure_ascii=False, indent=2) + "\n")
    return {item["document_id"]: item for item in metadata_records}


def dataset_report(original_catalog: dict[str, Any], records: list[dict[str, Any]], processed_metadata: dict[str, Any]) -> dict[str, Any]:
    usable = list(processed_metadata.values())
    status_counts = Counter(record.get("download_status", "not_attempted") for record in records)
    source_counts: dict[str, dict[str, int]] = {}
    department_counts: dict[str, int] = Counter()
    for record in usable:
        source = str(record.get("source_organization", "Unknown"))
        department = str(record.get("department", "Unknown"))
        source_counts.setdefault(source, {})[department] = source_counts.setdefault(source, {}).get(department, 0) + 1
        department_counts[department] += 1
    hash_groups: dict[str, set[str]] = {}
    for record in records:
        digest = record.get("content_hash")
        document_id = record.get("document_id")
        if digest and document_id:
            hash_groups.setdefault(digest, set()).add(document_id)
    duplicate_hashes = {digest: len(document_ids) for digest, document_ids in hash_groups.items() if len(document_ids) > 1}
    duplicate_document_count = sum(len(document_ids) - 1 for document_ids in hash_groups.values() if len(document_ids) > 1)
    raw_paths = {record.get("raw_path") for record in records if record.get("raw_path")}
    raw_bytes = 0
    for raw_path in raw_paths:
        path = Path(raw_path)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_file():
            raw_bytes += path.stat().st_size
    discovered = [record for record in records if record.get("record_type") == "public_handbook_document"]
    return {
        "generated_at": timestamp(),
        "original_catalog_records": len(original_catalog.get("records", [])),
        "additional_urls_discovered": len(discovered),
        "catalog_records_after_discovery": len(records),
        "actual_documents_downloaded": sum(bool(record.get("download_status") == "downloaded" and record.get("raw_path")) for record in records),
        "usable_documents_after_extraction": len(usable),
        "files_by_source_and_department": source_counts,
        "usable_documents_by_department": dict(sorted(department_counts.items())),
        "usable_documents_by_source": dict(sorted(Counter(item.get("source_organization", "Unknown") for item in usable).items())),
        "total_extracted_characters": sum(record.get("extracted_characters", 0) for record in records if record.get("document_id") in processed_metadata),
        "total_raw_file_bytes": raw_bytes,
        "duplicate_document_count": duplicate_document_count,
        "download_status_counts": dict(status_counts),
        "failed_records": [{"document_id": record.get("document_id"), "url": record.get("url"), "error": record.get("error")}
                           for record in records if record.get("download_status") == "failed"],
        "reference_only_records": [{"document_id": record.get("document_id"), "url": record.get("url"), "error": record.get("error")}
                                    for record in records if record.get("download_status") == "reference_only"],
        "duplicate_hashes": duplicate_hashes,
        "empty_or_short_extractions": [record.get("document_id") for record in records
                                        if record.get("download_status") in {"failed", "partial"}],
        "license_and_attribution": {
            "GitLab Handbook": "CC BY-SA 4.0 as stated by handbook pages; provide attribution and share adaptations alike.",
            "CUAD": "CC BY 4.0 according to The Atticus Project licensing page.",
            "MAUD": "CC BY 4.0 according to The Atticus Project licensing page.",
            "SEC EDGAR": "Public filing data; adhere to SEC fair-access and user-agent requirements.",
            "NIST/CISA": "Review each item for government and third-party copyright notices.",
        },
        "missing_sources_and_manual_follow_up": [{"document_id": record.get("document_id"), "url": record.get("url"), "error": record.get("error")}
                                                 for record in records
                                                 if record.get("download_status") in {"failed", "reference_only"}],
        "target_documents": 500,
        "target_reached": len(usable) >= 500,
    }


def validate_processed_dataset(records: list[dict[str, Any]], processed_metadata: dict[str, Any], processed_dir: Path) -> dict[str, Any]:
    jsonl_path = processed_dir / "documents.jsonl"
    rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    record_map = {record.get("document_id"): record for record in records}
    errors: list[str] = []
    for row in rows:
        document_id = row.get("document_id")
        source_record = record_map.get(document_id)
        metadata = row.get("metadata", {})
        text = row.get("text", "")
        if source_record is None or document_id not in processed_metadata:
            errors.append(f"{document_id}: processed document has no catalog source record")
            continue
        if len(text.strip()) < MIN_USABLE_CHARACTERS:
            errors.append(f"{document_id}: processed text is empty/short")
        if metadata.get("source_url") != source_record.get("url"):
            errors.append(f"{document_id}: source URL metadata mismatch")
        for key in ("raw_path", "processed_text_path"):
            value = source_record.get(key)
            path = Path(value) if value else Path("__missing__")
            if not path.is_absolute():
                path = ROOT / path
            if not path.is_file():
                errors.append(f"{document_id}: missing {key}")
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if metadata.get("content_hash") != text_hash:
            errors.append(f"{document_id}: extracted-text hash mismatch")
    return {"processed_jsonl_records": len(rows), "metadata_records": len(processed_metadata),
            "validation_errors": errors, "valid": not errors}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", help="Original catalog JSON; auto-detected by default")
    parser.add_argument("--output-dir", default="data/processed")
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--processed-catalog", default="data/catalog_downloaded.json")
    parser.add_argument("--failures", default="data/download_failures.json")
    parser.add_argument("--dataset-report", default="data/dataset_report.json")
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--delay", type=float, default=0.75, help="Minimum seconds between requests to each origin")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--limit", type=int, help="Process at most this many eligible catalog records")
    parser.add_argument("--max-urls", type=int, help="Maximum number of catalog URL records to process")
    parser.add_argument("--sec-cik", default="1653482", help="SEC CIK to use when --sec-filings is enabled")
    parser.add_argument("--sec-filings", type=int, default=0, help="Download up to N recent SEC 10-K/10-Q filings")
    parser.add_argument("--max-handbook-pages", type=int, default=250,
                        help="Discover up to this many additional Handbook pages from the public sitemap")
    parser.add_argument("--skip-atticus", action="store_true", help="Skip official CUAD and MAUD archives (useful for a small sample)")
    parser.add_argument("--handbook-crawl-depth", type=int, default=0, help="Discover more same-domain Handbook pages; 0 disables")
    parser.add_argument("--crawl-page-limit", type=int, default=25)
    parser.add_argument("--enable-pdf", action="store_true", help="Extract text from public PDFs using pypdf")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True,
                         help="Reuse prior files only when their saved SHA-256 still matches")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if (args.timeout <= 0 or args.delay < 0 or args.retries < 0 or
            (args.limit is not None and args.limit < 0) or (args.max_urls is not None and args.max_urls < 0) or
            args.sec_filings < 0 or args.handbook_crawl_depth < 0 or args.crawl_page_limit < 0 or
            args.max_handbook_pages < 0):
        LOG.error("Timeout must be positive; delays, retries, and limits must be non-negative")
        return 2
    try:
        source_path = discover_catalog(args.catalog)
        catalog = load_catalog(source_path)
        policy: dict[str, Any] = {}
        processed_path = (ROOT / args.processed_catalog).resolve()
        failures_path = (ROOT / args.failures).resolve()
        previous_records: list[dict[str, Any]] = []
        if args.resume and processed_path.is_file():
            try:
                previous_records = json.loads(processed_path.read_text(encoding="utf-8")).get("records", [])
            except (OSError, json.JSONDecodeError, AttributeError):
                LOG.warning("Could not load previous processed catalog for sitemap resume")
        original_records = list(catalog["records"])
        seen_ids = {record.get("document_id") for record in original_records}
        discovered_previous = [record for record in previous_records
                               if record.get("record_type") == "public_handbook_document"
                               and record.get("document_id") not in seen_ids]
        catalog["records"].extend(discovered_previous)
        seen_urls = {record.get("url") for record in catalog["records"]}
        existing_sitemap_records = [record for record in catalog["records"]
                                    if record.get("source") == "GitLab Handbook sitemap"]
        client = PublicSession(args.timeout, args.delay, args.retries)
        try:
            new_handbook_records = discover_handbook_sitemap(client, catalog["records"], args.max_handbook_pages)
            catalog["records"].extend(new_handbook_records)
        except DownloadError as exc:
            LOG.warning("Handbook sitemap discovery failed; continuing with catalog records: %s", exc)
        records = catalog["records"]
        departments = {record.get("department") for record in records if record.get("department")}
        urls = sum(bool(record.get("url")) for record in records)
        dataset_refs = sum(record.get("record_type") == "dataset_reference" for record in records)
        synthetic = sum(record.get("record_type") == "synthetic_test_record" for record in records)
        LOG.info("Catalog %s: %d records, %d departments, %d URLs, %d dataset references, %d synthetic fixtures",
                 source_path, len(records), len(departments), urls, dataset_refs, synthetic)
        output_dir = (ROOT / args.output_dir).resolve()
        raw_dir = (ROOT / args.raw_dir).resolve()
        processed_dir = output_dir
        for folder in set(DEPARTMENT_OUTPUT_FOLDERS.values()) | set(RAW_SOURCE_FOLDERS.values()) | {"other_handbooks", "cuad", "maud", "external_datasets"}:
            (output_dir / folder).mkdir(parents=True, exist_ok=True)
            (raw_dir / folder).mkdir(parents=True, exist_ok=True)
        processed = download_records(catalog, policy, client, output_dir, raw_dir, processed_path, failures_path,
                                     source_path, args.limit, args.max_urls, args.enable_pdf, args.resume)
        if args.limit is None and not args.skip_atticus:
            try:
                import_atticus_archives(processed, client, raw_dir, output_dir)
            except (DownloadError, OSError, ValueError, zipfile.BadZipFile) as exc:
                LOG.error("Atticus dataset import did not complete: %s", exc)
        discover_sec_filings(processed, client, policy, output_dir, raw_dir, processed_path, failures_path, source_path,
                             args.sec_cik, args.sec_filings)
        deduplicated: dict[str, str] = {}
        for record in processed:
            content_hash = record.get("content_hash")
            if content_hash and record.get("download_status") in {"downloaded", "partial"}:
                if content_hash in deduplicated:
                    record["is_duplicate"] = True
                    record["duplicate_of"] = deduplicated[content_hash]
                else:
                    deduplicated[content_hash] = record["document_id"]
        processed_metadata = write_processed_dataset(processed, processed_dir)
        validation = validate_processed_dataset(processed, processed_metadata, processed_dir)
        report = dataset_report({"records": original_records}, processed, processed_metadata)
        report["additional_urls_discovered"] = len({record.get("url") for record in processed
                                                     if record.get("record_type") == "public_handbook_document"})
        report["validation"] = validation
        atomic_write((ROOT / args.dataset_report).resolve(), json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        persist_state(processed, processed_path, failures_path, source_path)
        LOG.info("Dataset report: %s", json.dumps(report, ensure_ascii=False))
        if not validation["valid"]:
            LOG.error("Processed dataset validation found %d errors", len(validation["validation_errors"]))
            return 1
        return 0
    except (OSError, ValueError, yaml.YAMLError) as exc:
        LOG.error("Setup failed: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())