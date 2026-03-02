"use client";

import Link from "next/link";
import Image from "next/image";
import { Button } from "@/components/ui/button";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const is403 = error.message?.includes("403") || error.message?.toLowerCase().includes("forbidden");

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-border bg-card/50 backdrop-blur-sm sticky top-0 z-10">
        <div className="flex items-center px-4 sm:px-6 py-3">
          <Link href="/" className="flex items-center gap-2 hover:opacity-80 transition-opacity">
            <Image src="/logo.svg" alt="ClawFarm" width={28} height={28} className="h-7 w-7 rounded-md" />
            <span className="text-sm font-semibold tracking-tight text-foreground hidden sm:inline">
              ClawFarm
            </span>
          </Link>
        </div>
      </header>
      <div className="flex-1 flex items-center justify-center px-4">
        <div className="text-center space-y-4">
          {is403 ? (
            <>
              <p className="text-6xl font-bold tabular-nums text-muted-foreground/50">403</p>
              <h1 className="text-lg font-semibold text-foreground">Access denied</h1>
              <p className="text-sm text-muted-foreground max-w-xs mx-auto">
                You don&apos;t have permission to view this page.
              </p>
            </>
          ) : (
            <>
              <p className="text-6xl font-bold tabular-nums text-muted-foreground/50">500</p>
              <h1 className="text-lg font-semibold text-foreground">Something went wrong</h1>
              <p className="text-sm text-muted-foreground max-w-xs mx-auto">
                An unexpected error occurred. Try again or head back to the dashboard.
              </p>
            </>
          )}
          <div className="flex items-center justify-center gap-2 mt-2">
            <Button variant="secondary" size="sm" onClick={reset}>
              Try again
            </Button>
            <Button asChild variant="ghost" size="sm">
              <Link href="/">Dashboard</Link>
            </Button>
          </div>
          {error.digest && (
            <p className="text-xs text-muted-foreground/50 font-mono">{error.digest}</p>
          )}
        </div>
      </div>
    </div>
  );
}
