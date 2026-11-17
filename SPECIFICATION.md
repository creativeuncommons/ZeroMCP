# ZeroMCP: Raw JSON-RPC Model Context Protocol Core Specification

**Specification Version**: `1.0.0-rc1`  
**Protocol Alignment**: Model Context Protocol (MCP) `2024-11-05` / JSON-RPC `2.0`  
**Governing Gate**: Tri-Model Technical Steering Committee (Claude Opus 5.5, Codex Astra, Gemini 4 Argon)  

---

## 1. Mission Statement & Core Philosophy
ZeroMCP is an ultra-lean, zero-third-party-dependency, production-grade family of Model Context Protocol (MCP) server implementations built directly on the raw JSON-RPC 2.0 specification over standard I/O (`stdio`) and Server-Sent Events (`SSE`).

### Design Principles:
1. **Zero External Runtime Bloat**: Eliminate heavy frameworks (Pydantic, AnyIO, Starlette, websockets) that introduce supply-chain vulnerabilities, binary size inflation, and deployment friction.
2. **Wire Protocol Purity**: Deliver direct wire-level serialization and deserialization with sub-millisecond handshake and tool invocation latency.
3. **Transport Isolation**: Strict standard output (`stdout`) reservation for JSON-RPC frames, with all operational diagnostics, telemetry, and logging strictly redirected to standard error (`stderr`).
4. **Deterministic Error Semantics**: Precise adherence to standard JSON-RPC 2.0 error taxonomy and MCP tool execution error contracts.

---

## 2. Protocol Specification & Lifecycle (MCP 2024-11-05)

### 2.1 Transport Framing (`stdio`)
* Every JSON-RPC message is transmitted as a single line terminated with a newline (`\n` or `\r\n`).
* Whitespace lines and empty frames MUST be ignored without error.
* Standard error (`stderr`) is completely unconstrained for arbitrary human-readable logs and does not impact client protocol parsing.
* Messages exceeding safety thresholds (e.g. 10 MB per line) MUST be rejected with `-32700 Parse error`.

### 2.2 Protocol Lifecycle Flow

```
Client                                                  ZeroMCP Server
  |                                                           |
  | -------- 1. initialize request (id: 1) -----------------> |
  | <------- 2. initialize response (id: 1, capabilities) ---- |
  |                                                           |
  | -------- 3. notifications/initialized (no id) ----------> |
  |            [Server records state, emits NO response]      |
  |                                                           |
  | -------- 4. ping request (id: 2) -----------------------> |
  | <------- 5. ping response (id: 2, result: {}) ----------- |
  |                                                           |
  | -------- 6. tools/list request (id: 3) -----------------> |
  | <------- 7. tools/list response (id: 3, tools: [...]) --- |
  |                                                           |
  | -------- 8. tools/call request (id: 4, name, args) ------> |
  | <------- 9. tools/call response (id: 4, content: [...]) -- |
```

### 2.3 Handshake Contract
* **`initialize` Request**:
  ```json
  {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": { "name": "client-name", "version": "1.0.0" }
    }
  }
  ```
* **`initialize` Response**:
  ```json
  {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
      "protocolVersion": "2024-11-05",
      "capabilities": {
        "tools": { "listChanged": false }
      },
      "serverInfo": {
        "name": "zeromcp",
        "version": "0.1.0"
      }
    }
  }
  ```
* **`notifications/initialized` Notification**:
  ```json
  {
    "jsonrpc": "2.0",
    "method": "notifications/initialized"
  }
  ```
  *Crucial Rule*: Because this is a notification (no `"id"` field), the server MUST NOT send any response.

### 2.4 Diagnostic Ping
* **`ping` Request**:
  ```json
  { "jsonrpc": "2.0", "id": 2, "method": "ping" }
  ```
* **`ping` Response**:
  ```json
  { "jsonrpc": "2.0", "id": 2, "result": {} }
  ```

### 2.5 Tool Discovery & Dispatch
* **`tools/list` Request**:
  ```json
  { "jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {} }
  ```
* **`tools/list` Response**:
  ```json
  {
    "jsonrpc": "2.0",
    "id": 3,
    "result": {
      "tools": [
        {
          "name": "echo",
          "description": "Echoes back the input message",
          "inputSchema": {
            "type": "object",
            "properties": {
              "message": { "type": "string" }
            },
            "required": ["message"]
          }
        },
        {
          "name": "eval_math",
          "description": "Safely evaluates basic arithmetic expressions (+, -, *, /, %, **)",
          "inputSchema": {
            "type": "object",
            "properties": {
              "expression": { "type": "string" }
            },
            "required": ["expression"]
          }
        },
        {
          "name": "system_info",
          "description": "Returns host platform, architecture, and runtime telemetry",
          "inputSchema": {
            "type": "object",
            "properties": {}
          }
        }
      ]
    }
  }
  ```
* **`tools/call` Request**:
  ```json
  {
    "jsonrpc": "2.0",
    "id": 4,
    "method": "tools/call",
    "params": {
      "name": "echo",
      "arguments": { "message": "ZeroMCP Live" }
    }
  }
  ```
* **`tools/call` Success Response**:
  ```json
  {
    "jsonrpc": "2.0",
    "id": 4,
    "result": {
      "content": [
        {
          "type": "text",
          "text": "ZeroMCP Live"
        }
      ],
      "isError": false
    }
  }
  ```
* **`tools/call` Error Response (Tool-level failure)**:
  ```json
  {
    "jsonrpc": "2.0",
    "id": 4,
    "result": {
      "content": [
        {
          "type": "text",
          "text": "Division by zero"
        }
      ],
      "isError": true
    }
  }
  ```

### 2.5 Content-Addressed Storage (CAS) Protocol Extension
When tool responses meet or exceed the storage threshold (default: $4,096$ bytes), compliant servers MAY offload the raw content payload to a Content-Addressed Store, returning a standard pointer envelope:
* **Response Envelope**:
  ```json
  {
    "jsonrpc": "2.0",
    "id": 4,
    "result": {
      "content": [
        {
          "type": "text",
          "text": "[ZeroMCP-CAS: Payload Offloaded to Content-Addressed Store]\n• Reference: cas://sha256/<hash>\n• Size: 8192 bytes..."
        }
      ],
      "isError": false,
      "cas_ref": "cas://sha256/9dffc3a6...",
      "cas_bytes": 8192
    }
  }
  ```
* **Storage Path Determinism**: Blobs are addressed by the lowercase hexadecimal SHA-256 digest of their UTF-8 bytes and persisted atomically at `<cas_dir>/<hash[:2]>/<hash>.txt`.
* **Standard Retrieval Tools**:
  * `cas_fetch`: Retrieves bounded byte or line slices (`hash`, `offset`, `limit`, `mode`).
  * `cas_search`: Evaluates regex or substring queries over offloaded blobs with contextual line windows (`hash`, `query`, `max_matches`, `context_lines`).
  * `cas_stats`: Reports telemetry metrics on active CAS cache utilization.

---

## 3. Standard JSON-RPC 2.0 Error Codes

| Code | Constant | Meaning | Condition |
| :---: | :--- | :--- | :--- |
| **`-32700`** | `PARSE_ERROR` | Parse error | Received invalid JSON string. |
| **`-32600`** | `INVALID_REQUEST` | Invalid Request | JSON sent is not a valid Request object (missing `jsonrpc`, `method`, etc.). |
| **`-32601`** | `METHOD_NOT_FOUND` | Method not found | Method does not exist or is unrecognized. |
| **`-32602`** | `INVALID_PARAMS` | Invalid params | Invalid method parameters (missing arguments, schema mismatch). |
| **`-32603`** | `INTERNAL_ERROR` | Internal error | Unhandled server exception during request processing. |

---

## 4. Multi-Language Implementation Invariants

### 4.1 Python (`zeromcp-py`)
* Standard library only: `sys`, `os`, `json`, `ast`, `math`, `operator`, `platform`, `typing`, `hashlib`.
* Safe math evaluation: AST-based token and binary operator whitelist (strictly no `eval()` on raw strings).
* Token efficiency: Built-in Content-Addressed Storage (`zeromcp_cas.py`) and developer ergonomics suite.
* File name: `zeromcp.py`.

### 4.2 Rust (`zeromcp-rs`)
* Minimal crate footprint: `tokio` (async stdio), `serde`, `serde_json`.
* Zero allocation where possible; streaming line-by-line reader.
* Output: single statically linked binary (`zeromcp-rs`).

### 4.3 TypeScript (`zeromcp-ts`)
* Zero `node_modules` dependency: uses native Node.js / Bun `readline` and `process.stdin`/`process.stdout`.
* Safe math parsing: recursive-descent or tokenized math evaluator without `eval()` or `Function()`.
* Executable directly with `bun run zeromcp.ts` or `npx tsx zeromcp.ts`.

---

## 5. Security & Isolation Invariants
1. **No Code Execution**: Dynamic code evaluation (`eval`, `exec`, `Function`, `system`) is strictly banned.
2. **Crash Resilience**: Malformed JSON or unhandled tool exceptions MUST NEVER crash the stdio daemon. An error JSON-RPC response or stderr diagnostic must be returned, keeping the server listening for subsequent frames.
3. **Buffer Clamping**: Input lines exceeding 10 MB are rejected before JSON deserialization to thwart heap exhaustion attacks.

---

## 6. Fine-Grained Access Control (FGAC) & Token Delegation Protocol

### 6.1 Multi-Tenant Hierarchical Scope Hierarchy
ZeroMCP implements a deterministic, multi-tenant hierarchical scope matching engine supporting both dot (`.`) and colon (`:`) segment delimiters:

* **Canonical Delimiters**: `.` and `:` are interchangeable segment delimiters (e.g. `workspace.finance:read` is semantically identical to `workspace:finance:read`).
* **Wildcard Expansion**:
  * Global Wildcard `*`: Grants access to all tools and capabilities.
  * Hierarchical Tail Wildcard `path.*` or `path:*`: Covers the entire sub-tree (e.g., `workspace.*` matches `workspace.finance:read`, `workspace.billing:invoice:create`, and `workspace.hr`).
* **Explicit Negation & Restrictions (`!`)**:
  * Scopes prefixed with `!` define absolute restrictions that override positive grants (e.g., `["tools:*", "!tools:execute_shell"]`).
  * If a required scope matches any negative restriction, authorization is immediately rejected with `-32602` (`Forbidden`).
* **Ultra-Low Latency Invariant**: Evaluation executes in `< 50 microseconds` (empirical benchmark: `~1.9 µs` on standard CPU hardware).

### 6.2 Scoped Ephemeral Capability Tokens (`zeromcp_token.py`)
To enable parent orchestrators (e.g., HyperGate) to delegate bounded capabilities to subagents without contacting central identity providers (Keycloak, Okta), ZeroMCP defines an ultra-lightweight, zero-dependency HMAC-SHA256 capability token specification:

* **Wire Structure**: Standard URL-safe Base64 compact encoding: `<header_b64>.<payload_b64>.<signature_b64>`.
* **Cryptographic Algorithm**: HMAC-SHA256 (`HS256`).
* **Header**:
  ```json
  { "alg": "HS256", "typ": "ZMCP" }
  ```
* **Payload Claims Schema**:
  ```json
  {
    "sub": "subagent-uuid-or-id",
    "scopes": ["tools:*", "!tools:execute_shell"],
    "iat": 1731420000,
    "exp": 1731420300,
    "iss": "hypergate",
    "aud": "zeromcp-cluster",
    "tenant_id": "org_enterprise_1"
  }
  ```

### 6.3 Handshake Authentication Lifecycle
1. **Server Configuration**: When `ZEROMCP_AUTH_SECRET` is configured in the environment, ZeroMCP enforces mandatory token authentication during `initialize`.
2. **Token Ingestion**: The token may be supplied via `params.token`, `clientInfo.token`, `params._meta.token`, or `authorization: Bearer <token>`.
3. **Fail-Closed Gate**: If the token is missing, invalid, or expired, the server terminates the handshake immediately with JSON-RPC error `-32602` (`Authentication required / Authentication failed`).
4. **Session Scoping**: Upon successful verification, the token's granted scopes are pinned to `STATE["client_scopes"]`. Subsequent requests within the session are bounded by these claims.
5. **Tool Discovery Filtering**: When `filter_unauthorized: true` is passed to `tools/list` (or `ZEROMCP_FILTER_UNAUTHORIZED_TOOLS=1`), unauthorized tools are hidden from the discovery manifest.
