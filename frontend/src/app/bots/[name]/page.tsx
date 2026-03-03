"use client";

import { use, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Header } from "@/components/header";
import { MetricsDisplay } from "@/components/metrics-display";
import { BackupHistory } from "@/components/backup-history";
import { ConfigViewer } from "@/components/config-viewer";
import { SoulViewer } from "@/components/soul-viewer";
import { LogsDialog } from "@/components/logs-dialog";
import { NamePromptDialog } from "@/components/name-prompt-dialog";
import { useBotDetail } from "@/hooks/use-bot-detail";
import { useConfig } from "@/hooks/use-config";
import { statusColor, formatBytes, formatTokens, botUiUrl } from "@/lib/format";
import { api } from "@/lib/api";

export default function BotDetailPage({ params }: { params: Promise<{ name: string }> }) {
  const { name } = use(params);
  const decodedName = decodeURIComponent(name);
  const { detail, isLoading, mutate } = useBotDetail(decodedName);
  const config = useConfig();
  const router = useRouter();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  if (isLoading) {
    return (
      <div className="min-h-screen">
        <Header />
        <div className="flex items-center justify-center py-24 text-muted-foreground">Loading...</div>
      </div>
    );
  }

  if (!detail) {
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
        <div className="flex items-center gap-3 mb-4">
          <Link href="/" className="text-muted-foreground hover:text-foreground transition-colors text-sm">&larr;</Link>
          <h1 className="text-lg font-bold">{detail.name}</h1>
          <Badge className={statusColor(detail.status)}>{detail.status}</Badge>
        </div>

        <div className="space-y-4 max-w-5xl">
          {/* Overview */}
          <Card className="bg-card border-border">
            <CardHeader>
              <CardTitle className="text-sm">Overview</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
                <div>
                  <span className="text-muted-foreground">{detail.ui_path ? "Path" : "Port"}</span>
                  <div className="font-medium">{detail.ui_path || detail.port}</div>
                </div>
                <div>
                  <span className="text-muted-foreground">Container</span>
                  <div className="font-medium truncate">{detail.container_name || "—"}</div>
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
              {detail.token_usage && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
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
                    <div className="font-medium truncate">{detail.token_usage.model || "—"}</div>
                  </div>
                </div>
              )}
              {!detail.ui_path && detail.gateway_token && (
                <div className="text-xs">
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
              <div className="flex flex-wrap gap-1.5">
                {detail.status !== "running" && detail.status !== "starting" && detail.status !== "unhealthy" ? (
                  <Button size="sm" variant="secondary" onClick={() => action("Start", () => api.startBot(detail!.name))}>Start</Button>
                ) : (
                  <>
                    <Button size="sm" variant="secondary" onClick={() => action("Stop", () => api.stopBot(detail!.name))}>Stop</Button>
                    <Button size="sm" variant="secondary" onClick={() => action("Restart", () => api.restartBot(detail!.name))}>Restart</Button>
                  </>
                )}
                <LogsDialog botName={detail.name} />
                <NamePromptDialog
                  label="Duplicate"
                  title={`Duplicate "${detail.name}"`}
                  description="Enter a name for the duplicate bot."
                  onSubmit={(n) => action("Duplicate", () => api.duplicateBot(detail!.name, n))}
                />
                <NamePromptDialog
                  label="Fork"
                  title={`Fork "${detail.name}"`}
                  description="Enter a name for the forked bot."
                  onSubmit={(n) => action("Fork", () => api.forkBot(detail!.name, n))}
                />
                {(detail.ui_path || detail.port > 0) && (
                  <a
                    href={botUiUrl(detail, config?.portal_url, detail.gateway_token)}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={() => { api.approveDevices(detail!.name).catch(() => {}); }}
                  >
                    <Button size="sm" variant="secondary">Open UI</Button>
                  </a>
                )}
                {!confirmDelete ? (
                  <Button size="sm" variant="destructive" onClick={() => setConfirmDelete(true)}>Delete</Button>
                ) : (
                  <>
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
                  </>
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
    </div>
  );
}
