import json

import pytest

from auth import SESSIONS, _login_attempts, _login_lock


@pytest.fixture
def bot_env(monkeypatch, tmp_path):
    """Standard test environment with template and bots dirs."""
    template_dir = tmp_path / "template"
    template_dir.mkdir()
    bots_dir = tmp_path / "bots"
    bots_dir.mkdir()

    # New template structure: template_dir/default/openclaw.template.json
    default_tmpl = template_dir / "default"
    default_tmpl.mkdir()
    oc_template = {
        "models": {
            "mode": "merge",
            "providers": {
                "default": {
                    "baseUrl": "{{LLM_BASE_URL}}",
                    "apiKey": "{{LLM_API_KEY}}",
                    "api": "openai-completions",
                    "models": [{
                        "id": "{{LLM_MODEL}}",
                        "name": "{{LLM_MODEL}}",
                        "contextWindow": 128000,
                        "maxTokens": 8192,
                    }],
                }
            },
        },
        "agents": {"defaults": {"model": "default/{{LLM_MODEL}}"}},
    }
    (default_tmpl / "openclaw.template.json").write_text(json.dumps(oc_template))
    (default_tmpl / "SOUL.md").write_text("default soul")

    monkeypatch.setattr("config.TEMPLATE_DIR", template_dir)
    monkeypatch.setattr("config.BOTS_DIR", bots_dir)
    monkeypatch.setenv("LLM_BASE_URL", "http://10.0.0.1:8000/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW", "128000")
    monkeypatch.setenv("LLM_MAX_TOKENS", "8192")

    return {"template_dir": template_dir, "bots_dir": bots_dir}


@pytest.fixture(autouse=False)
def auth_env(monkeypatch, tmp_path):
    """Auth test environment with temp users file and clean sessions."""
    bots_dir = tmp_path / "bots"
    bots_dir.mkdir(exist_ok=True)
    users_file = bots_dir / ".users.json"
    monkeypatch.setattr("config.BOTS_DIR", bots_dir)
    monkeypatch.setenv("USERS_FILE", str(users_file))
    monkeypatch.setattr("config.AUTH_DISABLED", False)
    monkeypatch.setattr("config.SESSION_TTL", 3600)
    monkeypatch.setattr("config.ADMIN_USER", "admin")
    SESSIONS.clear()
    with _login_lock:
        _login_attempts.clear()
    yield {"bots_dir": bots_dir, "users_file": users_file}
    SESSIONS.clear()
    with _login_lock:
        _login_attempts.clear()
