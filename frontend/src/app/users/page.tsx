"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { toast } from "sonner";
import { Header } from "@/components/header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
        className="h-9 rounded-md border border-input bg-transparent px-3 text-sm text-left w-full truncate"
      >
        {label}
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full max-h-48 overflow-auto rounded-md border border-border bg-card shadow-lg p-1">
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPw !== confirmPw) {
      toast.error("Passwords do not match");
      return;
    }
    try {
      await api.changePassword(currentPw, newPw);
      setCurrentPw("");
      setNewPw("");
      setConfirmPw("");
      toast.success("Password changed. You will need to log in again.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to change password");
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Change Password</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="grid grid-cols-1 sm:grid-cols-4 gap-3 items-end">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Current Password</label>
            <Input type="password" value={currentPw} onChange={(e) => setCurrentPw(e.target.value)} required />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">New Password</label>
            <Input type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} required />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Confirm</label>
            <Input type="password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} required />
          </div>
          <Button type="submit" size="sm">Change</Button>
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.createUser({ username, password, role, bots });
      setUsername("");
      setPassword("");
      setRole("user");
      setBots([]);
      onCreated();
      toast.success(`User "${username}" created`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create user");
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Add User</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 items-end">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Username</label>
            <Input value={username} onChange={(e) => setUsername(e.target.value)} required />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Password</label>
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Role</label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
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
        </form>
      </CardContent>
    </Card>
  );
}

function UserCard({ user, onUpdated }: { user: User; onUpdated: () => void }) {
  const { user: currentUser } = useAuth();
  const [editing, setEditing] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState(user.role);
  const [newBots, setNewBots] = useState<string[]>([...user.bots]);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const isSelf = currentUser?.username === user.username;

  const handleSave = async () => {
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
      toast.success(`User "${user.username}" updated`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Update failed");
    }
  };

  const handleDelete = async () => {
    setDeleteOpen(false);
    try {
      await api.deleteUser(user.username);
      onUpdated();
      toast.success(`User "${user.username}" deleted`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed");
    }
  };

  return (
    <>
      <Card className="bg-card border-border">
        <CardContent className="p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">{user.username}</span>
              {isSelf && <span className="text-xs text-muted-foreground">(you)</span>}
              <Badge variant={user.role === "admin" ? "default" : "secondary"} className="text-[10px]">
                {user.role}
              </Badge>
            </div>
            <div className="flex gap-1">
              {editing ? (
                <>
                  <Button size="xs" onClick={handleSave}>Save</Button>
                  <Button size="xs" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
                </>
              ) : (
                <>
                  <Button size="xs" variant="ghost" onClick={() => setEditing(true)}>Edit</Button>
                  {!isSelf && (
                    <Button
                      size="xs"
                      variant="ghost"
                      className="text-red-400 hover:text-red-300"
                      onClick={() => setDeleteOpen(true)}
                    >
                      Delete
                    </Button>
                  )}
                </>
              )}
            </div>
          </div>

          {editing ? (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Role</label>
                <select
                  value={newRole}
                  onChange={(e) => setNewRole(e.target.value)}
                  className="h-8 w-full rounded border border-input bg-transparent px-2 text-xs"
                >
                  <option value="user">user</option>
                  <option value="admin">admin</option>
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">Bot Access</label>
                <BotSelector selected={newBots} onChange={setNewBots} />
              </div>
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">New Password</label>
                <Input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder="unchanged"
                  className="h-8 text-xs"
                />
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
              <div>
                <span className="text-[10px] uppercase tracking-wider block">Access</span>
                <span className="text-foreground">{user.bots.join(", ") || "none"}</span>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete &ldquo;{user.username}&rdquo;?</DialogTitle>
            <DialogDescription>This will permanently remove this user account.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setDeleteOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={handleDelete}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
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
      <div className="max-w-4xl mx-auto px-4 sm:px-6 py-6 space-y-6">
        <h1 className="text-lg font-semibold">{isAdmin ? "User Management" : "Account"}</h1>
        <ChangePasswordCard />
        {isAdmin && (
          <>
            <AddUserForm onCreated={() => mutate()} />
            <div className="space-y-2">
              {users?.map((u) => (
                <UserCard key={u.username} user={u} onUpdated={() => mutate()} />
              ))}
              {!users?.length && (
                <p className="text-center text-sm text-muted-foreground py-4">No users found</p>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
