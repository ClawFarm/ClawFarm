import type { Bot, BotDetail, BotStats, Backup, CreateBotRequest, FleetStats } from "./types";

const API_BASE = "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || "Request failed");
  }
  return res.json();
}

export interface PortalConfig {
  portal_url: string | null;
  caddy_port: number;
}

export const api = {
  getConfig: () => request<PortalConfig>("/config"),
  getFleetStats: () => request<FleetStats>("/fleet/stats"),
  listBots: () => request<Bot[]>("/bots"),
  createBot: (data: CreateBotRequest) =>
    request<Bot>("/bots", { method: "POST", body: JSON.stringify(data) }),
  startBot: (name: string) =>
    request<{ name: string; status: string }>(`/bots/${encodeURIComponent(name)}/start`, { method: "POST" }),
  stopBot: (name: string) =>
    request<{ name: string; status: string }>(`/bots/${encodeURIComponent(name)}/stop`, { method: "POST" }),
  restartBot: (name: string) =>
    request<{ name: string; status: string }>(`/bots/${encodeURIComponent(name)}/restart`, { method: "POST" }),
  deleteBot: (name: string) =>
    request<{ deleted: string }>(`/bots/${encodeURIComponent(name)}`, { method: "DELETE" }),
  getLogs: (name: string) =>
    request<{ name: string; logs: string }>(`/bots/${encodeURIComponent(name)}/logs`),
  duplicateBot: (name: string, newName: string) =>
    request<Bot>(`/bots/${encodeURIComponent(name)}/duplicate`, {
      method: "POST",
      body: JSON.stringify({ new_name: newName }),
    }),
  forkBot: (name: string, newName: string) =>
    request<Bot>(`/bots/${encodeURIComponent(name)}/fork`, {
      method: "POST",
      body: JSON.stringify({ new_name: newName }),
    }),
  createBackup: (name: string) =>
    request<Backup>(`/bots/${encodeURIComponent(name)}/backup`, { method: "POST" }),
  listBackups: (name: string) =>
    request<Backup[]>(`/bots/${encodeURIComponent(name)}/backups`),
  rollback: (name: string, timestamp: string) =>
    request<{ name: string; rolled_back_to: string }>(`/bots/${encodeURIComponent(name)}/rollback`, {
      method: "POST",
      body: JSON.stringify({ timestamp }),
    }),
  getDetail: (name: string) =>
    request<BotDetail>(`/bots/${encodeURIComponent(name)}/detail`),
  getStats: (name: string) =>
    request<BotStats>(`/bots/${encodeURIComponent(name)}/stats`),
  approveDevices: (name: string) =>
    request<{ approved: number; request_ids: string[] }>(`/bots/${encodeURIComponent(name)}/approve-devices`, {
      method: "POST",
    }),
};
