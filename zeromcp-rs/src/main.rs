use std::sync::atomic::{AtomicU8, Ordering};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
#[cfg(not(target_family = "wasm"))]
use tokio::io::{self, AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};

// JSON-RPC 2.0 Error Codes
const PARSE_ERR: i32 = -32700;
const INVALID_REQ: i32 = -32600;
const METHOD_NOT_FOUND: i32 = -32601;
const INVALID_PARAMS: i32 = -32602;
const INTERNAL_ERR: i32 = -32603;

// Resource Boundaries
const MAX_FRAME_SIZE: usize = 10 * 1024 * 1024; // 10 MiB
const MAX_MATH_LEN: usize = 1024; // 1,024 chars
const MAX_BATCH: usize = 100; // 100 requests

// Lifecycle Phase States
const STATE_UNINITIALIZED: u8 = 0;
const STATE_NEGOTIATED: u8 = 1;
const STATE_INITIALIZED: u8 = 2;

static LIFECYCLE_STATE: AtomicU8 = AtomicU8::new(STATE_UNINITIALIZED);

#[derive(Debug, Serialize, Deserialize)]
struct JsonRpcResponse {
    jsonrpc: &'static str,
    id: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<JsonRpcError>,
}

#[derive(Debug, Serialize, Deserialize)]
struct JsonRpcError {
    code: i32,
    message: String,
}

// Character-safe string truncation (prevents UTF-8 slice panics)
fn truncate_str(s: &str, max_chars: usize) -> &str {
    match s.char_indices().nth(max_chars) {
        Some((idx, _)) => &s[..idx],
        None => s,
    }
}

// Math Tokenizer & AST Evaluation (Zero eval, pure recursive-descent)
#[derive(Debug, Clone, PartialEq)]
enum Token {
    Number(f64),
    Plus,
    Minus,
    Mul,
    Div,
    Mod,
    Pow,
    LParen,
    RParen,
}

fn tokenize(expr: &str) -> Result<Vec<Token>, String> {
    let mut tokens = Vec::new();
    let mut chars = expr.chars().peekable();

    while let Some(&c) = chars.peek() {
        if c.is_whitespace() {
            chars.next();
            continue;
        }
        match c {
            '+' => {
                chars.next();
                tokens.push(Token::Plus);
            }
            '-' => {
                chars.next();
                tokens.push(Token::Minus);
            }
            '*' => {
                chars.next();
                if chars.peek() == Some(&'*') {
                    chars.next();
                    tokens.push(Token::Pow);
                } else {
                    tokens.push(Token::Mul);
                }
            }
            '/' => {
                chars.next();
                tokens.push(Token::Div);
            }
            '%' => {
                chars.next();
                tokens.push(Token::Mod);
            }
            '^' => {
                chars.next();
                tokens.push(Token::Pow);
            }
            '(' => {
                chars.next();
                tokens.push(Token::LParen);
            }
            ')' => {
                chars.next();
                tokens.push(Token::RParen);
            }
            '0'..='9' | '.' => {
                let mut num_str = String::new();
                let mut seen_dot = false;
                while let Some(&nc) = chars.peek() {
                    if nc.is_ascii_digit() {
                        num_str.push(chars.next().unwrap());
                    } else if nc == '.' && !seen_dot {
                        seen_dot = true;
                        num_str.push(chars.next().unwrap());
                    } else {
                        break;
                    }
                }
                let val = num_str.parse::<f64>().map_err(|_| format!("Invalid number: '{num_str}'"))?;
                tokens.push(Token::Number(val));
            }
            other => return Err(format!("Unexpected character: '{other}'")),
        }
    }
    Ok(tokens)
}

struct MathParser<'a> {
    tokens: &'a [Token],
    pos: usize,
}

impl<'a> MathParser<'a> {
    fn peek(&self) -> Option<&'a Token> {
        self.tokens.get(self.pos)
    }

    fn consume(&mut self) -> Option<&'a Token> {
        let tok = self.tokens.get(self.pos);
        if tok.is_some() {
            self.pos += 1;
        }
        tok
    }

    fn parse_factor(&mut self) -> Result<f64, String> {
        match self.peek() {
            Some(Token::Plus) => {
                self.consume();
                self.parse_factor()
            }
            Some(Token::Minus) => {
                self.consume();
                Ok(-self.parse_factor()?)
            }
            Some(Token::LParen) => {
                self.consume();
                let val = self.parse_expr()?;
                match self.consume() {
                    Some(Token::RParen) => Ok(val),
                    _ => Err("Expected closing parenthesis ')'".to_string()),
                }
            }
            Some(Token::Number(n)) => {
                let val = *n;
                self.consume();
                Ok(val)
            }
            Some(_) => Err("Unexpected token".to_string()),
            None => Err("Unexpected end of expression".to_string()),
        }
    }

    fn parse_exponent(&mut self) -> Result<f64, String> {
        let mut left = self.parse_factor()?;
        while let Some(Token::Pow) = self.peek() {
            self.consume();
            let right = self.parse_exponent()?;
            left = left.powf(right);
        }
        Ok(left)
    }

    fn parse_term(&mut self) -> Result<f64, String> {
        let mut left = self.parse_exponent()?;
        while let Some(tok) = self.peek() {
            match tok {
                Token::Mul => {
                    self.consume();
                    let right = self.parse_exponent()?;
                    left *= right;
                }
                Token::Div => {
                    self.consume();
                    let right = self.parse_exponent()?;
                    if right == 0.0 {
                        return Err("Division by zero".to_string());
                    }
                    left /= right;
                }
                Token::Mod => {
                    self.consume();
                    let right = self.parse_exponent()?;
                    if right == 0.0 {
                        return Err("Modulo by zero".to_string());
                    }
                    left %= right;
                }
                _ => break,
            }
        }
        Ok(left)
    }

    fn parse_expr(&mut self) -> Result<f64, String> {
        let mut left = self.parse_term()?;
        while let Some(tok) = self.peek() {
            match tok {
                Token::Plus => {
                    self.consume();
                    let right = self.parse_term()?;
                    left += right;
                }
                Token::Minus => {
                    self.consume();
                    let right = self.parse_term()?;
                    left -= right;
                }
                _ => break,
            }
        }
        Ok(left)
    }
}

fn safe_eval_math(expr: &str) -> Result<String, String> {
    if expr.chars().count() > MAX_MATH_LEN {
        return Err("Expression too long".to_string());
    }
    let tokens = tokenize(expr)?;
    let mut parser = MathParser { tokens: &tokens, pos: 0 };
    let result = parser.parse_expr()?;
    if parser.pos < tokens.len() {
        return Err("Unexpected trailing input".to_string());
    }
    if !result.is_finite() {
        return Err("Complex or non-finite numbers unsupported".to_string());
    }
    if result.fract() == 0.0 && result.abs() < 9_007_199_254_740_992.0 {
        Ok(format!("{}", result as i64))
    } else {
        Ok(format!("{result}"))
    }
}

fn parse_scope_segments(s: &str) -> Vec<&str> {
    s.split(|c| c == '.' || c == ':').filter(|seg| !seg.is_empty()).collect()
}

fn scope_matches(grant_segs: &[&str], req_segs: &[&str]) -> bool {
    if grant_segs == ["*"] {
        return true;
    }
    if let Some(&"*") = grant_segs.last() {
        let k = grant_segs.len() - 1;
        if req_segs.len() < k {
            return false;
        }
        return req_segs[..k] == grant_segs[..k];
    }
    if grant_segs.len() != req_segs.len() {
        return false;
    }
    for (g, r) in grant_segs.iter().zip(req_segs.iter()) {
        if *g == "*" || g == r {
            continue;
        }
        if g.ends_with('*') && r.starts_with(&g[..g.len() - 1]) {
            continue;
        }
        return false;
    }
    true
}

fn check_scope_authorized(required_scopes: &[&str], granted_scopes: &[String], tool_name: Option<&str>) -> bool {
    if granted_scopes.is_empty() {
        return required_scopes.is_empty();
    }

    let mut parsed_pos = Vec::new();
    let mut parsed_neg = Vec::new();

    for g in granted_scopes {
        let g_trimmed = g.trim();
        if g_trimmed.is_empty() {
            continue;
        }
        if g_trimmed.starts_with('!') {
            parsed_neg.push(parse_scope_segments(&g_trimmed[1..]));
        } else {
            parsed_pos.push(parse_scope_segments(g_trimmed));
        }
    }

    if required_scopes.is_empty() {
        if let Some(name) = tool_name {
            let tool_scope = format!("tools:{}", name);
            let tool_segs = parse_scope_segments(&tool_scope);
            for neg in &parsed_neg {
                if scope_matches(neg, &tool_segs) {
                    return false;
                }
            }
        }
        return true;
    }

    if parsed_pos.is_empty() {
        return false;
    }

    for req in required_scopes {
        let req_segs = parse_scope_segments(req);
        // 1. Any negation denies access
        for neg in &parsed_neg {
            if scope_matches(neg, &req_segs) {
                return false;
            }
        }
        // 2. Positive grant must match
        let mut satisfied = false;
        for pos in &parsed_pos {
            if scope_matches(pos, &req_segs) {
                satisfied = true;
                break;
            }
        }
        if !satisfied {
            return false;
        }
    }

    true
}

fn get_client_scopes(params: &Value) -> Vec<String> {
    let strict = std::env::var("ZEROMCP_AUTH_SECRET").map(|v| !v.is_empty()).unwrap_or(false)
        || std::env::var("ZEROMCP_STRICT_SCOPES").map(|v| v == "1").unwrap_or(false);
    if strict {
        return Vec::new();
    }
    if let Some(meta) = params.get("_meta").and_then(|m| m.get("scopes")).and_then(|s| s.as_array()) {
        return meta.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect();
    }
    if let Some(scopes) = params.get("scopes").and_then(|s| s.as_array()) {
        return scopes.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect();
    }
    if let Ok(env_scopes) = std::env::var("ZEROMCP_CLIENT_SCOPES") {
        let parsed: Vec<String> = env_scopes.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
        if !parsed.is_empty() {
            return parsed;
        }
    }
    vec!["*".to_string()]
}

fn handle_single_request(msg: &Value) -> Option<JsonRpcResponse> {
    let obj = match msg.as_object() {
        Some(o) => o,
        None => {
            return Some(JsonRpcResponse {
                jsonrpc: "2.0",
                id: Value::Null,
                result: None,
                error: Some(JsonRpcError {
                    code: INVALID_REQ,
                    message: "Invalid Request: expected object".to_string(),
                }),
            });
        }
    };

    // Drop inbound client response frames
    if obj.contains_key("id") && (obj.contains_key("result") || obj.contains_key("error")) && !obj.contains_key("method") {
        return None;
    }

    let has_id = obj.contains_key("id");
    let req_id = obj.get("id").cloned().unwrap_or(Value::Null);

    // Reject boolean, null, floating point, and non-primitive IDs
    if has_id {
        if req_id.is_boolean() || req_id.is_null() || (!req_id.is_string() && !req_id.is_i64() && !req_id.is_u64()) {
            return Some(JsonRpcResponse {
                jsonrpc: "2.0",
                id: Value::Null,
                result: None,
                error: Some(JsonRpcError {
                    code: INVALID_REQ,
                    message: "Invalid id: must be non-null string or integer".to_string(),
                }),
            });
        }
    }

    if obj.get("jsonrpc").and_then(|v| v.as_str()) != Some("2.0") {
        return Some(JsonRpcResponse {
            jsonrpc: "2.0",
            id: if has_id { req_id } else { Value::Null },
            result: None,
            error: Some(JsonRpcError {
                code: INVALID_REQ,
                message: "Invalid Request: jsonrpc must be '2.0'".to_string(),
            }),
        });
    }

    let method = match obj.get("method").and_then(|v| v.as_str()) {
        Some(m) => m,
        None => {
            return Some(JsonRpcResponse {
                jsonrpc: "2.0",
                id: if has_id { req_id } else { Value::Null },
                result: None,
                error: Some(JsonRpcError {
                    code: INVALID_REQ,
                    message: "Invalid Request: method must be string".to_string(),
                }),
            });
        }
    };

    if let Some(p) = obj.get("params") {
        if !p.is_object() && !p.is_array() {
            return Some(JsonRpcResponse {
                jsonrpc: "2.0",
                id: if has_id { req_id } else { Value::Null },
                result: None,
                error: Some(JsonRpcError {
                    code: INVALID_REQ,
                    message: "params must be an object or array".to_string(),
                }),
            });
        }
    }

    // Notifications early return: valid notifications NEVER receive a response
    if !has_id {
        if method == "notifications/initialized" && LIFECYCLE_STATE.load(Ordering::SeqCst) == STATE_NEGOTIATED {
            LIFECYCLE_STATE.store(STATE_INITIALIZED, Ordering::SeqCst);
        }
        return None;
    }

    let params = obj.get("params").unwrap_or(&Value::Null);
    if params.is_array() {
        return Some(JsonRpcResponse {
            jsonrpc: "2.0",
            id: req_id,
            result: None,
            error: Some(JsonRpcError {
                code: INVALID_PARAMS,
                message: "Positional array params unsupported in MCP".to_string(),
            }),
        });
    }

    match method {
        "initialize" => {
            if LIFECYCLE_STATE.load(Ordering::SeqCst) != STATE_UNINITIALIZED {
                return Some(JsonRpcResponse {
                    jsonrpc: "2.0",
                    id: req_id,
                    result: None,
                    error: Some(JsonRpcError {
                        code: INVALID_REQ,
                        message: "Server already initialized".to_string(),
                    }),
                });
            }
            let proto = params.get("protocolVersion").and_then(|v| v.as_str());
            let caps = params.get("capabilities").and_then(|v| v.as_object());
            let client = params.get("clientInfo").and_then(|v| v.as_object());

            if proto.is_none() || caps.is_none() || client.is_none() {
                return Some(JsonRpcResponse {
                    jsonrpc: "2.0",
                    id: req_id,
                    result: None,
                    error: Some(JsonRpcError {
                        code: INVALID_PARAMS,
                        message: "initialize requires string protocolVersion, object capabilities, and object clientInfo".to_string(),
                    }),
                });
            }
            let client_map = client.unwrap();
            if !client_map.get("name").map(|v| v.is_string()).unwrap_or(false)
                || !client_map.get("version").map(|v| v.is_string()).unwrap_or(false)
            {
                return Some(JsonRpcResponse {
                    jsonrpc: "2.0",
                    id: req_id,
                    result: None,
                    error: Some(JsonRpcError {
                        code: INVALID_PARAMS,
                        message: "clientInfo requires string name and version".to_string(),
                    }),
                });
            }

            LIFECYCLE_STATE.store(STATE_NEGOTIATED, Ordering::SeqCst);
            Some(JsonRpcResponse {
                jsonrpc: "2.0",
                id: req_id,
                result: Some(json!({
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": { "listChanged": false }
                    },
                    "serverInfo": {
                        "name": "zeromcp-rs",
                        "version": "1.0.0-rc1"
                    }
                })),
                error: None,
            })
        }
        "ping" => Some(JsonRpcResponse {
            jsonrpc: "2.0",
            id: req_id,
            result: Some(json!({})),
            error: None,
        }),
        m if LIFECYCLE_STATE.load(Ordering::SeqCst) != STATE_INITIALIZED && m.starts_with("tools/") => Some(JsonRpcResponse {
            jsonrpc: "2.0",
            id: req_id,
            result: None,
            error: Some(JsonRpcError {
                code: INVALID_REQ,
                message: "Server uninitialized: initialize handshake required".to_string(),
            }),
        }),
        "tools/list" => {
            let client_scopes = get_client_scopes(params);
            let filter_unauth = std::env::var("ZEROMCP_FILTER_UNAUTHORIZED_TOOLS").map(|v| v == "1").unwrap_or(false)
                || params.get("filter_unauthorized").and_then(|v| v.as_bool()).unwrap_or(false);

            let all_tools = vec![
                ("echo", "Echoes back the input message", json!({
                    "type": "object",
                    "properties": { "message": { "type": "string" } },
                    "required": ["message"]
                })),
                ("eval_math", "Safely evaluates IEEE-754 double precision arithmetic (+, -, *, /, %, **)", json!({
                    "type": "object",
                    "properties": { "expression": { "type": "string" } },
                    "required": ["expression"]
                })),
                ("system_info", "Returns host platform, architecture, and runtime telemetry", json!({
                    "type": "object",
                    "properties": {}
                })),
            ];

            let mut tools_out = Vec::new();
            for (name, desc, schema) in all_tools {
                if filter_unauth && !check_scope_authorized(&[], &client_scopes, Some(name)) {
                    continue;
                }
                tools_out.push(json!({
                    "name": name,
                    "description": desc,
                    "inputSchema": schema
                }));
            }

            Some(JsonRpcResponse {
                jsonrpc: "2.0",
                id: req_id,
                result: Some(json!({ "tools": tools_out })),
                error: None,
            })
        }
        "tools/call" => {
            let tool_name_str = match params.get("name") {
                Some(Value::String(s)) => s.clone(),
                Some(other) => other.to_string(),
                None => "".to_string(),
            };
            let tool_name = tool_name_str.as_str();
            if !["echo", "eval_math", "system_info"].contains(&tool_name) {
                return Some(JsonRpcResponse {
                    jsonrpc: "2.0",
                    id: req_id,
                    result: None,
                    error: Some(JsonRpcError {
                        code: INVALID_PARAMS,
                        message: format!("Tool '{}' not found", truncate_str(tool_name, 64)),
                    }),
                });
            }

            let client_scopes = get_client_scopes(params);
            if !check_scope_authorized(&[], &client_scopes, Some(tool_name)) {
                return Some(JsonRpcResponse {
                    jsonrpc: "2.0",
                    id: req_id,
                    result: None,
                    error: Some(JsonRpcError {
                        code: INVALID_PARAMS,
                        message: format!("Forbidden: client scopes {:?} do not satisfy required scopes for tool '{}'", client_scopes, tool_name),
                    }),
                });
            }
            let tool_args = match params.get("arguments") {
                Some(Value::Object(map)) => Some(map),
                Some(Value::Null) | None => None,
                Some(_) => {
                    return Some(JsonRpcResponse {
                        jsonrpc: "2.0",
                        id: req_id,
                        result: None,
                        error: Some(JsonRpcError {
                            code: INVALID_PARAMS,
                            message: "Tool arguments must be an object".to_string(),
                        }),
                    });
                }
            };

            match tool_name {
                "echo" => {
                    let msg_val = tool_args.and_then(|a| a.get("message"));
                    match msg_val {
                        Some(Value::String(m)) => Some(JsonRpcResponse {
                            jsonrpc: "2.0",
                            id: req_id,
                            result: Some(json!({
                                "content": [{ "type": "text", "text": m }],
                                "isError": false
                            })),
                            error: None,
                        }),
                        Some(_) => Some(JsonRpcResponse {
                            jsonrpc: "2.0",
                            id: req_id,
                            result: None,
                            error: Some(JsonRpcError {
                                code: INVALID_PARAMS,
                                message: "Property 'message' must be a string".to_string(),
                            }),
                        }),
                        None => Some(JsonRpcResponse {
                            jsonrpc: "2.0",
                            id: req_id,
                            result: None,
                            error: Some(JsonRpcError {
                                code: INVALID_PARAMS,
                                message: "Missing required property 'message'".to_string(),
                            }),
                        }),
                    }
                }
                "eval_math" => {
                    let expr_val = tool_args.and_then(|a| a.get("expression"));
                    match expr_val {
                        Some(Value::String(e)) => match safe_eval_math(e) {
                            Ok(res) => Some(JsonRpcResponse {
                                jsonrpc: "2.0",
                                id: req_id,
                                result: Some(json!({
                                    "content": [{ "type": "text", "text": res }],
                                    "isError": false
                                })),
                                error: None,
                            }),
                            Err(err) => Some(JsonRpcResponse {
                                jsonrpc: "2.0",
                                id: req_id,
                                result: Some(json!({
                                    "content": [{ "type": "text", "text": format!("Error: {err}") }],
                                    "isError": true
                                })),
                                error: None,
                            }),
                        },
                        Some(_) => Some(JsonRpcResponse {
                            jsonrpc: "2.0",
                            id: req_id,
                            result: None,
                            error: Some(JsonRpcError {
                                code: INVALID_PARAMS,
                                message: "Property 'expression' must be a string".to_string(),
                            }),
                        }),
                        None => Some(JsonRpcResponse {
                            jsonrpc: "2.0",
                            id: req_id,
                            result: None,
                            error: Some(JsonRpcError {
                                code: INVALID_PARAMS,
                                message: "Missing required property 'expression'".to_string(),
                            }),
                        }),
                    }
                }
                "system_info" => {
                    let runtime_tag = if cfg!(target_family = "wasm") {
                        "rust/wasm"
                    } else {
                        "rust/native"
                    };
                    let sys_info = json!({
                        "os": std::env::consts::OS,
                        "arch": std::env::consts::ARCH,
                        "family": std::env::consts::FAMILY,
                        "runtime": runtime_tag
                    });
                    Some(JsonRpcResponse {
                        jsonrpc: "2.0",
                        id: req_id,
                        result: Some(json!({
                            "content": [{ "type": "text", "text": sys_info.to_string() }],
                            "isError": false
                        })),
                        error: None,
                    })
                }
                _ => Some(JsonRpcResponse {
                    jsonrpc: "2.0",
                    id: req_id,
                    result: None,
                    error: Some(JsonRpcError {
                        code: INVALID_PARAMS,
                        message: format!("Tool '{}' not found", truncate_str(tool_name, 64)),
                    }),
                }),
            }
        }
        _ => Some(JsonRpcResponse {
            jsonrpc: "2.0",
            id: req_id,
            result: None,
            error: Some(JsonRpcError {
                code: METHOD_NOT_FOUND,
                message: format!("Method '{}' not found", truncate_str(method, 64)),
            }),
        }),
    }
}

#[cfg(not(target_family = "wasm"))]
#[tokio::main(flavor = "current_thread")]
async fn main() -> io::Result<()> {
    let stdin = io::stdin();
    let mut reader = BufReader::new(stdin);
    let mut stdout = io::stdout();

    let mut raw_buf = Vec::new();

    loop {
        raw_buf.clear();
        // Bounded read up to MAX_FRAME_SIZE + 1 bytes
        let mut take = (&mut reader).take((MAX_FRAME_SIZE + 1) as u64);
        let n = take.read_until(b'\n', &mut raw_buf).await?;
        if n == 0 {
            break; // EOF
        }

        if raw_buf.len() > MAX_FRAME_SIZE || (!raw_buf.ends_with(b"\n") && n == (MAX_FRAME_SIZE + 1)) {
            // Oversized line: drain remainder of the line up to newline
            if !raw_buf.ends_with(b"\n") {
                let mut b = [0u8; 1];
                while let Ok(1) = reader.read(&mut b).await {
                    if b[0] == b'\n' {
                        break;
                    }
                }
            }
            let err_resp = json!({
                "jsonrpc": "2.0",
                "id": Value::Null,
                "error": {
                    "code": PARSE_ERR,
                    "message": "Frame exceeded maximum allowed size"
                }
            });
            let out = serde_json::to_string(&err_resp).unwrap();
            stdout.write_all(format!("{out}\n").as_bytes()).await?;
            stdout.flush().await?;
            continue;
        }

        let line = match String::from_utf8(raw_buf.clone()) {
            Ok(s) => s,
            Err(_) => {
                let err_resp = json!({
                    "jsonrpc": "2.0",
                    "id": Value::Null,
                    "error": {
                        "code": PARSE_ERR,
                        "message": "Parse error: Invalid UTF-8"
                    }
                });
                let out = serde_json::to_string(&err_resp).unwrap();
                stdout.write_all(format!("{out}\n").as_bytes()).await?;
                stdout.flush().await?;
                continue;
            }
        };

        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }

        let parsed: Value = match serde_json::from_str(trimmed) {
            Ok(v) => v,
            Err(e) => {
                let err_resp = json!({
                    "jsonrpc": "2.0",
                    "id": Value::Null,
                    "error": {
                        "code": PARSE_ERR,
                        "message": format!("Parse error: {e}")
                    }
                });
                let out = serde_json::to_string(&err_resp).unwrap();
                stdout.write_all(format!("{out}\n").as_bytes()).await?;
                stdout.flush().await?;
                continue;
            }
        };

        if let Some(arr) = parsed.as_array() {
            if arr.is_empty() {
                let err_resp = json!({
                    "jsonrpc": "2.0",
                    "id": Value::Null,
                    "error": {
                        "code": INVALID_REQ,
                        "message": "Invalid Request: empty batch"
                    }
                });
                let out = serde_json::to_string(&err_resp).unwrap();
                stdout.write_all(format!("{out}\n").as_bytes()).await?;
                stdout.flush().await?;
                continue;
            }
            if arr.len() > MAX_BATCH {
                let err_resp = json!({
                    "jsonrpc": "2.0",
                    "id": Value::Null,
                    "error": {
                        "code": INVALID_REQ,
                        "message": format!("Batch size exceeds maximum of {MAX_BATCH}")
                    }
                });
                let out = serde_json::to_string(&err_resp).unwrap();
                stdout.write_all(format!("{out}\n").as_bytes()).await?;
                stdout.flush().await?;
                continue;
            }
            let mut resps = Vec::new();
            for item in arr {
                if let Some(r) = handle_single_request(item) {
                    resps.push(r);
                }
            }
            if !resps.is_empty() {
                let out = serde_json::to_string(&resps).unwrap();
                stdout.write_all(format!("{out}\n").as_bytes()).await?;
                stdout.flush().await?;
            }
        } else {
            if let Some(resp) = handle_single_request(&parsed) {
                let out = serde_json::to_string(&resp).unwrap();
                stdout.write_all(format!("{out}\n").as_bytes()).await?;
                stdout.flush().await?;
            }
        }
    }

    Ok(())
}

#[cfg(target_family = "wasm")]
fn main() -> std::io::Result<()> {
    use std::io::{self, BufRead, Read, Write};

    let stdin = io::stdin();
    let mut reader = io::BufReader::new(stdin);
    let mut stdout = io::stdout();

    let mut raw_buf = Vec::new();

    loop {
        raw_buf.clear();
        let mut take = (&mut reader).take((MAX_FRAME_SIZE + 1) as u64);
        let n = take.read_until(b'\n', &mut raw_buf)?;
        if n == 0 {
            break; // EOF
        }

        if raw_buf.len() > MAX_FRAME_SIZE || (!raw_buf.ends_with(b"\n") && n == (MAX_FRAME_SIZE + 1)) {
            if !raw_buf.ends_with(b"\n") {
                let mut b = [0u8; 1];
                while let Ok(1) = reader.read(&mut b) {
                    if b[0] == b'\n' {
                        break;
                    }
                }
            }
            let err_resp = json!({
                "jsonrpc": "2.0",
                "id": Value::Null,
                "error": {
                    "code": PARSE_ERR,
                    "message": "Frame exceeded maximum allowed size"
                }
            });
            let out = serde_json::to_string(&err_resp).unwrap();
            stdout.write_all(format!("{out}\n").as_bytes())?;
            stdout.flush()?;
            continue;
        }

        let line = match String::from_utf8(raw_buf.clone()) {
            Ok(s) => s,
            Err(_) => {
                let err_resp = json!({
                    "jsonrpc": "2.0",
                    "id": Value::Null,
                    "error": {
                        "code": PARSE_ERR,
                        "message": "Parse error: Invalid UTF-8"
                    }
                });
                let out = serde_json::to_string(&err_resp).unwrap();
                stdout.write_all(format!("{out}\n").as_bytes())?;
                stdout.flush()?;
                continue;
            }
        };

        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }

        let parsed: Value = match serde_json::from_str(trimmed) {
            Ok(v) => v,
            Err(e) => {
                let err_resp = json!({
                    "jsonrpc": "2.0",
                    "id": Value::Null,
                    "error": {
                        "code": PARSE_ERR,
                        "message": format!("Parse error: {e}")
                    }
                });
                let out = serde_json::to_string(&err_resp).unwrap();
                stdout.write_all(format!("{out}\n").as_bytes())?;
                stdout.flush()?;
                continue;
            }
        };

        if let Some(arr) = parsed.as_array() {
            if arr.is_empty() {
                let err_resp = json!({
                    "jsonrpc": "2.0",
                    "id": Value::Null,
                    "error": {
                        "code": INVALID_REQ,
                        "message": "Invalid Request: empty batch"
                    }
                });
                let out = serde_json::to_string(&err_resp).unwrap();
                stdout.write_all(format!("{out}\n").as_bytes())?;
                stdout.flush()?;
                continue;
            }
            if arr.len() > MAX_BATCH {
                let err_resp = json!({
                    "jsonrpc": "2.0",
                    "id": Value::Null,
                    "error": {
                        "code": INVALID_REQ,
                        "message": format!("Batch size exceeds maximum of {MAX_BATCH}")
                    }
                });
                let out = serde_json::to_string(&err_resp).unwrap();
                stdout.write_all(format!("{out}\n").as_bytes())?;
                stdout.flush()?;
                continue;
            }
            let mut resps = Vec::new();
            for item in arr {
                if let Some(r) = handle_single_request(item) {
                    resps.push(r);
                }
            }
            if !resps.is_empty() {
                let out = serde_json::to_string(&resps).unwrap();
                stdout.write_all(format!("{out}\n").as_bytes())?;
                stdout.flush()?;
            }
        } else {
            if let Some(resp) = handle_single_request(&parsed) {
                let out = serde_json::to_string(&resp).unwrap();
                stdout.write_all(format!("{out}\n").as_bytes())?;
                stdout.flush()?;
            }
        }
    }

    Ok(())
}
