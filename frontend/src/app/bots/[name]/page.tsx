"use client";

import { use, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Header } from "@/components/header";
import { MetricsDisplay } from "@/components/metrics-display";
import { BackupHistory } from "@/components/backup-history";
import { LogsDialog } from "@/components/logs-dialog";
import { TerminalDialog } from "@/components/terminal-dialog";
import { CloneDialog } from "@/components/clone-dialog";
import { Sparkline } from "@/components/sparkline";
import { useBotDetail } from "@/hooks/use-bot-detail";
import { useConfig } from "@/hooks/use-config";
import { statusColor, formatTokens, botUiUrl } from "@/lib/format";
import { api } from "@/lib/api";

export default function BotDetailPage({ params }: { params: Promise<{ name: string }> }) {
  const { name } = use(params);
  const decodedName = decodeURIComponent(name);
  const { detail, isLoading, mutate } = useBotDetail(decodedName);
  const config = useConfig();
  const router = useRouter();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const { data: sparklineData } = useSWR(
    detail ? `sparkline-${detail.name}` : null,
    () => api.getBotSparkline(decodedName),
    { refreshInterval: 60000 },
  );

  if (isLoading) {
    return (
      <div className="min-h-screen">
        <Header />
        <div className="flex items-center justify-center py-24 text-muted-foreground">Loading...</div>
      </div>
    );
  }

  if (!detail || detail.status === "not_found") {
    return (
      <div className="min-h-screen">
        <Header />
        <div className="flex flex-col items-center justify-center gap-4 py-24">
          <p className="text-muted-foreground">Bot not found.</p>
          <Link href="/" className="text-muted-foreground hover:text-foreground transition-colors">Back to dashboard</Link>
        </div>
      </div>
    );
  }

  const isRunning = detail.status === "running" || detail.status === "starting" || detail.status === "unhealthy";

  async function action(label: string, fn: () => Promise<unknown>) {
    try {
      await fn();
      mutate();
      toast.success(`${label} completed`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `${label} failed`);
    }
  }

  return (
    <div className="min-h-screen">
      <Header />
      <div className="px-4 sm:px-6 py-4">
        <div className="space-y-4 max-w-5xl">
          {/* Hero */}
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <Link href="/" className="text-muted-foreground hover:text-foreground transition-colors text-sm">&larr;</Link>
              <h1 className="text-lg font-bold">{detail.name}</h1>
              <Badge className={statusColor(detail.status)}>{detail.status}</Badge>
            </div>

            {/* Primary actions */}
            <div className="flex flex-wrap gap-1.5">
              {isRunning ? (
                <a
                  href={botUiUrl(detail, config?.portal_url, detail.gateway_token)}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={() => { api.approveDevices(detail!.name).catch(() => {}); }}
                >
                  <Button size="sm">Open UI</Button>
                </a>
              ) : (
                <Button size="sm" disabled>Open UI</Button>
              )}
              <TerminalDialog
                botName={detail.name}
                trigger={
                  <Button size="sm" variant="secondary" disabled={!isRunning}>
                    Terminal
                  </Button>
                }
              />
              <LogsDialog botName={detail.name} />
            </div>

            {/* Lifecycle actions */}
            <div className="flex flex-wrap gap-1.5">
              {!isRunning ? (
                <Button size="sm" variant="secondary" onClick={() => action("Start", () => api.startBot(detail!.name))}>
                  Start
                </Button>
              ) : (
                <>
                  <Button size="sm" variant="secondary" onClick={() => action("Stop", () => api.stopBot(detail!.name))}>
                    Stop
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => action("Restart", () => api.restartBot(detail!.name))}>
                    Restart
                  </Button>
                </>
              )}
              <CloneDialog
                sourceName={detail.name}
                onClone={async (newName, trackFork) => {
                  await api.cloneBot(detail!.name, newName, trackFork);
                  mutate();
                  toast.success("Clone completed");
                }}
              />
            </div>
          </div>

          {/* Token sparkline + stats */}
          <Card className="bg-card border-border">
            <CardContent className="pt-6">
              <div className="flex items-center gap-6">
                <Sparkline data={sparklineData ?? []} width={200} height={48} className="shrink-0 opacity-80" />
                {detail.token_usage && (
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs flex-1">
                    <div>
                      <span className="text-muted-foreground">Total Tokens</span>
                      <div className="font-medium">{formatTokens(detail.token_usage.total_tokens)}</div>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Input</span>
                      <div className="font-medium">{formatTokens(detail.token_usage.input_tokens)}</div>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Output</span>
                      <div className="font-medium">{formatTokens(detail.token_usage.output_tokens)}</div>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Model</span>
                      <div className="font-medium truncate">{detail.token_usage.model || "\u2014"}</div>
                    </div>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Gateway token (dev mode only) */}
          {!detail.ui_path && detail.gateway_token && (
            <div className="text-xs px-1">
              <span className="text-muted-foreground">Gateway Token</span>
              <div className="font-mono bg-secondary px-2 py-1 rounded mt-0.5 flex items-center gap-2">
                <span className="truncate">{detail.gateway_token}</span>
                <button
                  className="text-foreground hover:opacity-70 transition-opacity shrink-0"
                  onClick={() => {
                    navigator.clipboard.writeText(detail.gateway_token);
                    toast.success("Token copied to clipboard");
                  }}
                >
                  Copy
                </button>
              </div>
            </div>
          )}

          {/* Metrics */}
          <Card className="bg-card border-border">
            <CardHeader>
              <CardTitle className="text-sm">Metrics</CardTitle>
            </CardHeader>
            <CardContent>
              <MetricsDisplay stats={detail.stats} />
            </CardContent>
          </Card>

          {/* Cron Jobs */}
          {detail.cron_jobs.length > 0 && (
            <Card className="bg-card border-border">
              <CardHeader>
                <CardTitle className="text-sm">Cron Jobs</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-1">
                  {detail.cron_jobs.map((job) => (
                    <div
                      key={job.id}
                      className="flex items-center justify-between rounded-sm bg-secondary px-3 py-1.5 text-xs border border-border"
                    >
                      <div className="flex items-center gap-3">
                        <span className="font-medium font-mono">{job.schedule}</span>
                        <span className="text-muted-foreground">{job.name}</span>
                      </div>
                      <Badge className={job.enabled
                        ? "bg-blue-500/15 text-blue-400 border-blue-500/25"
                        : "bg-neutral-500/15 text-neutral-400 border-neutral-500/25"
                      }>
                        {job.enabled ? "active" : "disabled"}
                      </Badge>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Config & Soul — collapsible */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card className="bg-card border-border">
              <CardContent className="pt-6">
                <details>
                  <summary className="text-sm font-medium cursor-pointer select-none hover:text-muted-foreground transition-colors">
                    OpenClaw Config
                  </summary>
                  <div className="mt-3 rounded-sm bg-secondary border border-border p-3 overflow-auto max-h-64">
                    <pre className="text-xs whitespace-pre-wrap text-muted-foreground font-mono">
                      {JSON.stringify(detail.config, null, 2)}
                    </pre>
                  </div>
                </details>
              </CardContent>
            </Card>
            <Card className="bg-card border-border">
              <CardContent className="pt-6">
                <details>
                  <summary className="text-sm font-medium cursor-pointer select-none hover:text-muted-foreground transition-colors">
                    SOUL.md
                  </summary>
                  <div className="mt-3 rounded-sm bg-secondary border border-border p-3 overflow-auto max-h-48">
                    <pre className="text-xs whitespace-pre-wrap text-muted-foreground font-mono">
                      {detail.soul || "(empty)"}
                    </pre>
                  </div>
                </details>
              </CardContent>
            </Card>
          </div>

          {/* Backups — limited to 3 */}
          <Card className="bg-card border-border">
            <CardContent className="pt-6">
              <BackupHistory
                botName={detail.name}
                backups={detail.meta?.backups || []}
                onAction={mutate}
                initialLimit={3}
              />
            </CardContent>
          </Card>

          {/* Delete — bottom of page */}
          <div className="border-t border-border pt-4">
            {!confirmDelete ? (
              <Button size="sm" variant="destructive" onClick={() => setConfirmDelete(true)}>
                Delete this agent
              </Button>
            ) : (
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="destructive"
                  disabled={deleting}
                  onClick={async () => {
                    setDeleting(true);
                    try {
                      await api.deleteBot(detail!.name);
                      toast.success(`Deleted "${detail!.name}"`);
                      router.push("/");
                    } catch (err) {
                      toast.error(err instanceof Error ? err.message : "Delete failed");
                      setDeleting(false);
                      setConfirmDelete(false);
                    }
                  }}
                >
                  {deleting ? "Deleting..." : "Confirm delete"}
                </Button>
                <Button size="sm" variant="secondary" onClick={() => setConfirmDelete(false)}>Cancel</Button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
