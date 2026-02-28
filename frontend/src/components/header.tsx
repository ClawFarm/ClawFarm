"use client";

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useBots } from "@/hooks/use-bots";
import { useAuth } from "@/hooks/use-auth";

export function Header() {
  const { bots } = useBots();
  const { user, logout } = useAuth();
  const running = bots.filter((b) => b.status === "running").length;

  return (
    <header className="border-b border-border bg-card/50 backdrop-blur-sm sticky top-0 z-10">
      <div className="flex items-center justify-between px-6 py-3">
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="flex items-center gap-2.5 hover:opacity-80 transition-opacity"
          >
            <div className="h-7 w-7 rounded-md bg-emerald-500/15 border border-emerald-500/25 flex items-center justify-center">
              <span className="text-emerald-400 text-xs font-bold">C</span>
            </div>
            <span className="text-sm font-semibold tracking-tight text-foreground">
              ClawFleetManager
            </span>
          </Link>
          <span className="text-xs text-muted-foreground hidden sm:inline">Fleet Control</span>
        </div>
        <div className="flex items-center gap-4 text-xs">
          {bots.length > 0 && (
            <>
              <div className="flex items-center gap-1.5 text-emerald-400">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-400" />
                </span>
                {running} active
              </div>
              <span className="text-muted-foreground">
                {bots.length} total
              </span>
            </>
          )}
          {user && (
            <div className="flex items-center gap-2 border-l border-border pl-4">
              <span className="text-muted-foreground">{user.username}</span>
              <Badge variant={user.role === "admin" ? "default" : "secondary"} className="text-[10px]">
                {user.role}
              </Badge>
              <Link href="/users" className="text-muted-foreground hover:text-foreground transition-colors">
                {user.role === "admin" ? "Users" : "Account"}
              </Link>
              <Button variant="ghost" size="xs" onClick={logout} className="text-muted-foreground">
                Sign out
              </Button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
