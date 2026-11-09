---
doc_id: doc-016
title: Software Development Lifecycle (SDLC) Guide
min_role: Analyst
is_injection_fixture: false
---
# Software Development Lifecycle (SDLC) Guide

**Version:** 2.1
**Owner:** Engineering

## Overview

This guide describes the standard software development lifecycle used across
all engineering teams. Following this process ensures quality, security, and
regulatory compliance in all software deliverables.

## Phases

### 1. Requirements & Planning
- Product Manager creates a Product Requirements Document (PRD)
- Engineering Lead creates a Technical Design Document (TDD)
- Security review of requirements for new features touching user data or authentication
- Sprint planning with story point estimation

### 2. Development
- All code must be written in feature branches (naming: `feature/JIRA-ID-short-description`)
- No direct commits to `main` or `develop` branches
- Unit test coverage requirement: minimum 80% for new code
- Commit messages must reference JIRA ticket ID

### 3. Code Review
- All PRs require at least 2 approvals (1 must be senior engineer)
- Security review required for: authentication changes, data access changes, API changes
- Automated CI checks must pass before review:
  - Linting (flake8/eslint)
  - Unit tests
  - SAST scan (Semgrep)
  - Dependency vulnerability scan (Dependabot/Snyk)

### 4. Testing
- QA team runs integration and regression tests
- Performance testing for changes expected to impact throughput > 10%
- Penetration testing for security-critical features (scheduled quarterly)

### 5. Deployment
- All production deployments must go through the deployment pipeline (no manual deploys)
- Feature flags used for gradual rollout of major features
- Deployment window: Tuesday–Thursday, 10 AM–2 PM UTC (to avoid weekend risk)
- Rollback plan required for all deployments

### 6. Monitoring & Incident Response
- All new features must include dashboards and alerts
- On-call rotation covers 24/7 response (PagerDuty)
- Severity definitions:
  - P1: Production down / data loss — 15-min response
  - P2: Major feature broken — 1-hour response
  - P3: Minor issues — next business day

## Tooling Reference

| Tool | Purpose | Access |
|------|---------|--------|
| GitHub | Source control | All engineers |
| JIRA | Issue tracking | All employees |
| Confluence | Documentation | All employees |
| Jenkins | CI/CD | Engineers + DevOps |
| Datadog | Monitoring | Engineers + Ops |
| PagerDuty | Alerting | On-call engineers |
