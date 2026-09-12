import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.release_catalog import load_releases

ROOT = Path(__file__).resolve().parents[2]

# Windows PowerShell 5.1 writes a UTF-8 BOM for every UTF8 value of its
# text-writing cmdlets, so none of them may write a file that a non-Windows
# parser reads: the release catalog goes through json.loads, and deploy/.env
# through sed. The scripts use [System.Text.UTF8Encoding]::new($false) instead.
_UTF8_TEXT_WRITE_CMDLETS = re.compile(r"(?i)\b(?:Set-Content|Add-Content|Out-File)\b")


def test_compose_uses_postgres_and_required_runtime_secrets() -> None:
    compose = (ROOT / "deploy" / "compose.yml").read_text(encoding="utf-8")

    assert "  postgres:" in compose
    assert "DATABASE_URL:" in compose
    assert "POSTGRES_PASSWORD:" in compose
    assert "condition: service_healthy" in compose
    assert "STATE_FILE:" not in compose
    assert '"${HTTP_PORT:-39300}:80"' in compose
    # TLS 在上游反代（宝塔 nginx）终结：443/QUIC 端口映射已移除，
    # Caddy 只保留 HTTP 监听作为反代上游。HTTPS_PORT 仍留在 .env 供运行时展示。
    assert '"${HTTPS_PORT:-39301}:443"' not in compose
    assert '"${HTTPS_PORT:-39301}:443/udp"' not in compose
    assert "APP_PORT: ${APP_PORT:-39200}" in compose
    assert '"${APP_PORT:-39200}"' in compose
    assert "os.environ.get('APP_PORT', '39200')" in compose
    assert 'entrypoint: ["python", "-m", "app.mqtt_bridge"]' in compose
    assert "MQTT_BROKER_HOST: ${MQTT_BROKER_HOST:-}" in compose
    assert "RELAY_MQTT_BRIDGE_TOKEN: ${RELAY_MQTT_BRIDGE_TOKEN:-}" in compose


def test_caddy_uses_explicit_http_or_domain_site_without_a_fake_hostname() -> None:
    env_example = (ROOT / "deploy" / ".env.example").read_text(encoding="utf-8")
    bootstrap = (ROOT / "deploy" / "bootstrap.ps1").read_text(encoding="utf-8")
    caddy = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    caddy_domain = (ROOT / "deploy" / "Caddyfile.domain").read_text(encoding="utf-8")
    caddy_entrypoint = (ROOT / "deploy" / "caddy-entrypoint.sh").read_text(encoding="utf-8")
    compose = (ROOT / "deploy" / "compose.yml").read_text(encoding="utf-8")

    assert "RELAY_SITE=:80" in env_example
    assert "RELAY_DOMAIN=" in env_example
    assert "RELAY_SITE = ':80'" in bootstrap
    assert "RELAY_DOMAIN = ''" in bootstrap
    assert ":80 {" in caddy
    assert "import relay_proxy" in caddy
    assert "__RELAY_DOMAIN__" in caddy_domain
    assert "@domain host __RELAY_DOMAIN__" in caddy_domain
    assert "redir @domain https://__RELAY_DOMAIN__{uri}" in caddy_domain
    assert "reverse_proxy app:{$APP_PORT}" in caddy
    assert "RELAY_DOMAIN: ${RELAY_DOMAIN:-}" in compose
    assert "caddy-entrypoint.sh" in compose
    assert "Caddyfile.domain" in compose
    assert 'if [ -z "$domain" ] && [ "${RELAY_SITE:-:80}" != ":80" ]; then' in caddy_entrypoint
    assert 'sed "s/__RELAY_DOMAIN__/$domain/g"' in caddy_entrypoint
    assert "relay.example.com" not in caddy


def test_deployment_documents_keep_caddy_internal_and_host_ports_separate() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    deploy_readme = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8")

    assert "不要改成 `:39300`" in readme
    assert "宿主机 `39300 -> Caddy 容器 80`" in deploy_readme
    assert "`RELAY_SITE=:80`" in deploy_readme
    assert "`RELAY_DOMAIN`" in deploy_readme
    # TLS 由上游反代终结：443/QUIC 映射已从 Compose 移除（见文档说明）。
    assert "TLS 由上游反向代理" in deploy_readme
    assert "改为 `RELAY_SITE=:18080`" not in deploy_readme


def test_deployment_bootstrap_generates_missing_secrets_without_overwriting_existing_values() -> None:
    bootstrap = (ROOT / "deploy" / "bootstrap.ps1").read_text(encoding="utf-8")

    assert "RandomNumberGenerator" in bootstrap
    assert "ADMIN_TOKEN" in bootstrap
    assert "ADMIN_USERNAME" in bootstrap
    assert "ADMIN_PASSWORD" in bootstrap
    assert "DATA_ENCRYPTION_KEY" in bootstrap
    assert "POSTGRES_PASSWORD" in bootstrap
    assert "DATABASE_URL" in bootstrap
    assert "Test-Path -LiteralPath $Path" in bootstrap
    # The .env writer is asserted by
    # test_packaging_scripts_write_utf8_files_without_a_byte_order_mark: on Windows
    # PowerShell 5.1 the cmdlet form put a UTF-8 BOM on the first key name.
    assert "[System.IO.File]::WriteAllLines($envFile" in bootstrap
    assert "Do not overwrite" in bootstrap
    assert "APP_PORT = '39200'" in bootstrap
    assert "Generated administrator credentials" in bootstrap
    assert "Recovery token" in bootstrap


def test_deployment_preflight_checks_configuration_and_instance_isolation() -> None:
    powershell = (ROOT / "deploy" / "preflight.ps1").read_text(encoding="utf-8")
    unix = (ROOT / "deploy" / "preflight.sh").read_text(encoding="utf-8")

    for script in (powershell, unix):
        assert "ADMIN_PASSWORD" in script
        assert "DATA_ENCRYPTION_KEY" in script
        assert "HTTP_PORT" in script
        assert "HTTPS_PORT" in script
        assert "APP_PORT" in script
        assert "PUBLIC_BASE_URL" in script
        assert "RELAY_DOMAIN" in script
        assert "docker compose" in script
    assert "Resolve-DnsName" in powershell
    assert "getent ahosts" in unix
    # The old shape glob matched the `//` in `scheme://` and rejected every
    # non-empty PUBLIC_BASE_URL; the check now runs on the scheme-stripped
    # authority only.
    assert "*/[!/]*" not in unix
    assert "base_authority=${public_base_url#*://}" in unix
    for script in (powershell, unix):
        assert "PUBLIC_BASE_URL host does not match RELAY_DOMAIN" in script


def test_deployment_preflight_guards_against_a_second_compose_stack() -> None:
    powershell = (ROOT / "deploy" / "preflight.ps1").read_text(encoding="utf-8")
    unix = (ROOT / "deploy" / "preflight.sh").read_text(encoding="utf-8")

    for script in (powershell, unix):
        assert '{{.Label "com.docker.compose.project"}}' in script
        assert '{{.Label "com.docker.compose.project.config_files"}}' in script
        assert '{{.Label "com.docker.compose.project.working_dir"}}' in script
        assert 'index .Labels "com.docker.compose.project"' not in script
        assert "multiple Compose projects use" in script
        assert "COMPOSE_PROJECT_NAME=" in script
        assert "_postgres_data$" in script


def test_bootstrap_and_env_template_do_not_force_compose_project_name() -> None:
    unix_bootstrap = (ROOT / "deploy" / "bootstrap.sh").read_text(encoding="utf-8")
    powershell_bootstrap = (ROOT / "deploy" / "bootstrap.ps1").read_text(encoding="utf-8")
    env_template = (ROOT / "deploy" / ".env.example").read_text(encoding="utf-8")

    assert "set_if_empty COMPOSE_PROJECT_NAME" not in unix_bootstrap
    assert "COMPOSE_PROJECT_NAME = " not in powershell_bootstrap
    assert "COMPOSE_PROJECT_NAME=relay" not in env_template
    assert "# COMPOSE_PROJECT_NAME=" in env_template


def test_compose_passes_admin_login_credentials_to_the_application() -> None:
    compose = (ROOT / "deploy" / "compose.yml").read_text(encoding="utf-8")

    assert "ADMIN_USERNAME: ${ADMIN_USERNAME:?set ADMIN_USERNAME in .env}" in compose
    assert "ADMIN_PASSWORD: ${ADMIN_PASSWORD:?set ADMIN_PASSWORD in .env}" in compose


def test_unix_bootstrap_generates_and_reports_admin_credentials() -> None:
    bootstrap = (ROOT / "deploy" / "bootstrap.sh").read_text(encoding="utf-8")

    assert "set_if_empty ADMIN_USERNAME admin" in bootstrap
    assert "set_if_empty ADMIN_PASSWORD" in bootstrap
    assert "Generated administrator credentials" in bootstrap
    assert "Recovery token" in bootstrap


def test_container_runs_migrations_before_the_api() -> None:
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (ROOT / "deploy" / "entrypoint.sh").read_text(encoding="utf-8")
    migration_env = (ROOT / "backend" / "migrations" / "env.py").read_text(encoding="utf-8")

    assert "deploy/entrypoint.sh" in dockerfile
    assert "alembic upgrade head" in entrypoint
    assert "exec uvicorn" in entrypoint
    assert '${APP_PORT:-39200}' in entrypoint
    assert "--workers 1" in entrypoint
    assert 'os.environ.get("DATABASE_URL", "")' in migration_env


def test_deployment_keeps_single_worker_for_the_in_process_otp_broker() -> None:
    entrypoint = (ROOT / "deploy" / "entrypoint.sh").read_text(encoding="utf-8")
    caddy = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    plan = (ROOT / "docs" / "superpowers" / "plans" / "2026-08-23-air724-websocket-otp.md").read_text(encoding="utf-8")

    assert "--workers 1" in entrypoint
    assert "reverse_proxy app:{$APP_PORT}" in caddy
    assert "single-process" in plan
    assert "shared Redis/PostgreSQL notification broker" in plan


def test_air724_download_package_is_in_the_server_assets() -> None:
    downloads = ROOT / "web" / "public" / "assets" / "downloads"

    assert (downloads / "air724-otp-websocket.lua").is_file()
    assert (downloads / "air724-otp-websocket" / "main.lua").is_file()
    assert (downloads / "air724-mqtt-ingest.lua").is_file()
    assert (downloads / "air724-otp-websocket" / "config.example.lua").is_file()
    assert (downloads / "air724-otp-websocket" / "README.md").is_file()


def test_generic_script_api_reference_client_is_in_the_server_package_source() -> None:
    script_client = ROOT / "clients" / "script_api"

    assert (script_client / "otp_client.py").is_file()
    assert (script_client / "README.md").is_file()
    assert (ROOT / "backend" / "tests" / "test_script_api_network.py").is_file()


def test_alembic_has_no_implicit_local_database_fallback() -> None:
    alembic_ini = (ROOT / "backend" / "alembic.ini").read_text(encoding="utf-8")
    migration_env = (ROOT / "backend" / "migrations" / "env.py").read_text(encoding="utf-8")

    assert "relay-dev.db" not in alembic_ini
    assert "__DATABASE_URL_REQUIRED__" in alembic_ini
    assert "DATABASE_URL must be set" in migration_env


def test_repository_ignores_runtime_databases() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "*.db" in ignore
    assert "*.db.bak-*" in ignore
    assert "*-wal" in ignore
    assert "*-shm" in ignore


def test_database_artifacts_are_ignored_and_storage_boundaries_are_documented() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    deploy_readme = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8")

    for pattern in ("*.db", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3"):
        assert pattern in gitignore
    assert "`database_fingerprint`" in deploy_readme
    assert "`postgres_data`" in deploy_readme
    assert "`/data/adb/sms-relay`" in readme
    assert "`sms-relay-queue.db`" in readme


def test_server_package_excludes_local_runtime_and_secret_files() -> None:
    package_script = (ROOT / "deploy" / "package.ps1").read_text(encoding="utf-8")

    assert "web/public" in package_script
    assert "node_modules" in package_script
    assert "backend/.venv" in package_script
    assert "backend/.mypy_cache" in package_script
    assert "backend/.pytest_cache" in package_script
    assert "backend/.ruff_cache" in package_script
    assert "__pycache__" in package_script
    assert "sqlite3" in package_script
    assert ".pyc" in package_script
    assert "deploy/.env" in package_script
    assert "deploy/dist" in package_script
    assert ".dockerignore" in package_script
    assert "web/public/assets/downloads/air724-otp-websocket.lua" in package_script
    assert "web/public/assets/downloads/air724-otp-websocket/main.lua" in package_script
    assert "web/public/assets/downloads/air724-otp-websocket/config.example.lua" in package_script
    assert "web/public/assets/downloads/air724-otp-websocket/README.md" in package_script
    assert "web/public/assets/downloads/air724-mqtt-ingest.lua" in package_script
    assert "clients/air724/mqtt_ingest.lua" in package_script
    assert "backend/app/mqtt_bridge.py" in package_script
    assert "clients/script_api/otp_client.py" in package_script
    assert "clients/script_api/README.md" in package_script
    assert "docs/SESSION_01A06BD1_HANDOFF.md" in package_script
    assert "web/public/assets/releases/release-catalog.json" in package_script
    assert "release.download_url" in package_script
    assert 'platform -ne "deploy"' in package_script
    assert "Select-Object -First 2" in package_script
    assert "web/public/downloads" in package_script
    assert "sms-relay-root-module-*.zip" in package_script
    assert "manager-android" in package_script
    assert "*.p12" in package_script
    assert "*signing.properties" in package_script


def test_server_package_writes_forward_slash_zip_entry_names() -> None:
    # Compress-Archive on Windows PowerShell 5.1 stores backslash-separated entry
    # names. Info-ZIP on the server still extracts them, but it warns and returns
    # exit code 1, which looks like a failed upload. clients/root-module/pack.ps1
    # already writes entries by hand; package.ps1 now uses the same pattern.
    package_script = (ROOT / "deploy" / "package.ps1").read_text(encoding="utf-8")

    assert "Compress-Archive" not in package_script
    assert "CreateEntryFromFile" in package_script
    assert "Replace('\\', '/')" in package_script
    assert "Add-Type -AssemblyName System.IO.Compression.FileSystem" in package_script
    assert "backslash-separated entry names" in package_script


def test_manager_release_scripts_require_external_signing_and_publish_history() -> None:
    build_script = (ROOT / "manager-android" / "build-release.ps1").read_text(encoding="utf-8")
    publish_script = (ROOT / "manager-android" / "publish-update.ps1").read_text(encoding="utf-8")

    for variable in (
        "RELAY_MANAGER_SIGNING_STORE_FILE",
        "RELAY_MANAGER_SIGNING_STORE_PASSWORD",
        "RELAY_MANAGER_SIGNING_KEY_ALIAS",
        "RELAY_MANAGER_SIGNING_KEY_PASSWORD",
    ):
        assert variable in build_script
    assert "io.smsrelay.manager" in publish_script
    assert "platform = 'manager'" in publish_script
    assert "Relay-Hub-Manager-$versionName.apk" in publish_script
    assert "Signer SHA-256" in publish_script


def _kts_value(source: str, key: str) -> str:
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key} ="):
            return stripped.split("=", 1)[1].strip().strip('"')
    raise AssertionError(f"{key} is missing from build.gradle.kts")


def test_published_manager_release_matches_catalog_and_apk() -> None:
    gradle = (ROOT / "manager-android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    expected_code = int(_kts_value(gradle, "versionCode"))
    expected_name = _kts_value(gradle, "versionName")

    catalog_path = ROOT / "web" / "public" / "assets" / "releases" / "release-catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    manager_releases = [release for release in catalog["releases"] if release["platform"] == "manager"]

    assert manager_releases
    release = max(manager_releases, key=lambda item: item["version_code"])
    assert release["version_code"] == expected_code
    assert release["version_name"] == expected_name
    assert release["download_url"].startswith("/assets/releases/manager/")
    assert len(release["signer_sha256"]) == 64
    apk = ROOT / "web" / "public" / release["download_url"].removeprefix("/")
    assert apk.is_file()
    assert apk.stat().st_size == release["size_bytes"]
    assert hashlib.sha256(apk.read_bytes()).hexdigest() == release["sha256"]


def _prop_value(source: str, key: str) -> str:
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            return stripped.split("=", 1)[1].strip()
    raise AssertionError(f"{key} is missing from module.prop")


def test_root_module_publish_script_and_release_history_are_consistent() -> None:
    publish_script = (ROOT / "clients" / "root-module" / "publish-update.ps1").read_text(encoding="utf-8")
    assert "platform = 'root'" in publish_script
    assert "signer_sha256 = $null" in publish_script
    assert "module.prop" in publish_script

    module_prop = (ROOT / "clients" / "root-module" / "module.prop").read_text(encoding="utf-8")
    expected_code = int(_prop_value(module_prop, "versionCode"))
    expected_name = _prop_value(module_prop, "version")

    catalog_path = ROOT / "web" / "public" / "assets" / "releases" / "release-catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    root_releases = [release for release in catalog["releases"] if release["platform"] == "root"]
    assert root_releases
    release = max(root_releases, key=lambda item: item["version_code"])
    assert release["version_code"] == expected_code
    assert release["version_name"] == expected_name
    assert release["signer_sha256"] is None
    archive = ROOT / "web" / "public" / release["download_url"].removeprefix("/")
    assert archive.is_file()
    assert archive.stat().st_size == release["size_bytes"]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == release["sha256"]


def test_container_update_package_preserves_release_store_and_matches_catalog() -> None:
    package_update = (ROOT / "deploy" / "package-update.ps1").read_text(encoding="utf-8")
    package_script = (ROOT / "deploy" / "package.ps1").read_text(encoding="utf-8")
    publish_update = (ROOT / "deploy" / "publish-update.ps1").read_text(encoding="utf-8")
    apply_update = (ROOT / "deploy" / "apply-update.sh").read_text(encoding="utf-8")

    assert "ExcludeReleaseStore" not in package_update
    assert "ExcludeReleaseStore" not in package_script
    assert "web/public/assets/releases" in package_script
    # A shipped package must not carry the TypeScript incremental build cache: it embeds
    # absolute paths from the packaging machine and can make `tsc -b` skip emitting.
    assert "web/tsconfig.tsbuildinfo" in package_script
    assert "platform = 'deploy'" in publish_update
    assert "signer_sha256 = $null" in publish_update
    assert "relay-backup-" in apply_update
    assert '"platform"[[:space:]]*:[[:space:]]*"deploy"' in apply_update
    assert "web/public/assets/releases/deploy" in apply_update
    assert "web/public/assets/releases" in apply_update
    assert "deploy/.env" in apply_update
    assert "docker compose" in apply_update
    assert 'docker compose -p "$COMPOSE_PROJECT_NAME"' in apply_update
    assert "com.docker.compose.project.config_files" in apply_update
    assert "com.docker.compose.project.working_dir" in apply_update
    assert "docker ps -a" in apply_update
    assert "compose_projects_for_install" in apply_update
    assert '{{.Label "com.docker.compose.project"}}' in apply_update
    # docker ps renders .Labels as one comma-joined string, so
    # `index .Labels "<key>"` is a template error that silently hides every
    # existing Compose project. Only the prose counter-example may mention it.
    assert 'index .Labels "com.docker.compose.project"' not in apply_update
    assert "RELAY_COMPOSE_PROJECT" in apply_update
    assert "RELAY_UPDATE_DETECT_ONLY" in apply_update
    # The update must never source generated secrets as shell code.
    assert '. "$TARGET/deploy/.env"' not in apply_update
    assert "env_file_value POSTGRES_USER" in apply_update
    assert 'write_env_value COMPOSE_PROJECT_NAME "$COMPOSE_PROJECT_NAME"' in apply_update
    assert "multiple running Compose projects use" in apply_update
    assert "no existing app or caddy Compose project was detected" in apply_update

    deploy_readme = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8")
    assert 'index .Labels "com.docker.compose.project"' not in deploy_readme
    assert '{{.Label "com.docker.compose.project"}}' in deploy_readme
    assert "RELAY_COMPOSE_PROJECT" in deploy_readme
    assert "RELAY_UPDATE_DETECT_ONLY" in deploy_readme
    assert "compose build app" in apply_update
    assert "compose build --no-cache" not in apply_update
    assert 'cp "$SOURCE_ROOT/deploy/compose.yml" "$TARGET/deploy/compose.yml"' in apply_update
    assert 'update package is missing the filtered release catalog' in apply_update
    assert 'find "$BACKUP_DIR/release-store" -type f' not in apply_update
    assert 'if [ ! -e "$target_release_file" ]; then' not in apply_update
    assert 'cp "$SOURCE_ROOT/web/public/assets/releases/release-catalog.json"' not in apply_update
    assert 'cp -a "$SOURCE_ROOT/backend" "$SOURCE_ROOT/clients" "$SOURCE_ROOT/deploy" "$SOURCE_ROOT/web" "$TARGET/"' in apply_update

    # A failed update has already committed `alembic upgrade head` inside the entrypoint
    # (it runs before the health check), so rolling back only the source leaves the
    # database ahead of the revision the restored code knows and the app crash-loops with
    # "Can't locate revision identified by ...". The rollback must restore the dump too.
    assert "compose exec -T postgres pg_restore" in apply_update
    assert "--clean --if-exists" in apply_update
    assert "database restored from the pre-update backup" in apply_update
    # The app health check needs a cold-start grace period: without start_period a slow
    # first boot is judged unhealthy and `compose up -d` aborts the whole update.
    assert "start_period" in (ROOT / "deploy" / "compose.yml").read_text(encoding="utf-8")

    catalog_path = ROOT / "web" / "public" / "assets" / "releases" / "release-catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    deploy_releases = [release for release in catalog["releases"] if release["platform"] == "deploy"]
    assert deploy_releases
    release = max(deploy_releases, key=lambda item: item["version_code"])
    assert release["version_code"] >= 20260901
    assert release["version_name"]
    assert release["signer_sha256"] is None
    archive = ROOT / "web" / "public" / release["download_url"].removeprefix("/")
    assert archive.is_file()
    assert archive.stat().st_size == release["size_bytes"]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == release["sha256"]


def test_every_release_catalog_artifact_matches_declared_size_and_hash() -> None:
    catalog_path = ROOT / "web" / "public" / "assets" / "releases" / "release-catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

    assert catalog["releases"]
    for release in catalog["releases"]:
        artifact = ROOT / "web" / "public" / release["download_url"].removeprefix("/")
        assert artifact.is_file(), release["download_url"]
        assert artifact.stat().st_size == release["size_bytes"], release["download_url"]
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == release["sha256"], release["download_url"]


def test_packaging_scripts_write_utf8_files_without_a_byte_order_mark() -> None:
    # Third instance of the same Windows PowerShell 5.1 defect family (after the
    # backslash zip entry names): the packaged release catalog was written with a
    # UTF-8 BOM, json.loads refuses a leading BOM, and every /api/v1/releases/...
    # route degraded to 503 release_catalog_invalid, so the admin console and all
    # three clients showed an empty release history. bootstrap.ps1 had the same
    # latent bug on deploy/.env, where the BOM lands on the first key name. The two
    # setup-release-signing.ps1 scripts wrote their properties file the same way;
    # only build-release.ps1 reads it today and Get-Content strips the BOM, but a
    # POSIX reader or a Java Properties loader would drop the first key.
    package_script = (ROOT / "deploy" / "package.ps1").read_text(encoding="utf-8")
    bootstrap_script = (ROOT / "deploy" / "bootstrap.ps1").read_text(encoding="utf-8")
    android_signing = (ROOT / "android" / "setup-release-signing.ps1").read_text(encoding="utf-8")
    manager_signing = (ROOT / "manager-android" / "setup-release-signing.ps1").read_text(encoding="utf-8")

    for script in (package_script, bootstrap_script, android_signing, manager_signing):
        assert "[System.Text.UTF8Encoding]::new($false)" in script
        assert _UTF8_TEXT_WRITE_CMDLETS.search(script) is None
        # A BOM must not survive anywhere in the script either: read_text() keeps a
        # leading one as U+FEFF, and one typed into a comment is just as invisible.
        assert chr(0xFEFF) not in script

    assert "[System.IO.File]::WriteAllText(" in package_script
    assert "[System.IO.File]::WriteAllLines($envFile" in bootstrap_script
    # WriteAllLines resolves a relative path against the .NET current directory
    # rather than the PowerShell location, so both scripts absolutise the target.
    for script in (android_signing, manager_signing):
        assert "(Resolve-Path -LiteralPath $DestinationDirectory).ProviderPath" in script
    assert "[System.IO.File]::WriteAllLines($propertiesPath" in android_signing
    assert "[System.IO.File]::WriteAllLines($properties" in manager_signing
    # Reading may keep -Encoding UTF8: Get-Content strips a BOM instead of adding one.
    assert "Get-Content -LiteralPath $catalogPath -Raw -Encoding UTF8" in package_script
    # The archive is verified after it is written, so a BOM can never ship again.
    assert "starts with a UTF-8 BOM" in package_script
    assert "$catalogBytes[0] -eq 0xEF" in package_script

    publish_update = (ROOT / "deploy" / "publish-update.ps1").read_text(encoding="utf-8")
    assert "New-Object System.Text.UTF8Encoding($false)" in publish_update


def test_release_catalog_loads_when_the_file_starts_with_a_byte_order_mark(tmp_path: Path) -> None:
    catalog = {
        "schema_version": 1,
        "releases": [
            {
                "platform": "root",
                "channel": "stable",
                "version_code": 22,
                "version_name": "0.3.10",
                "published_at": "2026-09-05",
                "min_sdk": 21,
                "mandatory": False,
                "notes": "bom fixture",
                "size_bytes": 1024,
                "sha256": "a" * 64,
                "signer_sha256": None,
                "download_url": "/assets/releases/root/bom-fixture.zip",
            }
        ],
    }
    body = json.dumps(catalog).encode("utf-8")
    bom_catalog = tmp_path / "release-catalog.json"
    bom_catalog.write_bytes(b"\xef\xbb\xbf" + body)

    # json.loads refuses a leading BOM, which is what broke the packaged catalog.
    with pytest.raises(ValueError):
        json.loads(bom_catalog.read_text(encoding="utf-8"))

    releases = load_releases(bom_catalog)
    assert [release["version_code"] for release in releases] == [22]

    plain_catalog = tmp_path / "release-catalog-plain.json"
    plain_catalog.write_bytes(body)
    assert load_releases(plain_catalog) == releases


# --- Compose stack detection (deploy/apply-update.sh) -------------------------
#
# The 2026-09-05 server update refused to run against a healthy `deploy` stack
# because detection used `docker ps --format '{{ index .Labels "..." }}'`, which
# is a template error for the container formatter. These tests run the real
# script in detect-only mode against a stub `docker` so the regression cannot
# come back unnoticed.

_STUB_DOCKER = """#!/bin/sh
if [ "$1" = "ps" ]; then
    if [ -n "${STUB_DOCKER_FAIL:-}" ]; then
        printf '%s\\n' 'stub docker: daemon unreachable' >&2
        exit 1
    fi
    printf '%s' "${STUB_DOCKER_ROWS:-}"
    exit 0
fi
if [ "$1" = "volume" ]; then
    printf '%s' "${STUB_DOCKER_VOLUMES:-}"
    exit 0
fi
exit 0
"""


def _posix_sh() -> str | None:
    override = os.environ.get("RELAY_TEST_SH", "")
    if override and Path(override).is_file():
        return override
    found = shutil.which("sh")
    if found:
        return found
    git = shutil.which("git")
    if git:
        candidate = Path(git).resolve().parent.parent / "bin" / "sh.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def _posix_path(path: Path) -> str:
    text = path.resolve().as_posix()
    if len(text) > 2 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def _detect_only_install(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a minimal fake installation, update source and stub docker."""

    target = tmp_path / "install"
    source = tmp_path / "source"
    stub_dir = tmp_path / "stub-bin"
    for directory in (target / "deploy", source / "deploy", stub_dir):
        directory.mkdir(parents=True, exist_ok=True)

    (target / "deploy" / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    # A value that would run a command if the script ever sourced .env again.
    (target / "deploy" / ".env").write_text(
        "POSTGRES_USER=p$(id -u)_x\nPOSTGRES_DB=sms_relay\n", encoding="utf-8"
    )

    releases = source / "web" / "public" / "assets" / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    (releases / "release-catalog.json").write_text(
        json.dumps({"releases": [{"platform": "root"}]}), encoding="utf-8"
    )
    shutil.copyfile(ROOT / "deploy" / "apply-update.sh", source / "deploy" / "apply-update.sh")

    stub = stub_dir / "docker"
    stub.write_text(_STUB_DOCKER, encoding="utf-8", newline="\n")
    stub.chmod(0o755)
    return target, source, stub_dir


def _run_detect_only(
    tmp_path: Path, rows: str, *, project: str = "", fail: bool = False
) -> subprocess.CompletedProcess[str]:
    shell = _posix_sh()
    if shell is None:
        pytest.skip("POSIX sh is not available on this host")

    target, source, stub_dir = _detect_only_install(tmp_path)
    backup_root = tmp_path / "history"
    env = dict(os.environ)
    env["PATH"] = str(stub_dir) + os.pathsep + env.get("PATH", "")
    env["STUB_DOCKER_ROWS"] = rows.replace("__TARGET__", _posix_path(target))
    env["RELAY_UPDATE_DETECT_ONLY"] = "1"
    env["RELAY_COMPOSE_PROJECT"] = project
    if fail:
        env["STUB_DOCKER_FAIL"] = "1"

    result = subprocess.run(
        [
            shell,
            _posix_path(source / "deploy" / "apply-update.sh"),
            _posix_path(target),
            _posix_path(backup_root),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    # Detect-only must never create backups or touch the installation.
    assert not backup_root.exists()
    return result


def test_update_detects_the_running_stack_from_the_config_files_label(tmp_path: Path) -> None:
    rows = (
        "deploy|app|__TARGET__/deploy/compose.yml|__TARGET__/deploy\n"
        "deploy|caddy|__TARGET__/deploy/compose.yml|__TARGET__/deploy\n"
        "deploy|postgres|__TARGET__/deploy/compose.yml|__TARGET__/deploy\n"
    )
    result = _run_detect_only(tmp_path, rows)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "using existing Compose project: deploy" in result.stdout
    assert "detect-only: compose project deploy" in result.stdout
    # .env is parsed, never sourced: the literal value must survive verbatim.
    assert "detect-only: postgres user p$(id -u)_x, database sms_relay" in result.stdout


def test_update_detects_the_running_stack_from_the_working_dir_label(tmp_path: Path) -> None:
    # Stacks started with a relative -f path have no absolute config_files match.
    rows = "deploy|caddy|compose.yml|__TARGET__/deploy\n"
    result = _run_detect_only(tmp_path, rows)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "using existing Compose project: deploy" in result.stdout


def test_update_refuses_when_two_projects_share_the_same_compose_file(tmp_path: Path) -> None:
    rows = (
        "deploy|app|__TARGET__/deploy/compose.yml|__TARGET__/deploy\n"
        "relay|app|__TARGET__/deploy/compose.yml|__TARGET__/deploy\n"
    )
    result = _run_detect_only(tmp_path, rows)

    assert result.returncode == 2
    assert "multiple running Compose projects use" in result.stderr
    assert "deploy" in result.stderr
    assert "relay" in result.stderr


def test_update_refuses_when_no_project_matches_and_prints_the_override_hint(tmp_path: Path) -> None:
    rows = "other|app|/opt/other/deploy/compose.yml|/opt/other/deploy\n"
    result = _run_detect_only(tmp_path, rows)

    assert result.returncode == 2
    assert "no existing app or caddy Compose project was detected" in result.stderr
    assert "RELAY_COMPOSE_PROJECT=<project>" in result.stderr
    assert "other|app|/opt/other/deploy/compose.yml|/opt/other/deploy" in result.stderr


def test_update_accepts_a_verified_compose_project_override(tmp_path: Path) -> None:
    rows = "other|app|/opt/other/deploy/compose.yml|/opt/other/deploy\n"
    result = _run_detect_only(tmp_path, rows, project="other")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "using Compose project from RELAY_COMPOSE_PROJECT: other" in result.stdout


def test_update_rejects_a_compose_project_override_without_containers(tmp_path: Path) -> None:
    rows = "other|app|/opt/other/deploy/compose.yml|/opt/other/deploy\n"
    result = _run_detect_only(tmp_path, rows, project="ghost")

    assert result.returncode == 2
    assert "RELAY_COMPOSE_PROJECT=ghost has no app or caddy container" in result.stderr
    assert "other" in result.stderr


def test_update_stops_when_docker_cannot_be_queried(tmp_path: Path) -> None:
    result = _run_detect_only(tmp_path, "", fail=True)

    assert result.returncode == 2
    assert "failed to query Docker for existing Compose projects" in result.stderr
    assert "stub docker: daemon unreachable" in result.stderr


# Preflight runs before every deploy and update, so it is the earliest place that
# can catch the dual-stack state that made the entry point serve an empty
# database. These tests run the real script against the same stub `docker`.

_PREFLIGHT_ENV = """ADMIN_USERNAME=admin
ADMIN_PASSWORD=preflight-test-password
ADMIN_TOKEN=preflight-test-token
DATA_ENCRYPTION_KEY=preflight-test-key
POSTGRES_PASSWORD=preflight-test-db-password
DATABASE_URL=postgresql+asyncpg://sms_relay:x@postgres:5432/sms_relay
RELAY_SITE=:80
HTTP_PORT=39300
HTTPS_PORT=39301
APP_PORT=39200
"""


def _run_preflight(
    tmp_path: Path,
    rows: str,
    *,
    env_project: str | None = None,
    volumes: str = "",
    extra_env: str = "",
) -> subprocess.CompletedProcess[str]:
    shell = _posix_sh()
    if shell is None:
        pytest.skip("POSIX sh is not available on this host")

    deploy_dir = tmp_path / "deploy"
    stub_dir = tmp_path / "stub-bin"
    for directory in (deploy_dir, stub_dir):
        directory.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(ROOT / "deploy" / "preflight.sh", deploy_dir / "preflight.sh")
    (deploy_dir / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    env_text = _PREFLIGHT_ENV
    if env_project is not None:
        env_text += f"COMPOSE_PROJECT_NAME={env_project}\n"
    if extra_env:
        env_text += extra_env if extra_env.endswith("\n") else extra_env + "\n"
    (deploy_dir / ".env").write_text(env_text, encoding="utf-8")

    stub = stub_dir / "docker"
    stub.write_text(_STUB_DOCKER, encoding="utf-8", newline="\n")
    stub.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = str(stub_dir) + os.pathsep + env.get("PATH", "")
    env["STUB_DOCKER_ROWS"] = rows.replace("__DEPLOY__", _posix_path(deploy_dir))
    env["STUB_DOCKER_VOLUMES"] = volumes
    env["SKIP_DNS"] = "1"

    return subprocess.run(
        [
            shell,
            _posix_path(deploy_dir / "preflight.sh"),
            _posix_path(deploy_dir / ".env"),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_preflight_reports_the_single_running_compose_project(tmp_path: Path) -> None:
    rows = (
        "deploy|app|__DEPLOY__/compose.yml|__DEPLOY__\n"
        "deploy|caddy|__DEPLOY__/compose.yml|__DEPLOY__\n"
    )
    result = _run_preflight(tmp_path, rows, env_project="deploy")

    # The env has no RELAY_DOMAIN, which is the default IP-mode deployment, so
    # this also pins the exit status of a fully passing run at 0.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Compose project in use: deploy" in result.stdout
    assert "Preflight passed" in result.stdout


def test_preflight_fails_when_two_projects_share_the_compose_file(tmp_path: Path) -> None:
    rows = "deploy|app|__DEPLOY__/compose.yml|__DEPLOY__\nrelay|app|__DEPLOY__/compose.yml|__DEPLOY__\n"
    result = _run_preflight(tmp_path, rows)

    assert result.returncode == 1
    assert "multiple Compose projects use" in result.stderr
    assert "deploy relay" in result.stderr


def test_preflight_fails_when_the_env_project_name_does_not_match_the_stack(
    tmp_path: Path,
) -> None:
    rows = "deploy|caddy|compose.yml|__DEPLOY__\n"
    result = _run_preflight(tmp_path, rows, env_project="relay")

    assert result.returncode == 1
    assert "COMPOSE_PROJECT_NAME=relay does not match the running Compose project deploy" in (
        result.stderr
    )


def test_preflight_warns_about_extra_database_volumes(tmp_path: Path) -> None:
    rows = "deploy|app|__DEPLOY__/compose.yml|__DEPLOY__\n"
    volumes = "deploy_postgres_data\nrelay_postgres_data\ncaddy_data\n"
    result = _run_preflight(tmp_path, rows, volumes=volumes)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "more than one database volume exists" in result.stderr
    assert "deploy_postgres_data relay_postgres_data" in result.stderr


def test_preflight_warns_when_compose_project_name_is_set_by_hand(tmp_path: Path) -> None:
    rows = "other|app|/opt/other/deploy/compose.yml|/opt/other/deploy\n"
    result = _run_preflight(tmp_path, rows, env_project="relay")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "COMPOSE_PROJECT_NAME=relay is set by hand" in result.stderr


# deploy/README.md tells every production install to set PUBLIC_BASE_URL, so the
# shape check must accept a plain origin. The earlier `*/[!/]*` glob matched the
# `//` of `scheme://` itself and rejected every non-empty value, which made
# preflight exit 1 on a healthy server. The old harness never set the key, so the
# whole block was skipped by the empty-value branch and the bug survived.
_PREFLIGHT_ROWS_SINGLE = "deploy|app|__DEPLOY__/compose.yml|__DEPLOY__\n"

_PUBLIC_BASE_URL_ACCEPTED = (
    "https://sms.example.test",
    "https://sms.example.test/",
    "http://203.0.113.10:39300",
    "https://sms.example.test:39301",
)

_PUBLIC_BASE_URL_REJECTED = (
    "https://sms.example.test/api",
    "https://user:secret@sms.example.test",
    "https://sms.example.test?token=1",
    "https://sms.example.test#fragment",
    "sms.example.test",
)


@pytest.mark.parametrize("public_base_url", _PUBLIC_BASE_URL_ACCEPTED)
def test_preflight_accepts_a_public_base_url_origin(tmp_path: Path, public_base_url: str) -> None:
    result = _run_preflight(
        tmp_path,
        _PREFLIGHT_ROWS_SINGLE,
        extra_env=f"PUBLIC_BASE_URL={public_base_url}",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Preflight passed" in result.stdout
    assert "PUBLIC_BASE_URL must" not in result.stderr


@pytest.mark.parametrize("public_base_url", _PUBLIC_BASE_URL_REJECTED)
def test_preflight_rejects_a_public_base_url_that_is_not_an_origin(
    tmp_path: Path, public_base_url: str
) -> None:
    result = _run_preflight(
        tmp_path,
        _PREFLIGHT_ROWS_SINGLE,
        extra_env=f"PUBLIC_BASE_URL={public_base_url}",
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "PUBLIC_BASE_URL must" in result.stderr


def test_preflight_rejects_a_public_base_url_host_that_differs_from_relay_domain(
    tmp_path: Path,
) -> None:
    result = _run_preflight(
        tmp_path,
        _PREFLIGHT_ROWS_SINGLE,
        extra_env="RELAY_DOMAIN=sms.example.test\nPUBLIC_BASE_URL=https://other.example.test",
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "PUBLIC_BASE_URL host does not match RELAY_DOMAIN=sms.example.test" in result.stderr


def test_preflight_accepts_a_domain_install_whose_public_base_url_matches(tmp_path: Path) -> None:
    # HTTPS_PORT stays 39301 here, which is only a warning: the domain URL then has
    # to carry that port.
    result = _run_preflight(
        tmp_path,
        _PREFLIGHT_ROWS_SINGLE,
        extra_env="RELAY_DOMAIN=sms.example.test\nPUBLIC_BASE_URL=https://sms.example.test\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Preflight passed" in result.stdout
    assert "HTTPS hostname: sms.example.test" in result.stdout

