"""
MCP Settings Router — save/load which clients are enabled, HITL flag,
and one-click config + auto-restart for each MCP client.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
from app.mcp_server.server import _load_settings, save_mcp_settings

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

MCP_SSE_URL = "http://localhost:7979/mcp/sse"

# Per-client metadata: config path, detection hint, JSON to merge, restart command.
_CLIENT_CONFIG: dict = {
    "claude-desktop": {
        "path": Path("Library/Application Support/Claude/claude_desktop_config.json"),
        "detection": Path("Library/Application Support/Claude"),
        "merge": None,  # built dynamically (stdio bridge needs real paths)
        "app_name": "Claude",
        "kill_cmd": ["osascript", "-e", 'quit app "Claude"'],
        "open_cmd": ["open", "-a", "Claude"],
        "restart_delay": 2,
    },
    "cursor": {
        "path": Path(".cursor/mcp.json"),
        "detection": Path(".cursor"),
        "merge": {"mcpServers": {"unidocverse": {"url": MCP_SSE_URL, "transport": "sse"}}},
        "app_name": "Cursor",
        "kill_cmd": ["pkill", "-x", "Cursor"],
        "open_cmd": ["open", "-a", "Cursor"],
        "restart_delay": 2,
    },
    "zed": {
        "path": Path(".config/zed/settings.json"),
        "detection": None,
        "app_bundle": Path("/Applications/Zed.app"),
        "merge": {"context_servers": {"unidocverse": {"command": {"url": MCP_SSE_URL}}}},
        "app_name": "Zed",
        "kill_cmd": ["pkill", "-x", "Zed"],
        "open_cmd": ["open", "-a", "Zed"],
        "restart_delay": 2,
    },
    "continue": {
        "path": Path(".continue/config.json"),
        "detection": None,
        "merge": None,  # array-based
        # Continue is a VS Code extension — reload the VS Code window
        "app_name": "Code",
        "kill_cmd": None,  # don't kill VS Code; send reload command instead
        "open_cmd": ["open", "-a", "Visual Studio Code"],
        "restart_delay": 1,
    },
    "windsurf": {
        "path": Path(".codeium/windsurf/mcp_config.json"),
        "detection": Path(".codeium/windsurf"),
        "merge": {"mcpServers": {"unidocverse": {"serverUrl": MCP_SSE_URL, "transport": "sse"}}},
        "app_name": "Windsurf",
        "kill_cmd": ["pkill", "-x", "Windsurf"],
        "open_cmd": ["open", "-a", "Windsurf"],
        "restart_delay": 2,
    },
}


def _venv_python() -> str:
    service_dir = Path(__file__).parent.parent.parent
    for candidate in ["venv/bin/python3", ".venv/bin/python3"]:
        p = service_dir / candidate
        if p.exists() and p.resolve().exists():
            return str(p)
    return sys.executable


def _deep_merge(base: dict, overlay: dict) -> dict:
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _write_client_config(client: str) -> dict:
    """Write the MCP entry into the client config file. Returns status dict."""
    meta = _CLIENT_CONFIG.get(client)
    if not meta:
        raise HTTPException(status_code=400, detail=f"Unknown client: {client}")

    home = Path(os.environ.get("HOME", os.path.expanduser("~")))
    cfg_path: Path = home / meta["path"]

    detection: Path | None = meta.get("detection")
    app_bundle: Path | None = meta.get("app_bundle")
    if detection and not (home / detection).exists():
        if app_bundle is None or not app_bundle.exists():
            return {"status": "not_installed", "path": str(cfg_path)}

    existing: dict = {}
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text())
        except Exception:
            existing = {}

    # Claude Desktop — stdio bridge with real paths
    if client == "claude-desktop":
        service_dir = Path(__file__).parent.parent.parent
        bridge_path = str(service_dir / "mcp_stdio_bridge.py")
        python_path = _venv_python()
        overlay = {"mcpServers": {"unidocverse": {"command": python_path, "args": [bridge_path]}}}
        existing_entry = existing.get("mcpServers", {}).get("unidocverse", {})
        already = existing_entry.get("args") == [bridge_path]
        if not already:
            _deep_merge(existing, overlay)
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(existing, indent=2))
        return {"status": "already_configured" if already else "configured", "path": str(cfg_path)}

    # Continue — array-based
    if client == "continue":
        servers: list = existing.get("experimental", {}).get("modelContextProtocolServers", [])
        already = any(s.get("transport", {}).get("url") == MCP_SSE_URL for s in servers)
        if not already:
            servers.append({"transport": {"type": "sse", "url": MCP_SSE_URL}})
            existing.setdefault("experimental", {})["modelContextProtocolServers"] = servers
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(existing, indent=2))
        return {"status": "already_configured" if already else "configured", "path": str(cfg_path)}

    # All other clients — object deep merge
    overlay = meta["merge"]
    try:
        def _already(c: str, cfg: dict) -> bool:
            if c == "zed":
                return cfg.get("context_servers", {}).get("unidocverse", {}).get("command", {}).get("url") == MCP_SSE_URL
            if c == "windsurf":
                return cfg.get("mcpServers", {}).get("unidocverse", {}).get("serverUrl") == MCP_SSE_URL
            return cfg.get("mcpServers", {}).get("unidocverse", {}).get("url") == MCP_SSE_URL
        already = _already(client, existing)
    except Exception:
        already = False

    if not already:
        _deep_merge(existing, overlay)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(existing, indent=2))

    return {"status": "already_configured" if already else "configured", "path": str(cfg_path)}


def _restart_app(client: str) -> dict:
    """Kill then reopen the client app. Returns {"restarted": bool, "note": str}."""
    meta = _CLIENT_CONFIG.get(client, {})
    kill_cmd = meta.get("kill_cmd")
    open_cmd = meta.get("open_cmd")
    delay = meta.get("restart_delay", 2)

    if kill_cmd:
        try:
            subprocess.run(kill_cmd, capture_output=True, timeout=5)
        except Exception:
            pass  # app may not be running — that's fine
        time.sleep(delay)

    if open_cmd:
        try:
            subprocess.Popen(open_cmd)
            return {"restarted": True, "note": f"{meta.get('app_name', client)} restarted"}
        except Exception as e:
            return {"restarted": False, "note": str(e)}

    return {"restarted": False, "note": "no open command defined"}


class McpSettings(BaseModel):
    hitl_enabled: bool = True
    enabled_clients: List[str] = []


class ConnectClientBody(BaseModel):
    client: str


@router.get("/settings")
def get_settings():
    return _load_settings()


@router.post("/settings")
def update_settings(body: McpSettings):
    data = {"hitl_enabled": body.hitl_enabled, "enabled_clients": body.enabled_clients}
    save_mcp_settings(data)
    return {"ok": True, **data}


@router.post("/connect-client")
def connect_client(body: ConnectClientBody):
    """
    One-click MCP enable: write config + restart the client app automatically.
    Returns {"status": ..., "path": ..., "restarted": bool, "note": str}
    """
    cfg_result = _write_client_config(body.client)
    if cfg_result["status"] == "not_installed":
        return {**cfg_result, "restarted": False, "note": "App not installed"}
    restart_result = _restart_app(body.client)
    return {**cfg_result, **restart_result}


@router.get("/claude-desktop-snippet")
def claude_desktop_snippet():
    """Return the exact JSON snippet with real absolute paths for claude_desktop_config.json."""
    service_dir = Path(__file__).parent.parent.parent
    bridge_path = str(service_dir / "mcp_stdio_bridge.py")
    python_path = _venv_python()
    snippet = {"mcpServers": {"unidocverse": {"command": python_path, "args": [bridge_path]}}}
    return {
        "snippet": json.dumps(snippet, indent=2),
        "python_path": python_path,
        "bridge_path": bridge_path,
        "config_path": str(Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"),
    }


@router.get("/tools-info")
def mcp_tools_info():
    """Return the list of MCP tool names."""
    from app.services.agent_tools import TOOL_REGISTRY
    return {"tools": list(TOOL_REGISTRY.keys()), "count": len(TOOL_REGISTRY)}
