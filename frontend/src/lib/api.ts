import type { Bot, BotDetail, BotStats, Backup, CreateBotRequest, FleetStats, Template, User } from "./types";

const API_BASE = "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    ...options,
  });
  if (res.status === 401 && !path.startsWith("/auth/")) {
    window.location.href = "/login";
    throw new Error("Not authenticated");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    const err = new Error(body.detail || "Request failed");
    (err as unknown as { status: number }).status = res.status;
    throw err;
  }
  return res.json();
}

export interface PortalConfig {
  portal_url: string | null;
  caddy_port: number;
}

export const api = {
  // Auth (login uses raw fetch in login/page.tsx to avoid 401 redirect loop)
  logout: () =>
    request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  getMe: () =>
    request<User>("/auth/me"),
  listUsers: () =>
    request<User[]>("/auth/users"),
  createUser: (data: { username: string; password: string; role: string; bots: string[] }) =>
    request<User>("/auth/users", { method: "POST", body: JSON.stringify(data) }),
  updateUser: (username: string, data: { password?: string; role?: string; bots?: string[] }) =>
    request<User>(`/auth/users/${encodeURIComponent(username)}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteUser: (username: string) =>
    request<{ deleted: string }>(`/auth/users/${encodeURIComponent(username)}`, { method: "DELETE" }),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ ok: boolean }>("/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),

  // Config & fleet
  getConfig: () => request<PortalConfig>("/config"),
  listTemplates: () => request<Template[]>("/templates"),
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
