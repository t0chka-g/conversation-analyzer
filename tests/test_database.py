import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, select

from app.core.config import Settings, get_settings
from app.persistence.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.persistence.models import AnalysisJob

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_explicit_idempotent_initialization(tmp_path):
    path = tmp_path / "nested" / "database.db"
    engine = create_database_engine(f"sqlite:///{path}")
    try:
        assert not path.exists()
        assert not path.parent.exists()
        initialize_database(engine)
        initialize_database(engine)
        assert inspect(engine).get_table_names() == ["analysis_jobs"]
    finally:
        engine.dispose()


def test_init_cli_preserves_data(session_factory, job_id, tmp_path):
    environment = dict(os.environ, DATABASE_URL=f"sqlite:///{tmp_path / 'test.db'}")
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts/init_db.py")],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        assert "Database initialized" in result.stdout
    with session_factory() as session:
        assert session.scalar(select(AnalysisJob.id)) == str(job_id)


def test_storage_settings_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'custom.db'}")
    monkeypatch.setenv("AUDIO_STORAGE_DIR", str(tmp_path / "custom-audio"))
    monkeypatch.setenv("MAX_AUDIO_SIZE_MB", "5")
    settings = get_settings()
    assert settings.database_url == f"sqlite:///{tmp_path / 'custom.db'}"
    assert settings.audio_storage_dir == tmp_path / "custom-audio"
    assert settings.max_audio_size_mb == 5


@pytest.mark.parametrize(
    "values",
    [
        {"database_url": "invalid"},
        {"database_url": "postgresql:///db"},
        {"database_url": "sqlite+aiosqlite:///db"},
        {"database_url": "sqlite://user@/db"},
        {"database_url": "sqlite:///db?uri=true"},
        {"max_audio_size_mb": 0},
        {"max_audio_size_mb": -1},
        {"audio_storage_dir": " "},
    ],
)
def test_invalid_storage_settings(values):
    with pytest.raises(ValidationError):
        Settings(**values)


def test_imports_and_health_have_no_io_side_effects(tmp_path):
    # A fresh interpreter proves imports are lazy even if other tests imported modules.
    code = """
from unittest.mock import patch
with patch('socket.socket.connect', side_effect=AssertionError('network on import')), \
     patch('sqlite3.connect', side_effect=AssertionError('database on import')), \
     patch('sqlite3.dbapi2.connect', side_effect=AssertionError('database on import')):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.main import app as application
    import app.application.create_job
    import app.persistence.database
    import app.persistence.models
    import app.persistence.repository
    import app.storage.local
    assert isinstance(application, FastAPI)
    with TestClient(application) as client:
        assert client.get('/health').json() == {'status': 'ok'}
"""
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(PROJECT_ROOT)),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert not list(tmp_path.iterdir())


def test_session_closes_after_rollback(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'rollback.db'}")
    initialize_database(engine)
    factory = create_session_factory(engine)
    try:
        with pytest.raises(RuntimeError), factory.begin() as session:
            session.execute(select(AnalysisJob))
            raise RuntimeError("rollback")
        assert not session.in_transaction()
        assert engine.pool.checkedout() == 0
    finally:
        engine.dispose()
