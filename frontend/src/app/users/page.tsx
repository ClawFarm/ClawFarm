"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { Header } from "@/components/header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAuth } from "@/hooks/use-auth";
import { useBots } from "@/hooks/use-bots";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";

function BotSelector({ selected, onChange }: { selected: string[]; onChange: (bots: string[]) => void }) {
  const { bots } = useBots();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const isAll = selected.includes("*");

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const toggle = (name: string) => {
    if (name === "*") {
      onChange(isAll ? [] : ["*"]);
      return;
    }
    const filtered = selected.filter((b) => b !== "*");
    if (filtered.includes(name)) {
      onChange(filtered.filter((b) => b !== name));
    } else {
      onChange([...filtered, name]);
    }
  };

  const label = isAll ? "All bots (*)" : selected.length ? selected.join(", ") : "Select bots...";

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="h-9 rounded-md border border-input bg-transparent px-3 text-sm text-left w-48 truncate"
      >
        {label}
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-48 max-h-48 overflow-auto rounded-md border border-border bg-card shadow-lg p-1">
          <label className="flex items-center gap-2 px-2 py-1 text-xs hover:bg-secondary rounded cursor-pointer">
            <input type="checkbox" checked={isAll} onChange={() => toggle("*")} />
            All bots (*)
          </label>
          {bots.map((bot) => (
            <label key={bot.name} className="flex items-center gap-2 px-2 py-1 text-xs hover:bg-secondary rounded cursor-pointer">
              <input
                type="checkbox"
                checked={isAll || selected.includes(bot.name)}
                disabled={isAll}
                onChange={() => toggle(bot.name)}
              />
              {bot.name}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

function ChangePasswordCard() {
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSuccess("");
    if (newPw !== confirmPw) {
      setError("Passwords do not match");
      return;
    }
    try {
      await api.changePassword(currentPw, newPw);
      setCurrentPw("");
      setNewPw("");
      setConfirmPw("");
      setSuccess("Password changed. You will need to log in again.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to change password");
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Change Password</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Current Password</label>
            <Input type="password" value={currentPw} onChange={(e) => setCurrentPw(e.target.value)} required className="w-40" />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">New Password</label>
            <Input type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} required className="w-40" />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Confirm</label>
            <Input type="password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} required className="w-40" />
          </div>
          <Button type="submit" size="sm">Change</Button>
          {error && <p className="text-xs text-red-400 w-full">{error}</p>}
          {success && <p className="text-xs text-emerald-400 w-full">{success}</p>}
        </form>
      </CardContent>
    </Card>
  );
}

function AddUserForm({ onCreated }: { onCreated: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const [bots, setBots] = useState<string[]>([]);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await api.createUser({ username, password, role, bots });
      setUsername("");
      setPassword("");
      setRole("user");
      setBots([]);
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create user");
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Add User</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Username</label>
            <Input value={username} onChange={(e) => setUsername(e.target.value)} required className="w-36" />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Password</label>
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required className="w-36" />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Role</label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
            >
              <option value="user">user</option>
              <option value="admin">admin</option>
            </select>
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Bot Access</label>
            <BotSelector selected={bots} onChange={setBots} />
          </div>
          <Button type="submit" size="sm">Add</Button>
          {error && <p className="text-xs text-red-400 w-full">{error}</p>}
        </form>
      </CardContent>
    </Card>
  );
}

function UserRow({ user, onUpdated }: { user: User; onUpdated: () => void }) {
  const { user: currentUser } = useAuth();
  const [editing, setEditing] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState(user.role);
  const [newBots, setNewBots] = useState<string[]>([...user.bots]);
  const [error, setError] = useState("");

  const isSelf = currentUser?.username === user.username;

  const handleSave = async () => {
    setError("");
    try {
      const update: { password?: string; role?: string; bots?: string[] } = {};
      if (newPassword.trim()) update.password = newPassword;
      if (newRole !== user.role) update.role = newRole;
      if (JSON.stringify(newBots) !== JSON.stringify(user.bots)) update.bots = newBots;
      if (Object.keys(update).length === 0) {
        setEditing(false);
        return;
      }
      await api.updateUser(user.username, update);
      setNewPassword("");
      setEditing(false);
      onUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  };

  const handleDelete = async () => {
    if (!confirm(`Delete user "${user.username}"?`)) return;
    try {
      await api.deleteUser(user.username);
      onUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  if (editing) {
    return (
      <tr className="border-t border-border">
        <td className="px-3 py-2 text-sm">{user.username}</td>
        <td className="px-3 py-2">
          <select
            value={newRole}
            onChange={(e) => setNewRole(e.target.value)}
            className="h-7 rounded border border-input bg-transparent px-2 text-xs"
          >
            <option value="user">user</option>
            <option value="admin">admin</option>
          </select>
        </td>
        <td className="px-3 py-2">
          <BotSelector selected={newBots} onChange={setNewBots} />
        </td>
        <td className="px-3 py-2">
          <Input
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder="unchanged"
            className="h-7 text-xs w-28"
          />
        </td>
        <td className="px-3 py-2">
          <div className="flex gap-1">
            <Button size="xs" onClick={handleSave}>Save</Button>
            <Button size="xs" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
          {error && <p className="text-xs text-red-400 mt-1">{error}</p>}
        </td>
      </tr>
    );
  }

  return (
    <tr className="border-t border-border">
      <td className="px-3 py-2 text-sm">
        {user.username}
        {isSelf && <span className="text-xs text-muted-foreground ml-1">(you)</span>}
      </td>
      <td className="px-3 py-2">
        <Badge variant={user.role === "admin" ? "default" : "secondary"} className="text-[10px]">
          {user.role}
        </Badge>
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground">
        {user.bots.join(", ") || "none"}
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground">***</td>
      <td className="px-3 py-2">
        <div className="flex gap-1">
          <Button size="xs" variant="ghost" onClick={() => setEditing(true)}>Edit</Button>
          {!isSelf && (
            <Button size="xs" variant="ghost" className="text-red-400 hover:text-red-300" onClick={handleDelete}>
              Delete
            </Button>
          )}
        </div>
      </td>
    </tr>
  );
}

export default function UsersPage() {
  const { user, isLoading: authLoading } = useAuth();
  const { data: users, mutate } = useSWR<User[]>("users", () => api.listUsers());

  if (authLoading) {
    return <div className="min-h-screen flex items-center justify-center text-muted-foreground">Loading...</div>;
  }

  if (!user) {
    return (
      <div className="min-h-screen">
        <Header />
        <div className="text-center text-muted-foreground py-12">Please log in</div>
      </div>
    );
  }

  const isAdmin = user.role === "admin";

  return (
    <div className="min-h-screen">
      <Header />
      <div className="max-w-4xl mx-auto px-6 py-6 space-y-6">
        <h1 className="text-lg font-semibold">{isAdmin ? "User Management" : "Account"}</h1>
        <ChangePasswordCard />
        {isAdmin && (
          <>
            <AddUserForm onCreated={() => mutate()} />
            <Card>
              <CardContent className="p-0">
                <table className="w-full text-left">
                  <thead>
                    <tr className="text-xs text-muted-foreground">
                      <th className="px-3 py-2 font-medium">Username</th>
                      <th className="px-3 py-2 font-medium">Role</th>
                      <th className="px-3 py-2 font-medium">Bot Access</th>
                      <th className="px-3 py-2 font-medium">Password</th>
                      <th className="px-3 py-2 font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users?.map((u) => (
                      <UserRow key={u.username} user={u} onUpdated={() => mutate()} />
                    ))}
                    {!users?.length && (
                      <tr>
                        <td colSpan={5} className="px-3 py-4 text-center text-sm text-muted-foreground">
                          No users found
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
