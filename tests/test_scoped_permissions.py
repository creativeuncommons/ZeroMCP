#!/usr/bin/env python3
"""
Unit tests for Fine-Grained Access Control (FGAC), Multi-Tenant Scope Hierarchy,
and Ephemeral Capability Tokens in ZeroMCP.
Inspired by enterprise MCP gateway patterns, implemented with 0 dependencies.
"""
import unittest
import json
import time
import subprocess
import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ZEROMCP_PY = os.path.join(PROJECT_ROOT, "zeromcp-py", "zeromcp.py")

sys.path.insert(0, os.path.join(PROJECT_ROOT, "zeromcp-py"))
from zeromcp import (
    check_scope_authorized,
    get_client_scopes,
    register_tool,
    handle_single_request,
    parse_scope_segments,
    scope_matches,
    STATE,
    TOOLS
)
from zeromcp_token import (
    mint_token,
    verify_token,
    decode_unverified,
    TokenError,
    TokenExpiredError,
    TokenSignatureError,
    TokenMalformedError
)

class TestScopedPermissions(unittest.TestCase):

    def setUp(self):
        STATE["phase"] = "INITIALIZED"
        STATE.pop("client_scopes", None)
        STATE.pop("token_claims", None)

    def tearDown(self):
        STATE["phase"] = "INITIALIZED"
        STATE.pop("client_scopes", None)
        STATE.pop("token_claims", None)
        os.environ.pop("ZEROMCP_AUTH_SECRET", None)
        os.environ.pop("ZEROMCP_CLIENT_SCOPES", None)

    def test_scope_authorization_logic(self):
        # Unrestricted tool requires no scopes
        self.assertTrue(check_scope_authorized([], []))
        self.assertTrue(check_scope_authorized([], ["read"]))

        # Global wildcard grants all
        self.assertTrue(check_scope_authorized(["admin:write"], ["*"]))
        self.assertTrue(check_scope_authorized(["tools:read", "sports:predict"], ["*"]))

        # Exact match
        self.assertTrue(check_scope_authorized(["read"], ["read", "write"]))
        self.assertFalse(check_scope_authorized(["admin"], ["read", "write"]))

        # Prefix wildcard (e.g. sports:*)
        self.assertTrue(check_scope_authorized(["sports:read"], ["sports:*"]))
        self.assertTrue(check_scope_authorized(["sports:write:team"], ["sports:*"]))
        self.assertFalse(check_scope_authorized(["finance:read"], ["sports:*"]))

        # Multi-scope requirements
        self.assertTrue(check_scope_authorized(["sports:read", "sports:write"], ["sports:*"]))
        self.assertFalse(check_scope_authorized(["sports:read", "admin:all"], ["sports:*"]))

    def test_multi_tenant_hierarchical_scope_hierarchy(self):
        # Hierarchical dot and colon equivalence
        self.assertEqual(parse_scope_segments("workspace.finance:read"), ["workspace", "finance", "read"])
        self.assertEqual(parse_scope_segments("workspace:finance:read"), ["workspace", "finance", "read"])
        self.assertEqual(parse_scope_segments("workspace.finance.read"), ["workspace", "finance", "read"])

        # Wildcard scope covering hierarchy (e.g. workspace.* covers workspace.finance:read)
        self.assertTrue(check_scope_authorized(["workspace.finance:read"], ["workspace.*"]))
        self.assertTrue(check_scope_authorized(["workspace.finance.taxes:write"], ["workspace.*"]))
        self.assertTrue(check_scope_authorized(["workspace.finance:read"], ["workspace.finance:*"]))
        self.assertFalse(check_scope_authorized(["workspace.hr:read"], ["workspace.finance:*"]))

        # Multi-tenant partitioning
        tenant_1_grant = ["tenant.org_123:*"]
        self.assertTrue(check_scope_authorized(["tenant.org_123:tools:query"], tenant_1_grant))
        self.assertFalse(check_scope_authorized(["tenant.org_456:tools:query"], tenant_1_grant))

    def test_scope_negation_and_restrictions(self):
        # Allow all tools except execute_shell
        scoped_grants = ["tools:*", "!tools:execute_shell"]

        # Positive authorized
        self.assertTrue(check_scope_authorized(["tools:echo"], scoped_grants))
        self.assertTrue(check_scope_authorized(["tools:eval_math"], scoped_grants))
        self.assertTrue(check_scope_authorized(["tools:read_file"], scoped_grants))

        # Explicit negation must be denied
        self.assertFalse(check_scope_authorized(["tools:execute_shell"], scoped_grants))

        # Hierarchical negation (e.g. workspace.* but not secret)
        tenant_grants = ["workspace.*", "!workspace.secret:*"]
        self.assertTrue(check_scope_authorized(["workspace.billing:read"], tenant_grants))
        self.assertTrue(check_scope_authorized(["workspace.crm:contacts:list"], tenant_grants))
        self.assertFalse(check_scope_authorized(["workspace.secret:keys"], tenant_grants))
        self.assertFalse(check_scope_authorized(["workspace.secret.api:tokens"], tenant_grants))

        # Negation on default tool name check
        self.assertFalse(check_scope_authorized([], ["tools:*", "!tools:danger"], tool_name="danger"))
        self.assertTrue(check_scope_authorized([], ["tools:*", "!tools:danger"], tool_name="safe"))

    def test_ephemeral_token_generator_and_verifier(self):
        secret = "super-secret-key-32-chars-long!"
        scopes = ["workspace.finance:*", "!workspace.finance:admin", "tools:*"]

        # Mint token
        token = mint_token(secret, scopes, sub="subagent-42", ttl_seconds=120, iss="hypergate", aud="zeromcp-prod")
        self.assertIsInstance(token, str)
        self.assertEqual(len(token.split(".")), 3)

        # Verify token successfully
        claims = verify_token(token, secret, expected_aud="zeromcp-prod")
        self.assertEqual(claims["sub"], "subagent-42")
        self.assertEqual(claims["scopes"], scopes)
        self.assertEqual(claims["iss"], "hypergate")
        self.assertGreater(claims["exp"], claims["iat"])

        # Decode unverified
        hdr, payload = decode_unverified(token)
        self.assertEqual(hdr["alg"], "HS256")
        self.assertEqual(payload["sub"], "subagent-42")

        # Tampered signature detection
        parts = token.split(".")
        tampered_sig = parts[0] + "." + parts[1] + ".invalidSignature"
        with self.assertRaises(TokenSignatureError):
            verify_token(tampered_sig, secret)

        # Wrong secret key detection
        with self.assertRaises(TokenSignatureError):
            verify_token(token, "wrong-secret-key")

        # Expired token rejection
        expired_token = mint_token(secret, scopes, sub="subagent-expired", ttl_seconds=-10)
        with self.assertRaises(TokenExpiredError):
            verify_token(expired_token, secret)

        # Audience mismatch detection
        with self.assertRaises(TokenError):
            verify_token(token, secret, expected_aud="wrong-audience")

    def test_get_client_scopes_extraction(self):
        # 1. From _meta.scopes
        req1 = {"_meta": {"scopes": ["tool:exec", "query:read"]}}
        self.assertEqual(get_client_scopes(req1), ["tool:exec", "query:read"])

        # 2. From direct scopes
        req2 = {"scopes": ["sports:*"]}
        self.assertEqual(get_client_scopes(req2), ["sports:*"])

        # 3. From STATE
        STATE["client_scopes"] = ["tenant_a:*"]
        self.assertEqual(get_client_scopes({}), ["tenant_a:*"])
        STATE.pop("client_scopes", None)

        # 4. From ENV var
        os.environ["ZEROMCP_CLIENT_SCOPES"] = "env:scope1, env:scope2"
        try:
            self.assertEqual(get_client_scopes({}), ["env:scope1", "env:scope2"])
        finally:
            os.environ.pop("ZEROMCP_CLIENT_SCOPES", None)

        # 5. Default fallback to wildcard
        self.assertEqual(get_client_scopes({}), ["*"])

    def test_handshake_initialize_with_auth_secret(self):
        secret = "cluster-secret-key-12345"
        os.environ["ZEROMCP_AUTH_SECRET"] = secret
        STATE["phase"] = "UNINITIALIZED"

        # 1. Initialize without token fails closed
        init_req_no_token = {
            "jsonrpc": "2.0",
            "id": 100,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"}
            }
        }
        res_fail = handle_single_request(init_req_no_token)
        self.assertIn("error", res_fail)
        self.assertEqual(res_fail["error"]["code"], -32602)
        self.assertIn("Authentication required", res_fail["error"]["message"])

        # 2. Initialize with valid capability token succeeds
        valid_token = mint_token(secret, ["tools:echo", "vault:read"], sub="subagent-test", ttl_seconds=300)
        init_req_valid = {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0", "token": valid_token}
            }
        }
        res_ok = handle_single_request(init_req_valid)
        self.assertIn("result", res_ok)
        self.assertEqual(STATE["phase"], "NEGOTIATED")
        self.assertEqual(STATE["client_scopes"], ["tools:echo", "vault:read"])
        self.assertEqual(STATE["token_claims"]["sub"], "subagent-test")

    def test_rpc_scope_enforcement_and_discovery(self):
        # Register a privileged tool
        register_tool(
            name="secure_vault_wipe",
            description="Dangerously wipe vault data",
            input_schema={"type": "object", "properties": {"confirm": {"type": "string"}}, "required": ["confirm"]},
            handler=lambda a: "WIPED",
            required_scopes=["vault:admin"]
        )

        # 1. List tools with unprivileged scope without filter -> tool appears with scopes
        list_req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"scopes": ["vault:read"]}}
        res = handle_single_request(list_req)
        tools = res["result"]["tools"]
        wipe_tool = next((t for t in tools if t["name"] == "secure_vault_wipe"), None)
        self.assertIsNotNone(wipe_tool)
        self.assertEqual(wipe_tool.get("scopes"), ["vault:admin"])

        # 2. List tools with filter_unauthorized=True -> secure_vault_wipe filtered out
        list_filter_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"scopes": ["vault:read"], "filter_unauthorized": True}}
        res_filter = handle_single_request(list_filter_req)
        filtered_tools = res_filter["result"]["tools"]
        self.assertIsNone(next((t for t in filtered_tools if t["name"] == "secure_vault_wipe"), None))

        # 3. Call tool with insufficient scopes -> Rejected with -32602
        call_forbidden = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "secure_vault_wipe",
                "arguments": {"confirm": "YES"},
                "scopes": ["vault:read"]
            }
        }
        res_forbidden = handle_single_request(call_forbidden)
        self.assertIn("error", res_forbidden)
        self.assertEqual(res_forbidden["error"]["code"], -32602)
        self.assertIn("Forbidden", res_forbidden["error"]["message"])

        # 4. Call tool with negation restriction -> Rejected with -32602
        call_negated = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "secure_vault_wipe",
                "arguments": {"confirm": "YES"},
                "scopes": ["vault:*", "!vault:admin"]
            }
        }
        res_negated = handle_single_request(call_negated)
        self.assertIn("error", res_negated)
        self.assertEqual(res_negated["error"]["code"], -32602)
        self.assertIn("Forbidden", res_negated["error"]["message"])

        # 5. Call tool with authorized scope -> Success
        call_allowed = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "secure_vault_wipe",
                "arguments": {"confirm": "YES"},
                "scopes": ["vault:admin"]
            }
        }
        res_allowed = handle_single_request(call_allowed)
        self.assertIn("result", res_allowed)
        self.assertEqual(res_allowed["result"]["content"][0]["text"], "WIPED")

        # Clean up test tool
        TOOLS.pop("secure_vault_wipe", None)

if __name__ == "__main__":
    unittest.main()
