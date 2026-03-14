"use client";

import { BotCard } from "./bot-card";
import { useFleetSparklines } from "@/hooks/use-bots";
import type { Bot } from "@/lib/types";

export function BotGrid({ bots, onAction }: { bots: Bot[]; onAction: () => void }) {
  const { sparklines, isLoading: sparklinesLoading } = useFleetSparklines();

  if (bots.length === 0) {
    return (
      <div className="text-center text-muted-foreground py-16 text-sm">
        No agents yet. Create one above to get started.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 px-6 py-5">
      {bots.map((bot) => (
        <BotCard
          key={bot.name}
          bot={bot}
          sparkline={sparklines[bot.name] ?? []}
          sparklineLoading={sparklinesLoading}
          onAction={onAction}
        />
      ))}
    </div>
  );
}
