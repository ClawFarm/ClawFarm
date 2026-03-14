import json

from templates import _resolve_template, generate_config, list_templates, write_bot_files


class TestConfigGeneration:
    def test_template_resolved_with_env_vars(self, bot_env):
        config = generate_config("test")
        provider = config["models"]["providers"]["default"]
        assert provider["baseUrl"] == "http://10.0.0.1:8000/v1"
        assert provider["apiKey"] == "test-key"
        assert config["agents"]["defaults"]["model"] == "default/test-model"

    def test_soul_written_to_workspace(self, bot_env):
        config = generate_config("mybot")
        write_bot_files("mybot", config, soul="custom soul text")
        soul_path = bot_env["bots_dir"] / "mybot" / "SOUL.md"
        assert soul_path.exists()
        assert soul_path.read_text() == "custom soul text"

    def test_default_soul_when_blank(self, bot_env):
        config = generate_config("mybot")
        write_bot_files("mybot", config, soul="")
        soul_path = bot_env["bots_dir"] / "mybot" / "SOUL.md"
        assert soul_path.read_text() == "default soul"

    def test_extra_config_merges(self, bot_env):
        config = generate_config("mybot", extra_config={"agents": {"defaults": {"model": "custom/model"}}})
        assert config["agents"]["defaults"]["model"] == "custom/model"
        # Original field preserved
        assert config["models"]["mode"] == "merge"

    def test_workspace_dir_created(self, bot_env):
        config = generate_config("newbot")
        write_bot_files("newbot", config)
        bots_dir = bot_env["bots_dir"]
        assert (bots_dir / "newbot").is_dir()
        assert (bots_dir / "newbot" / "config.json").exists()
        assert (bots_dir / "newbot" / "SOUL.md").exists()


class TestResolveTemplate:
    def test_basic_substitution(self, monkeypatch):
        monkeypatch.setenv("MY_VAR", "hello")
        assert _resolve_template("value: {{MY_VAR}}") == "value: hello"

    def test_unset_var_kept(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        assert _resolve_template("{{NONEXISTENT_VAR}}") == "{{NONEXISTENT_VAR}}"

    def test_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        assert _resolve_template("{{A}} and {{B}}") == "1 and 2"

    def test_numeric_unquoted(self, monkeypatch):
        monkeypatch.setenv("NUM", "42")
        result = _resolve_template('{"val": {{NUM}}}')
        parsed = json.loads(result)
        assert parsed["val"] == 42


class TestListTemplates:
    def test_lists_default_template(self, bot_env):
        templates = list_templates()
        names = [t["name"] for t in templates]
        assert "default" in names

    def test_lists_multiple_templates(self, bot_env):
        # Add a second template
        custom = bot_env["template_dir"] / "custom"
        custom.mkdir()
        (custom / "openclaw.template.json").write_text("{}")
        (custom / "SOUL.md").write_text("I am custom")
        templates = list_templates()
        names = [t["name"] for t in templates]
        assert "default" in names
        assert "custom" in names

    def test_soul_preview(self, bot_env):
        templates = list_templates()
        default = next(t for t in templates if t["name"] == "default")
        assert default["soul_preview"] == "default soul"

    def test_hidden_dirs_excluded(self, bot_env):
        (bot_env["template_dir"] / ".hidden").mkdir()
        templates = list_templates()
        names = [t["name"] for t in templates]
        assert ".hidden" not in names

    def test_env_hint(self, bot_env):
        meta = {"description": "Test", "env_hint": "Requires TEST_KEY"}
        (bot_env["template_dir"] / "default" / "template.meta.json").write_text(json.dumps(meta))
        templates = list_templates()
        default = next(t for t in templates if t["name"] == "default")
        assert default["env_hint"] == "Requires TEST_KEY"

    def test_env_hint_missing_meta(self, bot_env):
        templates = list_templates()
        default = next(t for t in templates if t["name"] == "default")
        assert default["env_hint"] == ""

    def test_config_preview_default_shows_placeholders(self, bot_env):
        """Non-admin (default) sees raw template with placeholders."""
        templates = list_templates()
        default = next(t for t in templates if t["name"] == "default")
        assert default["config_preview"]
        assert "{{LLM_MODEL}}" in default["config_preview"]

    def test_config_preview_resolved_for_admin(self, bot_env):
        """Admin sees resolved config with actual env var values."""
        templates = list_templates(resolve_config=True)
        default = next(t for t in templates if t["name"] == "default")
        assert "test-model" in default["config_preview"]
        assert "{{LLM_MODEL}}" not in default["config_preview"]
        parsed = json.loads(default["config_preview"])
        assert "agents" in parsed or "models" in parsed

    def test_missing_vars_detected(self, bot_env, monkeypatch):
        """Unset env vars referenced in template are reported."""
        monkeypatch.delenv("LLM_API_KEY")
        templates = list_templates()
        default = next(t for t in templates if t["name"] == "default")
        assert "LLM_API_KEY" in default["missing_vars"]

    def test_missing_vars_empty_when_all_set(self, bot_env):
        """No missing vars when all referenced env vars are set."""
        templates = list_templates()
        default = next(t for t in templates if t["name"] == "default")
        assert default["missing_vars"] == []


class TestTemplateInCreateFlow:
    def test_generate_config_with_named_template(self, bot_env, monkeypatch):
        # Create a custom template
        custom = bot_env["template_dir"] / "custom"
        custom.mkdir()
        custom_tmpl = {"models": {"providers": {"openai": {"apiKey": "{{OPENAI_API_KEY}}"}}}}
        (custom / "openclaw.template.json").write_text(json.dumps(custom_tmpl))
        (custom / "SOUL.md").write_text("custom soul")

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        config = generate_config("test", template="custom")
        assert config["models"]["providers"]["openai"]["apiKey"] == "sk-test"

    def test_fallback_to_default_on_missing_template(self, bot_env):
        config = generate_config("test", template="nonexistent")
        # Should fall back to default template
        assert "models" in config
        assert "default" in config["models"]["providers"]

    def test_template_soul_used_when_no_custom_soul(self, bot_env):
        custom = bot_env["template_dir"] / "custom"
        custom.mkdir()
        (custom / "openclaw.template.json").write_text("{}")
        (custom / "SOUL.md").write_text("custom soul here")

        config = generate_config("mybot", template="custom")
        write_bot_files("mybot", config, soul="", template="custom")
        soul_path = bot_env["bots_dir"] / "mybot" / "SOUL.md"
        assert soul_path.read_text() == "custom soul here"
