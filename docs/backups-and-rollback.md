# Backups & Rollback

Compressed snapshots of bot state. Create them manually, run them on a schedule, or restore to any previous point.

## What's in a Backup

Backups are `tar.gz` archives containing the bot's full state.

**Included:**

- `openclaw.json` — model provider config, gateway settings
- `workspace/` — SOUL.md, MEMORY.md, date-based memory files
- `agents/`, `cron/`, sessions
- Root-level `config.json` and `SOUL.md`

**Excluded:**

- `logs/` — conversation logs (can be large, regenerated at runtime)
- `.bak` files — editor backup artifacts
- `update-check.json` — temporary OpenClaw file

## Creating Backups

### Manual

Click **Backup** on the bot detail page, or call the API:

```
POST /api/bots/{name}/backup
```

Returns:

```json
{
  "timestamp": "20260302T143000",
  "created_at": "2026-03-02T14:30:00Z",
  "label": "manual",
  "size_bytes": 45678
}
```

### Scheduled

A background thread creates backups for all bots at a fixed interval (default: every hour). Bots are discovered via Docker container labels.

Scheduled backups are labeled `"scheduled"` and are subject to automatic pruning (see Retention below).

### Pre-Rollback (automatic)

Before every rollback, the current state is automatically backed up with label `"pre-rollback"`. This is a safety net — you can always undo a rollback by rolling back to the pre-rollback snapshot.

## Backup Labels

| Label | Created by | Auto-pruned |
|-------|-----------|:-----------:|
| `manual` | User action (UI or API) | No |
| `scheduled` | Background scheduler | Yes |
| `pre-rollback` | Rollback operation | No |

Only scheduled backups count toward the retention limit. Manual and pre-rollback backups persist until you delete the bot.

## Storage

### Default (in-bot)

Backups are stored alongside the bot data:

```
bots/{name}/.backups/{timestamp}.tar.gz
```

### External (BACKUP_DIR)

Set `BACKUP_DIR` to store backups in a separate location:

```env
BACKUP_DIR=/mnt/backups/clawfarm
```

Backups go to:

```
{BACKUP_DIR}/{name}/{timestamp}.tar.gz
```

Benefits of external storage:

- Backups survive bot deletion (deleting a bot removes `bots/{name}/`)
- Can be a separate volume or network mount
- Easier offsite backup

When looking up a backup for rollback, the system checks both locations for compatibility.

## Retention & Pruning

After each scheduled backup cycle, old scheduled backups beyond `BACKUP_KEEP` (default: 24) are pruned per bot. Oldest are removed first.

Manual and pre-rollback backups are **never** auto-pruned.

With the default settings (hourly backups, keep 24), you get ~24 hours of scheduled backup history per bot.

To adjust:

```env
BACKUP_INTERVAL_SECONDS=1800   # Every 30 minutes
BACKUP_KEEP=48                 # Keep 48 (= 24h at 30min intervals)
```

Set `BACKUP_INTERVAL_SECONDS=0` to disable scheduled backups entirely.

## Rollback

Rollback restores a bot to a previous backup. The full sequence:

1. You select a backup timestamp (from the bot detail page or API)
2. Current state is auto-backed up with label `"pre-rollback"`
3. Current workspace, agents, and cron directories are cleared
4. Backup archive is extracted over the bot directory
5. Gateway auth token is preserved — active Control UI sessions keep working
6. Bot container is restarted

### API

```
POST /api/bots/{name}/rollback
Content-Type: application/json

{"timestamp": "20260302T143000"}
```

Returns:

```json
{
  "name": "my-bot",
  "rolled_back_to": "20260302T143000",
  "status": "running"
}
```

### What's Preserved During Rollback

- **Gateway auth token** — extracted from `openclaw.json` before restore and re-injected after, so the Control UI stays connected
- **Backup history** — the `.backups/` directory and metadata are not overwritten
- **Bot metadata** — `.meta.json` is updated with the rollback timestamp

### Listing Backups

```
GET /api/bots/{name}/backups
```

Returns all backups for a bot in chronological order:

```json
[
  {"timestamp": "20260302T100000", "created_at": "2026-03-02T10:00:00Z", "label": "scheduled", "size_bytes": 45000},
  {"timestamp": "20260302T140000", "created_at": "2026-03-02T14:00:00Z", "label": "manual", "size_bytes": 46500}
]
```

## Environment Variable Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKUP_DIR` | (empty) | External backup directory. Empty = store in bot's `.backups/` |
| `BACKUP_INTERVAL_SECONDS` | `3600` | Scheduled backup interval in seconds. `0` = disabled |
| `BACKUP_KEEP` | `24` | Max scheduled backups to retain per bot |
