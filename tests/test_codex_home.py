import json
from tgbridge import codex_engine


def test_custom_codex_home_is_used_for_auth_config_and_models(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(codex_engine, "CODEX_BIN", __file__)
    (tmp_path / "auth.json").write_text(json.dumps({"tokens": {"access_token": "fixture"}}))
    (tmp_path / "config.toml").write_text('model = "fixture-model"')
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [{"slug": "fixture-model"}]}))
    assert codex_engine.available() == (True, "ChatGPT subscription")
    assert codex_engine.default_model() == "fixture-model"
    assert codex_engine.list_models() == [("fixture-model", "fixture-model")]
    (tmp_path / "auth.json").unlink()
    assert not codex_engine.available()[0]


def test_empty_home_uses_standard_default(monkeypatch):
    monkeypatch.setenv("CODEX_HOME", "")
    assert codex_engine.codex_home() == codex_engine.Path.home() / ".codex"
