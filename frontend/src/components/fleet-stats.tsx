"use client";

import { useFleetStats } from "@/hooks/use-fleet-stats";
import { formatMB, formatBytes, formatUptime } from "@/lib/format";

function HeroStat({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-4 flex flex-col gap-1">
      <span className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium">
        {label}
      </span>
      <span className={`text-2xl font-bold tabular-nums leading-none ${accent ?? "text-foreground"}`}>
        {value}
      </span>
      {sub && (
        <span className="text-xs text-muted-foreground tabular-nums">{sub}</span>
      )}
    </div>
  );
}

function Skeleton() {
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-4 animate-pulse flex flex-col gap-2">
      <div className="h-3 w-14 bg-secondary rounded" />
      <div className="h-7 w-20 bg-secondary rounded" />
      <div className="h-3 w-24 bg-secondary rounded" />
    </div>
  );
}

export function FleetStats() {
  const { stats, isLoading } = useFleetStats();

  if (isLoading || !stats) {
    return (
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 px-6 pt-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} />
        ))}
      </div>
    );
  }

  const memPercent =
    stats.total_memory_limit_mb > 0
      ? ((stats.total_memory_mb / stats.total_memory_limit_mb) * 100).toFixed(0)
      : "0";

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 px-6 pt-6">
      <HeroStat
        label="Agents"
        value={String(stats.running_bots)}
        sub={`of ${stats.total_bots} running`}
        accent="text-emerald-400"
      />
      <HeroStat
        label="CPU"
        value={`${stats.total_cpu_percent.toFixed(1)}%`}
        sub="total utilization"
      />
      <HeroStat
        label="Memory"
        value={formatMB(stats.total_memory_mb)}
        sub={`${memPercent}% of ${formatMB(stats.total_memory_limit_mb)}`}
      />
      <HeroStat
        label="Storage"
        value={formatBytes(stats.total_storage_bytes)}
        sub="across all agents"
      />
      <HeroStat
        label="Network"
        value={formatMB(stats.total_network_tx_mb)}
        sub={`up · ${formatMB(stats.total_network_rx_mb)} down`}
      />
      <HeroStat
        label="Uptime"
        value={formatUptime(stats.max_uptime_seconds)}
        sub="longest running"
      />
    </div>
  );
}
