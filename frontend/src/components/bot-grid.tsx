"use client";

import { BotCard } from "./bot-card";
import type { Bot } from "@/lib/types";

export function BotGrid({ bots, onAction }: { bots: Bot[]; onAction: () => void }) {
  if (bots.length === 0) {
    return (
      <div className="text-center text-muted-foreground py-12">
        No bots yet. Create one above.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 p-4">
      {bots.map((bot) => (
        <BotCard key={bot.name} bot={bot} onAction={onAction} />
      ))}
    </div>
  );
}
