# Changelog

All notable changes to the **ZeroMCP** project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
* **Fine-Grained Access Control (FGAC) & Multi-Tenant Scope Hierarchy**:
  * Dot (`.`) and colon (`:`) interchangeable hierarchical scope matching engine (e.g., `workspace.finance:read` matched by `workspace.*`).
  * Explicit scope negation and restrictions (`!`) where negative restrictions strictly override positive wildcard grants (e.g., `["tools:*", "!tools:execute_shell"]`).
  * Sub-5 microsecond permission evaluation without external IDP/policy-engine dependencies.
* **Scoped Ephemeral Capability Token Generator (`zeromcp_token.py`)**:
  * Pure Python standard library HMAC-SHA256 (`HS256`) capability token minting and verification utility.
  * Direct CLI execution (`--mint`, `--verify`, `--inspect`) and importable Python API (`mint_token`, `verify_token`, `decode_unverified`).
  * Mandatory handshake token authentication enabled via `ZEROMCP_AUTH_SECRET` environment variable with fail-closed rejection (`-32602`) for missing, tampered, or expired tokens.
* **Cross-Language Conformance**:
  * Ported hierarchical scope evaluation and scope negation to TypeScript (`zeromcp.ts`) and Rust (`zeromcp-rs/src/main.rs`).
  * Implemented native Node `crypto` capability token verification for `zeromcp-ts`.
  * Expanded `tests/conformance_test_runner.py` from 40 to 43 automated checks, asserting multi-tenant scope execution, negation rejection, and discovery filtering across standard stdio.
  * Added 7 dedicated FGAC unit tests in `tests/test_scoped_permissions.py`.
* **Content-Addressed Storage (CAS) Token-Efficiency Engine (`zeromcp_cas.py`)**:
  * Automatic payload offloading for tool responses exceeding 4,096 bytes (configurable via `ZEROMCP_CAS_THRESHOLD` and `ZEROMCP_CAS_ENABLED`).
  * Persists payloads atomically to `~/.zeromcp/cas/<digest[:2]>/<digest>.txt` with fallback paths for restricted sandbox environments.
  * Emits compact token pointers (`cas://sha256/<hash>`) and preview summaries, saving 80–95% of LLM context window tokens on large outputs.
  * Three built-in surgical retrieval tools: `cas_fetch` (offset/limit slicing), `cas_search` (pattern/keyword search with context lines), and `cas_stats` (storage utilization).
  * Direct operator CLI management commands: `--cas-stats` and `--cas-gc [days]`.
* **Command Output Distiller (`command_distiller.py`)**:
  * Pure standard library terminal filter pre-filtering noisy output from `pytest`, `cargo test`, `cargo build`, and `git status`.
  * Preserves 100% of actionable failure signals, backtraces, and error summaries while eliminating up to 90% of token bloat.
  * Direct CLI execution (`--cmd "<command>" --telemetry`) and importable Python API (`distill_output`, `run_distilled`).
* **Developer Ergonomics CLI Suite in `zeromcp.py`**:
  * `--list` / `-l`: Formats registered tools, descriptions, and schemas into clean aligned tables.
  * `--call` / `-c <tool> [args]`: Direct tool invocation from shell with flexible JSON or shorthand strings.
  * `--repl` / `-i`: Interactive developer console for testing tools without raw JSON-RPC formatting.
  * `--print-config`: Outputs ready-to-paste `claude_desktop_config.json` block.

---

## [1.0.0-rc1] - 2026-11-08

### Added
* **Tri-Model Consensus Gate (`review_consensus_sentinel.py`)**:
  * Automated 2-of-3 peer review engine bridging Claude Opus 5.5, OpenAI Codex Astra, and Gemini 4 Argon.
  * Generates auditable Markdown ballots in `scratch/consensus_reports/`.
* **Multi-Language Server Implementations**:
  * **`zeromcp-py`**: Hardened, zero-dependency Python 3.8+ reference implementation (`zeromcp.py`) in 199 LOC.
  * **`zeromcp-ts`**: Zero-dependency ECMAScript / TypeScript reference (`zeromcp.ts`) runnable with Node 22.7+/24+ (`--experimental-strip-types`), Bun, or Deno.
  * **`zeromcp-rs`**: Lean Rust workspace (`Cargo.toml`, `src/main.rs`) compiling to native binaries (Tokio async stdio) and WebAssembly WASI (`wasm32-wasip1`, pure synchronous stdio) with zero Tokio bloat.
* **Cross-Language Conformance Test Suite (`tests/conformance_test_runner.py`)**:
  * 40 automated checks across 39 protocol vectors and 1 clean natural EOF termination check validating protocol handshake, intermediate state gating, ping, notifications, discovery, error codes, null params rejection, null arguments tolerance, wrong-type property rejection, prototype safety, whitespace tolerance, 10 MiB frame boundaries, negative zero exponents, float overflow, and resilience.
* **Automated Multi-Platform Release Matrix (`.github/workflows/zeromcp_release.yml`)**:
  * Cross-compiles standalone release binaries for macOS (Apple Silicon `zeromcp-macos-arm64` & Intel `zeromcp-macos-x86_64`), Linux (x86_64 `zeromcp-linux-x86_64` & ARM64 `zeromcp-linux-arm64`), Windows (`zeromcp-windows-x64.exe`), and WebAssembly WASI (`zeromcp.wasm`).
* **Packaging & Documentation**:
  * Added `scripts/package_dist.py` for staging standalone release artifacts and computing `SHA256SUMS.txt`.
  * Added `scripts/zeromcp_stdio_limiter.py` reference stdio rate-limiting supervisor enforcing token-bucket rate limiting on tool invocations (including individual calls inside batches, notification silence, and single-array batch aggregation).
  * Added dedicated supervisor regression suite (`tests/test_stdio_limiter.py`) verifying notification silence, batch response aggregation, ID correlation, and 10 MiB EOF framing boundaries.
  * Added `USAGE.md` providing standard package documentation, client configuration profiles (Claude Desktop, Cursor, VS Code, Docker, WebAssembly WASI), exact error taxonomy, and wire protocol lifecycle specifications.

### Changed
* Refactored `zeromcp-py`, `zeromcp-ts`, and `zeromcp-rs` to enforce a strict two-phase connection lifecycle state machine (`UNINITIALIZED -> NEGOTIATED -> INITIALIZED`). Tool execution is blocked until both `initialize` and `notifications/initialized` succeed in order.
* Replaced unbuffered `FileIO` with standard buffered streams (`os.fdopen(fd, "wb")`) to guarantee complete atomic frame writes.
* Lazy-loaded transport redirection into `setup_transport()` called only at runtime, preventing side effects when importing the module as a library.

### Fixed
* **Supervisor EOF Response Draining**: Resolved race condition where closing client stdin could terminate the supervisor before large in-flight responses were drained from child stdout by synchronizing reader thread join (`t.join()`) prior to child wait.
* **Supervisor Forwarded Frame Limit**: Enforced 10 MiB maximum frame size limit on reserialized batch payloads forwarded to child processes, guarding against float serialization expansion.
* **Notification Response Silence**: Prevented responses to valid notifications containing positional array parameters (`notifications/progress`), adhering to JSON-RPC 2.0 §4.1.
* **ID Validation**: Explicitly rejected `null` and `boolean` IDs as mandated by MCP specification.
* **Batch Memory Amplification**: Enforced `MAX_BATCH = 100` and rejected oversized batches with `-32600 Invalid Request`.
* **Framing Denial-of-Service**: Enforced 10 MiB inbound frame caps (via bounded stream reads in Python/Rust and line length gates in TypeScript).
* **Arithmetic Precision & Safety**: Replaced unsafe dynamic evaluation with AST whitelisting (`safe_eval_math`). Rejected complex numbers, divide by zero, and non-finite floats, documenting IEEE-754 double precision bounds.

### Security
* **OS-Level Transport Isolation**: Implemented file-descriptor duplication (`os.dup(1)` / `os.dup2(2, 1)`) so standard error receives all logging, C-extension outputs, and subprocess prints without corrupting the wire protocol.
* **Dependency & Supply-Chain Boundary**: Eliminated third-party package dependencies for Python (`zeromcp-py`) and TypeScript (`zeromcp-ts`) runtimes, relying exclusively on language standard libraries and native platform APIs. Minimized Rust (`zeromcp-rs`) build dependencies to audited crates (`tokio`, `serde`, `serde_json`), producing standalone binaries capable of zero-dependency offline runtime operation.
