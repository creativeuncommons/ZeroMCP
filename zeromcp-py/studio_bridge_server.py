#!/usr/bin/env python3
"""
Studio Bridge ZeroMCP Server
============================
Exposes local CLI bridges (Claude Opus 5.5, OpenAI Codex Astra) and
sports predictive database queries as zero-dependency Model Context Protocol (MCP) tools.

Enables external agents (Cursor, Claude Desktop, Antigravity) to query local subscription-backed
frontier models and sports databases via standard stdio JSON-RPC.
"""

import os
import sys
import json
import sqlite3
import platform
from typing import Dict, Any, Optional

# Workspace path resolution
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(CURRENT_DIR)))

# Import CLI bridges
NFL_STUDIO_DIR = os.path.join(WORKSPACE_DIR, "0_Projects", "NFL_Predictive_Analytics")
NBA_STUDIO_DIR = os.path.join(WORKSPACE_DIR, "0_Projects", "NBA_Predictive_Analytics")

if NFL_STUDIO_DIR not in sys.path:
    sys.path.insert(0, NFL_STUDIO_DIR)

try:
    from claude_cli_bridge import query_claude, resolve_claude_binary
except ImportError:
    query_claude = None
    resolve_claude_binary = None

try:
    from codex_cli_bridge import query_codex, resolve_codex_binary
except ImportError:
    query_codex = None
    resolve_codex_binary = None

from zeromcp import run_stdio, TOOLS


def _execute_claude_opus(args: Dict[str, Any]) -> str:
    """Invokes local Claude Opus 5.5 CLI session."""
    if not query_claude:
        raise RuntimeError("claude_cli_bridge module not found in studio path.")
    prompt = args.get("prompt", "")
    effort = args.get("effort", "high")
    model = args.get("model", "claude-opus-5-5")
    return query_claude(prompt, model=model, effort=effort, timeout=240)


def _execute_codex_astra(args: Dict[str, Any]) -> str:
    """Invokes local OpenAI Codex CLI session."""
    if not query_codex:
        raise RuntimeError("codex_cli_bridge module not found in studio path.")
    prompt = args.get("prompt", "")
    effort = args.get("reasoning_effort", "high")
    return query_codex(prompt, reasoning_effort=effort, timeout=240)


def _execute_sports_db(args: Dict[str, Any]) -> str:
    """Executes read-only SQL query against local sports analytics database."""
    sport = args.get("sport", "nfl").lower()
    sql = args.get("sql", "").strip()

    # Guardrail: Read-only check
    sql_upper = sql.upper()
    if not sql_upper.startswith("SELECT") and not sql_upper.startswith("PRAGMA"):
        raise ValueError("Only SELECT or PRAGMA queries are permitted on the sports database.")

    db_rel = "nfl_predictive.db" if sport == "nfl" else "nba_predictive.db"
    studio_dir = NFL_STUDIO_DIR if sport == "nfl" else NBA_STUDIO_DIR
    db_path = os.path.join(studio_dir, "data", db_rel)

    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    # Abort runaway/expensive queries after ~250k VM steps
    _step_counter = [0]
    def _progress_guard() -> int:
        _step_counter[0] += 1
        return 1 if _step_counter[0] > 250 else 0
    conn.set_progress_handler(_progress_guard, 1000)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchmany(100)  # Max 100 rows per query
        results = [dict(r) for r in rows]
        return json.dumps(results, indent=2, default=str)
    finally:
        conn.close()


# Extended Studio Tools Registry
STUDIO_TOOLS = {
    "query_claude_opus": {
        "description": "Queries local Claude Opus 5.5 session via CLI bridge (zero API cost)",
        "required_scopes": ["studio:llm:query"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Prompt text to send to Claude"},
                "effort": {"type": "string", "enum": ["low", "medium", "high"], "default": "high"},
                "model": {"type": "string", "default": "claude-opus-5-5"}
            },
            "required": ["prompt"]
        },
        "handler": _execute_claude_opus
    },
    "query_codex_astra": {
        "description": "Queries local OpenAI Codex session via CLI bridge (zero API cost)",
        "required_scopes": ["studio:llm:query"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Prompt text to send to Codex"},
                "reasoning_effort": {"type": "string", "enum": ["low", "medium", "high"], "default": "high"}
            },
            "required": ["prompt"]
        },
        "handler": _execute_codex_astra
    },
    "query_sports_database": {
        "description": "Executes read-only SQL queries against local NFL or NBA predictive SQLite databases",
        "required_scopes": ["studio:db:read"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "sport": {"type": "string", "enum": ["nfl", "nba"], "description": "Target sport studio"},
                "sql": {"type": "string", "description": "SELECT query to execute"}
            },
            "required": ["sport", "sql"]
        },
        "handler": _execute_sports_db
    },
    "studio_bridge_status": {
        "description": "Reports availability and binary paths for local Claude and Codex CLI bridges",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": lambda _: json.dumps({
            "claude_available": query_claude is not None,
            "claude_binary": resolve_claude_binary() if resolve_claude_binary else None,
            "codex_available": query_codex is not None,
            "codex_binary": resolve_codex_binary() if resolve_codex_binary else None,
            "platform": platform.platform()
        })
    }
}


def main():
    TOOLS.update(STUDIO_TOOLS)
    run_stdio()


if __name__ == "__main__":
    main()
