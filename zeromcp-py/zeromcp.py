#!/usr/bin/env python3
"""ZeroMCP: Ultra-lean, zero-dependency Model Context Protocol server (stdio, Python 3.10+ / 3.8+)."""
import sys, os, json, ast, math, operator as op, platform
from typing import Any, Dict, List, Optional, Tuple

PARSE_ERR, INVALID_REQ, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERR = -32700, -32600, -32601, -32602, -32603
MAX_FRAME_SIZE, MAX_MATH_LEN, MAX_BATCH = 10 * 1024 * 1024, 1024, 100

_WIRE_STDOUT = None
STATE = {"phase": "UNINITIALIZED"}  # UNINITIALIZED -> NEGOTIATED -> INITIALIZED
SAFE_OPS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
    ast.Mod: op.mod, ast.Pow: op.pow, ast.USub: op.neg, ast.UAdd: op.pos
}

def safe_eval_math(expr: str) -> str:
    """Evaluates arithmetic safely; IEEE-754 double precision; rejects complex and non-finite outputs."""
    if not isinstance(expr, str) or len(expr) > MAX_MATH_LEN: raise ValueError("Invalid expression")
    def _eval(node):
        if isinstance(node, ast.Expression): return _eval(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError(f"Disallowed constant: {type(node.value).__name__}")
            val = float(node.value)
            if not math.isfinite(val): raise ValueError("Non-finite constant disallowed")
            return val
        if isinstance(node, ast.BinOp) and type(node.op) in SAFE_OPS:
            try: res = SAFE_OPS[type(node.op)](_eval(node.left), _eval(node.right))
            except (ZeroDivisionError, OverflowError) as e: raise ValueError("Division by zero" if isinstance(node.op, ast.Div) and isinstance(e, ZeroDivisionError) else ("Modulo by zero" if isinstance(node.op, ast.Mod) and isinstance(e, ZeroDivisionError) else "Complex or non-finite numbers unsupported"))
            if isinstance(res, complex) or not math.isfinite(res): raise ValueError("Complex or non-finite numbers unsupported")
            return float(res)
        if isinstance(node, ast.UnaryOp) and type(node.op) in SAFE_OPS:
            res = SAFE_OPS[type(node.op)](_eval(node.operand))
            if isinstance(res, complex) or not math.isfinite(res): raise ValueError("Complex or non-finite numbers unsupported")
            return float(res)
        raise ValueError(f"Disallowed syntax: {type(node).__name__}")
    v = _eval(ast.parse(expr.strip(), mode="eval"))
    if not math.isfinite(v): raise ValueError("Complex or non-finite numbers unsupported")
    return str(int(v)) if v.is_integer() and abs(v) < 2**53 else str(v)

TOOLS = {
    "echo": {
        "description": "Echoes back the input message",
        "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
        "handler": lambda a: str(a["message"]),
        "required_scopes": []
    },
    "eval_math": {
        "description": "Safely evaluates IEEE-754 double precision arithmetic (+, -, *, /, %, **)",
        "inputSchema": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
        "handler": lambda a: safe_eval_math(a["expression"]),
        "required_scopes": []
    },
    "system_info": {
        "description": "Returns host platform, architecture, and runtime telemetry",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": lambda a: json.dumps({"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()}),
        "required_scopes": []
    }
}

def register_tool(name: str, description: str, input_schema: dict, handler, required_scopes: Optional[List[str]] = None):
    """Programmatically registers or overrides a tool with optional fine-grained access control (FGAC) scopes."""
    TOOLS[name] = {
        "description": description,
        "inputSchema": input_schema,
        "handler": handler,
        "required_scopes": [str(s) for s in (required_scopes or [])]
    }

# Optional Content-Addressed Storage (CAS) Engine
try:
    from zeromcp_cas import ZeroMCPCAS
    CAS_ENGINE = ZeroMCPCAS()
except Exception:
    CAS_ENGINE = None

if CAS_ENGINE and CAS_ENGINE.enabled:
    TOOLS["cas_fetch"] = {
        "description": "Retrieves targeted byte or line slice from an offloaded CAS payload",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hash": {"type": "string", "description": "SHA-256 hash or cas://sha256/<hash> reference"},
                "offset": {"type": "integer", "description": "Starting offset (byte or line index, default 0)"},
                "limit": {"type": "integer", "description": "Maximum bytes or lines to retrieve (default 4096)"},
                "mode": {"type": "string", "description": "Retrieval mode: 'bytes' or 'lines'"}
            },
            "required": ["hash"]
        },
        "handler": lambda a: json.dumps(CAS_ENGINE.retrieve(
            str(a["hash"]),
            int(a.get("offset", 0)),
            int(a.get("limit", 4096)),
            str(a.get("mode", "bytes"))
        ), indent=2)
    }
    TOOLS["cas_search"] = {
        "description": "Searches for matching lines inside an offloaded CAS payload with context lines",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hash": {"type": "string", "description": "SHA-256 hash or cas://sha256/<hash> reference"},
                "query": {"type": "string", "description": "Search keyword or pattern"},
                "max_matches": {"type": "integer", "description": "Maximum matching results (default 10)"},
                "context_lines": {"type": "integer", "description": "Surrounding context lines (default 2)"}
            },
            "required": ["hash", "query"]
        },
        "handler": lambda a: json.dumps(CAS_ENGINE.search(
            str(a["hash"]),
            str(a["query"]),
            int(a.get("max_matches", 10)),
            int(a.get("context_lines", 2))
        ), indent=2)
    }
    TOOLS["cas_stats"] = {
        "description": "Returns storage usage metrics of the Content-Addressed Store",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": lambda a: json.dumps(CAS_ENGINE.stats(), indent=2)
    }

def make_resp(req_id: Any, result: Optional[Any] = None, error: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    p: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
    if error is not None: p["error"] = error
    else: p["result"] = result if result is not None else {}
    return p

def validate_schema(tool_name: str, args: Any) -> Optional[str]:
    if not isinstance(args, dict): return "Tool arguments must be an object"
    schema = TOOLS[tool_name]["inputSchema"]
    for req in schema.get("required", []):
        if req not in args: return f"Missing required property '{req}'"
    for prop, spec in schema.get("properties", {}).items():
        if prop in args:
            expected_type = spec.get("type")
            if expected_type == "string" and not isinstance(args[prop], str):
                return f"Property '{prop}' must be a string"
            elif expected_type == "integer" and (isinstance(args[prop], bool) or not isinstance(args[prop], int)):
                return f"Property '{prop}' must be an integer"
    return None

def parse_scope_segments(s: str) -> List[str]:
    """Normalizes hierarchical scope strings (. and : delimiters) into canonical segments."""
    return [seg for seg in s.replace(".", ":").split(":") if seg]

def scope_matches(grant_segs: List[str], req_segs: List[str]) -> bool:
    """Evaluates whether a granted scope pattern covers a required scope."""
    if grant_segs == ["*"]:
        return True
    if grant_segs and grant_segs[-1] == "*":
        k = len(grant_segs) - 1
        if len(req_segs) < k:
            return False
        return req_segs[:k] == grant_segs[:k]
    if len(grant_segs) != len(req_segs):
        return False
    for g, r in zip(grant_segs, req_segs):
        if g == "*" or g == r:
            continue
        if g.endswith("*") and r.startswith(g[:-1]):
            continue
        return False
    return True

def check_scope_authorized(required_scopes: list, granted_scopes: list, tool_name: Optional[str] = None) -> bool:
    """
    Verifies that client's granted scopes satisfy tool's required scopes with:
    - Hierarchical dot/colon delimiter parsing (e.g. workspace.finance:read vs workspace.*).
    - Explicit scope negation / restriction support (e.g. ['tools:*', '!tools:execute_shell']).
    - Sub-5 microsecond deterministic evaluation.
    """
    if not granted_scopes:
        return False if required_scopes else True

    parsed_pos: List[List[str]] = []
    parsed_neg: List[List[str]] = []

    for g in granted_scopes:
        g_str = str(g).strip()
        if not g_str:
            continue
        if g_str.startswith("!"):
            parsed_neg.append(parse_scope_segments(g_str[1:]))
        else:
            parsed_pos.append(parse_scope_segments(g_str))

    effective_reqs = list(required_scopes) if required_scopes else []
    if not effective_reqs and tool_name:
        tool_scope_segs = parse_scope_segments(f"tools:{tool_name}")
        for neg in parsed_neg:
            if scope_matches(neg, tool_scope_segs):
                return False
        return True
    elif not effective_reqs:
        return True

    if not parsed_pos:
        return False

    for req in effective_reqs:
        req_segs = parse_scope_segments(str(req))
        # 1. Any negation match immediately denies access
        for neg in parsed_neg:
            if scope_matches(neg, req_segs):
                return False
        # 2. At least one positive grant must cover the required scope
        satisfied = False
        for pos in parsed_pos:
            if scope_matches(pos, req_segs):
                satisfied = True
                break
        if not satisfied:
            return False

    return True

def get_client_scopes(params: Any) -> list:
    """Extracts client scopes from verified token claims, session state, or environment."""
    if "client_scopes" in STATE and STATE.get("token_claims"):
        return STATE["client_scopes"]
    # Fail closed when authentication or strict scope mode is enabled
    if os.environ.get("ZEROMCP_AUTH_SECRET") or os.environ.get("ZEROMCP_STRICT_SCOPES") == "1":
        return []
    if isinstance(params, dict):
        meta = params.get("_meta")
        if isinstance(meta, dict) and isinstance(meta.get("scopes"), list):
            return [str(s) for s in meta["scopes"]]
        direct_scopes = params.get("scopes")
        if isinstance(direct_scopes, list):
            return [str(s) for s in direct_scopes]
    if "client_scopes" in STATE:
        return STATE["client_scopes"]
    env_scopes = os.environ.get("ZEROMCP_CLIENT_SCOPES")
    if env_scopes:
        return [s.strip() for s in env_scopes.split(",") if s.strip()]
    return ["*"]

def handle_single_request(msg: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(msg, dict):
        return make_resp(None, error={"code": INVALID_REQ, "message": "Invalid Request: expected object"})

    # Silently drop unexpected inbound responses from clients
    if "id" in msg and ("result" in msg or "error" in msg) and "method" not in msg:
        return None

    has_id, req_id = ("id" in msg), msg.get("id")
    if has_id and (isinstance(req_id, bool) or not isinstance(req_id, (str, int))):
        return make_resp(None, error={"code": INVALID_REQ, "message": "Invalid id: must be non-null string or integer"})
    if msg.get("jsonrpc") != "2.0":
        return make_resp(req_id, error={"code": INVALID_REQ, "message": "Invalid Request: jsonrpc must be '2.0'"})

    method = msg.get("method")
    if not isinstance(method, str):
        return make_resp(req_id, error={"code": INVALID_REQ, "message": "Invalid Request: method must be string"})

    if "params" in msg and not isinstance(msg["params"], (dict, list)):
        return make_resp(req_id, error={"code": INVALID_REQ, "message": "params must be an object or array"})

    # Notifications early exit: valid notifications NEVER receive a response
    if not has_id:
        if method == "notifications/initialized" and STATE["phase"] == "NEGOTIATED":
            STATE["phase"] = "INITIALIZED"
        return None

    params = msg.get("params", {})
    if isinstance(params, list):
        return make_resp(req_id, error={"code": INVALID_PARAMS, "message": "Positional array params unsupported in MCP"})

    try:
        if method == "initialize":
            if STATE["phase"] != "UNINITIALIZED": return make_resp(req_id, error={"code": INVALID_REQ, "message": "Server already initialized"})
            proto, caps, client = params.get("protocolVersion"), params.get("capabilities"), params.get("clientInfo")
            if not isinstance(proto, str) or not isinstance(caps, dict) or not isinstance(client, dict):
                return make_resp(req_id, error={"code": INVALID_PARAMS, "message": "initialize requires string protocolVersion, object capabilities, and object clientInfo"})
            if not isinstance(client.get("name"), str) or not isinstance(client.get("version"), str):
                return make_resp(req_id, error={"code": INVALID_PARAMS, "message": "clientInfo requires string name and version"})
            # Extract capability token from initialize params, clientInfo, _meta, or authorization header
            auth_secret = os.environ.get("ZEROMCP_AUTH_SECRET")
            raw_token = params.get("token")
            if not raw_token and isinstance(client, dict):
                raw_token = client.get("token")
            if not raw_token and isinstance(params.get("_meta"), dict):
                raw_token = params["_meta"].get("token")
            if not raw_token and "authorization" in params:
                auth_str = str(params["authorization"]).strip()
                raw_token = auth_str[7:].strip() if auth_str.lower().startswith("bearer ") else auth_str

            if auth_secret:
                if not raw_token:
                    return make_resp(req_id, error={"code": INVALID_PARAMS, "message": "Authentication required: missing capability token in initialize params"})
                try:
                    sys_dir = os.path.dirname(os.path.abspath(__file__))
                    if sys_dir not in sys.path: sys.path.insert(0, sys_dir)
                    from zeromcp_token import verify_token
                    claims = verify_token(raw_token, auth_secret)
                    STATE["token_claims"] = claims
                    if isinstance(claims.get("scopes"), list):
                        STATE["client_scopes"] = [str(s) for s in claims["scopes"]]
                except Exception as e:
                    return make_resp(req_id, error={"code": INVALID_PARAMS, "message": f"Authentication failed: {e}"})
            elif raw_token:
                try:
                    sys_dir = os.path.dirname(os.path.abspath(__file__))
                    if sys_dir not in sys.path: sys.path.insert(0, sys_dir)
                    from zeromcp_token import decode_unverified
                    _, claims = decode_unverified(raw_token)
                    STATE["token_claims"] = claims
                    if isinstance(claims.get("scopes"), list):
                        STATE["client_scopes"] = [str(s) for s in claims["scopes"]]
                except Exception:
                    pass
            elif isinstance(client.get("scopes"), list):
                STATE["client_scopes"] = [str(s) for s in client["scopes"]]
            elif "_meta" in params and isinstance(params["_meta"].get("scopes"), list):
                STATE["client_scopes"] = [str(s) for s in params["_meta"]["scopes"]]

            STATE["phase"] = "NEGOTIATED"
            return make_resp(req_id, result={"protocolVersion": "2024-11-05", "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "zeromcp-py", "version": "1.0.0-rc1"}})
        if method == "ping": return make_resp(req_id, result={})
        if STATE["phase"] != "INITIALIZED" and method.startswith("tools/"):
            return make_resp(req_id, error={"code": INVALID_REQ, "message": "Server uninitialized: initialize handshake required"})
        if method == "tools/list":
            client_scopes = get_client_scopes(params)
            filter_unauth = os.environ.get("ZEROMCP_FILTER_UNAUTHORIZED_TOOLS") == "1" or (isinstance(params, dict) and params.get("filter_unauthorized") is True)
            tools_out = []
            for k, v in TOOLS.items():
                req_scopes = v.get("required_scopes", [])
                if filter_unauth and not check_scope_authorized(req_scopes, client_scopes, tool_name=k):
                    continue
                item = {"name": k, "description": v["description"], "inputSchema": v["inputSchema"]}
                if req_scopes:
                    item["scopes"] = req_scopes
                tools_out.append(item)
            return make_resp(req_id, result={"tools": tools_out})
        if method == "tools/call":
            tool_name = params.get("name")
            if not isinstance(tool_name, str) or tool_name not in TOOLS:
                return make_resp(req_id, error={"code": INVALID_PARAMS, "message": f"Tool '{str(tool_name)[:64]}' not found"})
            
            tool_entry = TOOLS[tool_name]
            req_scopes = tool_entry.get("required_scopes", [])
            client_scopes = get_client_scopes(params)
            if not check_scope_authorized(req_scopes, client_scopes, tool_name=tool_name):
                return make_resp(req_id, error={"code": INVALID_PARAMS, "message": f"Forbidden: client scopes {client_scopes} do not satisfy required scopes {req_scopes or ['tools:' + tool_name]} for tool '{tool_name}'"})

            tool_args = {} if params.get("arguments") is None else params.get("arguments")
            schema_err = validate_schema(tool_name, tool_args)
            if schema_err: return make_resp(req_id, error={"code": INVALID_PARAMS, "message": schema_err})
            try:
                raw_res = str(TOOLS[tool_name]["handler"](tool_args))
                if CAS_ENGINE and CAS_ENGINE.enabled and not tool_name.startswith("cas_"):
                    sha256, fpath, b_len, stored = CAS_ENGINE.store(raw_res)
                    if stored:
                        summary_txt = CAS_ENGINE.format_pointer_summary(sha256, fpath, raw_res)
                        return make_resp(req_id, result={
                            "content": [{"type": "text", "text": summary_txt}],
                            "isError": False,
                            "cas_ref": f"cas://sha256/{sha256}",
                            "cas_bytes": b_len
                        })
                return make_resp(req_id, result={"content": [{"type": "text", "text": raw_res}], "isError": False})
            except Exception as e:
                return make_resp(req_id, result={"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
        return make_resp(req_id, error={"code": METHOD_NOT_FOUND, "message": f"Method '{str(method)[:64]}' not found"})
    except Exception as e:
        sys.stderr.write(f"[ZeroMCP-Core] Request exception: {e}\n")
        return make_resp(req_id, error={"code": INTERNAL_ERR, "message": "Internal error"})

def emit_frame(payload: Any):
    try:
        data = (json.dumps(payload, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
        _WIRE_STDOUT.write(data)
        _WIRE_STDOUT.flush()
    except (BrokenPipeError, OSError): os._exit(0)

def setup_transport():
    global _WIRE_STDOUT
    try:
        _WIRE_STDOUT = os.fdopen(os.dup(1), "wb")
        os.dup2(2, 1); sys.stdout = sys.stderr
    except Exception as e: sys.stderr.write(f"ZeroMCP Fatal: {e}\n"); os._exit(1)

def run_stdio():
    setup_transport()
    chk_flt = lambda s: float(s) if math.isfinite(float(s)) else (_ for _ in ()).throw(ValueError("Overflow"))
    chk_int = lambda s: int(s) if len(s) <= 4300 else (_ for _ in ()).throw(ValueError("Int limit"))
    while True:
        try: raw_bytes = sys.stdin.buffer.readline(MAX_FRAME_SIZE)
        except KeyboardInterrupt: os._exit(0)
        except Exception as e: sys.stderr.write(f"[ZeroMCP-IO] Read error: {e}\n"); break
        if not raw_bytes: break

        if len(raw_bytes) >= MAX_FRAME_SIZE and not raw_bytes.endswith(b"\n"):
            while True:
                chunk = sys.stdin.buffer.readline(MAX_FRAME_SIZE)
                if not chunk or chunk.endswith(b"\n"): break
            emit_frame(make_resp(None, error={"code": PARSE_ERR, "message": "Frame exceeded maximum allowed size"}))
            continue

        try: line_str = raw_bytes.decode("utf-8").strip()
        except UnicodeDecodeError as e:
            emit_frame(make_resp(None, error={"code": PARSE_ERR, "message": f"UTF-8 decode error: {e}"}))
            continue
        if not line_str: continue

        try:
            parsed = json.loads(line_str, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f"Constant {c} forbidden")),
                                parse_float=chk_flt, parse_int=chk_int)
        except (json.JSONDecodeError, ValueError, RecursionError) as e:
            emit_frame(make_resp(None, error={"code": PARSE_ERR, "message": f"Parse error: {e}"}))
            continue

        try:
            if isinstance(parsed, list):
                if len(parsed) == 0:
                    emit_frame(make_resp(None, error={"code": INVALID_REQ, "message": "Invalid Request: empty batch"}))
                    continue
                if len(parsed) > MAX_BATCH:
                    emit_frame(make_resp(None, error={"code": INVALID_REQ, "message": f"Batch size exceeds maximum of {MAX_BATCH}"}))
                    continue
                resps = [handle_single_request(item) for item in parsed]
                active_resps = [r for r in resps if r is not None]
                if active_resps: emit_frame(active_resps)
            else:
                resp = handle_single_request(parsed)
                if resp is not None: emit_frame(resp)
        except Exception as e:
            sys.stderr.write(f"[ZeroMCP-Core] Unhandled error: {e}\n")
            emit_frame(make_resp(None, error={"code": INTERNAL_ERR, "message": "Internal error"}))

def print_claude_config(server_name: str = "zeromcp"):
    """Outputs ready-to-paste Claude Desktop configuration JSON with absolute paths."""
    config = {
        "mcpServers": {
            server_name: {
                "command": sys.executable,
                "args": [os.path.abspath(__file__)]
            }
        }
    }
    print(json.dumps(config, indent=2))

def list_tools():
    """Prints a clean, human-readable table of all registered tools."""
    print(f"\n📦 Registered ZeroMCP Tools ({len(TOOLS)} total):")
    print("=" * 65)
    for name, spec in sorted(TOOLS.items()):
        desc = spec.get("description", "No description")
        schema = spec.get("inputSchema", {})
        props = list(schema.get("properties", {}).keys())
        req = set(schema.get("required", []))
        params_str = ", ".join(f"{p}*" if p in req else p for p in props) or "none"
        print(f"• {name}")
        print(f"  Description: {desc}")
        print(f"  Parameters:  {params_str} (* = required)")
        print("-" * 65)

def call_tool_cli(tool_name: str, args_input: str):
    """Executes a tool directly from CLI with flexible JSON or raw shorthand input."""
    if tool_name not in TOOLS:
        sys.stderr.write(f"❌ Error: Tool '{tool_name}' not found.\nAvailable: {', '.join(TOOLS.keys())}\n")
        sys.exit(1)
    
    args = {}
    trimmed = args_input.strip() if args_input else ""
    if trimmed.startswith("{") and trimmed.endswith("}"):
        try:
            args = json.loads(trimmed)
        except Exception as e:
            sys.stderr.write(f"❌ Error parsing JSON arguments: {e}\n")
            sys.exit(1)
    elif trimmed:
        # Ergonomic shorthand auto-mapping
        req_props = TOOLS[tool_name].get("schema", {}).get("required", [])
        if len(req_props) == 1:
            args = {req_props[0]: trimmed}
        elif tool_name == "eval_math":
            args = {"expression": trimmed}
        elif tool_name == "echo":
            args = {"message": trimmed}
        else:
            sys.stderr.write(f"❌ Error: Tool requires structured JSON: '{trimmed}'\n")
            sys.exit(1)

    val_err = validate_schema(tool_name, args)
    if val_err:
        sys.stderr.write(f"❌ Schema Validation Error: {val_err}\n")
        sys.exit(1)

    try:
        handler = TOOLS[tool_name]["handler"]
        res = handler(args)
        print(f"✅ Result ({tool_name}):\n{res}")
    except Exception as e:
        sys.stderr.write(f"❌ Execution Error: {e}\n")
        sys.exit(1)

def run_repl():
    """Interactive developer console for testing tools without raw JSON-RPC."""
    print("⚡ ZeroMCP Interactive Developer Console (type 'exit' or 'quit' to leave)")
    print("Commands: list | eval <expr> | echo <msg> | sysinfo | call <tool> <args>")
    print("-" * 65)
    while True:
        try:
            line = input("zeromcp> ").strip()
            if not line:
                continue
            if line in ("exit", "quit", "q"):
                print("Exiting console.")
                break
            if line in ("list", "--list", "-l"):
                list_tools()
                continue
            if line in ("sysinfo", "system_info"):
                print(TOOLS["system_info"]["handler"]({}))
                continue
            if line.startswith("echo "):
                print(TOOLS["echo"]["handler"]({"message": line[5:].strip()}))
                continue
            if line.startswith("eval "):
                print(TOOLS["eval_math"]["handler"]({"expression": line[5:].strip()}))
                continue
            parts = line.split(maxsplit=2)
            if parts[0] == "call":
                tool = parts[1] if len(parts) > 1 else ""
                args_raw = parts[2] if len(parts) > 2 else ""
                call_tool_cli(tool, args_raw)
            else:
                print(f"Unknown command: '{line}'. Try: list, eval <expr>, echo <msg>, sysinfo, call <tool> <args>")
        except (KeyboardInterrupt, EOFError):
            print("\nExiting console.")
            break

def handle_cli():
    """Parses developer ergonomics CLI flags."""
    args = sys.argv[1:]
    if not args:
        run_stdio()
        return

    cmd = args[0]
    if cmd in ("--config", "--print-config", "--print-claude-config"):
        server_name = args[1] if len(args) > 1 else "zeromcp"
        print_claude_config(server_name)
    elif cmd in ("--list", "-l", "list"):
        list_tools()
    elif cmd in ("--repl", "-i", "repl", "console"):
        run_repl()
    elif cmd in ("--call", "-c", "call"):
        if len(args) < 2:
            sys.stderr.write("Usage: python3 zeromcp.py --call <tool_name> ['<args>' | '{\"arg\": \"val\"}']\n")
            sys.exit(1)
        tool_name = args[1]
        args_input = args[2] if len(args) > 2 else ""
        call_tool_cli(tool_name, args_input)
    elif cmd in ("--cas-stats", "cas-stats"):
        if CAS_ENGINE:
            print(json.dumps(CAS_ENGINE.stats(), indent=2))
        else:
            print("ZeroMCP CAS Engine is disabled or unavailable.")
    elif cmd in ("--cas-gc", "cas-gc"):
        max_days = int(args[1]) if len(args) > 1 and args[1].isdigit() else 7
        if CAS_ENGINE:
            res = CAS_ENGINE.gc(max_age_days=max_days)
            print(f"🧹 CAS Garbage Collection: Removed {res['removed_count']} files, reclaimed {res['reclaimed_bytes']:,} bytes.")
        else:
            print("ZeroMCP CAS Engine is disabled or unavailable.")
    elif cmd in ("--help", "-h", "help"):
        print("ZeroMCP: Ultra-lean Model Context Protocol Server")
        print("\nCommands:")
        print("  (no args)             Run in production stdio JSON-RPC mode")
        print("  --list, -l            List all registered tools and their schemas")
        print("  --call, -c <tool> [a] Call a tool directly with shorthand argument or JSON")
        print("  --repl, -i            Interactive developer console for testing tools")
        print("  --print-config        Output copy-paste claude_desktop_config.json block")
        print("  --cas-stats           Show Content-Addressed Store telemetry and usage")
        print("  --cas-gc [days]       Prune CAS storage files older than N days (default 7)")
    else:
        # Default to stdio if unexpected argument passed
        run_stdio()

if __name__ == "__main__":
    handle_cli()
