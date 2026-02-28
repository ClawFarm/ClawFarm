"use client";

import { formatUptime, formatMB } from "@/lib/format";
import type { BotStats } from "@/lib/types";

function ProgressBar({ percent, color }: { percent: number; color: string }) {
  return (
    <div className="h-1.5 w-full rounded-full bg-secondary">
      <div
        className={`h-full rounded-full ${color}`}
        style={{ width: `${Math.min(percent, 100)}%` }}
      />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-sm font-medium">{value}</div>
    </div>
  );
}

export function MetricsDisplay({ stats }: { stats: BotStats | null }) {
  if (!stats) {
    return <div className="text-sm text-muted-foreground">No metrics available (container not running)</div>;
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">CPU</span>
            <span>{stats.cpu_percent}%</span>
          </div>
          <ProgressBar percent={stats.cpu_percent} color="bg-foreground/60" />
        </div>
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">Memory</span>
            <span>{stats.memory_percent}%</span>
          </div>
          <ProgressBar
            percent={stats.memory_percent}
            color={stats.memory_percent > 80 ? "bg-destructive" : "bg-foreground/60"}
          />
        </div>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <Stat label="Memory" value={`${formatMB(stats.memory_mb)} / ${formatMB(stats.memory_limit_mb)}`} />
        <Stat label="Network RX" value={formatMB(stats.network_rx_mb)} />
        <Stat label="Network TX" value={formatMB(stats.network_tx_mb)} />
        <Stat label="Uptime" value={formatUptime(stats.uptime_seconds)} />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <Stat label="Restart Count" value={String(stats.restart_count)} />
        <Stat label="Started At" value={stats.started_at ? new Date(stats.started_at).toLocaleString() : "—"} />
      </div>
    </div>
  );
}
