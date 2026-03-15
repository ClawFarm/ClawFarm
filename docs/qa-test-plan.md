# QA Test Plan — ClawFarm

Comprehensive user-simulation test plan for validating ClawFarm before releases.
Designed to be executed by an AI agent using Playwright (via MCP) against a running
Docker Compose deployment. Covers all user roles, every UI interaction, RBAC
enforcement, and edge cases.

## Prerequisites

- Full stack running via `docker compose -f docker-compose.dev.yml up -d`
- Playwright MCP server available
- Three test users configured:
  - **Admin user** — role=admin, bots=["*"]
  - **Limited user** — role=user, bots=[subset of bot names]
  - **Empty user** — role=user, bots=[]
- At least 4 bots existing (2+ running, 2+ exited)

## Environment

- **URL:** `https://localhost:8443` (default, or `CADDY_PORT` if changed)
- Credentials should be read from `.env` (`ADMIN_USER` / `ADMIN_PASSWORD`)
- Limited and empty user passwords can be reset via the admin API before testing

---

## Phase 1: Admin Full Walkthrough

### 1.1 Login
- [ ] Login with wrong password → error "Invalid username or password", stays on login page
- [ ] Login with correct credentials → redirects to dashboard
- [ ] Verify header shows: username, "admin" role badge, "Users" link, active bot count

### 1.2 Dashboard Overview
- [ ] Fleet stats row shows correct counts (total bots, running, tokens)
- [ ] Fleet token chart renders with bars (not the empty "data collection begins" state)
- [ ] Chart tooltip appears on hover with model breakdown
- [ ] Bot cards render in grid
- [ ] Running bots show green dot + "running" badge + Open UI / Terminal / Logs buttons enabled
- [ ] Unhealthy bots show red dot + "unhealthy" badge + Open UI / Terminal enabled (they're still running)
- [ ] Exited bots show red dot + "exited" badge + Open UI and Terminal disabled, Logs enabled
- [ ] Each card shows sparkline + token count
- [ ] Card dropdown menu works (⋯ button) with Start/Stop/Restart/Clone/Delete options

### 1.3 Bot Detail — Running Bot
- [ ] Click bot name → navigates to /bots/{name}
- [ ] Status badge shows "running"
- [ ] Overview section: path, container name, created date, storage
- [ ] Token stats: total, input, output, model
- [ ] Sparkline chart renders
- [ ] Metrics card: CPU%, memory%, network, uptime, restart count
- [ ] Config section expandable, shows redacted JSON (apiKey: "***")
- [ ] SOUL.md section expandable, shows personality text
- [ ] Action buttons: Stop, Restart, Logs, Terminal, Clone, Open UI, Delete
- [ ] Back arrow → returns to dashboard

### 1.4 Bot Detail — Exited Bot
- [ ] Navigate to detail page of an exited bot
- [ ] Status shows "exited"
- [ ] Start button visible (not Stop/Restart)
- [ ] Open UI button disabled
- [ ] Terminal button disabled
- [ ] Metrics show "No metrics available" or zeros
- [ ] Back arrow works

### 1.5 Bot Actions — Start/Stop/Restart
- [ ] On exited bot detail, click Start → status changes, toast shows
- [ ] On running bot detail, click Stop → status changes to "exited"
- [ ] On running bot detail, click Restart → status cycles back to running
- [ ] Dashboard card updates status in real-time (5s poll)

### 1.6 Open UI
- [ ] On running bot, click "Open UI" → opens /claw/{name}/ in new tab
- [ ] Control UI loads (may take a moment)
- [ ] Verify X-Forwarded-User header is passed (auth works in OpenClaw)

### 1.7 Terminal
- [ ] On running bot, click Terminal → dialog opens
- [ ] Status shows "Connecting..." then connected (green dot)
- [ ] Type `whoami` → shows "node"
- [ ] Type `pwd` → shows "/home/node"
- [ ] Type `ls .openclaw/` → shows files
- [ ] Resize browser window → terminal reflows
- [ ] Close dialog → terminal disconnects cleanly
- [ ] Reopen → new session starts

### 1.8 Logs
- [ ] Click Logs → dialog opens with container output
- [ ] Logs show in monospace
- [ ] Refresh button fetches fresh logs
- [ ] Close dialog

### 1.9 Backup & Rollback
- [ ] On bot detail, click "Create Backup" → toast "Backup created"
- [ ] New backup appears at top of list with "manual" label
- [ ] Backup shows size in KB
- [ ] Click "Show all N backups" → list expands
- [ ] Click Rollback on a backup → confirmation dialog appears
- [ ] Cancel rollback → nothing happens
- [ ] Confirm rollback → toast shows success, detail refreshes
- [ ] Verify a "pre-rollback" backup was auto-created

### 1.10 Clone (Duplicate)
- [ ] On bot card dropdown, click Clone → dialog opens
- [ ] Enter new name, leave "Track as fork" unchecked
- [ ] Click Clone → new bot appears on dashboard
- [ ] New bot has same soul/config but no forked_from in meta
- [ ] New bot starts automatically

### 1.11 Clone (Fork)
- [ ] On bot detail, click Clone → dialog opens
- [ ] Enter new name, check "Track as fork"
- [ ] Click Clone → new bot appears
- [ ] New bot meta shows forked_from = source bot name

### 1.12 Create Bot
- [ ] Click "+ Create new agent" → form expands
- [ ] Default template selected (blue border)
- [ ] Click different template → selection updates, SOUL.md prefills
- [ ] Enter name with special chars (e.g., "Test Bot!@#") → sanitization toast
- [ ] Enter valid name → create succeeds
- [ ] Try empty name → validation error shows
- [ ] Toggle network isolation off → switch grays out
- [ ] Custom SOUL.md text preserved when switching templates after editing
- [ ] Missing env var warning shows for templates missing API keys
- [ ] Cancel → form closes, fields clear

### 1.13 Delete Bot
- [ ] On bot detail page, click Delete → first confirmation state
- [ ] Click Cancel → returns to normal
- [ ] Click Delete again → confirm appears (red button)
- [ ] Confirm → bot deleted, redirect to dashboard, toast shown
- [ ] Bot no longer in list

### 1.14 User Management (Admin)
- [ ] Click "Users" in header → /users page
- [ ] All users listed with roles and bot access
- [ ] Current user shows "(you)" label, no Delete button

### 1.15 Add User
- [ ] Fill in username, password, role=user
- [ ] Select specific bots from dropdown
- [ ] Click Add → user created, toast shown, list refreshes
- [ ] Duplicate username → error toast

### 1.16 Edit User
- [ ] Click Edit on a user → form appears with current values
- [ ] Change role → save → role updates
- [ ] Change bot access → save → access updates
- [ ] Set new password → save → works (test by logging in as that user)
- [ ] Save with no changes → just closes edit mode

### 1.17 Delete User
- [ ] Click Delete on non-self user → confirmation dialog
- [ ] Cancel → nothing happens
- [ ] Confirm → user deleted, list refreshes

### 1.18 Change Own Password
- [ ] Enter current password, new password, confirm
- [ ] Mismatched confirm → error toast
- [ ] Wrong current password → error toast
- [ ] Correct change → success toast, prompted to re-login

### 1.19 Logout
- [ ] Click Sign Out → redirects to login
- [ ] Accessing dashboard → redirects to login (session cleared)

---

## Phase 2: Limited User

### 2.1 Login & Dashboard
- [ ] Login as limited user → dashboard shows
- [ ] Only sees bots in their access list
- [ ] Does NOT see bots outside their list
- [ ] Fleet stats reflect only accessible bots
- [ ] Header shows username, "user" role, "Account" link (not "Users")

### 2.2 Bot Actions as Limited User
- [ ] Can view detail of allowed bot
- [ ] Can start/stop/restart allowed bot
- [ ] Can view logs of allowed bot
- [ ] Can create backup of allowed bot
- [ ] Can clone allowed bot → new bot auto-granted to this user

### 2.3 RBAC Enforcement
- [ ] Navigate directly to /bots/{restricted-bot} → should show error or restricted view
- [ ] API call to /api/bots/{restricted-bot}/detail → 403
- [ ] API call to /api/bots/{restricted-bot}/stop → 403

### 2.4 User Management as Non-Admin
- [ ] Click "Account" → sees only Change Password card
- [ ] No user list visible
- [ ] No "Add User" form
- [ ] Can change own password

### 2.5 Create Bot as Limited User
- [ ] Create a new bot → bot created successfully
- [ ] New bot auto-added to this user's bot access list
- [ ] User can now see and manage the new bot

---

## Phase 3: Empty User

### 3.1 Login & Empty Dashboard
- [ ] Login as empty user → dashboard shows
- [ ] Zero bots visible
- [ ] Empty state message: "No agents yet..."
- [ ] Fleet stats show zeros

### 3.2 Create Bot
- [ ] Create a new bot → should succeed
- [ ] Bot auto-granted to this user
- [ ] User can now see and manage it

---

## Phase 4: Edge Cases & Error Handling

### 4.1 Invalid Inputs
- [ ] Create bot with name that's all special chars → error
- [ ] Create bot with 100+ char name → truncated to 48
- [ ] Clone with name that already exists → 409 error
- [ ] Rollback to non-existent timestamp → error

### 4.2 Concurrent State
- [ ] Start a bot, immediately check dashboard → status transitions visible
- [ ] Stop a running bot from detail, go back to dashboard → card updated

### 4.3 Session Expiry
- [ ] Let session sit idle (or manually clear cookie)
- [ ] Next API call → redirect to login

### 4.4 Network Resilience
- [ ] Fast-click multiple actions → no double-submit (buttons disable)
- [ ] Refresh page during action → page recovers

### 4.5 Bot That's Not Found
- [ ] Navigate to /bots/nonexistent-bot → "Bot not found" message + back link

---

## Phase 5: Cross-Feature Integration

### 5.1 Full Lifecycle
- [ ] Create bot → Start → Open UI → Terminal → Create Backup → Stop → Rollback → Start → Delete

### 5.2 Fork Chain
- [ ] Create bot A → Fork to B → Fork B to C
- [ ] Verify C's forked_from = B (not A)

### 5.3 Clone Preserves State
- [ ] Create bot, interact via UI to generate memories
- [ ] Clone bot → verify workspace (SOUL.md, MEMORY.md) copied
- [ ] Clone does NOT copy sessions (fresh conversation)

---

## Execution Notes

- Use Playwright browser (MCP) for all testing
- Take screenshots at key checkpoints for visual regression
- Log any bugs found with exact reproduction steps
- Clean up test bots created during testing after each phase
- If a test user doesn't exist, create one via the admin API before starting that phase
