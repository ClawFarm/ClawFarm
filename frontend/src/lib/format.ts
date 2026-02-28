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

export function statusColor(status: string): string {
  switch (status) {
    case "running":
      return "bg-[#2e7d32] text-[#c8e6c9]";
    case "exited":
      return "bg-[#c62828] text-[#ffcdd2]";
    case "created":
      return "bg-[#f57f17] text-[#fff9c4]";
    default:
      return "bg-[#555] text-[#ddd]";
  }
}
