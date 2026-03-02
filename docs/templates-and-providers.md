# Bot Templates & Providers

Each bot runs on an LLM provider. Templates define which provider to use and how it connects.

## Quick Start

Set your provider API key in `.env` and create a bot with the matching template:

```env
ANTHROPIC_API_KEY=sk-ant-...
```

Then create a bot in the dashboard with template **default**. That's it.

## Provider Reference

| Template | Provider | Type | Required Env Vars | Default Model |
|----------|----------|------|-------------------|---------------|
| `default` | Anthropic Claude | Built-in | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |
| `openai` | OpenAI GPT | Built-in | `OPENAI_API_KEY` | `gpt-4o` |
| `minimax` | MiniMax | OAI-compatible | `MINIMAX_API_KEY` | `MiniMax-M1` |
| `qwen` | Qwen / DashScope | OAI-compatible | `QWEN_API_KEY` | `qwen-plus` |
| `custom-endpoint` | Self-hosted (vLLM, Ollama, LM Studio) | OAI-compatible | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | (user-defined) |
| `researcher` | Self-hosted + web search | OAI-compatible | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | (user-defined) |

## How API Keys Work

There are two categories of templates, and they handle API keys differently.

### Built-in Provider Templates (Anthropic, OpenAI)

The API key is **forwarded to the bot container** as a Docker environment variable. It is never written to config files on disk. OpenClaw auto-detects the provider from the env var name.

### OAI-Compatible Templates (MiniMax, Qwen, custom-endpoint, researcher)

The API key is **baked into `openclaw.json`** at bot creation time. The template contains `{{ENV_VAR}}` placeholders that are replaced with your `.env` values when the bot is created. The key becomes part of the static config file on disk.

The base URL is also baked in — hardcoded for MiniMax (`https://api.minimaxi.chat/v1`) and Qwen (`https://dashscope.aliyuncs.com/compatible-mode/v1`), or from `LLM_BASE_URL` for custom-endpoint/researcher.

## Per-Provider Setup

### Anthropic (default)

```env
ANTHROPIC_API_KEY=sk-ant-...
# ANTHROPIC_MODEL=claude-sonnet-4-6
```

### OpenAI

```env
OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-4o
```

### MiniMax

```env
MINIMAX_API_KEY=...
# MINIMAX_MODEL=MiniMax-M1
# MINIMAX_CONTEXT_WINDOW=1000000
# MINIMAX_MAX_TOKENS=8192
```

### Qwen (DashScope)

```env
QWEN_API_KEY=...
# QWEN_MODEL=qwen-plus
# QWEN_CONTEXT_WINDOW=131072
# QWEN_MAX_TOKENS=8192
```

### Custom Endpoint (vLLM, Ollama, LM Studio)

```env
LLM_BASE_URL=http://your-server:8000/v1
LLM_MODEL=your-model-name
LLM_API_KEY=your-api-key
# LLM_CONTEXT_WINDOW=128000
# LLM_MAX_TOKENS=8192
```

Uses `openai-completions` API mode, which maps to `/v1/chat/completions` with full tool calling support. This is the correct mode for local models — `openai-responses` does NOT send tool definitions.

### Researcher

Same as custom-endpoint, plus optional web search:

```env
LLM_BASE_URL=http://your-server:8000/v1
LLM_MODEL=your-model-name
LLM_API_KEY=your-api-key
BRAVE_API_KEY=your-brave-key    # optional — enables web search tool
```

The only difference from custom-endpoint is the SOUL.md personality, which is optimized for research and structured analysis.

## Template File Structure

Each template lives in `bot-template/{name}/` and contains three files:

| File | Purpose |
|------|---------|
| `openclaw.template.json` | OpenClaw config with `{{ENV_VAR}}` placeholders |
| `SOUL.md` | Bot personality prompt |
| `template.meta.json` | Display metadata for the dashboard UI |

Example `template.meta.json`:

```json
{
  "description": "Custom OpenAI-compatible endpoint (vLLM, Ollama, LM Studio)",
  "env_hint": "Requires LLM_BASE_URL, LLM_MODEL, LLM_API_KEY"
}
```

`description` is shown on the template card when creating a bot. `env_hint` tells users what env vars they need.

Templates are auto-discovered from the `bot-template/` directory — no registration needed. Add a new directory and it appears in the dashboard immediately.

## Config Preview & API Key Visibility

The "Config preview" in the create-bot form shows the contents of `openclaw.template.json`:

- **Regular users** see the raw template with `{{PLACEHOLDER}}` syntax — API keys and secrets are never exposed.
- **Admin users** see the resolved config with actual environment variable values substituted.

If a template references environment variables that are not set on the server, those variables are listed as a warning on the template card. Bots created from templates with missing variables may fail to connect to their LLM provider.

## Placeholder Syntax

Templates use `{{VAR_NAME}}` placeholders that are replaced with environment variable values at bot creation time.

**Quoted** placeholders become JSON strings:
```json
"apiKey": "{{LLM_API_KEY}}"    →    "apiKey": "sk-abc123"
```

**Unquoted** placeholders become raw JSON values (numbers):
```json
"contextWindow": {{LLM_CONTEXT_WINDOW}}    →    "contextWindow": 128000
```

If an env var is not set, the placeholder is preserved as-is.

## Creating a Custom Template

1. Create a directory under `bot-template/`:
   ```bash
   mkdir bot-template/my-provider
   ```

2. Add `template.meta.json`:
   ```json
   {
     "description": "My Provider — short description",
     "env_hint": "Requires MY_PROVIDER_API_KEY"
   }
   ```

3. Add `SOUL.md` with the bot's default personality.

4. Copy `openclaw.template.json` from the closest existing template and edit it:
   - For cloud API providers with an OpenAI-compatible API, start from `custom-endpoint`
   - For providers with native OpenClaw support, start from `default`

5. Set default values for any new env vars in your `.env`.

6. The template appears in the dashboard immediately — no restart needed.

## Environment Variable Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | (none) | Anthropic API key |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Anthropic model name |
| `OPENAI_API_KEY` | (none) | OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o` | OpenAI model name |
| `MINIMAX_API_KEY` | (none) | MiniMax API key |
| `MINIMAX_MODEL` | `MiniMax-M1` | MiniMax model name |
| `MINIMAX_CONTEXT_WINDOW` | `1000000` | MiniMax context window (tokens) |
| `MINIMAX_MAX_TOKENS` | `8192` | MiniMax max output tokens |
| `QWEN_API_KEY` | (none) | DashScope API key |
| `QWEN_MODEL` | `qwen-plus` | Qwen model name |
| `QWEN_CONTEXT_WINDOW` | `131072` | Qwen context window (tokens) |
| `QWEN_MAX_TOKENS` | `8192` | Qwen max output tokens |
| `LLM_BASE_URL` | (none) | Custom endpoint base URL |
| `LLM_MODEL` | (none) | Custom endpoint model name |
| `LLM_API_KEY` | (none) | Custom endpoint API key |
| `LLM_CONTEXT_WINDOW` | `128000` | Custom endpoint context window (tokens) |
| `LLM_MAX_TOKENS` | `8192` | Custom endpoint max output tokens |
| `BRAVE_API_KEY` | (none) | Brave Search API key (enables web search tool for all bots) |
