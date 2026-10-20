import os
import subprocess
import sys

from sqlalchemy import create_engine, inspect


def _import_main_in_subprocess(database_url: str, app_env: str, allow_init_db: str) -> None:
    env = {
        **os.environ,
        "APP_ENV": app_env,
        "DATABASE_URL": database_url,
        "JWT_SECRET": f"{app_env}-jwt-secret-with-32-characters",
        "ALLOW_IMPORT_TIME_INIT_DB": allow_init_db,
        "CIRCLE_API_KEY": f"circle-{app_env}-startup-placeholder",
        "STRIPE_API_KEY": f"sk_{app_env}_startup_secret",
        "STRIPE_PRO_PRICE_ID": f"price_{app_env}_startup_pro",
        "STRIPE_WEBHOOK_SECRET": f"whsec_{app_env}_startup_secret",
        "CIRCLE_BILLING_WEBHOOK_SECRET": f"whsec_circle_{app_env}_startup_secret",
        "PYTHONPATH": "src",
    }
    subprocess.run(
        [sys.executable, "-c", "import tothemoon_api.main"],
        check=True,
        cwd=".",
        env=env,
        capture_output=True,
        text=True,
    )


def test_import_time_init_db_does_not_create_schema_by_default(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'production-startup.db'}"
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JWT_SECRET", "production-jwt-secret-with-32-characters")
    monkeypatch.setenv("ALLOW_IMPORT_TIME_INIT_DB", "false")

    _import_main_in_subprocess(database_url, "production", "false")

    engine = create_engine(database_url)
    try:
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_import_time_init_db_can_be_enabled_for_local_sqlite(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'local-startup.db'}"
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("JWT_SECRET", "local-jwt-secret-with-32-characters")
    monkeypatch.setenv("ALLOW_IMPORT_TIME_INIT_DB", "true")

    _import_main_in_subprocess(database_url, "local", "true")

    engine = create_engine(database_url)
    try:
        assert "users" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
