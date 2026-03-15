// Mock data for ClawFarm screenshot automation
// All data shapes match frontend/src/lib/types.ts exactly

const NOW = new Date("2026-03-15T14:30:00Z");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function iso(date: Date): string {
  return date.toISOString();
}

function hoursAgo(h: number): Date {
  return new Date(NOW.getTime() - h * 3600_000);
}

/** Seeded PRNG so screenshots are deterministic */
function seededRandom(seed: number) {
  let s = seed;
  return () => {
    s = (s * 16807 + 0) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

/** Generate sparkline with business-hours pattern + noise */
function generateSparkline(
  botName: string,
  baseRate: number,
  points: number = 96,
  intervalMinutes: number = 15,
): { ts: string; total: number }[] {
  const rand = seededRandom(hashString(botName));
  const result: { ts: string; total: number }[] = [];

  for (let i = 0; i < points; i++) {
    const t = new Date(NOW.getTime() - (points - 1 - i) * intervalMinutes * 60_000);
    const hour = t.getUTCHours();
    // Business hours multiplier (9-17 UTC higher activity)
    const bizFactor = hour >= 9 && hour <= 17 ? 1.5 : 0.4;
    // Sine wave for organic feel
    const wave = Math.sin((i / points) * Math.PI * 3) * 0.3 + 1;
    const noise = 0.5 + rand() * 1.0;
    const value = Math.max(0, Math.round(baseRate * bizFactor * wave * noise));
    result.push({ ts: iso(t), total: value });
  }
  return result;
}

function hashString(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h) || 1;
}

// ---------------------------------------------------------------------------
// Bot definitions
// ---------------------------------------------------------------------------

interface BotDef {
  name: string;
  template: string;
  status: string;
  model: string;
  totalTokens: number;
  inputTokens: number;
  outputTokens: number;
  contextTokens: number;
  uptimeSeconds: number;
  startedAt: Date;
  createdAt: Date;
  createdBy: string;
  port: number;
  sparklineBase: number;
  storageMb: number;
  backupCount: number;
  networkIsolation: boolean;
  forkedFrom: string | null;
  cronJobs: { id: string; name: string; schedule: string; enabled: boolean }[];
  cpuPercent: number;
  memoryMb: number;
  memoryLimitMb: number;
  networkRxMb: number;
  networkTxMb: number;
  restartCount: number;
}

const BOTS: BotDef[] = [
  {
    name: "customer-support",
    template: "default",
    status: "running",
    model: "claude-sonnet-4-6",
    totalTokens: 47200,
    inputTokens: 28300,
    outputTokens: 18900,
    contextTokens: 128000,
    uptimeSeconds: 3 * 86400 + 14 * 3600,
    startedAt: hoursAgo(3 * 24 + 14),
    createdAt: hoursAgo(7 * 24),
    createdBy: "storm",
    port: 3001,
    sparklineBase: 520,
    storageMb: 2.4,
    backupCount: 5,
    networkIsolation: true,
    forkedFrom: null,
    cronJobs: [
      { id: "c1", name: "memory-cleanup", schedule: "0 3 * * *", enabled: true },
    ],
    cpuPercent: 2.1,
    memoryMb: 187.4,
    memoryLimitMb: 512,
    networkRxMb: 24.8,
    networkTxMb: 12.3,
    restartCount: 0,
  },
  {
    name: "content-writer",
    template: "openai",
    status: "running",
    model: "gpt-5.4",
    totalTokens: 23800,
    inputTokens: 14200,
    outputTokens: 9600,
    contextTokens: 128000,
    uptimeSeconds: 1 * 86400 + 8 * 3600,
    startedAt: hoursAgo(32),
    createdAt: hoursAgo(5 * 24),
    createdBy: "storm",
    port: 3002,
    sparklineBase: 280,
    storageMb: 1.8,
    backupCount: 3,
    networkIsolation: true,
    forkedFrom: null,
    cronJobs: [],
    cpuPercent: 1.5,
    memoryMb: 156.2,
    memoryLimitMb: 512,
    networkRxMb: 11.4,
    networkTxMb: 6.8,
    restartCount: 0,
  },
  {
    name: "market-analyst",
    template: "default",
    status: "running",
    model: "claude-opus-4-6",
    totalTokens: 89100,
    inputTokens: 53400,
    outputTokens: 35700,
    contextTokens: 200000,
    uptimeSeconds: 5 * 86400 + 2 * 3600,
    startedAt: hoursAgo(5 * 24 + 2),
    createdAt: hoursAgo(10 * 24),
    createdBy: "storm",
    port: 3003,
    sparklineBase: 980,
    storageMb: 4.2,
    backupCount: 8,
    networkIsolation: false,
    forkedFrom: null,
    cronJobs: [
      { id: "c2", name: "daily-report", schedule: "0 8 * * 1-5", enabled: true },
      { id: "c3", name: "data-refresh", schedule: "*/30 * * * *", enabled: true },
    ],
    cpuPercent: 4.8,
    memoryMb: 312.6,
    memoryLimitMb: 1024,
    networkRxMb: 67.2,
    networkTxMb: 31.5,
    restartCount: 1,
  },
  {
    name: "code-reviewer",
    template: "default",
    status: "running",
    model: "claude-sonnet-4-6",
    totalTokens: 12400,
    inputTokens: 8700,
    outputTokens: 3700,
    contextTokens: 128000,
    uptimeSeconds: 18 * 3600,
    startedAt: hoursAgo(18),
    createdAt: hoursAgo(3 * 24),
    createdBy: "alex",
    port: 3004,
    sparklineBase: 150,
    storageMb: 0.9,
    backupCount: 2,
    networkIsolation: true,
    forkedFrom: null,
    cronJobs: [],
    cpuPercent: 0.8,
    memoryMb: 124.8,
    memoryLimitMb: 512,
    networkRxMb: 5.2,
    networkTxMb: 2.1,
    restartCount: 0,
  },
  {
    name: "research-agent",
    template: "researcher",
    status: "running",
    model: "claude-opus-4-6",
    totalTokens: 34600,
    inputTokens: 20800,
    outputTokens: 13800,
    contextTokens: 200000,
    uptimeSeconds: 2 * 86400 + 5 * 3600,
    startedAt: hoursAgo(53),
    createdAt: hoursAgo(6 * 24),
    createdBy: "storm",
    port: 3005,
    sparklineBase: 400,
    storageMb: 3.1,
    backupCount: 4,
    networkIsolation: false,
    forkedFrom: null,
    cronJobs: [
      { id: "c4", name: "web-scan", schedule: "0 */4 * * *", enabled: true },
    ],
    cpuPercent: 3.2,
    memoryMb: 245.1,
    memoryLimitMb: 1024,
    networkRxMb: 42.6,
    networkTxMb: 18.9,
    restartCount: 0,
  },
  {
    name: "data-processor",
    template: "minimax",
    status: "exited",
    model: "MiniMax-M1",
    totalTokens: 8100,
    inputTokens: 5400,
    outputTokens: 2700,
    contextTokens: 128000,
    uptimeSeconds: 0,
    startedAt: hoursAgo(48),
    createdAt: hoursAgo(4 * 24),
    createdBy: "alex",
    port: 3006,
    sparklineBase: 0,
    storageMb: 0.6,
    backupCount: 1,
    networkIsolation: true,
    forkedFrom: null,
    cronJobs: [],
    cpuPercent: 0,
    memoryMb: 0,
    memoryLimitMb: 512,
    networkRxMb: 3.1,
    networkTxMb: 1.2,
    restartCount: 2,
  },
];

// ---------------------------------------------------------------------------
// API Response Builders
// ---------------------------------------------------------------------------

function buildBotList() {
  return BOTS.map((b) => ({
    name: b.name,
    status: b.status,
    port: b.port,
    container_name: `openclaw-bot-${b.name}`,
    forked_from: b.forkedFrom,
    created_by: b.createdBy,
    created_at: iso(b.createdAt),
    template: b.template,
    network_isolation: b.networkIsolation,
    backup_count: b.backupCount,
    storage_bytes: Math.round(b.storageMb * 1024 * 1024),
    cron_jobs: b.cronJobs,
    token_usage: {
      input_tokens: b.inputTokens,
      output_tokens: b.outputTokens,
      total_tokens: b.totalTokens,
      context_tokens: b.contextTokens,
      model: b.model,
    },
    uptime_seconds: b.uptimeSeconds,
    started_at: b.status === "running" ? iso(b.startedAt) : null,
    ui_path: `/claw/${b.name}/`,
  }));
}

function buildFleetStats() {
  const running = BOTS.filter((b) => b.status === "running");
  return {
    total_bots: BOTS.length,
    running_bots: running.length,
    starting_bots: 0,
    total_cpu_percent: +(BOTS.reduce((s, b) => s + b.cpuPercent, 0).toFixed(1)),
    total_memory_mb: +(BOTS.reduce((s, b) => s + b.memoryMb, 0).toFixed(1)),
    total_memory_limit_mb: BOTS.reduce((s, b) => s + b.memoryLimitMb, 0),
    total_storage_bytes: BOTS.reduce((s, b) => s + Math.round(b.storageMb * 1024 * 1024), 0),
    total_network_rx_mb: +(BOTS.reduce((s, b) => s + b.networkRxMb, 0).toFixed(1)),
    total_network_tx_mb: +(BOTS.reduce((s, b) => s + b.networkTxMb, 0).toFixed(1)),
    max_uptime_seconds: Math.max(...BOTS.map((b) => b.uptimeSeconds)),
    total_tokens_used: BOTS.reduce((s, b) => s + b.totalTokens, 0),
    tokens_by_model: BOTS.reduce(
      (acc, b) => {
        acc[b.model] = (acc[b.model] || 0) + b.totalTokens;
        return acc;
      },
      {} as Record<string, number>,
    ),
  };
}

function buildFleetSparklines() {
  const result: Record<string, { ts: string; total: number }[]> = {};
  for (const b of BOTS) {
    result[b.name] = generateSparkline(b.name, b.sparklineBase);
  }
  return result;
}

function buildFleetTokenChart() {
  const models = ["claude-sonnet-4-6", "claude-opus-4-6", "gpt-5.4", "MiniMax-M1"];
  const baseRates = [350, 720, 200, 60];
  const points: { ts: string; models: Record<string, number> }[] = [];
  const rand = seededRandom(42);

  for (let i = 0; i < 168; i++) {
    const t = new Date(NOW.getTime() - (167 - i) * 3600_000);
    const hour = t.getUTCHours();
    const dayOfWeek = t.getUTCDay();
    const isWeekend = dayOfWeek === 0 || dayOfWeek === 6;

    // Business-hours pattern with weekend dip
    const bizFactor = hour >= 9 && hour <= 17 ? 1.6 : 0.3;
    const weekendFactor = isWeekend ? 0.35 : 1.0;
    // Gradual growth over the 7 days
    const growthFactor = 0.85 + (i / 168) * 0.3;

    const modelData: Record<string, number> = {};
    for (let m = 0; m < models.length; m++) {
      const noise = 0.5 + rand() * 1.0;
      const value = Math.round(baseRates[m] * bizFactor * weekendFactor * growthFactor * noise);
      if (value > 0) modelData[models[m]] = value;
    }
    points.push({ ts: iso(t), models: modelData });
  }
  return points;
}

function buildBotDetail(name: string) {
  const b = BOTS.find((bot) => bot.name === name);
  if (!b) return null;

  const backups = [];
  for (let i = 0; i < b.backupCount; i++) {
    const t = new Date(b.createdAt.getTime() + (i + 1) * 12 * 3600_000);
    backups.push({
      timestamp: t
        .toISOString()
        .replace(/[-:T]/g, "")
        .slice(0, 15)
        .replace(/^(\d{8})(\d{6})/, "$1-$2"),
      created_at: iso(t),
      label: i === b.backupCount - 1 ? "manual" : "scheduled",
      size_bytes: Math.round((b.storageMb * 1024 * 1024) / (1.5 + i * 0.2)),
    });
  }

  return {
    name: b.name,
    status: b.status,
    port: b.port,
    container_name: `openclaw-bot-${b.name}`,
    config: {
      agents: {
        defaults: {
          model: b.model,
          maxTokens: 8192,
        },
      },
      models: {
        providers: b.template === "openai"
          ? [{ name: "openai", apiKey: "***" }]
          : b.template === "minimax"
            ? [{ name: "minimax", baseURL: "https://api.minimax.chat/v1", apiKey: "***" }]
            : undefined,
      },
      gateway: {
        auth: { trustedProxy: { userHeader: "X-Forwarded-User" } },
        basePath: `/claw/${b.name}`,
      },
      tools: { allowedCommands: ["web-search", "file-read", "file-write"] },
    },
    soul: buildSoulContent(b.name),
    meta: {
      created_at: iso(b.createdAt),
      modified_at: iso(hoursAgo(2)),
      forked_from: b.forkedFrom,
      created_by: b.createdBy,
      template: b.template,
      backups,
    },
    stats:
      b.status === "running"
        ? {
            cpu_percent: b.cpuPercent,
            memory_mb: b.memoryMb,
            memory_limit_mb: b.memoryLimitMb,
            memory_percent: +((b.memoryMb / b.memoryLimitMb) * 100).toFixed(1),
            network_rx_mb: b.networkRxMb,
            network_tx_mb: b.networkTxMb,
            uptime_seconds: b.uptimeSeconds,
            restart_count: b.restartCount,
            started_at: iso(b.startedAt),
          }
        : null,
    gateway_token: "clw_mock_" + b.name.replace(/-/g, "_") + "_token",
    storage_bytes: Math.round(b.storageMb * 1024 * 1024),
    cron_jobs: b.cronJobs,
    token_usage: {
      input_tokens: b.inputTokens,
      output_tokens: b.outputTokens,
      total_tokens: b.totalTokens,
      context_tokens: b.contextTokens,
      model: b.model,
    },
    ui_path: `/claw/${b.name}/`,
  };
}

function buildSoulContent(name: string): string {
  const souls: Record<string, string> = {
    "customer-support": `# Customer Support Agent

You are a friendly, knowledgeable customer support representative for our SaaS platform.

## Core Behaviors
- Always greet customers warmly and acknowledge their issue
- Escalate billing issues to the human team with a summary
- Never share internal system details or API keys
- Follow up on unresolved tickets after 24 hours

## Knowledge Base
You have access to our product documentation, FAQ, and recent changelog.
Prefer linking to docs over writing long explanations.`,

    "content-writer": `# Content Writer

You are a professional content writer specializing in technical blog posts and documentation.

## Style Guide
- Write in active voice, keep sentences concise
- Target a technical audience (developers, DevOps engineers)
- Include code examples where relevant
- Aim for 800-1200 word articles`,

    "market-analyst": `# Market Analyst

You are a senior market research analyst tracking trends in the AI/ML industry.

## Responsibilities
- Monitor competitor announcements and product launches
- Analyze pricing changes across major LLM providers
- Produce weekly market briefings for the leadership team
- Flag significant regulatory developments`,

    "code-reviewer": `# Code Reviewer

You are a meticulous code reviewer focused on Python and TypeScript codebases.

## Review Criteria
- Security vulnerabilities (OWASP Top 10)
- Performance bottlenecks and N+1 queries
- Test coverage gaps
- API contract changes that could break clients`,

    "research-agent": `# Research Agent

You are a research assistant with web search capabilities.

## Focus Areas
- Academic papers and preprints (arxiv, semantic scholar)
- Industry reports and benchmarks
- Open-source project evaluations
- Summarize findings with citations and confidence levels`,

    "data-processor": `# Data Processor

You are a batch data processing agent for ETL pipelines.

## Tasks
- Parse and validate incoming CSV/JSON data feeds
- Apply transformation rules and output clean datasets
- Flag anomalies and data quality issues
- Generate processing reports with row counts and error rates`,
  };
  return souls[name] || "# Agent\n\nYou are a helpful assistant.";
}

function buildTemplates() {
  return [
    {
      name: "default",
      soul_preview: "You are a helpful AI assistant powered by Anthropic Claude...",
      description: "Anthropic Claude (recommended)",
      env_hint: "ANTHROPIC_API_KEY",
      config_preview: JSON.stringify(
        { agents: { defaults: { model: "claude-sonnet-4-6" } } },
        null,
        2,
      ),
      missing_vars: [],
    },
    {
      name: "openai",
      soul_preview: "You are a helpful AI assistant powered by OpenAI...",
      description: "OpenAI GPT",
      env_hint: "OPENAI_API_KEY",
      config_preview: JSON.stringify(
        { agents: { defaults: { model: "gpt-5.4" } } },
        null,
        2,
      ),
      missing_vars: [],
    },
    {
      name: "minimax",
      soul_preview: "You are a helpful AI assistant powered by MiniMax...",
      description: "MiniMax (budget-friendly)",
      env_hint: "MINIMAX_API_KEY",
      config_preview: JSON.stringify(
        {
          agents: { defaults: { model: "MiniMax-M1" } },
          models: { providers: [{ name: "minimax", baseURL: "https://api.minimax.chat/v1" }] },
        },
        null,
        2,
      ),
      missing_vars: ["MINIMAX_API_KEY"],
    },
    {
      name: "qwen",
      soul_preview: "You are a helpful AI assistant powered by Qwen...",
      description: "Qwen via DashScope (budget-friendly)",
      env_hint: "QWEN_API_KEY",
      config_preview: JSON.stringify(
        {
          agents: { defaults: { model: "qwen-plus" } },
          models: { providers: [{ name: "qwen", baseURL: "https://dashscope.aliyuncs.com/compatible-mode/v1" }] },
        },
        null,
        2,
      ),
      missing_vars: ["QWEN_API_KEY"],
    },
    {
      name: "custom-endpoint",
      soul_preview: "You are a helpful AI assistant running on a self-hosted model...",
      description: "Self-hosted (vLLM, Ollama, LM Studio)",
      env_hint: "LLM_BASE_URL, LLM_MODEL, LLM_API_KEY",
      config_preview: JSON.stringify(
        {
          agents: { defaults: { model: "{{LLM_MODEL}}" } },
          models: { providers: [{ name: "custom", baseURL: "{{LLM_BASE_URL}}" }] },
        },
        null,
        2,
      ),
      missing_vars: ["LLM_BASE_URL", "LLM_MODEL"],
    },
    {
      name: "researcher",
      soul_preview: "You are a research-focused AI assistant with web search...",
      description: "Research agent (custom endpoint + web search)",
      env_hint: "LLM_BASE_URL, LLM_MODEL, BRAVE_API_KEY",
      config_preview: JSON.stringify(
        {
          agents: { defaults: { model: "{{LLM_MODEL}}" } },
          models: { providers: [{ name: "custom", baseURL: "{{LLM_BASE_URL}}" }] },
          tools: { "web-search": { apiKey: "{{BRAVE_API_KEY}}" } },
        },
        null,
        2,
      ),
      missing_vars: ["LLM_BASE_URL", "LLM_MODEL", "BRAVE_API_KEY"],
    },
  ];
}

// ---------------------------------------------------------------------------
// Route handler
// ---------------------------------------------------------------------------

export type MockMode = "login" | "authenticated";

export function buildRouteHandler(mode: MockMode) {
  return (url: URL): { status: number; body: unknown } | null => {
    const path = url.pathname;

    // Auth endpoints
    if (path === "/api/auth/me") {
      if (mode === "login") {
        return { status: 401, body: { detail: "Not authenticated" } };
      }
      return {
        status: 200,
        body: { username: "storm", role: "admin", bots: ["*"] },
      };
    }

    if (path === "/api/auth/verify") {
      if (mode === "login") {
        return { status: 401, body: { detail: "Not authenticated" } };
      }
      return { status: 200, body: { username: "storm", role: "admin" } };
    }

    // Config
    if (path === "/api/config") {
      return {
        status: 200,
        body: { portal_url: "https://claws.example.com:8443", caddy_port: 8443 },
      };
    }

    // Templates
    if (path === "/api/templates") {
      return { status: 200, body: buildTemplates() };
    }

    // Fleet
    if (path === "/api/fleet/stats") {
      return { status: 200, body: buildFleetStats() };
    }

    if (path === "/api/fleet/sparklines") {
      return { status: 200, body: buildFleetSparklines() };
    }

    if (path === "/api/fleet/token-chart") {
      return { status: 200, body: buildFleetTokenChart() };
    }

    // Bot list
    if (path === "/api/bots" && !path.includes("/bots/")) {
      return { status: 200, body: buildBotList() };
    }

    // Bot-specific routes
    const botMatch = path.match(/^\/api\/bots\/([^/]+)\/(.+)$/);
    if (botMatch) {
      const [, botName, action] = botMatch;
      const decoded = decodeURIComponent(botName);

      if (action === "detail") {
        const detail = buildBotDetail(decoded);
        if (!detail) return { status: 404, body: { detail: "Bot not found" } };
        return { status: 200, body: detail };
      }

      if (action === "sparkline") {
        const sparkline = generateSparkline(
          decoded,
          BOTS.find((b) => b.name === decoded)?.sparklineBase ?? 100,
        );
        return { status: 200, body: sparkline };
      }

      if (action === "stats") {
        const b = BOTS.find((bot) => bot.name === decoded);
        if (!b) return { status: 404, body: { detail: "Bot not found" } };
        return {
          status: 200,
          body: {
            cpu_percent: b.cpuPercent,
            memory_mb: b.memoryMb,
            memory_limit_mb: b.memoryLimitMb,
            memory_percent: +((b.memoryMb / b.memoryLimitMb) * 100).toFixed(1),
            network_rx_mb: b.networkRxMb,
            network_tx_mb: b.networkTxMb,
            uptime_seconds: b.uptimeSeconds,
            restart_count: b.restartCount,
            started_at: iso(b.startedAt),
          },
        };
      }

      if (action === "logs") {
        return {
          status: 200,
          body: {
            name: decoded,
            logs: `[2026-03-15 14:00:01] OpenClaw gateway started on port 18789\n[2026-03-15 14:00:02] Auth mode: trusted-proxy\n[2026-03-15 14:00:02] Base path: /claw/${decoded}/\n[2026-03-15 14:00:03] Ready to accept connections\n`,
          },
        };
      }

      if (action === "backups") {
        const detail = buildBotDetail(decoded);
        return { status: 200, body: detail?.meta.backups ?? [] };
      }

      if (action === "approve-devices") {
        return { status: 200, body: { approved: 0, request_ids: [] } };
      }
    }

    // Health check
    if (path === "/api/health") {
      return { status: 200, body: { status: "ok" } };
    }

    return null;
  };
}

// Terminal TUI content to inject into xterm.js
export const TERMINAL_CONTENT = [
  "\x1b[2J\x1b[H", // Clear screen, cursor home
  "\x1b[32mnode@customer-support\x1b[0m:\x1b[34m~\x1b[0m$ openclaw status\r\n",
  "\r\n",
  "\x1b[1;36m  OpenClaw Gateway v2026.2.26\x1b[0m\r\n",
  "\x1b[90m  ──────────────────────────────\x1b[0m\r\n",
  "\r\n",
  "  \x1b[1mStatus:\x1b[0m    \x1b[32m● Running\x1b[0m\r\n",
  "  \x1b[1mAuth:\x1b[0m      trusted-proxy\r\n",
  "  \x1b[1mBase Path:\x1b[0m /claw/customer-support/\r\n",
  "  \x1b[1mPort:\x1b[0m      18789\r\n",
  "  \x1b[1mModel:\x1b[0m     claude-sonnet-4-6\r\n",
  "  \x1b[1mUptime:\x1b[0m    3d 14h 22m\r\n",
  "\r\n",
  "\x1b[90m  Sessions\x1b[0m\r\n",
  "  \x1b[1mActive:\x1b[0m    2\r\n",
  "  \x1b[1mTotal:\x1b[0m     47\r\n",
  "\r\n",
  "\x1b[90m  Token Usage\x1b[0m\r\n",
  "  \x1b[1mInput:\x1b[0m     28.3k tokens\r\n",
  "  \x1b[1mOutput:\x1b[0m    18.9k tokens\r\n",
  "  \x1b[1mTotal:\x1b[0m     \x1b[33m47.2k tokens\x1b[0m\r\n",
  "\r\n",
  "\x1b[90m  Tools\x1b[0m\r\n",
  "  web-search  file-read  file-write\r\n",
  "\r\n",
  "\x1b[32mnode@customer-support\x1b[0m:\x1b[34m~\x1b[0m$ \x1b[5m_\x1b[0m",
].join("");
