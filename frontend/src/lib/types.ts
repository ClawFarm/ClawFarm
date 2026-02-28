export interface Bot {
  name: string;
  status: "running" | "exited" | "created" | string;
  port: number;
  container_name: string;
  forked_from: string | null;
  created_at: string | null;
  backup_count: number;
}

export interface Backup {
  timestamp: string;
  created_at: string;
  label: string;
}

export interface BotMeta {
  created_at: string;
  modified_at: string;
  forked_from: string | null;
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
}

export interface CreateBotRequest {
  name: string;
  soul?: string;
  extra_config?: Record<string, unknown>;
}
