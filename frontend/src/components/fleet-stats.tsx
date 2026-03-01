"use client";

import { useFleetStats } from "@/hooks/use-fleet-stats";
import { formatMB, formatTokens } from "@/lib/format";

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
      <div className="grid grid-cols-3 gap-3 px-6 pt-6">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} />
        ))}
      </div>
    );
  }

  const memFreeMb = stats.total_memory_limit_mb - stats.total_memory_mb;
  const memFreePercent =
    stats.total_memory_limit_mb > 0
      ? ((memFreeMb / stats.total_memory_limit_mb) * 100).toFixed(0)
      : "—";

  return (
    <div className="grid grid-cols-3 gap-3 px-6 pt-6">
      <HeroStat
        label="Agents"
        value={String(stats.running_bots)}
        sub={
          stats.starting_bots > 0
            ? `of ${stats.total_bots} total (${stats.starting_bots} starting)`
            : `of ${stats.total_bots} running`
        }
        accent="text-emerald-400"
      />
      <HeroStat
        label="RAM Free"
        value={formatMB(memFreeMb)}
        sub={`${memFreePercent}% of ${formatMB(stats.total_memory_limit_mb)}`}
        accent={memFreeMb < 1024 ? "text-red-400" : undefined}
      />
      <HeroStat
        label="Tokens Used"
        value={formatTokens(stats.total_tokens_used)}
        sub="across all agents"
      />
    </div>
  );
}
