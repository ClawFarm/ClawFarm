"use client";

import { use } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MetricsDisplay } from "@/components/metrics-display";
import { BackupHistory } from "@/components/backup-history";
import { ConfigViewer } from "@/components/config-viewer";
import { SoulViewer } from "@/components/soul-viewer";
import { LogsDialog } from "@/components/logs-dialog";
import { NamePromptDialog } from "@/components/name-prompt-dialog";
import { useBotDetail } from "@/hooks/use-bot-detail";
import { useConfig } from "@/hooks/use-config";
import { statusColor, formatBytes, botUiUrl } from "@/lib/format";
import { api } from "@/lib/api";

export default function BotDetailPage({ params }: { params: Promise<{ name: string }> }) {
  const { name } = use(params);
  const decodedName = decodeURIComponent(name);
  const { detail, isLoading, mutate } = useBotDetail(decodedName);
  const config = useConfig();

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-muted-foreground">
        Loading...
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4">
        <p className="text-muted-foreground">Bot not found.</p>
        <Link href="/" className="text-muted-foreground hover:text-foreground transition-colors">Back to dashboard</Link>
      </div>
    );
  }

  async function action(fn: () => Promise<unknown>) {
    try {
      await fn();
      mutate();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Action failed");
    }
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-border px-6 py-4 flex items-center gap-4">
        <Link href="/" className="text-muted-foreground hover:text-foreground transition-colors text-sm">&larr; Dashboard</Link>
        <h1 className="text-lg font-bold">{detail.name}</h1>
        <Badge className={statusColor(detail.status)}>{detail.status}</Badge>
      </header>

      <div className="p-4 space-y-4 max-w-5xl">
        {/* Overview */}
        <Card className="bg-card border-border">
          <CardHeader>
            <CardTitle className="text-sm">Overview</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
              <div>
                <span className="text-muted-foreground">Port</span>
                <div className="font-medium">{detail.port}</div>
              </div>
              <div>
                <span className="text-muted-foreground">Container</span>
                <div className="font-medium">{detail.container_name || "—"}</div>
              </div>
              <div>
                <span className="text-muted-foreground">Created</span>
                <div className="font-medium">
                  {detail.meta?.created_at ? new Date(detail.meta.created_at).toLocaleString() : "—"}
                </div>
              </div>
              <div>
                <span className="text-muted-foreground">Storage</span>
                <div className="font-medium">{formatBytes(detail.storage_bytes)}</div>
              </div>
            </div>
            {detail.gateway_token && (
              <div className="text-xs">
                <span className="text-muted-foreground">Gateway Token</span>
                <div className="font-mono bg-secondary px-2 py-1 rounded mt-0.5 flex items-center gap-2">
                  <span className="truncate">{detail.gateway_token}</span>
                  <button
                    className="text-foreground hover:opacity-70 transition-opacity shrink-0"
                    onClick={() => {
                      navigator.clipboard.writeText(detail.gateway_token);
                      alert("Token copied to clipboard");
                    }}
                  >
                    Copy
                  </button>
                </div>
              </div>
            )}
            <div className="flex flex-wrap gap-1.5">
              <Button size="sm" variant="secondary" onClick={() => action(() => api.startBot(detail!.name))}>Start</Button>
              <Button size="sm" variant="secondary" onClick={() => action(() => api.stopBot(detail!.name))}>Stop</Button>
              <Button size="sm" variant="secondary" onClick={() => action(() => api.restartBot(detail!.name))}>Restart</Button>
              <LogsDialog botName={detail.name} />
              <NamePromptDialog
                label="Duplicate"
                title={`Duplicate "${detail.name}"`}
                description="Enter a name for the duplicate bot."
                onSubmit={(n) => action(() => api.duplicateBot(detail!.name, n))}
              />
              <NamePromptDialog
                label="Fork"
                title={`Fork "${detail.name}"`}
                description="Enter a name for the forked bot."
                onSubmit={(n) => action(() => api.forkBot(detail!.name, n))}
              />
              <Button
                size="sm"
                variant="outline"
                onClick={() => action(() => api.approveDevices(detail!.name))}
              >
                Approve Devices
              </Button>
              {detail.port > 0 && (
                <a
                  href={botUiUrl(detail.port, config?.portal_url, detail.gateway_token)}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={() => { api.approveDevices(detail!.name).catch(() => {}); }}
                >
                  <Button size="sm" variant="secondary">Open UI</Button>
                </a>
              )}
            </div>
          </CardContent>
        </Card>

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
                      ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/25"
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

        {/* Config & Soul */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card className="bg-card border-border">
            <CardContent className="pt-6">
              <ConfigViewer config={detail.config} />
            </CardContent>
          </Card>
          <Card className="bg-card border-border">
            <CardContent className="pt-6">
              <SoulViewer soul={detail.soul} />
            </CardContent>
          </Card>
        </div>

        {/* Backups */}
        <Card className="bg-card border-border">
          <CardContent className="pt-6">
            <BackupHistory
              botName={detail.name}
              backups={detail.meta?.backups || []}
              onAction={mutate}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
