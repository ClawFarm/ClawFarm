import json
import os
import re

import config
from utils import _now_iso, deep_merge, write_meta


def _resolve_template(template_text: str) -> str:
    """Replace {{VAR_NAME}} placeholders with env var values."""
    def replacer(match):
        var_name = match.group(1)
        value = os.environ.get(var_name, "")
        return value if value else match.group(0)  # Keep placeholder if unset
    return re.sub(r"\{\{(\w+)\}\}", replacer, template_text)


def list_templates(resolve_config: bool = False) -> list[dict]:
    """List available bot templates.

    Args:
        resolve_config: If True (admin), resolve {{VAR}} placeholders in config
                        preview. If False (regular user), show raw template with
                        placeholders intact.
    """
    templates = []
    for d in sorted(config.TEMPLATE_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        soul_path = d / "SOUL.md"
        soul_preview = ""
        if soul_path.exists():
            soul_preview = soul_path.read_text()[:200]
        description = ""
        env_hint = ""
        meta_path = d / "template.meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                description = meta.get("description", "")
                env_hint = meta.get("env_hint", "")
            except (json.JSONDecodeError, OSError):
                pass
        config_preview = ""
        missing_vars: list[str] = []
        tmpl_path = d / "openclaw.template.json"
        if tmpl_path.exists():
            try:
                raw = tmpl_path.read_text()
                placeholders = re.findall(r"\{\{(\w+)\}\}", raw)
                missing_vars = sorted(set(v for v in placeholders if not os.environ.get(v)))
                if resolve_config:
                    resolved = _resolve_template(raw)
                    config_preview = json.dumps(json.loads(resolved), indent=2)
                else:
                    config_preview = raw.strip()
            except (json.JSONDecodeError, OSError):
                config_preview = ""
        templates.append({
            "name": d.name,
            "soul_preview": soul_preview,
            "description": description,
            "env_hint": env_hint,
            "config_preview": config_preview,
            "missing_vars": missing_vars,
        })
    return templates


def generate_config(name: str, extra_config: dict | None = None,
                    template: str = "default") -> dict:
    """Load and resolve an OpenClaw template, deep-merge extra_config."""
    tmpl_path = config.TEMPLATE_DIR / template / "openclaw.template.json"
    if not tmpl_path.exists():
        tmpl_path = config.TEMPLATE_DIR / "default" / "openclaw.template.json"
    raw = tmpl_path.read_text()
    resolved = _resolve_template(raw)
    cfg = json.loads(resolved)

    if extra_config:
        cfg = deep_merge(cfg, extra_config)

    return cfg


def write_bot_files(name: str, cfg: dict, soul: str | None = None,
                    forked_from: str | None = None, created_by: str | None = None,
                    template: str = "default", network_isolation: bool = True):
    """Create bots/{name}/, write config.json + SOUL.md + .meta.json. Returns bot dir."""
    bot_dir = config.BOTS_DIR / name
    bot_dir.mkdir(parents=True, exist_ok=True)

    with open(bot_dir / "config.json", "w") as f:
        json.dump(cfg, f, indent=2)

    if soul and soul.strip():
        soul_text = soul
    else:
        # Look for SOUL.md in the template directory first, then fall back
        tmpl_soul = config.TEMPLATE_DIR / template / "SOUL.md"
        if tmpl_soul.exists():
            soul_text = tmpl_soul.read_text()
        else:
            default_soul = config.TEMPLATE_DIR / "default" / "SOUL.md"
            soul_text = default_soul.read_text() if default_soul.exists() else ""

    with open(bot_dir / "SOUL.md", "w") as f:
        f.write(soul_text)

    now = _now_iso()
    meta = {
        "created_at": now,
        "modified_at": now,
        "forked_from": forked_from,
        "created_by": created_by,
        "template": template,
        "network_isolation": network_isolation,
        "backups": [],
    }
    write_meta(name, meta)

    return bot_dir
