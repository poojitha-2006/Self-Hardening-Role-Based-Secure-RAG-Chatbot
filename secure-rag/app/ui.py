"""
secure-rag/app/ui.py
====================
Streamlit client for the authenticated Secure RAG API.

Usage:
  From secure-rag/:
    streamlit run app/ui.py

  From repository root:
    streamlit run secure-rag/app/ui.py
"""

from __future__ import annotations

import os
import uuid
from html import escape
from typing import Any, Dict, Optional

import httpx
import streamlit as st

API_BASE_URL = os.environ.get("SECURE_RAG_API_URL", "http://127.0.0.1:8000").rstrip("/")

st.set_page_config(
    page_title="Secure RAG Chatbot",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .main-header { font-size: 2rem; font-weight: 700; margin-bottom: .2rem; color: #1e293b; }
    .sub-header { font-size: .95rem; color: #64748b; margin-bottom: 1.2rem; }
    .user-profile-box { background: #f8fafc; border: 1px solid #e2e8f0;
        border-radius: 10px; padding: 14px 16px; margin-bottom: 14px; }
    .verdict { border-radius: 6px; padding: 7px 10px; margin-top: 8px; }
    .verdict-allowed { background: #f0fdf4; border: 1px solid #bbf7d0; color: #15803d; }
    .verdict-blocked { background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c; }
    .source-tag { display: inline-block; background: #eff6ff; color: #1d4ed8;
        border: 1px solid #bfdbfe; border-radius: 4px; padding: 2px 7px;
        font-size: .78rem; font-family: monospace; margin: 2px 4px 4px 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

for key, default in (
    ("jwt_token", None),
    ("current_user", None),
    ("messages", []),
    ("active_tab", "Chat"),
    ("session_id", str(uuid.uuid4())),
):
    if key not in st.session_state:
        st.session_state[key] = default


class ApiError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def _clear_session() -> None:
    st.session_state.jwt_token = None
    st.session_state.current_user = None
    st.session_state.messages = []
    st.session_state.session_id = str(uuid.uuid4())


def _api_request(
    method: str,
    path: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = httpx.request(
            method,
            f"{API_BASE_URL}{path}",
            json=payload,
            headers=headers,
            timeout=60,
        )
    except httpx.RequestError as exc:
        raise ApiError("Could not reach the Secure RAG API.") from exc

    if response.status_code == 401 and token:
        _clear_session()
        raise ApiError("Your session is no longer valid. Please sign in again.", 401)
    if not response.is_success:
        if response.status_code == 403:
            message = "Administrator access is required for this operation."
        elif response.status_code == 401:
            message = "Invalid username or password."
        else:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            message = str(detail) if detail else f"API request failed (HTTP {response.status_code})."
        raise ApiError(message, response.status_code)
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise ApiError("The Secure RAG API returned an invalid response.") from exc


def _request_with_session(method: str, path: str, payload: Optional[Dict[str, Any]] = None):
    return _api_request(
        method,
        path,
        payload=payload,
        token=st.session_state.jwt_token,
    )


def _render_sources(sources: list[dict[str, Any]]) -> None:
    if not sources:
        return
    st.markdown("**Sources**")
    for source in sources:
        label = escape(str(source.get("title") or source.get("doc_id") or "Source"))
        st.markdown(f"<span class='source-tag'>{label}</span>", unsafe_allow_html=True)
    if st.session_state.current_user.get("role") == "Administrator":
        with st.expander("Retrieved source previews (Administrator)"):
            for source in sources:
                st.write(source.get("title") or source.get("doc_id"))
                st.caption(
                    f"Document: {source.get('doc_id')} · "
                    f"Chunk: {source.get('chunk_index')} · "
                    f"Minimum role: {source.get('min_role')}"
                )
                if source.get("content_preview"):
                    st.write(source["content_preview"])


def _render_verdict(message: Dict[str, Any]) -> None:
    blocked = message.get("blocked")
    class_name = "verdict-blocked" if blocked else "verdict-allowed"
    label = "Blocked" if blocked else "Allowed"
    details = []
    for decision in message.get("guardrail_decisions", []):
        layer = decision.get("layer")
        action = decision.get("decision")
        if layer and action:
            details.append(f"{layer}: {action}")
        score = decision.get("score")
        if isinstance(score, (int, float)):
            details.append(f"score {score:.2f}")
    if message.get("latency_ms") is not None:
        details.append(f"{message['latency_ms']:.0f} ms")
    if message.get("block_reason"):
        details.append(str(message["block_reason"]))
    detail_text = escape(" · ".join(details))
    label = escape(label)
    st.markdown(
        f"<div class='verdict {class_name}'><strong>{label}</strong>"
        f"{' · ' + detail_text if detail_text else ''}</div>",
        unsafe_allow_html=True,
    )


def _show_login() -> None:
    st.markdown("<div class='main-header'>Secure RAG Chatbot</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='sub-header'>Sign in with your assigned username and password.</div>",
        unsafe_allow_html=True,
    )
    with st.form("login_form"):
        username = st.text_input("Username", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Sign in", use_container_width=True)
    if submitted:
        try:
            login_data = _api_request(
                "POST",
                "/login",
                payload={"username": username, "password": password},
            )
        except ApiError as exc:
            st.error(
                "Invalid username or password."
                if exc.status_code == 401
                else str(exc)
            )
        else:
            st.session_state.jwt_token = login_data["access_token"]
            st.session_state.current_user = {
                key: login_data.get(key)
                for key in ("username", "role", "department", "display_role", "full_name")
            }
            st.session_state.messages = []
            st.rerun()


def _render_chat() -> None:
    user = st.session_state.current_user
    st.markdown("<div class='main-header'>Secure RAG Assistant</div>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='sub-header'>Authenticated as {escape(str(user['username']))} "
        f"({escape(str(user['display_role']))} · {escape(str(user['department']))})</div>",
        unsafe_allow_html=True,
    )
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])
            if message["role"] == "assistant":
                _render_sources(message.get("sources", []))
                _render_verdict(message)

    query = st.chat_input("Type your question...")
    if not query:
        return
    st.session_state.messages.append({"role": "user", "content": query})
    with st.chat_message("user"):
        st.write(query)
    try:
        with st.chat_message("assistant"):
            with st.spinner("Processing through the authenticated API..."):
                response = _request_with_session(
                    "POST",
                    "/chat",
                    {"message": query, "session_id": st.session_state.session_id},
                )
            assistant_message = {
                "role": "assistant",
                "content": response["answer"],
                "sources": response.get("sources", []),
                "guardrail_decisions": response.get("guardrail_decisions", []),
                "blocked": response.get("blocked"),
                "block_reason": response.get("block_reason"),
                "latency_ms": response.get("latency_ms"),
            }
            st.write(assistant_message["content"])
            _render_sources(assistant_message["sources"])
            _render_verdict(assistant_message)
    except ApiError as exc:
        st.error(str(exc))
        if (
            st.session_state.messages
            and st.session_state.messages[-1].get("content") == query
        ):
            st.session_state.messages.pop()
        return
    st.session_state.messages.append(assistant_message)
    st.rerun()


def _render_admin_dashboard() -> None:
    st.markdown("<div class='main-header'>Security Dashboard</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='sub-header'>Data is served by administrator-protected API endpoints.</div>",
        unsafe_allow_html=True,
    )
    try:
        stats = _request_with_session("GET", "/security/stats")
        metrics = _request_with_session("GET", "/admin/metrics")
        audit = _request_with_session("GET", "/admin/audit")
        attacks = _request_with_session("GET", "/admin/attacks")
        results = _request_with_session("GET", "/admin/results")
    except ApiError as exc:
        st.error(str(exc))
        return

    st.metric("Known attack store size", stats["known_attack_store_size"])
    st.caption(f"Guardrail mode: {stats['guardrail_mode']}")
    st.subheader("Evaluation metrics")
    if metrics["rounds"]:
        st.dataframe(metrics["rounds"], use_container_width=True)
        st.caption(f"Source file: {metrics['source_file']}")
    else:
        st.info("No evaluation metrics are available from the API.")

    st.subheader("Recent audit events")
    if audit["events"]:
        st.dataframe(audit["events"], use_container_width=True)
    else:
        st.info("No audit events are available.")

    with st.expander(f"Attack store entries ({attacks['total']})"):
        if attacks["attacks"]:
            st.dataframe(attacks["attacks"], use_container_width=True)
        else:
            st.info("The attack store contains no entries.")

    with st.expander("Existing evaluation result files"):
        if results["results"]:
            for result in results["results"]:
                st.markdown(f"**{result['filename']}**")
                st.json(result["data"])
        else:
            st.info("No evaluation result files are available.")

    st.subheader("Run red-team round")
    if st.button("Run red-team round", type="primary"):
        try:
            with st.spinner("Running the configured red-team round..."):
                report = _request_with_session(
                    "POST",
                    "/admin/redteam/run",
                    {"rounds": 1, "attacker_role": "Analyst"},
                )
            st.json(report)
        except ApiError as exc:
            st.error(str(exc))


def _render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### Secure RAG")
        st.caption("Enterprise RBAC & self-hardening guardrails")
        st.markdown("---")
        user = st.session_state.current_user
        if not user:
            st.caption("Sign in to access the chatbot.")
            return

        st.markdown(
            f"**{escape(str(user['username']))}**  \n"
            f"{escape(str(user['display_role']))} · {escape(str(user['department']))}"
        )
        if st.button("Log out", use_container_width=True):
            _clear_session()
            st.rerun()

        if user.get("role") == "Administrator":
            st.session_state.active_tab = st.radio(
                "View",
                ("Chat", "Admin dashboard"),
                key="authenticated_navigation",
            )

        st.markdown("---")
        st.markdown("#### System status")
        try:
            stats = _request_with_session("GET", "/security/stats")
            st.caption(f"Guardrail mode: `{stats['guardrail_mode']}`")
            st.caption(f"Known attacks: `{stats['known_attack_store_size']}`")
        except ApiError as exc:
            st.caption(str(exc))


_render_sidebar()
if not st.session_state.current_user:
    _show_login()
elif (
    st.session_state.current_user.get("role") == "Administrator"
    and st.session_state.active_tab == "Admin dashboard"
):
    _render_admin_dashboard()
else:
    _render_chat()
