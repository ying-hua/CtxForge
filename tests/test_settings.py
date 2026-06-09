from __future__ import annotations

from ctxforge.config.settings import load_settings


def test_project_config_overrides_defaults(tmp_path):
    (tmp_path / "ctxforge.toml").write_text(
        """
[deepseek]
model = "deepseek-reasoner"

[context]
max_tokens = 8192

[logging]
level = "debug"
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(project_dir=tmp_path, include_user_config=False)

    assert settings.deepseek.model == "deepseek-reasoner"
    assert settings.context.max_tokens == 8192
    assert settings.logging.level == "DEBUG"


def test_environment_overrides_project_config(tmp_path, monkeypatch):
    (tmp_path / "ctxforge.toml").write_text(
        """
[deepseek]
model = "from-project"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CTXFORGE_DEEPSEEK_MODEL", "from-env")

    settings = load_settings(project_dir=tmp_path, include_user_config=False)

    assert settings.deepseek.model == "from-env"


def test_dotenv_overrides_project_config_but_not_environment(tmp_path, monkeypatch):
    (tmp_path / "ctxforge.toml").write_text(
        """
[deepseek]
model = "from-project"
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        """
DEEPSEEK_API_KEY="from-dotenv"
CTXFORGE_DEEPSEEK_MODEL=from-dotenv
CTXFORGE_DEEPSEEK_MAX_RETRIES=3
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CTXFORGE_DEEPSEEK_MODEL", "from-env")

    settings = load_settings(project_dir=tmp_path, include_user_config=False)

    assert settings.deepseek.api_key == "from-dotenv"
    assert settings.deepseek.model == "from-env"
    assert settings.deepseek.max_retries == 3
