"use client";

import { useFleetStats } from "@/hooks/use-fleet-stats";
import { FleetChart } from "./fleet-chart";
import { formatTokens } from "@/lib/format";

export function FleetStats() {
  const { stats, isLoading } = useFleetStats();

  if (isLoading || !stats) {
    return (
      <div className="px-6 pt-6 space-y-3">
        <div className="h-5 w-48 bg-secondary rounded animate-pulse" />
        <div className="h-32 w-full bg-secondary rounded-lg animate-pulse" />
      </div>
    );
  }

  const parts: string[] = [];
  parts.push(`${stats.total_bots} bot${stats.total_bots !== 1 ? "s" : ""}`);

  if (stats.starting_bots > 0) {
    parts.push(`${stats.running_bots} running (${stats.starting_bots} starting)`);
  } else {
    parts.push(`${stats.running_bots} running`);
  }

  if (stats.total_tokens_used > 0) {
    parts.push(`${formatTokens(stats.total_tokens_used)} tokens`);
  }

  return (
    <div className="px-6 pt-6 space-y-3">
      <p className="text-sm text-muted-foreground tabular-nums">
        {parts.join(" \u00b7 ")}
      </p>
      <FleetChart />
    </div>
  );
}
