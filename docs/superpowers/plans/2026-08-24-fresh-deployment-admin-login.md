# Fresh Deployment And Admin Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a clean first-install server package with configurable ports and username/password administration.

**Architecture:** Docker Compose exposes only configurable Caddy ports while Uvicorn uses a configurable internal port. A singleton administrator is seeded into an empty database, passwords are Argon2-hashed, and signed versioned admin sessions preserve the existing `X-Admin-Token` API contract.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Argon2, React, TypeScript, Docker Compose, Caddy, PowerShell, POSIX shell.

---

### Task 1: Configurable Deployment Ports

**Files:**
- Modify: `backend/tests/test_deploy.py`
- Modify: `deploy/compose.yml`
- Modify: `deploy/Caddyfile`
- Modify: `deploy/entrypoint.sh`
- Modify: `deploy/.env.example`
- Modify: `deploy/bootstrap.ps1`
- Modify: `deploy/bootstrap.sh`
- Modify: `debug/start-preview.ps1`

- [ ] Write assertions for `APP_PORT`, dynamic Caddy upstream, dynamic health check, and preview parameters.
- [ ] Run `pytest backend/tests/test_deploy.py -q` and confirm the new assertions fail.
- [ ] Pass the variables through Compose, Caddy, Uvicorn and preview startup.
- [ ] Re-run the deployment tests and confirm they pass.

### Task 2: Administrator Credential And Session Backend

**Files:**
- Create: `backend/app/core/admin_auth.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/db/models.py`
- Create: `backend/migrations/versions/20260824_0006_admin_credentials.py`
- Modify: `backend/app/services/store.py`
- Modify: `backend/app/api/relay.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_admin_auth.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_config.py`
- Modify: `backend/tests/test_migrations.py`

- [ ] Write failing tests for initial seeding, password login, signed sessions, recovery token compatibility and password-change invalidation.
- [ ] Run the focused tests and confirm failure is caused by missing administrator behavior.
- [ ] Add the administrator table, Argon2 store operations and signed session codec.
- [ ] Add login, session validation and account-update endpoints.
- [ ] Run the focused and full backend suites.

### Task 3: Username And Password Management UI

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.css`
- Modify: `web/src/App.test.tsx`

- [ ] Replace token-login tests with username/password login and session restoration tests.
- [ ] Confirm the focused frontend tests fail before implementation.
- [ ] Add the login request, session storage, logout and account-update form.
- [ ] Run all frontend tests and the production build.

### Task 4: Clean Bootstrap And Documentation

**Files:**
- Modify: `deploy/bootstrap.ps1`
- Modify: `deploy/bootstrap.sh`
- Modify: `deploy/README.md`
- Modify: `docs/DEPLOYMENT_AUDIT_2026-08-23.md`
- Modify: `deploy/package.ps1`

- [ ] Assert bootstrap output and clean-package rules in deployment tests.
- [ ] Generate missing administrator credentials without overwriting existing values.
- [ ] Document first install, custom ports, login, recovery token and the purpose of Alembic files.
- [ ] Rebuild the ZIP and scan all entries for runtime data and secrets.

### Task 5: Final Verification

**Files:**
- Verify all changed source, tests, documentation and package artifacts.

- [ ] Run the full backend test suite.
- [ ] Run Ruff and Python compilation checks.
- [ ] Run Air724 contract tests.
- [ ] Run frontend tests and production build.
- [ ] Rebuild the deployment archive, verify required entries and forbidden paths, and record SHA-256.
