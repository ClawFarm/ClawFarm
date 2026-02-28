"use client";

import Link from "next/link";
import { useBots } from "@/hooks/use-bots";

export function Header() {
  const { bots } = useBots();
  const running = bots.filter((b) => b.status === "running").length;
  const stopped = bots.length - running;

  return (
    <header className="border-b border-border px-6 py-4 flex items-center justify-between">
      <Link href="/" className="text-xl font-bold text-foreground hover:opacity-80 transition-opacity">
        ClawFleetManager
      </Link>
      {bots.length > 0 && (
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            {running} running
          </span>
          {stopped > 0 && (
            <span className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-neutral-500" />
              {stopped} stopped
            </span>
          )}
        </div>
      )}
    </header>
  );
}
