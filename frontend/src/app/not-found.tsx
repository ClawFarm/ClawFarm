import Link from "next/link";
import Image from "next/image";
import { Button } from "@/components/ui/button";

export default function NotFound() {
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
          <p className="text-6xl font-bold tabular-nums text-muted-foreground/50">404</p>
          <h1 className="text-lg font-semibold text-foreground">Page not found</h1>
          <p className="text-sm text-muted-foreground max-w-xs mx-auto">
            The page you&apos;re looking for doesn&apos;t exist or has been moved.
          </p>
          <Button asChild variant="secondary" size="sm" className="mt-2">
            <Link href="/">Back to dashboard</Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
