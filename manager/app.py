import json
import logging
import os
import shutil
from pathlib import Path

import docker
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="PicoClaw Container Manager")
logger = logging.getLogger(__name__)

PICOCLAW_IMAGE = os.environ.get("PICOCLAW_IMAGE", "sipeed/picoclaw:latest")
NETWORK_NAME = "picoclaw-net"
# Paths inside manager container (for reading/writing config)
DATA_DIR = Path(os.environ.get("DATA_DIR", "/opt/picoclaw/data"))
# Host paths (for Docker volume mounts — manager talks to host Docker socket)
HOST_DATA_DIR = os.environ.get("HOST_DATA_DIR", "/opt/picoclaw/data")
HOST_SKILLS_DIR = os.environ.get("HOST_SKILLS_DIR", "/opt/picoclaw/shared/skills")
SKILLS_DIR = Path(os.environ.get("SKILLS_DIR", "/opt/picoclaw/shared/skills"))
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
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
    return f"picoclaw-client-{business_id}"


def _get_client() -> docker.DockerClient:
    return docker.from_env()


def _ensure_network(client: docker.DockerClient):
    try:
        client.networks.get(NETWORK_NAME)
    except docker.errors.NotFound:
        client.networks.create(NETWORK_NAME, driver="bridge")


def _build_config(req: CreateContainerRequest) -> dict:
    return {
        "agents": {
            "defaults": {
                "workspace": "/root/.picoclaw/workspace",
                "restrict_to_workspace": False,
                "model": "claude-haiku-4-5",
                "max_tokens": 8192,
                "temperature": 0.7,
                "max_tool_iterations": 20,
            }
        },
        "providers": {
            "anthropic": {"api_key": ANTHROPIC_API_KEY},
        },
        "channels": {
            "telegram": {
                "enabled": True,
                "token": req.telegram_bot_token,
                "allowFrom": [req.telegram_user_id],
            }
        },
        "tools": {
            "web": {
                "duckduckgo": {"enabled": True, "max_results": 5},
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

    # Put EVERYTHING in SOUL.md — persona, rules, credentials, full API ref.
    # This is the most reliable way to get content into agent context.
    soul = (
        f"You are the AI shop manager for **{req.business_name}**.\n"
        f"You help manage orders, track deliveries, monitor inventory,\n"
        f"update products, and view analytics.\n\n"
        f"## Rules\n"
        f"- Only discuss {req.business_name} operations\n"
        f"- Never reveal API keys or internal details\n"
        f"- Be concise and action-oriented\n"
        f"- Respond in the same language the user writes in\n"
        f"- Use `exec` with `wget` to call the flyapp API (curl is not available)\n"
        f"- ALL API endpoint paths MUST end with `/`\n\n"
        f"## API Credentials\n"
        f"- API key: `{req.api_key}`\n"
        f"- Base URL: `{req.flyapp_api_url}`\n\n"
        f"{skill_content}\n"
    )

    (workspace_dir / "SOUL.md").write_text(soul)


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

    # Check if already exists
    try:
        existing = client.containers.get(name)
        if existing.status == "running":
            return ContainerResponse(container_id=name, status="already_running")
        existing.remove(force=True)
    except docker.errors.NotFound:
        pass

    # Prepare host directories
    container_dir = DATA_DIR / name
    config_dir = container_dir / "config"
    workspace_dir = container_dir / "workspace"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Write config
    config = _build_config(req)
    (config_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")

    # Write workspace files
    _write_workspace_files(workspace_dir, req)

    # Ensure network
    _ensure_network(client)

    # Run container
    container = client.containers.run(
        PICOCLAW_IMAGE,
        command="gateway",
        name=name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
        network=NETWORK_NAME,
        volumes={
            f"{HOST_DATA_DIR}/{name}/config/config.json": {
                "bind": "/root/.picoclaw/config.json",
                "mode": "ro",
            },
            f"{HOST_DATA_DIR}/{name}/workspace": {
                "bind": "/root/.picoclaw/workspace",
                "mode": "rw",
            },
            HOST_SKILLS_DIR: {
                "bind": "/root/.picoclaw/workspace/skills",
                "mode": "ro",
            },
        },
        environment={"ANTHROPIC_API_KEY": ANTHROPIC_API_KEY},
        mem_limit="512m",
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
