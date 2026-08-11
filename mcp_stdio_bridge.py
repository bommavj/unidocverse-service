"""
Stdio bridge for Claude Desktop → UniDocVerse MCP.
Claude Desktop speaks stdio; this script runs the same FastMCP
server (with all 67 tools) in stdio mode.

Usage in claude_desktop_config.json:
  "command": "/path/to/venv/bin/python",
  "args": ["/path/to/unidocverse-service/mcp_stdio_bridge.py"]
"""
import asyncio
import sys
import os

# Ensure the service package is importable
sys.path.insert(0, os.path.dirname(__file__))

# Backend must be running — tools call localhost:8000
from app.mcp_server.server import mcp

if __name__ == "__main__":
    asyncio.run(mcp.run_stdio_async())
