"use client";

import { useFleetStats } from "@/hooks/use-fleet-stats";
import { formatMB, formatBytes, formatUptime } from "@/lib/format";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-card px-4 py-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
    </div>
  );
}

export function FleetStats() {
  const { stats, isLoading } = useFleetStats();

  if (isLoading || !stats) {
    return (
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 px-4 pt-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="rounded-md border border-border bg-card px-4 py-3 animate-pulse">
            <div className="h-3 w-12 bg-secondary rounded mb-2" />
            <div className="h-6 w-16 bg-secondary rounded" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 px-4 pt-4">
      <Stat
        label="Running"
        value={`${stats.running_bots} / ${stats.total_bots}`}
      />
      <Stat
        label="CPU"
        value={`${stats.total_cpu_percent.toFixed(1)}%`}
      />
      <Stat
        label="Memory"
        value={`${formatMB(stats.total_memory_mb)} / ${formatMB(stats.total_memory_limit_mb)}`}
      />
      <Stat
        label="Storage"
        value={formatBytes(stats.total_storage_bytes)}
      />
      <Stat
        label="Network"
        value={`↑ ${formatMB(stats.total_network_tx_mb)}  ↓ ${formatMB(stats.total_network_rx_mb)}`}
      />
      <Stat
        label="Uptime"
        value={formatUptime(stats.max_uptime_seconds)}
      />
    </div>
  );
}
