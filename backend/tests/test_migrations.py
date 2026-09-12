from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

EXPECTED_TABLES = {
    "alembic_version",
    "api_clients",
    "audit_events",
    "device_nonces",
    "device_sim_profiles",
    "devices",
    "messages",
    "otps",
}


def test_upgrade_head_creates_expected_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'migration-test.db').as_posix()}"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert EXPECTED_TABLES <= set(inspector.get_table_names())
    device_columns = {column["name"]: column for column in inspector.get_columns("devices")}
    assert {
        "client_instance_id",
        "client_version",
        "client_mode",
        "last_source_fingerprint",
        "runtime_updated_at",
        "credential_reuse_warning",
        "duplicate_collection_warning",
    } <= set(device_columns)
    assert device_columns["client_instance_id"]["nullable"] is True
    assert device_columns["runtime_updated_at"]["nullable"] is True
    columns = {column["name"]: column for column in inspector.get_columns("device_sim_profiles")}
    assert set(columns) == {
        "device_id",
        "slot_index",
        "subscription_id",
        "phone_number_nonce",
        "phone_number_ciphertext",
        "phone_number_key_version",
        "carrier_name",
        "source",
        "updated_at",
    }
    assert columns["subscription_id"]["nullable"] is True
    assert "phone_number" not in columns
    assert columns["phone_number_nonce"]["nullable"] is True
    assert columns["phone_number_ciphertext"]["nullable"] is True
    assert columns["phone_number_key_version"]["nullable"] is True
    assert columns["carrier_name"]["nullable"] is True
    assert inspector.get_pk_constraint("device_sim_profiles")["constrained_columns"] == [
        "device_id",
        "slot_index",
    ]
    assert inspector.get_unique_constraints("device_sim_profiles") == []
    assert inspector.get_indexes("device_sim_profiles") == []
    message_columns = {column["name"]: column for column in inspector.get_columns("messages")}
    assert {
        "receiving_phone_lookup_hash",
        "receiving_phone_nonce",
        "receiving_phone_ciphertext",
        "receiving_phone_key_version",
        "receiving_identity_status",
        "read_at",
    } <= set(message_columns)
    message_indexes = {index["name"] for index in inspector.get_indexes("messages")}
    assert "ix_messages_receiving_phone_lookup_hash" in message_indexes
    assert "ix_messages_receiving_identity_status" in message_indexes
    api_client_columns = {column["name"]: column for column in inspector.get_columns("api_clients")}
    assert {
        "token_nonce",
        "token_ciphertext",
        "token_key_version",
    } <= set(api_client_columns)
    assert api_client_columns["token_nonce"]["nullable"] is True
    assert api_client_columns["token_ciphertext"]["nullable"] is True
    assert api_client_columns["token_key_version"]["nullable"] is True
    assert "device_id" in api_client_columns
    assert api_client_columns["device_id"]["nullable"] is True
    api_client_indexes = {index["name"]: index for index in inspector.get_indexes("api_clients")}
    assert api_client_indexes["ix_api_clients_device_id"]["unique"] == 1


def test_sim_profile_revision_downgrades_without_removing_initial_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'migration-downgrade.db').as_posix()}"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    command.downgrade(config, "20260816_0001")

    engine = create_engine(database_url)
    tables = set(inspect(engine).get_table_names())
    assert "device_sim_profiles" not in tables
    assert "devices" in tables


def test_message_identity_revision_downgrades_to_sim_profile_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'message-identity-downgrade.db').as_posix()}"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    command.downgrade(config, "20260817_0002")

    columns = {column["name"] for column in inspect(create_engine(database_url)).get_columns("messages")}
    assert "receiving_phone_lookup_hash" not in columns
    assert "read_at" not in columns
    assert "device_id" in columns


def test_device_runtime_revision_downgrades_to_admin_credentials_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'runtime-downgrade.db').as_posix()}"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    command.downgrade(config, "20260824_0006")

    columns = {
        column["name"] for column in inspect(create_engine(database_url)).get_columns("devices")
    }
    assert "client_instance_id" not in columns
    assert "credential_reuse_warning" not in columns
    assert "last_seen_at" in columns
