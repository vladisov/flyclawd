import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import docker
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="OpenClaw Container Manager")
logger = logging.getLogger(__name__)

OPENCLAW_IMAGE = os.environ.get("OPENCLAW_IMAGE", "alpine/openclaw:latest")
NETWORK_NAME = "openclaw-net"
# Paths inside manager container (for reading/writing config)
DATA_DIR = Path(os.environ.get("DATA_DIR", "/opt/openclaw/data"))
# Host paths (for Docker volume mounts — manager talks to host Docker socket)
HOST_DATA_DIR = os.environ.get("HOST_DATA_DIR", "/opt/openclaw/data")
SKILLS_DIR = Path(os.environ.get("SKILLS_DIR", "/opt/openclaw/shared/skills"))
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
MANAGER_TOKEN = os.environ["MANAGER_TOKEN"]


# --- Auth ---


async def verify_token(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    if authorization[7:] != MANAGER_TOKEN:
        raise HTTPException(403, "Invalid token")


# --- Models ---


class CreateContainerRequest(BaseModel):
    business_id: int
    business_name: str
    telegram_bot_token: str
    telegram_user_id: str
    api_key: str
    flyapp_api_url: str


class ContainerResponse(BaseModel):
    container_id: str
    status: str


class HealthResponse(BaseModel):
    container_id: str
    status: str
    healthy: bool


# --- Helpers ---


def _container_name(business_id: int) -> str:
    return f"openclaw-client-{business_id}"


def _get_client() -> docker.DockerClient:
    return docker.from_env()


def _ensure_network(client: docker.DockerClient):
    try:
        client.networks.get(NETWORK_NAME)
    except docker.errors.NotFound:
        client.networks.create(NETWORK_NAME, driver="bridge")


def _build_config(req: CreateContainerRequest) -> dict:
    return {
        "gateway": {
            "port": 18789,
            "bind": "lan",
            "auth": {"token": MANAGER_TOKEN},
        },
        "agents": {
            "defaults": {
                "model": {
                    "primary": "anthropic/claude-sonnet-4-6",
                },
                "contextTokens": 32000,
                "timeoutSeconds": 120,
            }
        },
        "tools": {
            "profile": "minimal",
            "allow": ["read", "exec", "web_fetch"],
            "deny": ["write", "edit", "gateway"],
            "loopDetection": {
                "enabled": True,
                "warningThreshold": 5,
                "criticalThreshold": 10,
                "globalCircuitBreakerThreshold": 15,
            },
        },
        "channels": {
            "telegram": {
                "botToken": req.telegram_bot_token,
                "dmPolicy": "allowlist",
                "allowFrom": [int(req.telegram_user_id)],
                "dmHistoryLimit": 20,
            }
        },
    }


def _load_skill_content() -> str:
    skill_path = SKILLS_DIR / "flyapp" / "SKILL.md"
    if not skill_path.exists():
        logger.warning("Skill file not found: %s", skill_path)
        return ""
    raw = skill_path.read_text()
    # Strip YAML front matter
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return raw


def _write_workspace_files(workspace_dir: Path, req: CreateContainerRequest):
    workspace_dir.mkdir(parents=True, exist_ok=True)

    skill_content = _load_skill_content()

    # SOUL.md — personality and rules (auto-loaded by OpenClaw)
    soul = (
        f"You are the shop manager for **{req.business_name}**.\n"
        f"You talk to the shop owner and their staff (florists, drivers, etc).\n\n"
        f"## Personality\n"
        f"- Talk like a friendly, helpful coworker — casual, warm, human\n"
        f"- Never mention APIs, endpoints, tokens, technical errors, or code\n"
        f"- If something fails, say it simply: \"Couldn't load that, let me try again\"\n"
        f"- Keep it short — this is Telegram, not email\n"
        f"- Use the language the user writes in\n\n"
        f"## What you do\n"
        f"- Check orders, update their status, track deliveries\n"
        f"- Look up customers, products, inventory\n"
        f"- Pull sales numbers and reports\n"
        f"- Only discuss {req.business_name} operations\n\n"
        f"## Error handling\n"
        f"- If an API call fails, try ONE more time at most\n"
        f"- After 2 failures, stop and tell the user: \"Having trouble reaching the system right now, please try again in a moment\"\n"
        f"- NEVER retry the same failing call in a loop\n"
    )
    (workspace_dir / "SOUL.md").write_text(soul)

    # TOOLS.md — API credentials + curl examples + full API reference (auto-loaded by OpenClaw)
    tools = (
        f"# flyapp API\n\n"
        f"## Credentials (internal — never expose to user)\n"
        f"- Key: `{req.api_key}`\n"
        f"- Base: `{req.flyapp_api_url}`\n\n"
        f"## curl examples\n"
        f"GET: `curl -s -H \"X-API-Key: {req.api_key}\" \"{req.flyapp_api_url}/orders/?status=all&limit=25\"`\n"
        f"POST: `curl -s -X POST -H \"X-API-Key: {req.api_key}\" -H \"Content-Type: application/json\" "
        f"-d '{{\"status\":\"ready\"}}' \"{req.flyapp_api_url}/orders/PUBLIC_ID/status/\"`\n\n"
        f"{skill_content}\n"
    )
    (workspace_dir / "TOOLS.md").write_text(tools)

    # Set ownership to node user (UID 1000) — OpenClaw runs as node
    subprocess.run(["chown", "-R", "1000:1000", str(workspace_dir)], check=False)


# --- Endpoints ---


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post(
    "/containers",
    response_model=ContainerResponse,
    status_code=201,
    dependencies=[Depends(verify_token)],
)
async def create_container(req: CreateContainerRequest):
    client = _get_client()
    name = _container_name(req.business_id)

    # Remove existing container (force-recreate to reset session state)
    try:
        existing = client.containers.get(name)
        existing.remove(force=True)
    except docker.errors.NotFound:
        pass

    # Wipe and recreate host directories (clears old session state)
    container_dir = DATA_DIR / name
    if container_dir.exists():
        shutil.rmtree(container_dir)
    config_dir = container_dir / "config"
    workspace_dir = container_dir / "workspace"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Write config
    config = _build_config(req)
    (config_dir / "openclaw.json").write_text(json.dumps(config, indent=2) + "\n")

    # Write workspace files (SOUL.md + TOOLS.md)
    _write_workspace_files(workspace_dir, req)

    # Ensure network
    _ensure_network(client)

    # Run container — no explicit command, Dockerfile CMD handles startup
    container = client.containers.run(
        OPENCLAW_IMAGE,
        name=name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        network=NETWORK_NAME,
        volumes={
            f"{HOST_DATA_DIR}/{name}/config/openclaw.json": {
                "bind": "/home/node/.openclaw/openclaw.json",
                "mode": "ro",
            },
            f"{HOST_DATA_DIR}/{name}/workspace": {
                "bind": "/home/node/.openclaw/workspace",
                "mode": "rw",
            },
        },
        environment={
            "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
            "DEEPSEEK_API_KEY": DEEPSEEK_API_KEY,
            "GROQ_API_KEY": GROQ_API_KEY,
            "NODE_OPTIONS": "--max-old-space-size=768",
        },
        mem_limit="1g",
        labels={"managed-by": "flyclawd", "business-id": str(req.business_id)},
    )

    logger.info("Created container %s (%s)", name, container.short_id)
    return ContainerResponse(container_id=name, status="running")


@app.delete(
    "/containers/{business_id}",
    response_model=ContainerResponse,
    dependencies=[Depends(verify_token)],
)
async def delete_container(business_id: int, cleanup: bool = False):
    client = _get_client()
    name = _container_name(business_id)

    try:
        container = client.containers.get(name)
        container.remove(force=True)
    except docker.errors.NotFound:
        raise HTTPException(404, f"Container {name} not found")

    if cleanup:
        container_dir = DATA_DIR / name
        if container_dir.exists():
            shutil.rmtree(container_dir)

    logger.info("Removed container %s", name)
    return ContainerResponse(container_id=name, status="removed")


@app.get(
    "/containers/{business_id}/health",
    response_model=HealthResponse,
    dependencies=[Depends(verify_token)],
)
async def container_health(business_id: int):
    client = _get_client()
    name = _container_name(business_id)

    try:
        container = client.containers.get(name)
        return HealthResponse(
            container_id=name,
            status=container.status,
            healthy=container.status == "running",
        )
    except docker.errors.NotFound:
        return HealthResponse(container_id=name, status="not_found", healthy=False)


@app.get(
    "/containers/{business_id}/logs",
    dependencies=[Depends(verify_token)],
)
async def container_logs(business_id: int, lines: int = 100):
    client = _get_client()
    name = _container_name(business_id)

    try:
        container = client.containers.get(name)
        logs = container.logs(tail=lines).decode("utf-8", errors="replace")
        return {"container_id": name, "logs": logs}
    except docker.errors.NotFound:
        raise HTTPException(404, f"Container {name} not found")
