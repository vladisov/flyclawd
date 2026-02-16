# Flyapp AI Agent Provisioning — Technical Spec

## Overview

Automatically provision isolated AI shop-management agents for flyapp.so clients. Each business that signs up gets their own AI assistant accessible via Telegram DM, powered by OpenClaw multi-agent on a single gateway.

Two fully decoupled services communicating only via HTTP over private VPC:
- **EC2-A** (`ff_all/backend`) — Django + Django Ninja backend
- **EC2-B** (`flyclawd`) — OpenClaw gateway in Docker

## Architecture

```
EC2-A (flyapp.so)                         EC2-B (flyclawd)
┌──────────────────────┐   private VPC    ┌──────────────────────────┐
│  Django + Ninja      │ ──────────────→  │  Docker: OpenClaw        │
│                      │  HTTP :18789     │  bind: "lan"             │
│  POST /api/ai-agents/│  + auth token    │                          │
│    provision         │                  │  ├── agent: admin        │
│    deprovision       │                  │  ├── agent: client-1     │
│    status            │                  │  ├── agent: client-2     │
│                      │                  │  └── ...                 │
│  DB: ai_agents table │                  │                          │
└──────────────────────┘                  │  Volume: EBS (persists   │
                                          │  workspaces + config)    │
                                          └──────────────────────────┘
                                            ↕ outbound HTTPS
                                          Telegram API + Anthropic API
```

Single Telegram bot. Clients DM the same bot — routing by Telegram user ID via OpenClaw bindings. All agents run concurrently (OpenClaw handles parallel sessions natively).

---

## EC2-B: flyclawd (OpenClaw Deployment)

Repo: `flyclawd/`

### Docker Compose (`docker-compose.yml`)

```yaml
services:
  openclaw:
    image: node:22-slim
    command: >
      sh -c "npm install -g openclaw && openclaw gateway"
    restart: unless-stopped
    ports:
      - "18789:18789"
    volumes:
      - openclaw-data:/root/.openclaw
      - ./config/openclaw.json:/root/.openclaw/openclaw.json:ro
      - ./skills:/root/.openclaw/skills
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - GATEWAY_TOKEN=${GATEWAY_TOKEN}

volumes:
  openclaw-data:
    driver: local
```

### OpenClaw Config (`config/openclaw.json`)

```json
{
  "gateway": {
    "port": 18789,
    "mode": "local",
    "bind": "lan",
    "auth": {
      "mode": "token",
      "token": "${GATEWAY_TOKEN}"
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "botToken": "${TELEGRAM_BOT_TOKEN}",
      "dmPolicy": "allowlist",
      "allowFrom": [],
      "groupPolicy": "disabled",
      "streamMode": "partial"
    }
  },
  "agents": {
    "defaults": {
      "maxConcurrent": 8,
      "compaction": { "mode": "safeguard" }
    },
    "list": [
      {
        "id": "admin",
        "default": true,
        "name": "Admin",
        "workspace": "/root/.openclaw/workspace"
      }
    ]
  },
  "bindings": [],
  "commands": { "native": "auto", "nativeSkills": "auto" },
  "tools": {
    "media": {
      "audio": {
        "enabled": true,
        "models": [{ "provider": "openai", "model": "gpt-4o-mini-transcribe" }]
      }
    }
  },
  "skills": {
    "install": { "nodeManager": "npm" }
  },
  "hooks": {
    "enabled": true,
    "token": "${GATEWAY_TOKEN}",
    "mappings": [
      {
        "id": "provision",
        "match": { "path": "/hooks/provision" },
        "action": "agent",
        "agentId": "admin",
        "messageTemplate": "Provision a new client workspace. Create the directory and files as instructed:\n\n{{body}}\n\nWrite all files exactly as specified. Confirm when done."
      }
    ]
  }
}
```

### Workspace File Creation

Fully decoupled — no shared filesystem or SSH. Flyapp sends workspace file contents via HTTP to the OpenClaw provision webhook (`POST /hooks/provision`). The admin agent writes the files to disk.

Provision payload sent by flyapp:

```json
{
  "workspace": "/root/.openclaw/workspace-client-1",
  "business_name": "Bloom & Co",
  "api_key": "ff_abc123...",
  "files": {
    "SOUL.md": "...",
    "TOOLS.md": "...",
    "AGENTS.md": "..."
  }
}
```

### Security Group (EC2-B)

| Rule | Port | Source | Purpose |
|------|------|--------|---------|
| Inbound | 18789 | EC2-A SG only | flyapp.so API calls |
| Inbound | 22 | your IP | SSH management |
| Outbound | 443 | 0.0.0.0/0 | Telegram + Anthropic API |

No public exposure on 18789. Only flyapp.so can reach it.

### Instance

- **Type:** t3.medium (2 vCPU, 4GB RAM) — handles ~50 agents
- **OS:** Amazon Linux 2023 or Ubuntu 24.04
- **Storage:** 30GB gp3 EBS (workspaces are ~10KB each)
- **Scale up:** t3.large for 50-200 agents

### Environment Variables (`.env`)

```bash
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
GATEWAY_TOKEN=your-secret-token-here
```

---

## EC2-A: Flyapp.so Integration

Repo: `ff_all/backend/`
Stack: Django 5.0 + Django Ninja + PostgreSQL + Celery

### Environment Variables

Added to `ff_api/settings.py`:

```python
# OPENCLAW INTEGRATION
OPENCLAW_URL = os.getenv("OPENCLAW_URL")   # http://<ec2-b-private-ip>:18789
OPENCLAW_TOKEN = os.getenv("OPENCLAW_TOKEN")
```

### DB Model: `AIAgent`

File: `ff_api/model/ai_agent.py`

```python
class AIAgent(models.Model):
    business = models.OneToOneField(Business, on_delete=models.CASCADE, related_name="ai_agent")
    agent_id = models.CharField(max_length=100, unique=True)       # "client-{business_id}"
    telegram_user_id = models.CharField(max_length=50)
    workspace_path = models.TextField()
    status = models.CharField(max_length=20, default="active")     # active | inactive
    provisioned_at = models.DateTimeField(auto_now_add=True)
    deprovisioned_at = models.DateTimeField(null=True, blank=True)
```

Migration: `0071_aiagent.py`

### API Endpoints

File: `ff_api/api/ai_agent_api.py`
Route: `/api/ai-agents/`

#### `POST /api/ai-agents/provision`

Auth: JWT, owner only. Called when a business owner activates their AI assistant.

**Request:**
```json
{
  "telegram_user_id": "987654321"
}
```

**Logic:**
1. Auto-create an API key for the agent (`APIKey.create_key`)
2. POST to OpenClaw `/api/config` — adds agent config + binding + allowFrom entry
3. POST to OpenClaw `/hooks/provision` — triggers admin agent to write workspace files (SOUL.md, TOOLS.md, AGENTS.md)
4. Save `AIAgent` record to DB

**Response (201):**
```json
{
  "id": 1,
  "agent_id": "client-1",
  "telegram_user_id": "987654321",
  "status": "active",
  "provisioned_at": "2026-02-16T22:00:00Z",
  "deprovisioned_at": null
}
```

#### `POST /api/ai-agents/deprovision`

Auth: JWT, owner only.

**Logic:**
1. Look up active `AIAgent` for the business
2. GET OpenClaw `/api/config` — read full config
3. Filter out the agent from `agents.list[]`, its binding from `bindings[]`, and telegram_user_id from `allowFrom[]`
4. PUT OpenClaw `/api/config` — write full config back
5. Update DB: `status=inactive`, set `deprovisioned_at`

#### `GET /api/ai-agents/status`

Auth: JWT, owner or manager. Returns the active agent for the business (or null).

### Service Layer

File: `ff_api/services/openclaw_service.py`

Functions:
- `provision_agent(business_id, business_name, api_key, telegram_user_id)` — config patch + webhook
- `deprovision_agent(agent_id, telegram_user_id)` — config read/filter/write
- `get_health()` — GET `/health`

Uses `httpx.AsyncClient` with Bearer token auth.

### DTOs

File: `ff_api/api/dto/ai_agent_dto.py`

- `AIAgentProvisionIn` — `telegram_user_id: str`
- `AIAgentResponse` — full agent details with `from_attributes = True`

---

## OpenClaw Agent Template

### Per-Client Config (added to `agents.list[]`)

```json
{
  "id": "client-{business_id}",
  "name": "{business_name} Assistant",
  "workspace": "/root/.openclaw/workspace-client-{business_id}",
  "skills": ["flyapp"],
  "model": { "primary": "anthropic/claude-sonnet-4-5" },
  "tools": {
    "profile": "minimal",
    "allow": ["read", "exec", "web_fetch"],
    "deny": [
      "write", "edit", "browser", "canvas", "nodes",
      "cron", "gateway", "message", "sessions_spawn",
      "sessions_list", "sessions_history", "sessions_send",
      "memory_search", "memory_get", "tts"
    ]
  }
}
```

### Per-Client Binding (added to `bindings[]`)

```json
{
  "agentId": "client-{business_id}",
  "match": {
    "channel": "telegram",
    "peer": { "kind": "direct", "id": "{telegram_user_id}" }
  }
}
```

### What Each Client Agent Can Do
- Read workspace files (their TOOLS.md with API key)
- Execute curl/HTTP calls (flyapp skill uses exec for API)
- Fetch web content
- Manage orders, deliveries, stock, products, analytics via API
- No file writing — can't modify their own config
- No browser, cron, gateway access
- No cross-agent messaging or session access
- No access to admin agent or other clients
- No TTS/voice (cost control)

---

## Telegram Bot Setup

One bot for all clients.

- `dmPolicy: "allowlist"` — only provisioned users can chat
- `allowFrom[]` updated on each provision/deprovision
- `groupPolicy: "disabled"` — no group chat access
- Routing via bindings — each user ID maps to their agent

### Onboarding Flow (client-facing)
1. Business signs up on flyapp.so
2. Business owner enters their Telegram user ID (or links via deep link)
3. Owner clicks "Activate AI Assistant" → calls `POST /api/ai-agents/provision`
4. Owner messages the bot → routed to their agent → ready to manage their shop

---

## Config API Reference

Flyapp.so communicates with OpenClaw via these HTTP endpoints:

| Action | Method | Endpoint | Notes |
|--------|--------|----------|-------|
| Read config | GET | `/api/config` | Returns full config JSON |
| Patch config (add) | POST | `/api/config` | Merges arrays — appends to `agents.list[]` and `bindings[]` |
| Apply full config (replace) | PUT | `/api/config` | Full overwrite — needed for removing agents |
| Provision workspace | POST | `/hooks/provision` | Webhook — admin agent writes workspace files |
| Health check | GET | `/health` | 200 if gateway is running |

All requests require `Authorization: Bearer {GATEWAY_TOKEN}`.

Gateway auto-restarts after config writes. Allow 2-3 seconds before the new agent is reachable.

**Important:** `config.patch` (POST) **merges/appends** arrays. To **remove** an agent, you must read the full config, filter out the agent + binding, and write back with PUT (config.apply).

---

## Scaling

| Agents | EC2 Type | RAM | Notes |
|--------|----------|-----|-------|
| < 50 | t3.medium | 4GB | Starting point |
| 50-200 | t3.large | 8GB | One gateway still fine |
| 200+ | t3.xlarge or shard | 16GB | Consider multiple gateways |

**Primary cost driver:** Anthropic API usage per agent (~$3/MTok input, $15/MTok output for Sonnet), not compute.

---

## File Map

### flyclawd repo (EC2-B)

| Path | Purpose |
|------|---------|
| `docker-compose.yml` | OpenClaw container definition |
| `config/openclaw.json` | Gateway config (mounted read-only) |
| `.env` | Secrets (not committed) |
| `.env.example` | Template for secrets |
| `skills/flyapp/` | Shared skill for all agents |

### ff_all/backend (EC2-A)

| Path | Purpose |
|------|---------|
| `ff_api/model/ai_agent.py` | AIAgent Django model |
| `ff_api/migrations/0071_aiagent.py` | Database migration |
| `ff_api/api/ai_agent_api.py` | Provision/deprovision/status endpoints |
| `ff_api/api/dto/ai_agent_dto.py` | Request/response schemas |
| `ff_api/services/openclaw_service.py` | OpenClaw HTTP client |
| `ff_api/api/routes.py` | Router registration (`/ai-agents`) |
| `ff_api/settings.py` | `OPENCLAW_URL` + `OPENCLAW_TOKEN` env vars |

### OpenClaw volumes (EC2-B runtime)

| Path | Purpose |
|------|---------|
| `/root/.openclaw/openclaw.json` | Live gateway config |
| `/root/.openclaw/workspace/` | Admin workspace |
| `/root/.openclaw/workspace-client-{id}/` | Client workspaces |
