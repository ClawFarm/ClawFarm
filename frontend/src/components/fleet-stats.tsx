"use client";

import { useFleetStats } from "@/hooks/use-fleet-stats";
import { useBots } from "@/hooks/use-bots";
import { useAuth } from "@/hooks/use-auth";
import { formatUptime, formatTokens } from "@/lib/format";

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

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary/50 px-2.5 py-1 text-xs whitespace-nowrap">
      <span className="text-muted-foreground truncate max-w-[120px]">{label}</span>
      <span className="font-semibold tabular-nums text-foreground">{value}</span>
    </span>
  );
}

export function FleetStats() {
  const { stats, isLoading } = useFleetStats();
  const { bots } = useBots();
  const { user } = useAuth();
  const isLimitedUser = user && user.role !== "admin" && !user.bots.includes("*");

  if (isLoading || !stats) {
    return (
      <div className="grid grid-cols-3 gap-3 px-6 pt-6">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} />
        ))}
      </div>
    );
  }

  const modelEntries = Object.entries(stats.tokens_by_model ?? {}).sort(
    ([, a], [, b]) => b - a,
  );
  const hasModels = modelEntries.length > 0;

  const runningBots = bots
    .filter((b) => b.status === "running" && b.uptime_seconds > 0)
    .sort((a, b) => b.uptime_seconds - a.uptime_seconds);

  return (
    <div className="grid grid-cols-3 gap-3 px-6 pt-6">
      <HeroStat
        label="Agents"
        value={String(stats.running_bots)}
        sub={
          stats.starting_bots > 0
            ? `of ${stats.total_bots} total (${stats.starting_bots} starting)`
            : `of ${stats.total_bots} total`
        }
        accent="text-blue-400"
      />
      <div className="rounded-lg border border-border bg-card px-4 py-4 flex flex-col gap-1">
        <span className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium">
          Uptime
        </span>
        {runningBots.length > 0 ? (
          <div className="flex items-center gap-1.5 overflow-x-auto pt-1 pb-0.5 scrollbar-none">
            {runningBots.map((bot) => (
              <Chip key={bot.name} label={bot.name} value={formatUptime(bot.uptime_seconds)} />
            ))}
          </div>
        ) : (
          <span className="text-2xl font-bold tabular-nums leading-none text-muted-foreground">
            —
          </span>
        )}
        <span className="text-xs text-muted-foreground tabular-nums">
          {runningBots.length > 0
            ? `${formatUptime(stats.max_uptime_seconds)} longest`
            : "no agents running"}
        </span>
      </div>
      <div className="rounded-lg border border-border bg-card px-4 py-4 flex flex-col gap-1">
        <span className="text-[11px] uppercase tracking-wider text-muted-foreground font-medium">
          Tokens
        </span>
        {hasModels ? (
          <div className="flex items-center gap-1.5 overflow-x-auto pt-1 pb-0.5 scrollbar-none">
            {modelEntries.map(([model, tokens]) => (
              <Chip key={model} label={model} value={formatTokens(tokens)} />
            ))}
          </div>
        ) : (
          <span className="text-2xl font-bold tabular-nums leading-none text-foreground">
            {formatTokens(stats.total_tokens_used)}
          </span>
        )}
        <span className="text-xs text-muted-foreground tabular-nums">
          {hasModels
            ? `${formatTokens(stats.total_tokens_used)} total`
            : isLimitedUser ? "across your agents" : "across all agents"}
        </span>
      </div>
    </div>
  );
}
