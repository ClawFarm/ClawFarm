"use client";

import Image from "next/image";
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
      <div className="flex items-center justify-between px-4 sm:px-6 py-3 gap-2">
        <Link
          href="/"
          className="flex items-center gap-2 hover:opacity-80 transition-opacity shrink-0"
        >
          <Image src="/logo.svg" alt="ClawFarm" width={28} height={28} className="h-7 w-7 rounded-md" />
          <span className="text-sm font-semibold tracking-tight text-foreground hidden sm:inline">
            ClawFarm
          </span>
        </Link>

        <div className="flex items-center gap-2 sm:gap-4 text-xs">
          {bots.length > 0 && (
            <div className="flex items-center gap-1.5 text-blue-400 shrink-0">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-400" />
              </span>
              <span>{running}<span className="hidden sm:inline"> active</span></span>
              <span className="text-muted-foreground hidden sm:inline">/ {bots.length}</span>
            </div>
          )}
          {user && (
            <>
              <div className="h-4 border-l border-border" />
              <span className="text-muted-foreground hidden sm:inline">{user.username}</span>
              <Badge variant={user.role === "admin" ? "default" : "secondary"} className="text-[10px]">
                {user.role}
              </Badge>
              <Link href="/users" className="text-muted-foreground hover:text-foreground transition-colors hidden sm:inline">
                {user.role === "admin" ? "Users" : "Account"}
              </Link>
              <Button variant="ghost" size="xs" onClick={logout} className="text-muted-foreground">
                <span className="hidden sm:inline">Sign out</span>
                <span className="sm:hidden text-xs">Out</span>
              </Button>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
