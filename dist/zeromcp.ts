#!/usr/bin/env node
/**
 * ZeroMCP (TypeScript Reference Implementation)
 * ==============================================
 * Ultra-lean, zero-dependency, production-grade Model Context Protocol (MCP) server
 * implementing raw JSON-RPC 2.0 over standard I/O (stdio).
 * Runnable directly with Node (v22.7+ / v24+) via `--experimental-strip-types`,
 * or with Bun (`bun run zeromcp.ts`), or tsx (`npx tsx zeromcp.ts`).
 */

import * as readline from "node:readline";
import * as os from "node:os";
import process from "node:process";
import * as crypto from "node:crypto";

const PARSE_ERR = -32700;
const INVALID_REQ = -32600;
const METHOD_NOT_FOUND = -32601;
const INVALID_PARAMS = -32602;
const INTERNAL_ERR = -32603;

const MAX_FRAME_SIZE = 10 * 1024 * 1024; // 10 MiB frame limit
const MAX_BATCH = 100;
interface ServerState {
  phase: string;
  clientScopes?: string[];
  tokenClaims?: Record<string, any>;
}
const STATE: ServerState = { phase: "UNINITIALIZED" }; // UNINITIALIZED -> NEGOTIATED -> INITIALIZED

interface ToolDefinition {
  description: string;
  inputSchema: Record<string, any>;
  handler: (args: Record<string, any>) => string;
  requiredScopes?: string[];
}

// Character-safe string truncation (handles Unicode code points)
function truncateStr(s: any, maxChars: number): string {
  return Array.from(String(s)).slice(0, maxChars).join("");
}

// Safe math tokenizer and recursive-descent parser (zero eval, pure ECMAScript)
function safeEvalMath(expr: string): string {
  if (typeof expr !== "string" || Array.from(expr).length > 1024) throw new Error("Invalid expression");
  const tokens = expr.match(/\d+(\.\d+)?|\*\*|[+\-*/%^()]|\S/g) || [];
  let pos = 0;

  function peek(): string | null {
    return pos < tokens.length ? tokens[pos] : null;
  }

  function consume(expected?: string): string {
    const token = tokens[pos++];
    if (expected && token !== expected) {
      throw new Error(`Expected '${expected}', got '${token}'`);
    }
    return token;
  }

  function parseFactor(): number {
    const token = peek();
    if (!token) throw new Error("Unexpected end of expression");
    if (token === "+") {
      consume();
      return parseFactor();
    }
    if (token === "-") {
      consume();
      return -parseFactor();
    }
    if (token === "(") {
      consume("(");
      const val = parseExpr();
      consume(")");
      return val;
    }
    if (/^\d+(\.\d+)?$/.test(token)) {
      consume();
      return parseFloat(token);
    }
    throw new Error(`Invalid token: '${token}'`);
  }

  function parseExponent(): number {
    let left = parseFactor();
    while (peek() === "^" || peek() === "**") {
      consume();
      const right = parseExponent();
      left = Math.pow(left, right);
    }
    return left;
  }

  function parseTerm(): number {
    let left = parseExponent();
    while (peek() === "*" || peek() === "/" || peek() === "%") {
      const op = consume();
      const right = parseExponent();
      if (op === "*") left *= right;
      else if (op === "/") {
        if (right === 0) throw new Error("Division by zero");
        left /= right;
      } else if (op === "%") {
        if (right === 0) throw new Error("Modulo by zero");
        left %= right;
      }
    }
    return left;
  }

  function parseExpr(): number {
    let left = parseTerm();
    while (peek() === "+" || peek() === "-") {
      const op = consume();
      const right = parseTerm();
      if (op === "+") left += right;
      else if (op === "-") left -= right;
    }
    return left;
  }

  const result = parseExpr();
  if (pos < tokens.length) {
    throw new Error(`Unexpected trailing input: '${tokens.slice(pos).join(" ")}'`);
  }
  if (!Number.isFinite(result) || Number.isNaN(result)) {
    throw new Error("Complex or non-finite numbers unsupported");
  }
  return Number.isInteger(result) && Math.abs(result) < Number.MAX_SAFE_INTEGER
    ? result.toFixed(0)
    : String(result);
}

const TOOLS: Record<string, ToolDefinition> = {
  echo: {
    description: "Echoes back the input message",
    inputSchema: {
      type: "object",
      properties: { message: { type: "string" } },
      required: ["message"]
    },
    handler: (args) => String(args.message)
  },
  eval_math: {
    description: "Safely evaluates IEEE-754 double precision arithmetic (+, -, *, /, %, **)",
    inputSchema: {
      type: "object",
      properties: { expression: { type: "string" } },
      required: ["expression"]
    },
    handler: (args) => safeEvalMath(args.expression)
  },
  system_info: {
    description: "Returns host platform, architecture, and runtime telemetry",
    inputSchema: {
      type: "object",
      properties: {}
    },
    handler: () => {
      const runtime = (globalThis as any).Bun
        ? `bun ${(globalThis as any).Bun.version}`
        : (globalThis as any).Deno
        ? `deno ${(globalThis as any).Deno.version.deno}`
        : `node ${process.version}`;
      return JSON.stringify({
        platform: os.platform(),
        release: os.release(),
        arch: os.arch(),
        runtime
      });
    }
  }
};

function makeResp(
  id: any,
  result?: any,
  error?: { code: number; message: string; data?: any }
): Record<string, any> {
  const resp: Record<string, any> = { jsonrpc: "2.0", id };
  if (error !== undefined) {
    resp.error = error;
  } else {
    resp.result = result !== undefined ? result : {};
  }
  return resp;
}

function validateSchema(toolName: string, args: any): string | null {
  if (typeof args !== "object" || args === null || Array.isArray(args)) {
    return "Tool arguments must be an object";
  }
  const spec = TOOLS[toolName].inputSchema;
  const required = spec.required || [];
  for (const field of required) {
    if (!(field in args)) {
      return `Missing required property '${field}'`;
    }
  }
  const properties = spec.properties || {};
  for (const [prop, schema] of Object.entries(properties)) {
    if (prop in args && (schema as any).type === "string" && typeof args[prop] !== "string") {
      return `Property '${prop}' must be a string`;
    }
  }
  return null;
}

function parseScopeSegments(s: string): string[] {
  return s.replace(/\./g, ":").split(":").filter(seg => seg.length > 0);
}

function scopeMatches(grantSegs: string[], reqSegs: string[]): boolean {
  if (grantSegs.length === 1 && grantSegs[0] === "*") return true;
  if (grantSegs.length > 0 && grantSegs[grantSegs.length - 1] === "*") {
    const k = grantSegs.length - 1;
    if (reqSegs.length < k) return false;
    for (let i = 0; i < k; i++) {
      if (grantSegs[i] !== reqSegs[i]) return false;
    }
    return true;
  }
  if (grantSegs.length !== reqSegs.length) return false;
  for (let i = 0; i < grantSegs.length; i++) {
    const g = grantSegs[i];
    const r = reqSegs[i];
    if (g === "*" || g === r) continue;
    if (g.endsWith("*") && r.startsWith(g.slice(0, -1))) continue;
    return false;
  }
  return true;
}

function checkScopeAuthorized(requiredScopes: string[], grantedScopes: string[], toolName?: string): boolean {
  if (!grantedScopes || grantedScopes.length === 0) {
    return (!requiredScopes || requiredScopes.length === 0);
  }

  const parsedPos: string[][] = [];
  const parsedNeg: string[][] = [];

  for (const g of grantedScopes) {
    const gStr = String(g).trim();
    if (!gStr) continue;
    if (gStr.startsWith("!")) {
      parsedNeg.push(parseScopeSegments(gStr.slice(1)));
    } else {
      parsedPos.push(parseScopeSegments(gStr));
    }
  }

  const effectiveReqs = requiredScopes ? [...requiredScopes] : [];
  if (effectiveReqs.length === 0 && toolName) {
    const toolScopeSegs = parseScopeSegments(`tools:${toolName}`);
    for (const neg of parsedNeg) {
      if (scopeMatches(neg, toolScopeSegs)) return false;
    }
    return true;
  } else if (effectiveReqs.length === 0) {
    return true;
  }

  if (parsedPos.length === 0) return false;

  for (const req of effectiveReqs) {
    const reqSegs = parseScopeSegments(String(req));
    for (const neg of parsedNeg) {
      if (scopeMatches(neg, reqSegs)) return false;
    }
    let satisfied = false;
    for (const pos of parsedPos) {
      if (scopeMatches(pos, reqSegs)) {
        satisfied = true;
        break;
      }
    }
    if (!satisfied) return false;
  }

  return true;
}

function getClientScopes(params: any): string[] {
  if (STATE.clientScopes && STATE.tokenClaims) {
    return STATE.clientScopes;
  }
  if (params && typeof params === "object") {
    if (Array.isArray(params._meta?.scopes)) {
      return params._meta.scopes.map(String);
    }
    if (Array.isArray(params.scopes)) {
      return params.scopes.map(String);
    }
  }
  if (STATE.clientScopes) {
    return STATE.clientScopes;
  }
  const envScopes = process.env.ZEROMCP_CLIENT_SCOPES;
  if (envScopes) {
    return envScopes.split(",").map(s => s.trim()).filter(s => s.length > 0);
  }
  return ["*"];
}

function verifyCapabilityToken(token: string, secret: string): Record<string, any> {
  const parts = token.trim().split(".");
  if (parts.length !== 3) throw new Error("Expected 3 dot-delimited segments");
  const [hB64, pB64, sigB64] = parts;
  const signingInput = `${hB64}.${pB64}`;
  const expectedSig = crypto.createHmac("sha256", secret).update(signingInput).digest("base64url");
  if (!crypto.timingSafeEqual(Buffer.from(sigB64), Buffer.from(expectedSig))) {
    throw new Error("Cryptographic signature mismatch");
  }
  const payload = JSON.parse(Buffer.from(pB64, "base64url").toString("utf-8"));
  const now = Math.floor(Date.now() / 1000);
  if (typeof payload.exp === "number" && now > payload.exp) {
    throw new Error(`Token expired at ${payload.exp}`);
  }
  return payload;
}

function handleSingleRequest(msg: any): Record<string, any> | null {
  if (!msg || typeof msg !== "object" || Array.isArray(msg)) {
    return makeResp(null, undefined, { code: INVALID_REQ, message: "Invalid Request: expected object" });
  }

  // Silently drop inbound client responses
  if ("id" in msg && ("result" in msg || "error" in msg) && !("method" in msg)) {
    return null;
  }

  const hasId = "id" in msg;
  const reqId = msg.id;

  if (hasId && (typeof reqId === "boolean" || reqId === null || (typeof reqId !== "string" && (typeof reqId !== "number" || !Number.isInteger(reqId))))) {
    return makeResp(null, undefined, { code: INVALID_REQ, message: "Invalid id: must be non-null string or integer" });
  }

  if (msg.jsonrpc !== "2.0") {
    return makeResp(reqId ?? null, undefined, { code: INVALID_REQ, message: "Invalid Request: jsonrpc must be '2.0'" });
  }

  const method = msg.method;
  if (typeof method !== "string") {
    return makeResp(reqId ?? null, undefined, { code: INVALID_REQ, message: "Invalid Request: method must be string" });
  }

  if ("params" in msg && (typeof msg.params !== "object" || msg.params === null)) {
    return makeResp(reqId ?? null, undefined, { code: INVALID_REQ, message: "params must be an object or array" });
  }

  // Notifications early exit: valid notifications NEVER receive a response
  if (!hasId) {
    if (method === "notifications/initialized" && STATE.phase === "NEGOTIATED") {
      STATE.phase = "INITIALIZED";
    }
    return null;
  }

  const params = msg.params || {};
  if (Array.isArray(params)) {
    return makeResp(reqId, undefined, { code: INVALID_PARAMS, message: "Positional array params unsupported in MCP" });
  }

  try {
    if (method === "initialize") {
      if (STATE.phase !== "UNINITIALIZED") {
        return makeResp(reqId, undefined, { code: INVALID_REQ, message: "Server already initialized" });
      }
      const { protocolVersion, capabilities, clientInfo } = params;
      if (typeof protocolVersion !== "string" || !capabilities || typeof capabilities !== "object" || Array.isArray(capabilities) || !clientInfo || typeof clientInfo !== "object" || Array.isArray(clientInfo)) {
        return makeResp(reqId, undefined, { code: INVALID_PARAMS, message: "initialize requires string protocolVersion, object capabilities, and object clientInfo" });
      }
      if (typeof clientInfo.name !== "string" || typeof clientInfo.version !== "string") {
        return makeResp(reqId, undefined, { code: INVALID_PARAMS, message: "clientInfo requires string name and version" });
      }

      const authSecret = process.env.ZEROMCP_AUTH_SECRET;
      let rawToken = params.token || clientInfo.token || (params._meta && params._meta.token);
      if (!rawToken && typeof params.authorization === "string") {
        const authStr = params.authorization.trim();
        rawToken = authStr.toLowerCase().startsWith("bearer ") ? authStr.slice(7).trim() : authStr;
      }

      if (authSecret) {
        if (!rawToken) {
          return makeResp(reqId, undefined, { code: INVALID_PARAMS, message: "Authentication required: missing capability token in initialize params" });
        }
        try {
          const claims = verifyCapabilityToken(rawToken, authSecret);
          STATE.tokenClaims = claims;
          if (Array.isArray(claims.scopes)) {
            STATE.clientScopes = claims.scopes.map(String);
          }
        } catch (e: any) {
          return makeResp(reqId, undefined, { code: INVALID_PARAMS, message: `Authentication failed: ${e.message}` });
        }
      } else if (rawToken) {
        try {
          const parts = rawToken.trim().split(".");
          if (parts.length === 3) {
            const claims = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf-8"));
            STATE.tokenClaims = claims;
            if (Array.isArray(claims.scopes)) {
              STATE.clientScopes = claims.scopes.map(String);
            }
          }
        } catch {}
      } else if (Array.isArray(clientInfo.scopes)) {
        STATE.clientScopes = clientInfo.scopes.map(String);
      } else if (Array.isArray(params._meta && params._meta.scopes)) {
        STATE.clientScopes = params._meta.scopes.map(String);
      }

      STATE.phase = "NEGOTIATED";
      return makeResp(reqId, {
        protocolVersion: "2024-11-05",
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: "zeromcp-ts", version: "1.0.0-rc1" }
      });
    }

    if (method === "ping") return makeResp(reqId, {});

    if (STATE.phase !== "INITIALIZED" && method.startsWith("tools/")) {
      return makeResp(reqId, undefined, { code: INVALID_REQ, message: "Server uninitialized: initialize handshake required" });
    }

    if (method === "tools/list") {
      const clientScopes = getClientScopes(params);
      const filterUnauth = process.env.ZEROMCP_FILTER_UNAUTHORIZED_TOOLS === "1" || (params && params.filter_unauthorized === true);
      const toolsOut: any[] = [];
      for (const [name, spec] of Object.entries(TOOLS)) {
        const reqScopes = spec.requiredScopes || [];
        if (filterUnauth && !checkScopeAuthorized(reqScopes, clientScopes, name)) {
          continue;
        }
        const item: any = {
          name,
          description: spec.description,
          inputSchema: spec.inputSchema
        };
        if (reqScopes.length > 0) {
          item.scopes = reqScopes;
        }
        toolsOut.push(item);
      }
      return makeResp(reqId, { tools: toolsOut });
    }

    if (method === "tools/call") {
      const toolName = params.name;
      if (typeof toolName !== "string" || !Object.prototype.hasOwnProperty.call(TOOLS, toolName)) {
        return makeResp(reqId, undefined, { code: INVALID_PARAMS, message: `Tool '${truncateStr(toolName, 64)}' not found` });
      }
      const toolEntry = TOOLS[toolName];
      const reqScopes = toolEntry.requiredScopes || [];
      const clientScopes = getClientScopes(params);
      if (!checkScopeAuthorized(reqScopes, clientScopes, toolName)) {
        return makeResp(reqId, undefined, {
          code: INVALID_PARAMS,
          message: `Forbidden: client scopes [${clientScopes.join(", ")}] do not satisfy required scopes [${(reqScopes.length > 0 ? reqScopes : ['tools:' + toolName]).join(", ")}] for tool '${toolName}'`
        });
      }
      const toolArgs = params.arguments === undefined || params.arguments === null ? {} : params.arguments;
      const schemaErr = validateSchema(toolName, toolArgs);
      if (schemaErr) return makeResp(reqId, undefined, { code: INVALID_PARAMS, message: schemaErr });

      try {
        const outStr = String(TOOLS[toolName].handler(toolArgs));
        return makeResp(reqId, { content: [{ type: "text", text: outStr }], isError: false });
      } catch (err: any) {
        return makeResp(reqId, { content: [{ type: "text", text: `Error: ${err.message || String(err)}` }], isError: true });
      }
    }

    return makeResp(reqId, undefined, { code: METHOD_NOT_FOUND, message: `Method '${truncateStr(method, 64)}' not found` });
  } catch (err: any) {
    process.stderr.write(`[ZeroMCP-ts-Core] Request exception: ${err}\n`);
    return makeResp(reqId, undefined, { code: INTERNAL_ERR, message: "Internal error" });
  }
}

let activeRl: readline.Interface | null = null;

function emitFrame(payload: any): void {
  try {
    const ok = process.stdout.write(JSON.stringify(payload) + "\n");
    if (!ok && activeRl) {
      activeRl.pause();
      process.stdout.once("drain", () => {
        if (activeRl) activeRl.resume();
      });
    }
  } catch (err) {
    process.exit(0);
  }
}

function runStdio(): void {
  if (typeof process !== "undefined" && process.stdout && typeof process.stdout.on === "function") {
    process.stdout.on("error", (_err: any) => {
      process.exit(0);
    });
  }

  const rl = readline.createInterface({
    input: process.stdin,
    terminal: false
  });
  activeRl = rl;

  const encoder = new TextEncoder();

  rl.on("line", (line: string) => {
    if (encoder.encode(line).length >= MAX_FRAME_SIZE) {
      emitFrame(makeResp(null, undefined, { code: PARSE_ERR, message: "Frame exceeded maximum allowed size" }));
      return;
    }
    const trimmed = line.trim();
    if (!trimmed) return;

    let parsed: any;
    try {
      parsed = JSON.parse(trimmed);
    } catch (err: any) {
      emitFrame(makeResp(null, undefined, { code: PARSE_ERR, message: `Parse error: ${err.message}` }));
      return;
    }

    try {
      if (Array.isArray(parsed)) {
        if (parsed.length === 0) {
          emitFrame(makeResp(null, undefined, { code: INVALID_REQ, message: "Invalid Request: empty batch" }));
          return;
        }
        if (parsed.length > MAX_BATCH) {
          emitFrame(makeResp(null, undefined, { code: INVALID_REQ, message: `Batch size exceeds maximum of ${MAX_BATCH}` }));
          return;
        }
        const resps = parsed.map(handleSingleRequest).filter((r) => r !== null);
        if (resps.length > 0) emitFrame(resps);
      } else {
        const resp = handleSingleRequest(parsed);
        if (resp !== null) emitFrame(resp);
      }
    } catch (err: any) {
      process.stderr.write(`[ZeroMCP-ts-Core] Unhandled error: ${err}\n`);
      emitFrame(makeResp(null, undefined, { code: INTERNAL_ERR, message: "Internal error" }));
    }
  });

  rl.on("close", () => {
    if (process.stdout.writableLength > 0) {
      process.stdout.once("drain", () => process.exit(0));
    } else {
      process.exit(0);
    }
  });
}

function checkDirectRun(): boolean {
  if (typeof process === "undefined" || !process.argv || !process.argv[1]) return false;
  const noAutoStart = String(process.env?.ZEROMCP_NO_AUTO_START || "").toLowerCase();
  if (noAutoStart === "1" || noAutoStart === "true") return false;
  if ("main" in import.meta && (import.meta as any).main) return true;
  const rawArg = process.argv[1].replace(/\\/g, "/");
  const metaPath = import.meta.url.replace(/^file:\/\/\/?/, "").replace(/\\/g, "/");
  return metaPath.endsWith(rawArg) || rawArg.endsWith("/zeromcp.ts") || rawArg.endsWith("/zeromcp.js") || rawArg === "zeromcp.ts" || rawArg === "zeromcp.js";
}

if (checkDirectRun()) {
  runStdio();
}

export { runStdio, safeEvalMath, TOOLS };
