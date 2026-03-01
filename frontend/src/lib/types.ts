export interface CronJob {
  id: string;
  name: string;
  schedule: string;
  enabled: boolean;
  [key: string]: unknown;
}

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  context_tokens: number;
}

export interface Bot {
  name: string;
  status: "running" | "exited" | "created" | string;
  port: number;
  container_name: string;
  forked_from: string | null;
  created_by: string | null;
  created_at: string | null;
  backup_count: number;
  storage_bytes: number;
  cron_jobs: CronJob[];
  token_usage: TokenUsage;
  ui_path: string | null;
}

export interface Backup {
  timestamp: string;
  created_at: string;
  label: string;
  size_bytes?: number;
}

export interface BotMeta {
  created_at: string;
  modified_at: string;
  forked_from: string | null;
  created_by: string | null;
  backups: Backup[];
}

export interface BotStats {
  cpu_percent: number;
  memory_mb: number;
  memory_limit_mb: number;
  memory_percent: number;
  network_rx_mb: number;
  network_tx_mb: number;
  uptime_seconds: number;
  restart_count: number;
  started_at: string;
}

export interface BotDetail {
  name: string;
  status: string;
  port: number;
  container_name: string;
  config: Record<string, unknown>;
  soul: string;
  meta: BotMeta;
  stats: BotStats | null;
  gateway_token: string;
  storage_bytes: number;
  cron_jobs: CronJob[];
  token_usage: TokenUsage;
  ui_path: string | null;
}

export interface FleetStats {
  total_bots: number;
  running_bots: number;
  total_cpu_percent: number;
  total_memory_mb: number;
  total_memory_limit_mb: number;
  total_storage_bytes: number;
  total_network_rx_mb: number;
  total_network_tx_mb: number;
  max_uptime_seconds: number;
  total_tokens_used: number;
}

export interface CreateBotRequest {
  name: string;
  soul?: string;
  extra_config?: Record<string, unknown>;
}

export interface User {
  username: string;
  role: string;
  bots: string[];
}
