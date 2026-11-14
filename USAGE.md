# ZeroMCP: Multi-Language Usage, Integration & Configuration Manual

**Specification Alignment**: Model Context Protocol (MCP) `2024-11-05` / JSON-RPC `2.0`  
**Current Release**: `1.0.0-rc1` (Release Candidate 1)  
**Changelog**: See [`CHANGELOG.md`](CHANGELOG.md)  
**Automated Release Workflow**: See [`.github/workflows/zeromcp_release.yml`](.github/workflows/release.yml)

### Specification Conformance Exceptions & Scope
ZeroMCP implements the core JSON-RPC 2.0 wire protocol and MCP 2024-11-05 tool schemas with zero third-party dependencies. Full conformance with MCP normative requirements carries three documented implementation boundaries (detailed in §6):
1. **Tool Rate Limiting (MUST)**: Standalone server binaries intentionally omit embedded token-bucket rate limiting as an architectural choice to maintain lean, zero-dependency implementations (strictly $<200$ LOC in Python reference). Throttling is addressed via the companion stdio supervisor (`scripts/zeromcp_stdio_limiter.py`).
2. **Access Controls (MUST)**: Binaries execute under ambient OS permissions of the launching process with zero network calls and read-only system telemetry; fine-grained OS access controls and sandboxing are unverified and delegated to host execution environments (Docker, Wasmtime, systemd).
3. **Output Sanitization (MUST)**: All runtime exceptions and tool errors return standardized, user-safe error strings (`Error: Division by zero`) without leaking raw interpreter stack traces; tool return values (such as `echo`) pass caller text through verbatim without semantic sanitization.

---

## 1. Variant Selection & Implementation Matrix

| Variant | Distribution Format | Build Dependencies | Runtime Dependencies | Verification Status | Recommended Use Case |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Python (`zeromcp-py`)** | Single script (`zeromcp.py`) | **None** (No build step) | **Zero** (Python stdlib: `sys`, `os`, `json`, `ast`, `math`, `operator`, `platform`, `typing`. Python 3.10+ recommended; verified on Python 3.12 in CI; Python 3.8–3.9 legacy best-effort, unverified) | **Verified Reference (Passed 40/40 Regression Tests on Python 3.12)** | AI/ML pipelines, Python agent harnesses, local LLM tooling. |
| **TypeScript (`zeromcp-ts`)** | Single script (`zeromcp.ts`) | **None** (No build step) | **Zero** `node_modules` (Minimum supported Node version: 22.7; CI execution target: Node 24; Bun 1.0+ and Deno 2 are unverified community compatibility targets based on standard stream APIs) | **Engineered Implementation (Automated CI verification executed on Node v24; Node 22.7–22.17, Bun, and Deno runtime execution unverified in release gate)** | IDE extensions, Node.js agent runners, headless MCP pipelines. |
| **Rust Native (`zeromcp-rs`)** | Standalone binary (`zeromcp`) | Cargo (`tokio`, `serde`, `serde_json`) | **Zero** for Linux musl (fully static); Windows MSVC links C-runtime dynamically unless built with `+crt-static` | **Engineered Implementation (Linux x86_64 configured for 40-check CI runtime conformance; macOS and Windows configured for build verification in CI matrix; execution unverified on local host lacking cargo)** | High-throughput edge microservices, minimal Docker containers (single-threaded Tokio scheduler with sequential stdio request processing). |
| **WebAssembly WASI (`zeromcp-wasm`)** | Single binary (`zeromcp.wasm`) | Cargo (`serde`, `serde_json` with target `wasm32-wasip1`) | **Zero** dependencies; requires standard WASI runtime (`wasmtime`, `wasmer`) or Node.js (`node:wasi` in Node 22+) | **Engineered Implementation (WASI Preview 1 source implemented with synchronous stdio; release binary build and Wasmtime 40-check conformance gate configured in CI; Wasmer and Node WASI launcher are unverified community targets)** | CLI and serverless environments supporting WASI Preview 1 (`wasmtime`); Wasmer, Node.js, browser and edge workers require platform-specific WASI runtime shims and remain unverified in rc1. |

> [!NOTE]
> **WebAssembly / WASI Architecture & Isolation**: `zeromcp.wasm` compiles the core protocol engine with `target_family = "wasm"`, using pure synchronous Rust standard library streams (`std::io::BufReader`, `std::io::stdin()`, `std::io::stdout()`) without Tokio or async reactor dependencies. Sandboxing and capability isolation depend strictly on host runtime configuration (e.g. granted directory preopens and resource limits); runtimes such as Node.js explicitly state that `node:wasi` does not provide comprehensive filesystem security for untrusted code. Deployers should use hardened engines like `wasmtime` with zero directory preopens for isolated execution.

---

## 2. Quickstart & Direct CLI Execution

### 2.1 Python (`zeromcp-py`)
Zero `pip install` required. Requires standard Python 3.10+ (Python 3.12 verified in CI; Python 3.8–3.9 legacy best-effort, unverified):

```bash
# When executing from repository clone (working directory: ):
python3 zeromcp-py/zeromcp.py

# When executing downloaded release script (working directory: download folder):
python3 ./zeromcp.py
```

*(Tip: Staged distribution scripts in `dist/` can be refreshed locally from source at any time by running `python3 scripts/package_dist.py`).*

### 2.2 TypeScript / JavaScript (`zeromcp-ts`)
Zero `node_modules` required. Uses native Node.js / Bun / Deno APIs:

```bash
# Option A: Modern Node.js (v22.7+ or v24+) with native type-stripping
node --experimental-strip-types zeromcp-ts/zeromcp.ts   # From repository
node --experimental-strip-types ./zeromcp.ts          # From release asset

# Option B: Bun (hyper-fast execution)
bun run zeromcp-ts/zeromcp.ts

# Option C: Deno 2 (requires --allow-sys=osRelease for system_info and --allow-env for startup configuration)
deno run --allow-sys=osRelease --allow-env=ZEROMCP_NO_AUTO_START zeromcp-ts/zeromcp.ts
```

> [!NOTE]
> **Node.js Type Stripping & Runtime Verification Scope**: Node.js 22.7–22.17 requires `--experimental-strip-types` to execute `.ts` files directly. Default unflagged type-stripping is available starting in Node.js 22.18+ and 23.6+. (Node 22.7+ is required for the standalone release asset because it detects ES module syntax automatically without a `package.json`). Automated CI release conformance runs against Node.js v24; Bun 1.0+ and Deno 2 are unverified community compatibility targets based on standard ECMAScript APIs and should be validated within your local environment.

---

### 2.3 Direct End-to-End Handshake & Smoke Test
You can immediately verify raw standard I/O wire compliance across any language implementation using a three-frame pipeline (`initialize` $\rightarrow$ `notifications/initialized` $\rightarrow$ `tools/call`):
```bash
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke-test","version":"1.0.0"}}}\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"echo","arguments":{"message":"hello world"}}}\n' | python3 zeromcp-py/zeromcp.py
```
**Expected Standard Output**:
```jsonl
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{"listChanged":false}},"serverInfo":{"name":"zeromcp-py","version":"1.0.0-rc1"}}}
{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"hello world"}],"isError":false}}
```
*(Tip: When smoke-testing other variants, `serverInfo.name` reports `zeromcp-ts` or `zeromcp-rs`. Furthermore, Rust emits object keys in sorted alphabetical order, e.g. `{"capabilities":...,"protocolVersion":...,"serverInfo":...}` and `{"text":"hello world","type":"text"}`).*

> [!NOTE]
> **Sequential Initialization in Production**: While piping multi-frame requests simultaneously demonstrates raw newline-delimited wire processing for smoke testing, production MCP clients should await the server's `initialize` response before dispatching `notifications/initialized` and subsequent tool requests.

### 2.4 Rust Native (`zeromcp-rs`)

#### Compiling from Source
Requires the Rust toolchain (`cargo`):
```bash
cd zeromcp-rs
cargo build --release
./target/release/zeromcp
```

#### Pre-Compiled Release Binaries & Checksum Verification
Standalone native binaries and universal WebAssembly modules are built across all major platforms via GitHub Actions ([`.github/workflows/zeromcp_release.yml`](.github/workflows/release.yml)):
* **macOS Apple Silicon**: `zeromcp-macos-arm64`
* **macOS Intel**: `zeromcp-macos-x86_64`
* **Linux x86_64**: `zeromcp-linux-x86_64` (statically linked musl binary)
* **Linux ARM64**: `zeromcp-linux-arm64` (statically linked musl binary)
* **Windows x64**: `zeromcp-windows-x64.exe`
* **WebAssembly / WASI Universal**: `zeromcp.wasm` (portable bytecode for `wasmtime`, `wasmer`, or Node.js)

> [!NOTE]
> **Release Asset Availability**: Pre-compiled binary assets and `SHA256SUMS.txt` are published automatically by GitHub Actions upon pushing a release tag (e.g. `v1.0.0-rc1`). Prior to tag publication, compile native binaries locally from source using `cargo build --release`.

Download the appropriate binary and `SHA256SUMS.txt` from the [GitHub Releases page](https://github.com/creativeuncommons/ZeroMCP/releases). Execute only upon successful checksum verification (fail-closed, strictly validating that exactly one 64-character hexadecimal entry exists in the manifest):

**Linux:**
```bash
( count=$(grep -cE '^[a-fA-F0-9]{64}[[:space:]]+zeromcp-linux-x86_64$' SHA256SUMS.txt 2>/dev/null || true)
  if [ "$count" -eq 1 ]; then
    grep -E '^[a-fA-F0-9]{64}[[:space:]]+zeromcp-linux-x86_64$' SHA256SUMS.txt | sha256sum -c && chmod +x zeromcp-linux-x86_64 && ./zeromcp-linux-x86_64
  else
    echo "Checksum verification failed: expected exactly one entry for zeromcp-linux-x86_64 in SHA256SUMS.txt" >&2
    exit 1
  fi )
```

**macOS:**
```bash
( count=$(grep -cE '^[a-fA-F0-9]{64}[[:space:]]+zeromcp-macos-arm64$' SHA256SUMS.txt 2>/dev/null || true)
  if [ "$count" -eq 1 ]; then
    grep -E '^[a-fA-F0-9]{64}[[:space:]]+zeromcp-macos-arm64$' SHA256SUMS.txt | shasum -a 256 -c && (xattr -d com.apple.quarantine zeromcp-macos-arm64 2>/dev/null || true) && chmod +x zeromcp-macos-arm64 && ./zeromcp-macos-arm64
  else
    echo "Checksum verification failed: expected exactly one entry for zeromcp-macos-arm64 in SHA256SUMS.txt" >&2
    exit 1
  fi )
```

**Windows (PowerShell):**
```powershell
$entries = Get-Content SHA256SUMS.txt | Select-String -Pattern '^\s*([a-fA-F0-9]{64})\s+zeromcp-windows-x64\.exe\s*$'
if ($entries.Count -ne 1) { throw "Expected exactly one entry for zeromcp-windows-x64.exe in SHA256SUMS.txt, found $($entries.Count)!" }
$expected = $entries[0].Matches[0].Groups[1].Value
$actual = (Get-FileHash -Algorithm SHA256 .\zeromcp-windows-x64.exe).Hash
if ($actual.ToLower() -eq $expected.ToLower()) {
    .\zeromcp-windows-x64.exe
} else {
    throw "Checksum verification failed! File content hash does not match manifest."
}
```

*(Note for macOS Intel and Linux ARM64: In the commands above, substitute `zeromcp-macos-x86_64` or `zeromcp-linux-arm64` as appropriate).*
*(Note on Binary Startup: Upon successful checksum validation and launch, the server binary waits silently for newline-delimited JSON-RPC messages on standard input; press `Ctrl-D` / send EOF to exit).*

> [!NOTE]
> **Checksum Verification Scope**: Release candidate assets (`rc1`) are unsigned. `SHA256SUMS.txt` verifies bit-level download integrity against transmission corruption, but does not assert provenance or anti-tamper authenticity.

> [!NOTE]
> **Windows VC++ Runtime Dependency**: The pre-compiled `zeromcp-windows-x64.exe` links the Microsoft Visual C++ runtime dynamically. Systems without the standard Visual C++ Redistributable installed will encounter missing DLL errors. To generate a fully standalone Windows binary without runtime dependencies, compile from source:
> * **PowerShell**: `$env:RUSTFLAGS="-C target-feature=+crt-static"; cargo build --release`
> * **POSIX Shell / bash**: `RUSTFLAGS="-C target-feature=+crt-static" cargo build --release`

### 2.5 WebAssembly / WASI (`zeromcp.wasm`)

ZeroMCP provides a portable WebAssembly bytecode module (`zeromcp.wasm`) compiled for the universal `wasm32-wasip1` target. It communicates over standard I/O pipes using pure synchronous Rust standard library streams.

#### Direct CLI Execution (Dedicated WASI Runtimes)
Dedicated WASI runtimes execute the compiled `.wasm` binary directly from the command line:

```bash
# Option A: Wasmtime (Bytecode Alliance reference engine)
wasmtime zeromcp.wasm

# Option B: Wasmer
wasmer run zeromcp.wasm

# Direct Handshake Smoke Test via wasmtime:
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke-test","version":"1.0.0"}}}\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"system_info"}}\n' | wasmtime zeromcp.wasm
```

#### Node.js WASI Execution Launcher (Community Compatibility Profile)
Node.js does not execute standalone WASI command binaries directly from the CLI; launch using the included zero-dependency ES module runner (`node_wasi_runner.mjs`, packaged in release assets and available in the repository at `scripts/node_wasi_runner.mjs`; provided as an unverified community profile for Node.js WASI Preview 1). The runner accepts an explicit path to `zeromcp.wasm` as an argument or defaults to looking for a colocated `zeromcp.wasm` in the same directory:

```javascript
// node_wasi_runner.mjs
import { readFile } from "node:fs/promises";
import { WASI } from "node:wasi";
import { fileURLToPath } from "node:url";

const wasmArg = process.argv[2];
const wasmPath = wasmArg && !wasmArg.startsWith("-") 
  ? wasmArg 
  : fileURLToPath(new URL("./zeromcp.wasm", import.meta.url));

const wasi = new WASI({ version: "preview1", args: process.argv.slice(2), env: {}, stdin: 0, stdout: 1, stderr: 2 });
const wasmBytes = await readFile(wasmPath);
const { instance } = await WebAssembly.instantiate(wasmBytes, wasi.getImportObject());
wasi.start(instance);
```

Launch commands:
```bash
# Option A (Repository Root): Launching compiled WASI binary using repository runner script
node scripts/node_wasi_runner.mjs zeromcp-rs/target/wasm32-wasip1/release/zeromcp.wasm

# Option B (Downloaded Release Assets): When node_wasi_runner.mjs and zeromcp.wasm are colocated in the same download directory
node node_wasi_runner.mjs
```

#### Deno Runtime Guidance (WASI Stubs vs. TypeScript Profile)
Deno does not support WASI Preview 1 command execution out-of-the-box: Deno's built-in `node:wasi` compatibility module provides non-functional stubs. Deno users wishing to run ZeroMCP should directly execute the pure TypeScript reference implementation (`zeromcp-ts`), which is natively supported:
```bash
deno run --allow-sys=osRelease --allow-env=ZEROMCP_NO_AUTO_START zeromcp-ts/zeromcp.ts
```

#### Compiling WebAssembly from Source
Requires the Rust toolchain with the WASI target installed:
```bash
rustup target add wasm32-wasip1
cd zeromcp-rs
cargo build --release --target wasm32-wasip1
# Compiled binary located at target/wasm32-wasip1/release/zeromcp.wasm
```

### 2.6 Developer Ergonomics & Direct Terminal Inspection CLI

ZeroMCP includes zero-dependency ergonomic CLI flags for human developers and local agents to inspect, test, and configure tools without requiring awkward raw JSON-RPC stdio pipes:

#### A. Tool Schema Inspection (`--list` / `-l`)
Inspects all registered tools, descriptions, and required argument signatures in an aligned terminal table:
```bash
python3 zeromcp-py/zeromcp.py --list
```

#### B. Direct Tool Execution (`--call` / `-c`)
Invokes tools directly from the command line with either human shorthand arguments or structured JSON, bypassing manual JSON-RPC framing:
```bash
# Shorthand arithmetic evaluation (auto-wrapped into {"expression": "..."})
python3 zeromcp-py/zeromcp.py --call eval_math "42 * 1024 / 8"

# Shorthand message echo (auto-wrapped into {"message": "..."})
python3 zeromcp-py/zeromcp.py --call echo "Hello ZeroMCP"

# Structured JSON input
python3 zeromcp-py/zeromcp.py --call eval_math '{"expression": "2 ** 16"}'
```

#### C. Interactive Developer REPL (`--repl` / `-i`)
Launches an interactive developer console for rapid ad-hoc testing:
```bash
python3 zeromcp-py/zeromcp.py --repl
# zeromcp> eval 100 * 25
# zeromcp> echo Test message
# zeromcp> sysinfo
# zeromcp> exit
```

#### D. Copy-Paste Client Config Generator (`--print-config`)
Automatically discovers the active Python binary and outputs a syntactically valid, ready-to-paste `claude_desktop_config.json` configuration block:
```bash
python3 zeromcp-py/zeromcp.py --print-config
```

#### E. Studio Bridge Server (`zeromcp-py/studio_bridge_server.py`)
Exposes local CLI bridges (Claude Opus 5.5, OpenAI Codex Astra) and SQLite sports databases as standard ZeroMCP tools over stdio:
```bash
python3 zeromcp-py/studio_bridge_server.py
```

---

## 3. Client Integration Profiles

> [!IMPORTANT]
> **Client Integration Status & Validation Scope**: The configuration templates below provide illustrative configuration profiles derived from official vendor specifications: [Claude Desktop MCP User Guide](https://modelcontextprotocol.io/quickstart/user), [Cursor MCP Documentation](https://docs.cursor.com/context/model-context-protocol), and [VS Code v1.99 Release Notes (Agent Mode & MCP)](https://code.visualstudio.com/updates/v1_99). Client versions (such as Cursor v0.46+ and VS Code v1.99+) serve as illustrative compatibility baselines rather than independently certified test targets. Standalone wire protocol and tool execution conformance are verified via the 40-check automated regression test suite; desktop UI process integration in specific client environments depends on local client host process management and is not verified by headless CLI test runners.
>
> **GUI Application PATH Resolution**: Desktop GUI applications (such as Claude Desktop and Cursor) launch in an isolated session that may not inherit shell profile configurations (`~/.zshrc`, `~/.bashrc`, Homebrew, nvm). **Always specify absolute paths** to binaries (`which node`, `which python3`) to prevent `spawn ENOENT` errors.
> * **macOS Apple Silicon**: Homebrew binaries are installed to `/opt/homebrew/bin/`.
> * **macOS Intel**: Homebrew binaries are installed to `/usr/local/bin/`.
> * **Windows Backslash Escaping**: In JSON configuration files on Windows, all file paths MUST use double-escaped backslashes (`\\`) or forward slashes (`/`). Pasting single unescaped backslashes (`\`) creates invalid JSON escape sequences that break client configuration loaders.
> * Replace `<PATH_TO_ZEROMCP>` in the templates below with the absolute path to your ZeroMCP installation.

### 3.1 Claude Desktop Configuration
Configuration file locations:
* **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
* **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

#### macOS Template (Apple Silicon):
```json
{
  "mcpServers": {
    "zeromcp-python": {
      "command": "/opt/homebrew/bin/python3",
      "args": ["<PATH_TO_ZEROMCP>/zeromcp-py/zeromcp.py"]
    },
    "zeromcp-typescript": {
      "command": "/opt/homebrew/bin/node",
      "args": [
        "--experimental-strip-types",
        "<PATH_TO_ZEROMCP>/zeromcp-ts/zeromcp.ts"
      ]
    }
  }
}
```

#### Windows Configuration (Concrete Escaped Path Example):
```json
{
  "mcpServers": {
    "zeromcp-python": {
      "command": "C:\\Python312\\python.exe",
      "args": ["C:\\Users\\Username\\Projects\\ZeroMCP\\zeromcp-py\\zeromcp.py"]
    },
    "zeromcp-rust": {
      "command": "C:\\Users\\Username\\Projects\\ZeroMCP\\zeromcp-rs\\target\\release\\zeromcp.exe",
      "args": []
    }
  }
}
```
*(Tip: On Windows, run `where.exe python` in PowerShell or CMD to find your Python interpreter path, e.g. `%LOCALAPPDATA%\Programs\Python\Python312\python.exe` or `C:\Python312\python.exe`. When updating paths in the template, remember to double every backslash: `C:\\...`).*

---

### 3.2 Cursor IDE Integration (v0.45+ / v0.46+)
Requires Cursor **v0.45+** (which introduced Model Context Protocol support) or **v0.46+** (which added workspace `.cursor/mcp.json` support; alternatively configure via Settings > Features > MCP). In your project workspace root, add `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "zeromcp": {
      "type": "stdio",
      "command": "<ABSOLUTE_PYTHON_PATH>",
      "args": ["<PATH_TO_ZEROMCP>/zeromcp-py/zeromcp.py"]
    }
  }
}
```
*(Tip: Run `which python3` on macOS/Linux or `where.exe python` on Windows to obtain `<ABSOLUTE_PYTHON_PATH>`).*

---

### 3.3 Native VS Code Integration (v1.99+)
Requires VS Code **1.99+** with GitHub Copilot Chat in **Agent Mode** (which introduced native Model Context Protocol server support via `.vscode/mcp.json`). In your workspace root:
```json
{
  "servers": {
    "zeromcp": {
      "type": "stdio",
      "command": "<ABSOLUTE_PYTHON_PATH>",
      "args": ["<PATH_TO_ZEROMCP>/zeromcp-py/zeromcp.py"]
    }
  }
}
```
*(Tip: Run `which python3` on macOS/Linux or `where.exe python` on Windows to obtain `<ABSOLUTE_PYTHON_PATH>`).*

---

### 3.4 Docker Container Execution
The included `Dockerfile` packages the zero-dependency Python reference implementation. Note: `docker build` must be run from `` within a cloned repository (the Dockerfile is not packaged in standalone release archives; for reproducible deployments, `python:3.12-alpine` can be pinned to an immutable image digest `python:3.12-alpine@sha256:...`). Because ZeroMCP communicates via standard I/O pipes, containers **must** be launched interactively with `-i` to keep standard input open:

```bash
# Build the container image from the repository directory containing Dockerfile
docker build -t zeromcp:1.0.0-rc1 .

# Launch container interactively over stdio (hardened with non-root user, dropped capabilities, no-new-privileges, zero networking, and read-only filesystem)
docker run -i --rm \
  --user 65534:65534 \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --network none \
  --read-only \
  zeromcp:1.0.0-rc1
```

Claude Desktop configuration for Docker:
*(Tip: On macOS, Docker Desktop installs the CLI to `/usr/local/bin/docker` or per-user at `~/.docker/bin/docker`. Run `which docker` to verify your path. Prerequisite: You MUST build the image first using `docker build -t zeromcp:1.0.0-rc1 .` from the repository root before launching via Claude Desktop).*
```json
{
  "mcpServers": {
    "zeromcp-docker": {
      "command": "/usr/local/bin/docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "--user", "65534:65534",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--network", "none",
        "--read-only",
        "zeromcp:1.0.0-rc1"
      ]
    }
  }
}
```

---

### 3.5 WebAssembly / WASI Client Integration Profiles

To launch the WebAssembly module (`zeromcp.wasm`) from desktop AI agents (Claude Desktop, Cursor, VS Code), configure the client to execute a standalone WASI runtime (such as `wasmtime`) or Node.js with the `node_wasi_runner.mjs` wrapper.

#### Claude Desktop Configuration (Wasmtime Runtime):
*(Tip: Run `which wasmtime` to find your absolute runtime path, e.g. `/usr/local/bin/wasmtime` or `~/.wasmtime/bin/wasmtime`).*
```json
{
  "mcpServers": {
    "zeromcp-wasi": {
      "command": "/usr/local/bin/wasmtime",
      "args": [
        "run",
        "<PATH_TO_ZEROMCP>/zeromcp-rs/target/wasm32-wasip1/release/zeromcp.wasm"
      ]
    }
  }
}
```

#### Claude Desktop Configuration (Node.js WASI Runner):
**Option A: Repository Clone:**
```json
{
  "mcpServers": {
    "zeromcp-wasi-node": {
      "command": "/usr/local/bin/node",
      "args": [
        "<PATH_TO_ZEROMCP>/scripts/node_wasi_runner.mjs",
        "<PATH_TO_ZEROMCP>/zeromcp-rs/target/wasm32-wasip1/release/zeromcp.wasm"
      ]
    }
  }
}
```

**Option B: Downloaded Release Package (colocated in download folder):**
```json
{
  "mcpServers": {
    "zeromcp-wasi-node": {
      "command": "/usr/local/bin/node",
      "args": [
        "<DOWNLOAD_PATH>/node_wasi_runner.mjs",
        "<DOWNLOAD_PATH>/zeromcp.wasm"
      ]
    }
  }
}
```

#### Cursor IDE Integration (`.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "zeromcp-wasi": {
      "type": "stdio",
      "command": "/usr/local/bin/wasmtime",
      "args": [
        "run",
        "<PATH_TO_ZEROMCP>/zeromcp-rs/target/wasm32-wasip1/release/zeromcp.wasm"
      ]
    }
  }
}
```

> [!NOTE]
> **Browser & Edge-Worker Deployment Boundary**: ZeroMCP communicates strictly via standard I/O streams (`stdin`/`stdout`). Browser and edge-worker environments do not provide POSIX standard I/O pipes by default; deploying `zeromcp.wasm` in web workers or serverless edge workers requires platform-specific WebAssembly WASI host shims and standard I/O virtualization, and requires additional integration beyond the stdio profiles documented here.

---

## 4. Built-in Tool Catalog & Specifications

### 4.1 Tool: `echo`
Echoes back the input message string verbatim.
* **Schema**:
  ```json
  {
    "type": "object",
    "properties": {
      "message": { "type": "string" }
    },
    "required": ["message"]
  }
  ```
* **Sample Request**:
  ```json
  {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"echo","arguments":{"message":"Hello ZeroMCP"}}}
  ```
* **Sample Response**:
  ```json
  {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"Hello ZeroMCP"}],"isError":false}}
  ```

---

### 4.2 Tool: `eval_math`
Safely evaluates arithmetic expressions using AST / recursive-descent parsing with zero dynamic `eval()`.
* **Numerical Precision**: IEEE-754 binary64 double precision (64-bit float). Exactly represents all integers up to $\pm 2^{53}$, with the conventional safe-integer range spanning $\pm(2^{53}-1)$ (`9,007,199,254,740,991`). Results are formatted as integer strings when whole (subject to the platform decimal formatting rules detailed below); arbitrary expression results remain subject to standard floating-point arithmetic rounding.
* **Portable Grammar**:
  - Operands: Non-negative integers and decimal fractions (`15`, `3.14`).
    * **Decimal Point Alignment**: Python and Rust permit leading/trailing decimal points (`.5`, `5.`), whereas TypeScript requires explicit leading and trailing digits (`0.5`). For universal portability, always include both digits (e.g. `0.5`).
    * **Leading Zero Prohibition**: Omit leading zeros on nonzero decimal integers (e.g. use `10`, never `010`). Python's AST parser rejects nonzero integers with leading zeros as syntax errors, whereas TypeScript and Rust accept them.
  - Binary Operators: `+`, `-`, `*`, `/`, `%`, `**` (canonical exponentiation across all variants).
  - Unary Operators: `+`, `-`.
  - Grouping: Parentheses `(...)`.
* **Error Handling**: Math errors return a standard tool result with `"isError": true`, enclosed in a complete JSON-RPC 2.0 response envelope:
  - **Division by Zero**: All variants return `{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"Error: Division by zero"}],"isError":true}}`.
  - **Modulo by Zero**: All variants return `{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"Error: Modulo by zero"}],"isError":true}}`.
  - **Complex or Non-Finite Powers (`0 ** -1` or `(-4) ** 0.5`)**: All variants return `{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"Error: Complex or non-finite numbers unsupported"}],"isError":true}}`.
  - **Python Numeric Literal & Operation Overflow Reporting**: In `zeromcp-py`, numeric overflow produces three distinct user-safe messages: (1) integer literals within the 1,024-character expression limit that exceed Python's internal string-to-float conversion bounds return `Error: int too large to convert to float`, (2) float literals exceeding IEEE-754 capacity (e.g. `1e400`) return `Error: Non-finite constant disallowed`, and (3) intermediate mathematical operations resulting in non-finite floats (e.g. `10 ** 400`) return `Error: Complex or non-finite numbers unsupported`. (In TypeScript and Rust, AST nodes evaluate subject to standard 64-bit float precision).
* **Cross-Language Precedence & Semantics**:
  - **Exponentiation (`**`)**: In Python, unary `-` binds looser than `**` (`-2**2 == -4`), whereas in TypeScript and Rust recursive-descent parsers, unary `-` binds to the base (`(-2)**2 == 4`). **Portability Rule**: Always use explicit parentheses `-(2**2)` or `(-2)**2`.
  - **Modulo (`%`)**: Python evaluates floor modulo (`-5 % 3 == 1`), while JavaScript and Rust evaluate truncated remainder (`-5 % 3 == -2`).
  - **Alternate Caret Operator**: TypeScript and Rust also accept `^` for exponentiation (`2^3 == 8`); Python strictly rejects `^` (as bitwise XOR is disallowed in its AST whitelist).
  - **Scientific Notation & Numeric Literals**: Python accepts scientific notation (`1e3`), hex (`0x10`), and underscores (`1_000`), whereas TypeScript and Rust accept standard base-10 numbers and decimal fractions. (Note: Numeric literals within math expressions are bounded by the 1,024-character expression length limit).
  - **Number Formatting**:
    - **Small Numbers**:
      * **Python**: Nonzero floats with absolute value $< 10^{-4}$ switch to scientific notation (e.g. `0.00001` formats as `1e-05`, while exact zero `0.0` formats as `"0"` via Python's integer-formatting branch).
      * **TypeScript**: Numbers switch to exponential notation only below $10^{-6}$ (e.g. `0.00001` formats as `"0.00001"`, `0.000001` formats as `"0.000001"`, and `0.0000001` switches to `"1e-7"`).
      * **Rust**: Default `f64` display emits fixed-point decimal format (e.g. `"0.0000001"`).
    - **Large Numbers**: For whole numbers $\ge 2^{53}$, Python formats float values with a trailing `.0` (e.g. `9007199254740992.0`) up to $10^{16}$ and switches to scientific notation (`1e+16`) at $\ge 10^{16}$; TypeScript formats decimal integers up to $10^{21}$ and switches to exponential notation at $\ge 10^{21}$; Rust formats full-precision `f64` decimals.
  - **Intermediate Evaluation**: Python eagerly validates that constants and intermediate node evaluations remain finite; TypeScript and Rust evaluate the AST/token tree and validate that the final computed value is finite. For example, for `1 / (10 ** 400)`, Python eagerly catches the float overflow in `10.0 ** 400.0` and returns `Error: Complex or non-finite numbers unsupported`, whereas TypeScript and Rust evaluate intermediate float overflow to `Infinity` and compute `1 / Infinity = 0`, returning `"0"`.
* **Security & Recursion Bounds**:
  - Max expression length is strictly **1,024 Unicode characters** across all variants. Exceeding this limit returns a tool error with `isError: true` (`"Error: Invalid expression"` in Python and TypeScript; `"Error: Expression too long"` in Rust).
  - Rejects non-finite values (`inf`, `nan`), complex numbers, and division by zero.
  - **Parser Recursion & Depth**: All three variants use recursive parsing (AST recursion in Python, recursive-descent parsing in TypeScript and Rust).
    * In TypeScript and Rust, recursion depth is bounded by the 1,024-character expression length (at most ~511 parenthetical levels or ~1,023 unary operators).
    * In Python, `ast.parse` enforces an internal parenthesis depth limit (~200 on Python 3.12, ~100 on Python 3.8); long unary chains without parentheses trigger `RecursionError` in `_eval`, returning a standard math error (`isError: true`).
    * **Portability Rule**: Keep parenthetical expressions within 50 levels of nesting.
* **Schema**:
  ```json
  {
    "type": "object",
    "properties": {
      "expression": { "type": "string" }
    },
    "required": ["expression"]
  }
  ```
* **Sample Request**:
  ```json
  {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"eval_math","arguments":{"expression":"100 + (25 * 4) / 2"}}}
  ```
* **Sample Response**:
  ```json
  {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"150"}],"isError":false}}
  ```

---

### 4.3 Tool: `system_info`
Returns host platform and runtime telemetry.
* **Schema**: `{"type": "object", "properties": {}}`
* **Variant Response Shapes**:
  - **Python**: `{"system": "Darwin", "machine": "arm64", "python": "3.12.2"}` (formatted with standard Python json spacing)
  - **TypeScript**: `{"platform":"darwin","release":"24.5.0","arch":"arm64","runtime":"node v24.22.1"}` (compact JSON; `runtime` reports `node vX`, `bun X`, or `deno X`)
  - **Rust Native**: `{"arch":"aarch64","family":"unix","os":"macos","runtime":"rust/native"}` (compact JSON with sorted alphabetical keys)
  - **WebAssembly WASI**: `{"arch":"wasm32","family":"","os":"wasi","runtime":"rust/wasm"}` (compact JSON with sorted alphabetical keys; Rust `std::env::consts::FAMILY` emits an empty string under `wasm32-wasip1`. Note that `zeromcp.wasm` reports `"serverInfo": {"name": "zeromcp-rs", "version": "1.0.0-rc1"}`).
* **Security & Information Exposure**: `system_info` transmits the host platform OS, kernel release (TypeScript), machine architecture, and interpreter runtime version to the calling client.
* **Sample Request**:
  ```json
  {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"system_info"}}
  ```
* **Sample Response**:
  ```json
  {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{\"system\": \"Darwin\", \"machine\": \"arm64\", \"python\": \"3.12.2\"}"}],"isError":false}}
  ```

---

## 5. Raw Wire Protocol & Lifecycle Handshake

All ZeroMCP implementations enforce the MCP `2024-11-05` lifecycle over newline-delimited JSON-RPC 2.0. Note that ZeroMCP communicates over bidirectional stdio, with sequential request processing.

> [!WARNING]
> **Mandatory Specification Exception: Tool Rate Limiting**: The MCP 2024-11-05 Tools Security specification mandates that servers *MUST rate limit tool invocations* without normative exemption for dedicated local or single-client processes. Standalone ZeroMCP servers across all four implementation variants (Python, TypeScript, Rust Native, and WebAssembly WASI) intentionally omit an embedded rate limiter. Consequently, standalone ZeroMCP has a documented normative specification exception.
>
> Wrapping the server in an external rate-limiting supervisor addresses the documented rate-limiting exception; full conformance requires separate verification. Because ZeroMCP communicates over standard I/O pipes (stdio), generic HTTP reverse proxies cannot directly supervise this protocol. Instead, deployments requiring rate-limiting supervision should employ a stdio-aware stream proxy that parses inbound JSON-RPC frames and throttles individual `tools/call` invocations (including invocations embedded within batch requests).
>
> The reference stdio rate-limiting supervisor is provided in `zeromcp_stdio_limiter.py` (packaged in release assets and available at `scripts/zeromcp_stdio_limiter.py`, compatible with Python 3.10+ and legacy 3.8+, verified via `tests/test_stdio_limiter.py` with 24 regression tests; defaults to `--rate 10.0` calls/sec and `--burst 20.0`, propagating the child process exit code upon termination) that enforces:
> - **Bounded Ingestion**: Caps inbound frames at 10 MiB using bounded stream reads (`bounded_readline`), draining oversized lines before emitting `-32700 Frame exceeded maximum allowed size`.
> - **Batch Size Limit Enforcement**: Immediately rejects batches exceeding 100 items with `-32600 Batch size exceeds maximum of 100` before filtering or processing.
> - **Strict JSON Ingestion**: Enforces strict JSON syntax (`parse_constant` disallowing `NaN`, `Infinity`, and `-Infinity`); non-standard constants pass through to the child to trigger canonical `-32700 Parse error` responses rather than being parsed permissively.
> - **Request Envelope & ID Validation**: Validates JSON-RPC 2.0 request structure and ID types (requiring string or integer IDs, including integer-valued floats such as `1001.0` accepted by JavaScript runtimes) prior to generating synthetic `-32000` rate limit errors. Malformed envelopes, non-integer floats (e.g. `1.5`), or invalid ID types are forwarded directly to the child server to return canonical `-32600 Invalid Request` errors (preserving valid request IDs when present, e.g. on invalid `jsonrpc` version or malformed `params`, and returning `"id": null` when the ID itself is invalid, omitted, or unparseable per JSON-RPC 2.0 §5). Note that because the supervisor operates as an external language-agnostic stream proxy without inspecting child ASTs or interpreter type systems, requests with integer-valued float IDs (e.g. `1001.0`) receive synthetic `-32000` rate limit errors when throttled, whereas an unthrottled request forwarded to Python or Rust will receive the child's `-32600 Invalid id` rejection; throttling takes precedence over variant-specific ID validation.
> - **Strict Notification Silence & Filtering**: Throttled notifications without an `id` field are suppressed silently without emitting response bytes, adhering to JSON-RPC 2.0 §4.1; throttled notifications are stripped from forwarded batch payloads so that throttled calls never reach the child process.
> - **Batch Response Aggregation & Surrogate-Safe Unicode Preservation**: Batch requests containing throttled tool invocations aggregate synthetic errors with child responses (including invalid members such as primitive values or requests with `id: null`), returning a single merged JSON array response in original request order. All forwarded batch lines serialize with compact delimiters `separators=(',', ':')`, `ensure_ascii=False`, and `backslashreplace` encoding (preserving lone surrogate escapes `\ud800` as valid JSON ASCII sequences rather than emitting invalid UTF-8 bytes). If the child process emits a single whole-frame error (e.g. `-32700`), the supervisor broadcasts that error across all forwarded items restoring their original request IDs so that clients can correlate each response in the batch.
> - **Forwarded Frame Size & Serialization Expansion Guard**: When rewriting mixed batches, reserialization may expand certain scientific notation floats (e.g. `1e15` expanding to `1000000000000000.0`). The supervisor checks forwarded payload lengths against the 10 MiB limit before writing to child stdin, emitting `-32700 Frame exceeded maximum allowed size` if reserialization expansion exceeds transport limits.
> - **Clean EOF Drain & Reader Thread Synchronization**: When the client closes standard input (EOF), the supervisor closes child stdin and waits for its reader thread (`t.join()`) to drain all pending response bytes from the child's stdout before waiting on child exit (`proc.wait()`), guaranteeing large response payloads in transit are fully emitted before the supervisor terminates.
> - **Thread-Safe Write Serialization**: Writes to standard output across reader and supervisor threads are synchronized via a shared concurrency lock.
>
> ```bash
> # Supervise ZeroMCP Python with 30 tool calls/sec and burst capacity of 10:
> python3 scripts/zeromcp_stdio_limiter.py --rate 30 --burst 10 -- python3 zeromcp-py/zeromcp.py
>
> # Supervise WebAssembly WASI via wasmtime:
> python3 scripts/zeromcp_stdio_limiter.py --rate 30 --burst 10 -- wasmtime zeromcp-rs/target/wasm32-wasip1/release/zeromcp.wasm
> ```

> [!IMPORTANT]
> **Session-Unique Request IDs**: Per MCP 2024-11-05 message requirements, clients MUST ensure request IDs are unique throughout an active session. While ZeroMCP echoes incoming IDs statelessly without maintaining historical session state, client applications and test harnesses must preserve ID uniqueness across requests.

### Complete Handshake Sequence

#### Step 1: Client sends `initialize`
```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test-client","version":"1.0.0"}}}
```
**Server Response**:
```json
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{"listChanged":false}},"serverInfo":{"name":"zeromcp-py","version":"1.0.0-rc1"}}}
```
*(Note: Per MCP 2024-11-05 specification, ZeroMCP negotiates and returns `"protocolVersion": "2024-11-05"`. If the calling client cannot support this version, it SHOULD disconnect (per MCP 2024-11-05 lifecycle specification). `serverInfo.name` reports `zeromcp-py`, `zeromcp-ts`, or `zeromcp-rs` (the WebAssembly module `zeromcp.wasm` shares the Rust `zeromcp-rs` server identifier). Sending `initialize` a second time is rejected with `-32600 Server already initialized`.)*

#### Step 2: Client sends `notifications/initialized`
```json
{"jsonrpc":"2.0","method":"notifications/initialized"}
```
*(Server transitions from `NEGOTIATED` to `INITIALIZED`. Per JSON-RPC 2.0 §4.1, NO response is emitted for notifications.)*

#### Step 3: Client discovers tools via `tools/list`
```json
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
```
**Server Response**:
```json
{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"echo","description":"Echoes back the input message","inputSchema":{"type":"object","properties":{"message":{"type":"string"}},"required":["message"]}},{"name":"eval_math","description":"Safely evaluates IEEE-754 double precision arithmetic (+, -, *, /, %, **)","inputSchema":{"type":"object","properties":{"expression":{"type":"string"}},"required":["expression"]}},{"name":"system_info","description":"Returns host platform, architecture, and runtime telemetry","inputSchema":{"type":"object","properties":{}}}]}}
```

#### Step 4: Diagnostic Heartbeat via `ping`
```json
{"jsonrpc":"2.0","id":3,"method":"ping"}
```
**Server Response**:
```json
{"jsonrpc":"2.0","id":3,"result":{}}
```

#### Lifecycle State Enforcement
Per ZeroMCP enforcement policy (aligning with MCP 2024-11-05 lifecycle guidance that clients SHOULD NOT issue tool requests during initialization), calling `tools/list` or `tools/call` before completing both `initialize` and `notifications/initialized` returns:
```json
{"jsonrpc":"2.0","id":4,"error":{"code":-32600,"message":"Server uninitialized: initialize handshake required"}}
```

---

## 6. Security Model & Resource Bounds

### MCP 2024-11-05 Tools Security Specification Matrix
The Model Context Protocol `2024-11-05` Tools Security specification specifies four normative server requirements:

| Security Requirement (MCP 2024-11-05) | Server Normative Level | ZeroMCP Implementation Status | Architecture & Enforcement Mechanism |
| :--- | :---: | :---: | :--- |
| **Input Validation** | **MUST** | **Conformant** | Strict input validation across all variants. Parameter types, missing required arguments, non-object arguments, prototype pollution (`toString`), and arithmetic grammar/lengths ($\le 1,024$ chars) are validated before dispatch. |
| **Access Controls** | **MUST** | **Unverified / Delegated to Host** | Server binaries execute under ambient OS permissions of the launching process with zero outbound network calls and read-only system telemetry. Operating system sandboxing, capability restrictions, and file access controls are unverified and delegated to host process managers (e.g. Docker, Wasmtime, systemd). |
| **Tool Rate Limiting** | **MUST** | **Documented Exception (Addressed via supervisor)** | Standalone binaries intentionally omit embedded rate limiting as an architectural choice to maintain lean, zero-third-party-dependency single-file implementations (strictly $<200$ LOC in Python reference). External stream supervisor (`zeromcp_stdio_limiter.py`) enforces strict token-bucket rate limiting on `tools/call`. |
| **Output Sanitization** | **MUST** | **Scoped Implementation (Verbatim text passthrough)** | All tool errors and runtime exceptions return standardized, user-safe error strings (`Error: Division by zero`) without leaking raw interpreter stack traces. Tool return values (such as `echo`) pass caller text through verbatim without semantic sanitization. |

1. **Process Privilege Boundary**: ZeroMCP binaries execute under the ambient operating system permissions of the calling process. AST and schema validation protect against arbitrary code execution, but do not replace OS containerization.
2. **Network Isolation**: All ZeroMCP servers communicate exclusively through standard I/O pipes. Built-in tools make **zero outbound network requests**.
3. **Transport Wire Isolation**:
   - In Python, file descriptor `1` is duplicated to a private descriptor via `os.dup(1)`, and `fd 1` is redirected to `stderr` via `os.dup2(2, 1)`. This protects the wire against accidental standard output writes (such as third-party library `print()` calls or standard C-extension `printf` statements) occurring after transport setup. It does not provide security isolation against adversarial code executing within the same process (which could manipulate file descriptors or access the private wire stream directly).
   - In TypeScript and Rust, all operational diagnostics and errors route to standard error. Developers adding custom tools should ensure standard output is not written to directly.
4. **Inbound Framing & Buffer Defense**:
   ZeroMCP enforces inbound framing caps across all transports, rejecting payloads exceeding transport bounds with `-32700 Frame exceeded maximum allowed size`. Exact maximum accepted byte counts, ingestion memory models, and flush behaviors are detailed in the comparison matrix below:

| Transport Variant | Delimiter Policy | Max Accepted Content | Inbound Wire Frame Size | Input Memory Model | Output Transport Model |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Python (`zeromcp-py`)** | Splits on `\n` (`sys.stdin.buffer.readline`) | 10,485,759 bytes (LF/EOF) / 10,485,758 bytes (CRLF); exactly 10,485,760 unterminated bytes at EOF are rejected | $\le 10,485,760$ bytes | Strictly bounded (bounded stream read up to 10 MiB) | Buffered stdio flush per frame (`write` + `flush`) |
| **Rust Native (`zeromcp-rs`)** | Splits on `\n` (`tokio::io::AsyncBufReadExt::read_until`) | 10,485,759 bytes (LF) / 10,485,758 bytes (CRLF); accepts 10,485,760 bytes at EOF | $\le 10,485,760$ bytes | Bounded stream read via `take()`; buffers allocate working parse capacity | Tokio current-thread scheduler (`current_thread`) flush per frame (`write_all` + `flush().await`) |
| **WebAssembly WASI (`zeromcp.wasm`)** | Splits on `\n` (`std::io::BufRead::read_until`) | 10,485,759 bytes (LF) / 10,485,758 bytes (CRLF); accepts 10,485,760 bytes at EOF | $\le 10,485,760$ bytes | Bounded stream read via `take()`; buffers allocate working parse capacity | Synchronous stdio flush per frame (`write_all` + `flush()`) |
| **Stdio Supervisor (`zeromcp_stdio_limiter.py`)** | Splits on `\n` (`stream.readline(10485761)`) | 10,485,759 bytes (LF) / 10,485,758 bytes (CRLF); accepts 10,485,760 bytes at EOF | $\le 10,485,760$ bytes | Strictly bounded (`stream.readline(MAX_FRAME_SIZE + 1)` reads up to 10,485,761 bytes; drains oversized lines) | Serialized stdio flush via shared concurrency lock |
| **TypeScript (`zeromcp-ts`)** | `node:readline` strips `\r`, `\n`, or `\r\n` | 10,485,759 bytes | Up to 10,485,761 bytes (with CRLF) | Acceptance gate (no bounded pre-dispatch buffer; heap buffers chunks until delimiter) | Stream backpressure (`activeRl.pause()` / `'drain'`) |

*(Note: The exact byte counts in the table above represent source-derived analytical boundaries based on parser delimiters and buffer gates; the automated regression suite asserts boundary rejection on request payloads > 10 MiB).*

   - **TypeScript Pre-Dispatch Ingestion Memory Disclosure**: In `zeromcp-ts`, Node's `node:readline` buffers incoming byte chunks in process memory until a delimiter is received. Consequently, TypeScript does **not** enforce a bounded memory ceiling during line ingestion; an unterminated stream without a newline can consume heap until process exhaustion. For untrusted clients, place a streaming proxy in front of the TypeScript server.
   - **TypeScript Output Buffering & Stream Backpressure**: In `zeromcp-ts`, `emitFrame()` monitors `process.stdout.write()`. When `write()` returns `false` (indicating Node's internal stream buffer is full because the client is consuming responses slowly), the server pauses the input line reader (`activeRl.pause()`) and resumes only after receiving standard output's `'drain'` event. While this prevents unbounded response heap accumulation during standard lockstep exchanges, clients pipelining multiple rapid requests without reading stdout can still cause internal buffering before backpressure propagates through the event loop. In contrast, Python flushes directly via standard library buffered I/O, Rust Native flushes per frame asynchronously with Tokio, and WebAssembly WASI flushes per frame synchronously with standard library `std::io::Write::flush()`.
   - **Oversized Stream Draining & Availability Boundary**: When an oversized line is detected, Python, Rust Native, and WebAssembly WASI drain the remainder of the oversized line until a newline delimiter or EOF is reached before emitting `-32700 Frame exceeded maximum allowed size`. Consequently, an untrusted client transmitting an unbounded or stalled stream without newlines will hold the process busy draining bytes; deployments should use an external stream timeout or supervising transport proxy to enforce connection liveness.
   - **Lone Carriage Returns (`\r`) in TypeScript**: Node.js `node:readline` treats lone `\r` characters as line endings. Bare `\r` within JSON whitespace splits the frame into multiple chunks, resulting in `-32700 Parse error` in TypeScript (whereas Python and Rust split strictly on `\n`).
   - Maximum batch request size is capped at **100 items** (`MAX_BATCH = 100`). Batches exceeding this are rejected with `-32600 Batch size exceeds maximum of 100`.
   - Arithmetic expressions are capped at **1,024 Unicode characters**.
5. **JSON-RPC Error Taxonomy**:
   - `-32700 Parse error`: Malformed JSON, frames exceeding transport bounds, or invalid UTF-8 bytes. (Note: Invalid UTF-8 byte handling is variant-specific: Python and Rust strictly decode bytes and reject invalid UTF-8 sequences with code `-32700` (Python emits a message beginning `UTF-8 decode error: ...`; Rust emits `Parse error: ...`). In TypeScript, Node.js streams decode invalid byte sequences into JavaScript strings using replacement characters (`\uFFFD`); consequently, invalid UTF-8 bytes occurring within JSON quoted strings are decoded to U+FFFD characters and parsed successfully by `JSON.parse` (reaching tool handlers rather than returning `-32700`), whereas invalid bytes outside strings typically trigger `-32700 Parse error` during JSON tokenization. Furthermore, syntactically valid JSON numbers overflowing IEEE-754 range, such as `1e400`, produce `-32700 Parse error` in Python and Rust (via serde_json), whereas JavaScript `JSON.parse` evaluates them to `Infinity`. Python wire-level integer token limit: In `zeromcp-py`, `json.loads` uses a custom `parse_int` hook capping raw JSON integer tokens (including sign) at 4,300 characters, returning `-32700 Parse error: Int limit` on larger numeric tokens, whereas TypeScript and Rust parse numbers subject to standard 64-bit float precision).
   - `-32600 Invalid Request`: Malformed envelope, boolean/null/fractional-float/object IDs, null params, empty batch `[]` (returns `-32600 Invalid Request: empty batch`), batch overflow (>100 items), duplicate `initialize`, or `tools/*` calls sent before `initialize`. String and integer IDs are valid. (Note: `"id": 1.0` is rejected as a float by Python and Rust, but accepted by TypeScript because `JSON.parse` evaluates `1.0` and `1` identically as integer numbers in JavaScript. In Rust, `serde_json` parses integer numbers into `i64` or `u64`. When an integer literal exceeds 64-bit integer capacity (e.g. `18446744073709551616`), `serde_json`'s default parser falls back to parsing it as a floating-point `f64`; because this produces a float rather than an integer, ZeroMCP's ID validation rejects it with `-32600 Invalid Request`. Only numeric literals that exceed double-precision floating-point exponent range (e.g. `1e400`) cause `serde_json` parser range failure, returning `-32700 Parse error`. To ensure universal compatibility across all runtimes, client authors should use string IDs or integers within JavaScript safe integer bounds ($\pm(2^{53}-1)$)).
   - **Client Response Message Handling**: Inbound messages containing an `id` property alongside `result` or `error` without a `method` field (conforming to the syntax of a JSON-RPC response rather than a request or notification) are silently dropped without server response. Because ZeroMCP operates strictly as a server and issues no outbound requests to the client, unsolicited inbound responses have no correlation target and produce no output.
   - `-32601 Method not found`: Unrecognized method names (safely truncated to 64 Unicode characters in error messages).
   - `-32602 Invalid params`: Missing required tool properties, positional array arguments on MCP requests, or malformed initialize parameters.
   - `-32603 Internal error`: Unhandled server runtime exceptions. In Python and TypeScript, unhandled request dispatch or routing exceptions occurring outside tool execution are caught and returned as `-32603 Internal error`. When an executed tool raises an exception, it is caught and returned as a standard tool result with `isError: true` per MCP specification, NOT a `-32603` protocol error. In Rust (`zeromcp-rs`), anticipated arithmetic evaluation errors are returned as tool results with `isError: true` via `Result<Value, String>`; Rust does not implement dynamic panic catching (`catch_unwind`), so unexpected panics, serialization failures, or broken pipes terminate the process rather than emitting `-32603`.
   - **JSON Key Ordering**: Python and TypeScript preserve dictionary insertion order in JSON responses. In Rust (`zeromcp-rs`), serialized response and error structs (`JsonRpcResponse`, `JsonRpcError`) retain their Rust struct field declaration order, whereas dynamic `serde_json::Value` map objects (such as tool definitions in `tools/list` and transport-level error frames constructed with `json!`, e.g. `{"error":...,"id":null,"jsonrpc":"2.0"}`) sort keys in alphabetical order. Both orderings are semantically equivalent per JSON-RPC 2.0.
   - **JSON Nesting Depth Limit**: Rust's `serde_json` rejects JSON structures nested deeper than 128 levels with `-32700 Parse error`, while Python and TypeScript accept deeper nesting up to their process call-stack limits.
6. **WebAssembly WASI Sandbox Boundaries**: While WebAssembly bytecode executes inside a memory-isolated virtual machine, the practical security of WASI Preview 1 deployments depends strictly on host runtime configuration (e.g. granted directory preopens, environment variable inheritance, and system call filtering). Runtimes such as Node.js explicitly document that `node:wasi` does not provide a comprehensive security boundary for untrusted code and should not be relied upon for hostile multi-tenant isolation. Deployers should use hardened, capability-restricted engines (such as `wasmtime` with zero directory preopens and isolated memory limits) when executing untrusted workloads.

---

## 7. Troubleshooting & Diagnostics

### Capturing Server Logs
Because standard output is reserved strictly for JSON-RPC frames, all diagnostic logging and errors are emitted to standard error. To capture debug logs:
```bash
python3 zeromcp-py/zeromcp.py 2> zeromcp_debug.log
```

### Common Issues & Resolutions

| Error / Symptom | Root Cause | Resolution |
| :--- | :--- | :--- |
| `initialize requires string protocolVersion, object capabilities, and object clientInfo` (`-32602`) | Missing or malformed handshake parameters. | Supply `protocolVersion: "2024-11-05"`, `capabilities: {}`, and `clientInfo: {name, version}`. |
| `clientInfo requires string name and version` (`-32602`) | `clientInfo` object missing `name` or `version` strings. | Pass `clientInfo: {name: "client-name", version: "1.0.0"}`. |
| `Server uninitialized: initialize handshake required` (`-32600`) | Calling `tools/list` or `tools/call` before handshake. | Ensure client sends `initialize` followed by `notifications/initialized` before calling tools. |
| `Tool '...' not found` (`-32602`) | Tool name does not match any registered tool in TOOLS or accesses an inherited prototype property (e.g. `toString`). | Call `tools/list` to inspect registered tool names. |
| `Tool arguments must be an object` (`-32602`) | Tool arguments passed as a non-object type (e.g. JSON array, number, boolean, or string). | Pass arguments as a key-value JSON object `{}`. Note: The MCP specification defines arguments as an optional object (allowing clients to omit the field entirely or supply `{}`). ZeroMCP accepts `"arguments": null` as a permissive implementation extension for client robustness; interoperable client implementations should omit the property or pass `{}`. |
| `Missing required property 'message'` (`-32602`) | Missing mandatory tool argument. | Pass required property matching the tool's inputSchema. |
| `Property 'message' must be a string` (`-32602`) | Tool property passed with incorrect type. | Ensure argument values match schema types. |
| `Frame exceeded maximum allowed size` (`-32700`) | Request payload exceeded 10 MiB limit. | Break large payloads into smaller independent requests. |
| `Batch size exceeds maximum of 100` (`-32600`) | Batch request exceeded 100 items. | Split batches into chunks of 100 or fewer requests. |
| `Invalid Request: empty batch` (`-32600`) | Client sent an empty JSON array `[]`. | Send a non-empty array containing 1 to 100 JSON-RPC request objects. |
| Inbound response frame produces no server output | Client transmitted a JSON-RPC response frame (with `result` or `error` rather than `method`). | ZeroMCP processes requests and notifications; unsolicited incoming responses are silently dropped because the server issues no requests to the client. |
| `spawn node ENOENT` / `spawn python3 ENOENT` | Desktop GUI client (Claude/Cursor) cannot find binary in minimal PATH. | Specify absolute binary paths (`/usr/local/bin/node` or `/opt/homebrew/bin/node`) in configuration JSON. |
| macOS pop-up: "The python3 command requires the command line developer tools" on launch | `/usr/bin/python3` shim requires Xcode CLT on macOS. | Use a dedicated Python interpreter (`/opt/homebrew/bin/python3` or official python.org installer). |
| Windows opens Microsoft Store when running `python` | Windows App Execution Alias stub intercepts uninstalled/unlinked Python paths. | Disable App Installer aliases in Windows Settings > Apps > Advanced app settings > App execution aliases, or specify absolute path (`%LOCALAPPDATA%\Programs\Python\Python312\python.exe`). |
| TypeScript server auto-starts or terminates unexpectedly upon module import | `checkDirectRun()` tests: (1) `import.meta.main` in Bun/Deno, (2) `metaPath.endsWith(rawArg)` in Node, (3) entry-point path suffix ending in `/zeromcp.ts` or `/zeromcp.js`, or (4) bare `process.argv[1]` equaling `"zeromcp.ts"` or `"zeromcp.js"`. If an importing application's entrypoint matches one of these conditions, auto-start triggers. | Set `ZEROMCP_NO_AUTO_START=1` in the process environment prior to importing, or ensure the importing application's entrypoint path does not match these conditions. |
| Process terminates on client disconnect (exit code 0 or 1) | When the client closes standard input (EOF), Python reaches normal interpreter exit (exit code 0) upon reading empty bytes (`""`), TypeScript exits with 0 upon `rl.close()`, and Rust breaks its read loop and exits with 0. If standard output is severed (broken pipe) during a write, Python calls `os._exit(0)` from its broken pipe handler, TypeScript terminates via its asynchronous `process.stdout` `'error'` listener (`EPIPE`), and Rust exits with code 1. | Standard Unix stdio lifecycle. Servers terminate on client EOF or severed pipe. Note that regression testing exercises stdin EOF with stdout intact; testing severed output pipes requires OS-level broken pipe injection. |

---

## 8. Conformance & Verification Suite

ZeroMCP maintains an automated regression suite exercising 39 protocol vectors, including two notification-silence checks, plus one EOF termination check (40 total checks):
```bash
# Run standalone server conformance suite across available runtimes
python3 tests/conformance_test_runner.py

# Run dedicated stdio rate-limiting supervisor test suite (24 regression tests)
python3 tests/test_stdio_limiter.py
```

### Automated Regression Matrix (40 Protocol Checks) & Scope
* **Lifecycle State Gating**: Validates two-phase lifecycle gate rejection: pre-handshake (`UNINITIALIZED`) rejection of `tools/list` and `tools/call` with `-32600`, and intermediate (`NEGOTIATED`) rejection of `tools/call` prior to `notifications/initialized`.
* **Handshake Negotiation**: Asserts standard `2024-11-05` protocol version response, capabilities negotiation, duplicate `initialize` rejection, and presence of `tools` capability; note that regression checks do not test alternative or deprecated protocol version fallback.
* **Notification Response Silence**: Confirms zero response leakage on valid known notifications (`notifications/initialized`) and tolerant handling of non-conforming JSON-RPC notifications with positional array parameters (`notifications/progress`), per JSON-RPC 2.0 §4.1 (syntactically invalid envelopes or malformed JSON return standard JSON-RPC error responses with `id: null`). Note that while JSON-RPC 2.0 permits array parameters, MCP 2024-11-05 standard notifications define parameters as objects; ZeroMCP tolerates both silently.
* **Heartbeat & Diagnostics**: Validates `ping` response.
* **Tool Discovery & Execution**: Asserts that `tools/list` returns a valid JSON-RPC 2.0 envelope containing a tools array of at least 3 tools, with each entry possessing `name`, `description`, and `inputSchema` fields, including `echo`, `eval_math`, and `system_info` (testing regression presence rather than exhaustive JSONSchema AST equivalence across arbitrary user schemas). Tests tool execution for `echo`, `eval_math`, and `system_info` (parsing the returned JSON string and asserting a dictionary with at least 3 system telemetry keys), as well as validating tolerance for `"arguments": null`.
* **Arithmetic Safety & Bounds**: Tests safe math evaluation, division by zero (`isError: true`), modulo by zero (`isError: true`), complex numbers (`(-8)**0.5`), negative zero power (`0 ** -1`), float overflow (`10 ** 400`), syntax errors (`1 2`), and the 1,024-character expression limit.
* **Batch Processing**: Tests valid batch execution, empty batch rejection (`-32600 Invalid Request: empty batch`), and rejects batches exceeding 100 items (`-32600 Batch size exceeds maximum of 100`).
* **Error Taxonomy & Prototype Defense**: Asserts `-32700` (malformed JSON, NaN, and Infinity constants), `-32600` (bad ID types, boolean IDs, null IDs, float IDs, null params, missing `jsonrpc` field), `-32601` (method not found with 64-char safe truncation), and `-32602` (missing tool properties, wrong argument types like `"message": 42`, non-object tool arguments, and prototype property safety rejecting `toString`).
* **Wire Hygiene, Framing Boundaries & Clean Shutdown**: Validates leading whitespace tolerance before valid JSON, empty line tolerance, oversized frame boundary rejection (`>10 MiB` returns `-32700 Frame exceeded maximum allowed size`), per-test 5.0-second response-wait deadlines on stdout reads (terminating child processes if they fail to emit expected response frames within 5.0s), and clean process termination upon client stdin EOF (exit code 0).

> [!NOTE]
> **Runtime Coverage Scope & CI Verification**: The regression runner discovers runtimes sequentially: testing Python (verified locally on Python 3.12 passing 40/40 checks), selecting the first available JavaScript runtime (verifying `node -v` $\ge 22.7.0$ before invoking `--experimental-strip-types`, or falling back to Bun or Deno), testing Rust if `zeromcp` or `zeromcp.exe` is compiled in `zeromcp-rs/target/release/`, and testing WebAssembly if `wasmtime` and `zeromcp.wasm` are present. In local developer environments, missing runtimes are reported as skipped. In contrast, the GitHub Actions release workflow (`.github/workflows/zeromcp_release.yml`) provisions Python 3.12, Node 24, Rust (cross-compiled Linux x86_64 binary), and Wasmtime (installed in CI via the official installer script `curl https://wasmtime.dev/install.sh | bash`; production deployments may pin specific releases such as `v27.0.0`), and executes both `tests/test_stdio_limiter.py` (24 regression tests) and `conformance_test_runner.py --ci`. Under the `--ci` flag, the runner strictly fails if fewer than 4 execution targets are discovered and pass, providing automated verification across all 4 platforms before release asset publication. Standalone release binaries and WebAssembly modules are cross-compiled across 6 target platforms.

### Release Pipeline Supply Chain & Security Disclosures
* **Release Asset Signatures & macOS Gatekeeper**: Standalone release binaries and WebAssembly modules in `1.0.0-rc1` are unsigned. macOS users removing the quarantine attribute (`xattr -d com.apple.quarantine`) should verify SHA-256 checksums from `SHA256SUMS.txt`. The project roadmap commits to cryptographically signed releases (via Sigstore or GitHub Artifact Attestation) prior to the v1.0.0 General Availability (GA) release.
* **CI Dependency Pinning**: In the automated release workflow (`.github/workflows/zeromcp_release.yml`), third-party GitHub Actions are pinned to major version tags (alongside `dtolnay/rust-toolchain@stable`), Wasmtime is installed via the official shell installer script (`curl ... | bash`), and `cross` is installed from git via `cargo install cross --git https://github.com/cross-rs/cross`. Enterprise environments with strict supply-chain policies may fork the workflow to pin actions to immutable commit SHAs and pin toolchain compiler binaries.

---

## 9. Token-Efficiency Primitives: CAS Offloading & Command Distillation

In long-running agent workflows, bloated tool responses and raw terminal logs rapidly exhaust LLM context windows, inflate latency, and drive up API costs. ZeroMCP provides two pure-standard-library primitives to keep agent contexts ultra-compact.

### 9.1 Content-Addressed Storage (CAS) Payload Offloading

When an MCP tool produces a large response (default $\ge 4,096$ bytes, e.g. multi-megabyte log files, large AST dumps, or extensive tabular queries), ZeroMCP's CAS engine (`zeromcp_cas.py`):
1. Computes the deterministic SHA-256 hex digest of the raw content.
2. Atomically persists the raw payload into a sharded local storage directory (`~/.zeromcp/cas/<digest[:2]>/<digest>.txt`, with automatic sandboxed fallbacks to `./.zeromcp_cas` or `/tmp/zeromcp_cas`).
3. Returns a compact structural token pointer and preview summary to the agent context instead of dumping megabytes of text:

```
[ZeroMCP-CAS: Payload Offloaded to Content-Addressed Store]
• Reference: cas://sha256/9dffc3a69188b06069ee27335bcdae7a870cccb48115ab0b56fe7d18c59a3236
• Size: 7,443 bytes (~1,860 tokens saved)
• Local Path: /Users/jcvanocker/Antigravity_Workspace/.zeromcp_cas/9d/9dffc3a69188b06069ee27335bcdae7a870cccb48115ab0b56fe7d18c59a3236.txt
• Preview (Head):
  TOP_SECRET_LINE_1
  Payload content line data 1234567890...
• Preview (Tail):
  ... CRITICAL_KEYWORD_FIND_ME

💡 Content was offloaded to prevent context bloat. To inspect sections:
   - Tool 'cas_fetch': params {'hash': '9dffc3a6...', 'offset': 0, 'limit': 1000}
   - Tool 'cas_search': params {'hash': '9dffc3a6...', 'query': 'keyword'}
```

#### Built-in CAS Retrieval Tools
ZeroMCP exposes three zero-dependency tools for surgical inspection:
* **`cas_fetch`**: Retrieves a bounded slice of an offloaded payload by hash.
  * Parameters: `hash` (string, required), `offset` (integer, default `0`), `limit` (integer, default `4096`), `mode` (`"bytes"` or `"lines"`).
* **`cas_search`**: Searches inside an offloaded payload using substring or regex matching, returning line numbers and surrounding context without loading the entire file into the model's context.
  * Parameters: `hash` (string, required), `query` (string, required), `max_matches` (integer, default `10`), `context_lines` (integer, default `2`).
* **`cas_stats`**: Reports total stored blobs, aggregate byte size, and storage path.

#### Operator CLI Commands
Inspect and manage the local CAS store directly from the terminal:
```bash
# View storage metrics and blob count
python3 zeromcp-py/zeromcp.py --cas-stats

# Prune blobs older than N days (default: 7)
python3 zeromcp-py/zeromcp.py --cas-gc 7
```

#### Configuration Environment Variables
* `ZEROMCP_CAS_ENABLED`: `1` (enabled by default) or `0` (disabled).
* `ZEROMCP_CAS_THRESHOLD`: Threshold in bytes before offloading triggers (default: `4096`).
* `ZEROMCP_CAS_DIR`: Custom directory path for CAS blobs (default: `~/.zeromcp/cas`).

---

### 9.2 Command Output Distiller (`command_distiller.py`)

Terminal test runners and build systems emit massive volumes of passing-test spam, progress bars, and ANSI color codes that consume valuable context. `command_distiller.py` strips away noise and isolates failure signals:

* **`pytest` / `unittest`**: Strips passing test dots (`.`) and setup boilerplate; extracts exact failure headers, traceback assertions, and summary lines (typically **40–80% token reduction**).
* **`cargo test`**: Filters out compilation messages and passing tests; extracts panic assertions, failing test identifiers, and outcome tallies (typically **35–70% token reduction**).
* **`cargo build` / `cargo check`**: Suppresses dependency checking output; isolates `error[E...]` compiler diagnostics and file line markers.
* **`git status`**: Collapses verbose guidance text into a compact tree of modified, staged, and untracked files (typically **60–85% token reduction**).

#### Command Line Usage
```bash
# Execute and distill pytest
python3 zeromcp-py/command_distiller.py --cmd "pytest" --telemetry

# Execute and distill cargo test
python3 zeromcp-py/command_distiller.py --cmd "cargo test" --telemetry

# Pipe command output via stdin
git status | python3 zeromcp-py/command_distiller.py --type git_status
```

#### Python API Usage
```python
from command_distiller import distill_output, run_distilled

# Distill an existing output string
distilled_text, telemetry = distill_output("pytest", raw_pytest_stdout)
print(f"Saved {telemetry['savings_percent']}% context tokens!")

# Run and distill directly
return_code, distilled_text, telemetry = run_distilled(["cargo", "test"])
```

---

## 10. Fine-Grained Access Control (FGAC) & Token Delegation

ZeroMCP provides enterprise-grade Fine-Grained Access Control (FGAC) designed for zero-trust agent environments and multi-tenant gateways (such as HyperGate) with **zero third-party dependencies** and **sub-5 microsecond** evaluation latency.

### 10.1 Multi-Tenant Hierarchical Scope Parsing & Negation
Scopes use dot (`.`) and colon (`:`) interchangeably and support hierarchical wildcard matching and negation:

| Granted Scope | Allows | Blocks | Rationale |
| :--- | :--- | :--- | :--- |
| `["tools:*"]` | `tools:echo`, `tools:eval_math` | None | Full tool catalog access. |
| `["tools:*", "!tools:execute_shell"]` | `tools:echo`, `tools:eval_math` | `tools:execute_shell` | Negation (`!`) strictly overrides positive wildcards. |
| `["workspace.*"]` | `workspace.finance:read`, `workspace.billing` | `admin:*` | Hierarchical wildcard matches all nested segments. |
| `["workspace.*", "!workspace.secret:*"]` | `workspace.finance:read` | `workspace.secret:keys` | Granular restriction on sensitive sub-trees. |

### 10.2 Ephemeral Capability Token Generator (`zeromcp_token.py`)
Parent gateways (HyperGate) can mint short-lived HMAC-SHA256 capability tokens for subagents without contacting central identity providers:

#### Command Line Tool
```bash
# Mint a 5-minute capability token for a subagent
python3 zeromcp-py/zeromcp_token.py --mint \
  --secret "my-cluster-shared-secret" \
  --scopes "tools:*,!tools:execute_shell" \
  --sub "subagent-finance-01" \
  --ttl 300

# Verify an existing token
python3 zeromcp-py/zeromcp_token.py --verify "<TOKEN_STRING>" \
  --secret "my-cluster-shared-secret"

# Inspect token claims without verifying signature
python3 zeromcp-py/zeromcp_token.py --inspect "<TOKEN_STRING>"
```

#### Python Programmatic API
```python
from zeromcp_token import mint_token, verify_token

# Mint ephemeral capability token
token = mint_token(
    secret="my-cluster-shared-secret",
    scopes=["workspace.finance:*", "!workspace.finance:admin"],
    sub="subagent-analyst",
    ttl_seconds=300,
    iss="hypergate",
    aud="zeromcp-cluster"
)

# Verify token in gateway middleware (< 5 microseconds)
claims = verify_token(token, secret="my-cluster-shared-secret", expected_aud="zeromcp-cluster")
print("Granted scopes:", claims["scopes"])
```

### 10.3 Handshake Authentication Lifecycle
Enable authentication by setting `ZEROMCP_AUTH_SECRET` on the ZeroMCP server process:
```bash
export ZEROMCP_AUTH_SECRET="my-cluster-shared-secret"
python3 zeromcp-py/zeromcp.py
```

Clients pass the capability token in the `initialize` handshake params:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {
      "name": "hypergate-subagent",
      "version": "1.0",
      "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IlpNQ1AifQ..."
    }
  }
}
```

* **Authentication Success**: The handshake returns `protocolVersion: "2024-11-05"` and pins the token's granted scopes to the active session.
* **Authentication Failure**: If the token is missing, expired, or tampered with, the server rejects the connection with `-32602` (`Authentication required / Authentication failed`).
* **Tool Call Enforcement**: Any subsequent `tools/call` for an unauthorized tool is rejected with `-32602` (`Forbidden: client scopes ... do not satisfy required scopes`).
* **Discovery Filtering**: Calling `tools/list` with `{"filter_unauthorized": true}` automatically omits tools that the client lacks permission to call.
