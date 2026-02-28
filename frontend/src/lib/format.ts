export function formatUptime(seconds: number): string {
  if (seconds <= 0) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function formatMB(mb: number): string {
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb.toFixed(0)} MB`;
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export function botUiUrl(port: number, portalUrl?: string | null): string {
  if (typeof window === "undefined") return `http://localhost:${port}`;
  if (portalUrl) return `${portalUrl}:${port}/`;
  if (window.location.protocol === "https:") {
    return `https://${window.location.hostname}:${port}/`;
  }
  return `http://${window.location.hostname}:${port}`;
}

export function statusColor(status: string): string {
  switch (status) {
    case "running":
      return "bg-emerald-500/15 text-emerald-400 border-emerald-500/25";
    case "exited":
      return "bg-red-500/15 text-red-400 border-red-500/25";
    case "created":
      return "bg-amber-500/15 text-amber-400 border-amber-500/25";
    default:
      return "bg-neutral-500/15 text-neutral-400 border-neutral-500/25";
  }
}
