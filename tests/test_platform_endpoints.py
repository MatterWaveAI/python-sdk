"""Unit tests for the `/api/v1/*` OIDC, OAuth, universal connector catalog,
decision trail, replay, checkpoint, reliability, telemetry, HITL, guardrails,
and tool planning client methods on `MatterWave` and `AsyncMatterWave`."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import REQUEST_ID

from matterwave_sdk import (
    GRT_HF_MODELS,
    GRT_WHITEPAPER_SHA256,
    AsyncMatterWave,
    CheckpointList,
    ConnectorCatalogList,
    CredentialProxyResponse,
    DecisionTrail,
    EnforceClaimsResult,
    GrtHfModelSpec,
    GrtSuiteManifest,
    GuardrailEvaluation,
    HitlInteraction,
    HitlInteractionList,
    MatterWave,
    OAuthAuthenticator,
    OidcConfig,
    OidcSyncResult,
    ReliabilityMetrics,
    ReliabilitySloReport,
    ReliabilityTrends,
    ReplayRunResult,
    RerankResponse,
    ResumeRunResult,
    SpeechSynthesisResponse,
    TelemetryExecutionMetrics,
    TelemetryStackStatus,
    ToolPlan,
)

H = {"X-Request-Id": REQUEST_ID}


def sent(route) -> httpx.Request:
    return route.calls.last.request


def body_of(route) -> dict:
    return json.loads(sent(route).content)


def test_oidc_config_sync_and_enforce_claims(api, client: MatterWave) -> None:
    cfg_route = api.get("/api/v1/oidc/config").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "adapter": "zitadel",
                "issuer_url": "https://auth.matterwave.test",
                "token_endpoint": "https://auth.matterwave.test/oauth/v2/token",
                "jwks_uri": "https://auth.matterwave.test/oauth/v2/keys",
                "admin_url": "https://auth.matterwave.test/admin/v1",
                "project_id": "proj-1",
                "connected": True,
                "status": "ready",
            },
        )
    )
    cfg = client.get_oidc_config()
    assert isinstance(cfg, OidcConfig)
    assert cfg.adapter == "zitadel"
    assert cfg.connected is True
    assert cfg.request_id == REQUEST_ID
    assert cfg_route.called

    sync_route = api.post("/api/v1/agent-keys/key-01/oidc-sync").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "key_id": "key-01",
                "idp_client_id": "mw-agent-key-01@matterwave.local",
                "oidc_issuer": "https://auth.matterwave.test",
                "adapter": "zitadel",
                "synced_at": "2026-11-01T10:00:00Z",
                "scopes": ["openid", "urn:matterwave:org:acme"],
                "resource_pattern": "org:acme/agents/*",
                "a2a": {
                    "allowed_tools": ["Read", "Grep"],
                    "denied_tools": ["Bash"],
                    "allowed_models": ["claude-sonnet-4-6"],
                    "cwd_scope": "/workspace/app",
                    "limits": {
                        "max_tokens": 50000,
                        "max_duration_seconds": 600,
                        "max_cost_usd": 2.5,
                    },
                },
            },
        )
    )
    synced = client.sync_agent_key_oidc("key-01")
    assert isinstance(synced, OidcSyncResult)
    assert synced.key_id == "key-01"
    assert synced.a2a.allowed_tools == ["Read", "Grep"]
    assert synced.a2a.limits.max_cost_usd == 2.5
    assert synced.request_id == REQUEST_ID
    assert sync_route.called

    enforce_route = api.post("/api/v1/oidc/enforce-claims").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "allowed": True,
                "violation_claim": None,
                "detail": "All A2A claims satisfied",
                "effective_tools": ["Read"],
                "resolved_cwd": "/workspace/app",
                "limits": {
                    "max_tokens": 50000,
                    "max_duration_seconds": 600,
                    "max_cost_usd": 2.5,
                },
            },
        )
    )
    enforced = client.enforce_oidc_claims(
        {
            "key_id": "key-01",
            "requested_tools": ["Read"],
            "requested_model": "claude-sonnet-4-6",
            "requested_cwd": "/workspace/app",
        }
    )
    assert isinstance(enforced, EnforceClaimsResult)
    assert enforced.allowed is True
    assert enforced.effective_tools == ["Read"]
    assert enforced.limits.max_tokens == 50000
    assert body_of(enforce_route)["key_id"] == "key-01"


def test_connector_catalog_and_oauth_broker(api, client: MatterWave) -> None:
    cat_route = api.get("/api/v1/connectors/catalog").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "data": [
                    {
                        "provider_id": "github",
                        "name": "GitHub",
                        "category": "devtools",
                        "auth_types": ["oauth2"],
                        "authorize_url": "https://github.com/login/oauth/authorize",
                        "token_url": "https://github.com/login/oauth/access_token",
                        "api_base_url": "https://api.github.com",
                        "default_scopes": ["repo", "read:org"],
                        "pkce_supported": True,
                        "refresh_supported": True,
                        "revoke_url": "https://api.github.com/applications/client_id/token",
                        "docs_url": "https://docs.github.com",
                        "icon": "github",
                    }
                ],
                "total": 1,
            },
        )
    )
    catalog = client.list_connector_catalog(category="devtools", q="git")
    assert isinstance(catalog, ConnectorCatalogList)
    assert len(catalog) == 1
    assert list(catalog)[0].provider_id == "github"
    assert catalog.total == 1
    assert dict(sent(cat_route).url.params) == {"category": "devtools", "q": "git"}

    reg_route = api.post("/api/v1/oauth/register").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "authenticator_id": "auth-gh-01",
                "name": "GitHub App",
                "provider_id": "github",
                "org_id": "acme",
                "client_id": "Iv1.123456",
                "client_secret_masked": "ghs_****",
                "authorize_url": "https://github.com/login/oauth/authorize",
                "token_url": "https://github.com/login/oauth/access_token",
                "redirect_uri": "https://matterwave.test/api/v1/oauth/callback",
                "scopes": ["repo"],
                "api_base_url": "https://api.github.com",
                "tenant_id": None,
                "extra_authorize_params": {},
                "created_at": "2026-11-01T10:00:00Z",
            },
        )
    )
    auth = client.register_oauth_authenticator(
        {
            "name": "GitHub App",
            "provider_id": "github",
            "client_id": "Iv1.123456",
            "client_secret": "secret-val",
            "scopes": ["repo"],
        }
    )
    assert isinstance(auth, OAuthAuthenticator)
    assert auth.authenticator_id == "auth-gh-01"
    assert auth.client_secret_masked == "ghs_****"
    assert body_of(reg_route)["provider_id"] == "github"

    proxy_route = api.post("/api/v1/oauth/proxy/cred-01").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "credential_id": "cred-01",
                "provider_id": "github",
                "method": "POST",
                "target_url": "https://api.github.com/repos/acme/app/issues",
                "status_code": 201,
                "auto_refreshed": False,
                "broker": "fiberwise-credential-service",
                "headers_injected": ["Authorization"],
                "data": {"id": 42, "title": "Bug report"},
            },
        )
    )
    proxied = client.proxy_oauth_request(
        "cred-01",
        "/repos/acme/app/issues",
        method="post",
        params={"state": "open"},
        headers={"X-Custom": "1"},
        body={"title": "Bug report"},
        dry_run=True,
    )
    assert isinstance(proxied, CredentialProxyResponse)
    assert proxied.credential_id == "cred-01"
    assert proxied.status_code == 201
    assert proxied.data["id"] == 42
    sent_proxy = body_of(proxy_route)
    assert sent_proxy == {
        "endpoint": "/repos/acme/app/issues",
        "method": "POST",
        "params": {"state": "open"},
        "headers": {"X-Custom": "1"},
        "body": {"title": "Bug report"},
        "dry_run": True,
    }


def test_decision_trail_replay_checkpoints_and_resume(api, client: MatterWave) -> None:
    trail_route = api.get("/api/v1/runs/run-123/decision-trail").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "run_id": "run-123",
                "org": "acme",
                "nodes": [
                    {
                        "id": "node-1",
                        "type": "tool_call",
                        "label": "Search codebase",
                        "step_id": "step-1",
                        "timestamp": "2026-11-01T10:00:00Z",
                        "duration_ms": 140,
                        "confidence": 0.95,
                        "tokens_used": 320,
                        "cost_usd": 0.002,
                        "outcome": "succeeded",
                        "rationale": "Locate handler definition",
                        "evidence": [{"file": "main.py"}],
                        "metadata": {"tool": "Grep"},
                    }
                ],
                "edges": [
                    {
                        "from_node": "node-0",
                        "to_node": "node-1",
                        "edge_type": "sequential",
                        "label": "next",
                    }
                ],
                "statistics": {
                    "total_nodes": 1,
                    "total_edges": 1,
                    "tool_calls_count": 1,
                    "total_tokens": 320,
                    "total_cost_usd": 0.002,
                    "duration_ms": 140,
                    "success": True,
                },
            },
        )
    )
    trail = client.get_run_decision_trail("run-123")
    assert isinstance(trail, DecisionTrail)
    assert trail.run_id == "run-123"
    assert len(trail.nodes) == 1
    assert trail.nodes[0].label == "Search codebase"
    assert trail.statistics.total_tokens == 320
    assert trail_route.called

    replay_route = api.post("/api/v1/runs/run-123/replay").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "replay_id": "rpl-01",
                "run_id": "run-123",
                "mode": "counterfactual",
                "success": True,
                "original_outcome": {"status": "completed"},
                "replayed_outcome": {"status": "completed"},
                "differences": [
                    {
                        "step_id": "step-1",
                        "field": "model",
                        "original": "claude-sonnet-4-6",
                        "replayed": "claude-opus-4-6",
                        "significance": "minor",
                    }
                ],
                "duration_ms": 95,
                "created_at": "2026-11-01T10:01:00Z",
            },
        )
    )
    replay = client.replay_run(
        "run-123",
        mode="counterfactual",
        overrides={"model": "claude-opus-4-6"},
        from_step_id="step-1",
    )
    assert isinstance(replay, ReplayRunResult)
    assert replay.replay_id == "rpl-01"
    assert replay.differences[0].field == "model"
    assert body_of(replay_route) == {
        "mode": "counterfactual",
        "overrides": {"model": "claude-opus-4-6"},
        "from_step_id": "step-1",
    }

    cp_route = api.get("/api/v1/runs/run-123/checkpoints").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "run_id": "run-123",
                "data": [
                    {
                        "checkpoint_id": "cp-01",
                        "thread_id": "thread-1",
                        "run_id": "run-123",
                        "step_id": "step-1",
                        "step_index": 0,
                        "timestamp": "2026-11-01T10:00:00Z",
                        "state": {"cursor": 1},
                        "metadata": {"saver": "sql"},
                        "parent_checkpoint_id": None,
                    }
                ],
            },
        )
    )
    checkpoints = client.list_run_checkpoints("run-123")
    assert isinstance(checkpoints, CheckpointList)
    assert len(checkpoints) == 1
    assert list(checkpoints)[0].checkpoint_id == "cp-01"
    assert cp_route.called

    resume_route = api.post("/api/v1/runs/run-123/resume").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "run_id": "run-456",
                "source_run_id": "run-123",
                "resumed_from_checkpoint_id": "cp-01",
                "resumed_from_step": "step-1",
                "state": "queued",
                "restored_state": {"cursor": 1, "retry": True},
                "created_at": "2026-11-01T10:02:00Z",
            },
        )
    )
    resumed = client.resume_run("run-123", "cp-01", state_overrides={"retry": True})
    assert isinstance(resumed, ResumeRunResult)
    assert resumed.run_id == "run-456"
    assert resumed.resumed_from_checkpoint_id == "cp-01"
    assert body_of(resume_route) == {
        "checkpoint_id": "cp-01",
        "state_overrides": {"retry": True},
    }


def test_reliability_telemetry_hitl_guardrails_and_tool_plan(api, client: MatterWave) -> None:
    rel_route = api.get("/api/v1/reliability/metrics").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "org": "acme",
                "agent_id": "agent-1",
                "sr": 0.98,
                "cr": 0.96,
                "pc": 0.99,
                "hir": 0.02,
                "ma": 0.97,
                "tcl": 120.5,
                "wct": 4500.0,
                "total_runs": 50,
                "window": "24h",
            },
        )
    )
    metrics = client.get_reliability_metrics(agent_id="agent-1")
    assert isinstance(metrics, ReliabilityMetrics)
    assert metrics.sr == 0.98
    assert metrics.agent_id == "agent-1"
    assert dict(sent(rel_route).url.params) == {"agent_id": "agent-1"}

    api.get("/api/v1/reliability/slo").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "org": "acme",
                "mtte_p95_ms": 180.0,
                "mtte_target_ms": 500.0,
                "mtte_compliant": True,
                "rsr": 0.995,
                "rsr_target": 0.99,
                "rsr_compliant": True,
                "overall_compliant": True,
                "total_measurements": 50,
                "total_replays": 10,
            },
        )
    )
    slo = client.get_reliability_slo()
    assert isinstance(slo, ReliabilitySloReport)
    assert slo.overall_compliant is True

    api.get("/api/v1/reliability/trends").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "org": "acme",
                "buckets": [
                    {
                        "bucket": "2026-11-01T00:00:00Z",
                        "sr": 0.98,
                        "cr": 0.97,
                        "pc": 0.99,
                        "hir": 0.01,
                        "ma": 0.98,
                        "tcl": 110.0,
                        "wct": 4100.0,
                        "runs": 25,
                    }
                ],
                "anomalies": [
                    {
                        "metric": "tcl",
                        "severity": "warning",
                        "current_value": 250.0,
                        "baseline_value": 110.0,
                        "threshold": 200.0,
                        "message": "Tool call latency spike",
                        "detected_at": "2026-11-01T09:00:00Z",
                    }
                ],
            },
        )
    )
    trends = client.get_reliability_trends()
    assert isinstance(trends, ReliabilityTrends)
    assert len(trends.buckets) == 1
    assert trends.anomalies[0].metric == "tcl"

    api.get("/api/v1/telemetry/status").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "org": "acme",
                "otel_enabled": True,
                "prometheus_scraped": True,
                "grafana_dashboards": ["agent-overview"],
                "tempo_tracing_enabled": True,
                "loki_logging_enabled": True,
                "endpoints": [
                    {
                        "service": "prometheus",
                        "url": "http://prometheus:9090",
                        "healthy": True,
                        "protocol": "http",
                    }
                ],
            },
        )
    )
    tel_status = client.get_telemetry_status()
    assert isinstance(tel_status, TelemetryStackStatus)
    assert tel_status.otel_enabled is True
    assert tel_status.endpoints[0].service == "prometheus"

    api.get("/api/v1/telemetry/executions/exec-99/metrics").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "execution_id": "exec-99",
                "pipeline_name": "code-review",
                "status": "completed",
                "total_duration_ms": 1240.5,
                "total_tokens": 1500,
                "total_cost_usd": 0.015,
                "trace_id": "trace-abc",
                "steps": [
                    {
                        "step_id": "s1",
                        "step_name": "analyze",
                        "status": "completed",
                        "duration_ms": 1240.5,
                        "tokens_used": 1500,
                        "cost_usd": 0.015,
                        "retries": 0,
                    }
                ],
            },
        )
    )
    exec_tel = client.get_execution_telemetry("exec-99")
    assert isinstance(exec_tel, TelemetryExecutionMetrics)
    assert exec_tel.execution_id == "exec-99"
    assert exec_tel.steps[0].step_name == "analyze"

    hitl_list_route = api.get("/api/v1/hitl/interactions").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "data": [
                    {
                        "interaction_id": "hitl-01",
                        "execution_id": "exec-99",
                        "run_id": "run-123",
                        "step_id": "step-2",
                        "org": "acme",
                        "title": "Approve deployment",
                        "prompt": "Proceed with production rollout?",
                        "channels": ["web", "slack"],
                        "status": "pending",
                        "ui_schema": {"type": "object"},
                        "context": {"env": "prod"},
                        "decision": None,
                        "response_data": None,
                        "responded_by": None,
                        "created_at": "2026-11-01T10:00:00Z",
                        "responded_at": None,
                    }
                ],
                "total": 1,
            },
        )
    )
    hitl_list = client.list_hitl_interactions(status="pending")
    assert isinstance(hitl_list, HitlInteractionList)
    assert len(hitl_list) == 1
    assert dict(sent(hitl_list_route).url.params) == {"status": "pending"}

    hitl_resp_route = api.post("/api/v1/hitl/interactions/hitl-01/respond").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "interaction_id": "hitl-01",
                "execution_id": "exec-99",
                "run_id": "run-123",
                "step_id": "step-2",
                "org": "acme",
                "title": "Approve deployment",
                "prompt": "Proceed with production rollout?",
                "channels": ["web", "slack"],
                "status": "approved",
                "ui_schema": {"type": "object"},
                "context": {"env": "prod"},
                "decision": "approved",
                "response_data": {"note": "LGTM"},
                "responded_by": "alice@acme.test",
                "created_at": "2026-11-01T10:00:00Z",
                "responded_at": "2026-11-01T10:05:00Z",
            },
        )
    )
    hitl_resolved = client.respond_hitl_interaction(
        "hitl-01",
        "approved",
        response_data={"note": "LGTM"},
        responded_by="alice@acme.test",
    )
    assert isinstance(hitl_resolved, HitlInteraction)
    assert hitl_resolved.decision == "approved"
    assert body_of(hitl_resp_route) == {
        "decision": "approved",
        "response_data": {"note": "LGTM"},
        "responded_by": "alice@acme.test",
    }

    guard_route = api.post("/api/v1/guardrails/evaluate").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "rail_type": "input",
                "action": "allow",
                "allowed": True,
                "modified_content": "Summarize the quarterly report",
                "refusal_message": None,
                "triggered_rails": [],
                "violations": [],
            },
        )
    )
    verdict = client.evaluate_guardrails(
        "input",
        "Summarize the quarterly report",
        policies=["pii_redaction"],
    )
    assert isinstance(verdict, GuardrailEvaluation)
    assert verdict.allowed is True
    assert body_of(guard_route) == {
        "rail_type": "input",
        "content": "Summarize the quarterly report",
        "policies": ["pii_redaction"],
    }

    plan_route = api.post("/api/v1/tools/plan").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "plan_id": "plan-01",
                "task": "Find and fix lint errors",
                "steps": [
                    {
                        "step_id": "step_1",
                        "tool_name": "Grep",
                        "description": "Locate lint issues",
                        "arguments": {"pattern": "TODO"},
                        "depends_on": [],
                        "estimated_cost_usd": 0.001,
                        "estimated_time_ms": 200,
                        "fallback_tool": "Read",
                    }
                ],
                "execution_order": [["step_1"]],
                "total_estimated_cost_usd": 0.001,
                "total_estimated_time_ms": 200,
                "confidence": 0.92,
            },
        )
    )
    plan = client.plan_tools("Find and fix lint errors", ["Grep", "Read", "Edit"])
    assert isinstance(plan, ToolPlan)
    assert plan.plan_id == "plan-01"
    assert plan.steps[0].tool_name == "Grep"
    assert body_of(plan_route) == {
        "task": "Find and fix lint errors",
        "goal": "Find and fix lint errors",
        "available_tools": ["Grep", "Read", "Edit"],
    }


@pytest.mark.asyncio()
async def test_async_platform_methods(api, async_client: AsyncMatterWave) -> None:
    api.get("/api/v1/oidc/config").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "adapter": "keycloak",
                "issuer_url": "https://kc.matterwave.test/realms/mw",
                "token_endpoint": "https://kc.matterwave.test/token",
                "jwks_uri": "https://kc.matterwave.test/certs",
                "admin_url": "https://kc.matterwave.test/admin",
                "project_id": "mw",
                "connected": True,
                "status": "ready",
            },
        )
    )
    cfg = await async_client.get_oidc_config()
    assert cfg.adapter == "keycloak"

    api.post("/api/v1/agent-keys/k1/oidc-sync").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "key_id": "k1",
                "idp_client_id": "c1",
                "oidc_issuer": "https://kc.matterwave.test",
                "adapter": "keycloak",
                "synced_at": "2026-11-01T10:00:00Z",
                "scopes": ["openid"],
                "resource_pattern": "*",
                "a2a": {
                    "allowed_tools": [],
                    "denied_tools": [],
                    "allowed_models": [],
                    "cwd_scope": "/",
                    "limits": {
                        "max_tokens": 1000,
                        "max_duration_seconds": 60,
                        "max_cost_usd": 1.0,
                    },
                },
            },
        )
    )
    assert (await async_client.sync_agent_key_oidc("k1")).key_id == "k1"

    api.post("/api/v1/oidc/enforce-claims").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "allowed": True,
                "violation_claim": None,
                "detail": "ok",
                "effective_tools": [],
                "resolved_cwd": "/",
                "limits": {
                    "max_tokens": 1000,
                    "max_duration_seconds": 60,
                    "max_cost_usd": 1.0,
                },
            },
        )
    )
    assert (await async_client.enforce_oidc_claims({"key_id": "k1"})).allowed is True

    api.get("/api/v1/connectors/catalog").mock(
        return_value=httpx.Response(200, headers=H, json={"data": [], "total": 0})
    )
    cat = await async_client.list_connector_catalog()
    assert cat.total == 0

    api.post("/api/v1/oauth/register").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "authenticator_id": "a1",
                "name": "GH",
                "provider_id": "github",
                "org_id": "acme",
                "client_id": "cid",
                "client_secret_masked": "***",
                "authorize_url": "https://github.com/login/oauth/authorize",
                "token_url": "https://github.com/login/oauth/access_token",
                "redirect_uri": "https://matterwave.test/cb",
                "scopes": [],
                "api_base_url": "https://api.github.com",
                "tenant_id": None,
                "extra_authorize_params": {},
                "created_at": "2026-11-01T10:00:00Z",
            },
        )
    )
    assert (
        await async_client.register_oauth_authenticator(
            {"name": "GH", "provider_id": "github", "client_id": "cid"}
        )
    ).authenticator_id == "a1"

    api.post("/api/v1/oauth/proxy/cred-1").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "credential_id": "cred-1",
                "provider_id": "github",
                "method": "GET",
                "target_url": "https://api.github.com/user",
                "status_code": 200,
                "auto_refreshed": False,
                "broker": "fiberwise-credential-service",
                "headers_injected": ["Authorization"],
                "data": {"login": "octocat"},
            },
        )
    )
    assert (await async_client.proxy_oauth_request("cred-1", "/user")).status_code == 200

    api.get("/api/v1/runs/r1/decision-trail").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "run_id": "r1",
                "org": "acme",
                "nodes": [],
                "edges": [],
                "statistics": {
                    "total_nodes": 0,
                    "total_edges": 0,
                    "tool_calls_count": 0,
                    "total_tokens": 0,
                    "total_cost_usd": 0.0,
                    "duration_ms": 0,
                    "success": True,
                },
            },
        )
    )
    assert (await async_client.get_run_decision_trail("r1")).run_id == "r1"

    api.post("/api/v1/runs/r1/replay").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "replay_id": "rp1",
                "run_id": "r1",
                "mode": "simulated",
                "success": True,
                "original_outcome": {},
                "replayed_outcome": {},
                "differences": [],
                "duration_ms": 10,
                "created_at": "2026-11-01T10:00:00Z",
            },
        )
    )
    assert (await async_client.replay_run("r1")).replay_id == "rp1"

    api.get("/api/v1/runs/r1/checkpoints").mock(
        return_value=httpx.Response(200, headers=H, json={"run_id": "r1", "data": []})
    )
    assert len(await async_client.list_run_checkpoints("r1")) == 0

    api.post("/api/v1/runs/r1/resume").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "run_id": "r2",
                "source_run_id": "r1",
                "resumed_from_checkpoint_id": "cp1",
                "resumed_from_step": "s1",
                "state": "queued",
                "restored_state": {},
                "created_at": "2026-11-01T10:00:00Z",
            },
        )
    )
    assert (await async_client.resume_run("r1", "cp1")).run_id == "r2"

    api.get("/api/v1/reliability/metrics").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "org": "acme",
                "agent_id": None,
                "sr": 1.0,
                "cr": 1.0,
                "pc": 1.0,
                "hir": 0.0,
                "ma": 1.0,
                "tcl": 50.0,
                "wct": 1000.0,
                "total_runs": 1,
                "window": "24h",
            },
        )
    )
    assert (await async_client.get_reliability_metrics()).sr == 1.0

    api.get("/api/v1/reliability/slo").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "org": "acme",
                "mtte_p95_ms": 100.0,
                "mtte_target_ms": 500.0,
                "mtte_compliant": True,
                "rsr": 1.0,
                "rsr_target": 0.99,
                "rsr_compliant": True,
                "overall_compliant": True,
                "total_measurements": 1,
                "total_replays": 1,
            },
        )
    )
    assert (await async_client.get_reliability_slo()).overall_compliant is True

    api.get("/api/v1/reliability/trends").mock(
        return_value=httpx.Response(
            200, headers=H, json={"org": "acme", "buckets": [], "anomalies": []}
        )
    )
    assert (await async_client.get_reliability_trends()).org == "acme"

    api.get("/api/v1/telemetry/status").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "org": "acme",
                "otel_enabled": True,
                "prometheus_scraped": True,
                "grafana_dashboards": [],
                "tempo_tracing_enabled": True,
                "loki_logging_enabled": True,
                "endpoints": [],
            },
        )
    )
    assert (await async_client.get_telemetry_status()).otel_enabled is True

    api.get("/api/v1/telemetry/executions/ex1/metrics").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "execution_id": "ex1",
                "pipeline_name": "p1",
                "status": "completed",
                "total_duration_ms": 10.0,
                "total_tokens": 5,
                "total_cost_usd": 0.001,
                "trace_id": "t1",
                "steps": [],
            },
        )
    )
    assert (await async_client.get_execution_telemetry("ex1")).execution_id == "ex1"

    api.get("/api/v1/hitl/interactions").mock(
        return_value=httpx.Response(200, headers=H, json={"data": [], "total": 0})
    )
    assert (await async_client.list_hitl_interactions()).total == 0

    api.post("/api/v1/hitl/interactions/h1/respond").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "interaction_id": "h1",
                "execution_id": "ex1",
                "run_id": "r1",
                "step_id": "s1",
                "org": "acme",
                "title": "T",
                "prompt": "P",
                "channels": ["web"],
                "status": "approved",
                "ui_schema": {},
                "context": {},
                "decision": "approved",
                "response_data": None,
                "responded_by": "u1",
                "created_at": "2026-11-01T10:00:00Z",
                "responded_at": "2026-11-01T10:01:00Z",
            },
        )
    )
    assert (await async_client.respond_hitl_interaction("h1", "approved")).status == "approved"

    api.post("/api/v1/guardrails/evaluate").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "rail_type": "output",
                "action": "allow",
                "allowed": True,
                "modified_content": "hi",
                "refusal_message": None,
                "triggered_rails": [],
                "violations": [],
            },
        )
    )
    assert (await async_client.evaluate_guardrails("output", "hi")).allowed is True

    api.post("/api/v1/tools/plan").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "plan_id": "plan-async",
                "task": "Run tests",
                "steps": [],
                "execution_order": [],
                "total_estimated_cost_usd": 0.0,
                "total_estimated_time_ms": 100,
                "confidence": 0.9,
            },
        )
    )
    plan = await async_client.plan_tools("Run tests")
    assert plan.plan_id == "plan-async"


def test_validation_rejects_blank_required_inputs(client: MatterWave) -> None:
    with pytest.raises(ValueError, match="key_id"):
        client.sync_agent_key_oidc("bad/id")
    with pytest.raises(ValueError, match="payload"):
        client.enforce_oidc_claims({})
    with pytest.raises(ValueError, match="payload"):
        client.register_oauth_authenticator({})
    with pytest.raises(ValueError, match="endpoint"):
        client.proxy_oauth_request("cred-1", "   ")
    with pytest.raises(ValueError, match="rail_type"):
        client.evaluate_guardrails("", "hello")
    with pytest.raises(ValueError, match="goal"):
        client.plan_tools("   ")
    with pytest.raises(ValueError, match="query"):
        client.rerank("   ", ["doc 1"])
    with pytest.raises(ValueError, match="documents"):
        client.rerank("query", [])
    with pytest.raises(ValueError, match="input"):
        client.synthesize_speech("   ")


def test_grt_hf_models_catalog_rerank_tts_and_suite(api, client: MatterWave) -> None:
    assert len(GRT_HF_MODELS) == 9
    expected_ids = [
        "MatterWaveAI/grt-30m",
        "MatterWaveAI/grt-30m-chat",
        "MatterWaveAI/grt-30m-mor",
        "MatterWaveAI/grt-30m-embedding",
        "MatterWaveAI/grt-30m-reranker",
        "MatterWaveAI/grt-30m-vl",
        "MatterWaveAI/grt-30m-tts",
        "MatterWaveAI/grt-30m-omni",
        "MatterWaveAI/grt-30m-tokenizer",
    ]
    assert [m.id for m in GRT_HF_MODELS] == expected_ids
    for spec in GRT_HF_MODELS:
        assert isinstance(spec, GrtHfModelSpec)
        assert spec["id"] == spec.id
        assert spec.vocab_size == 151669
        assert spec.whitepaper_sha256 == GRT_WHITEPAPER_SHA256

    rerank_route = api.post("/v1/rerank").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "id": "rerank-01",
                "model": "MatterWaveAI/grt-30m-reranker",
                "results": [
                    {
                        "index": 1,
                        "relevance_score": 0.965,
                        "document": {"text": "Fermionic antisymmetric attention"},
                    },
                    {
                        "index": 0,
                        "relevance_score": 0.312,
                        "document": {"text": "General background note"},
                    },
                ],
                "usage": {"total_tokens": 24},
            },
        )
    )
    ranked = client.rerank(
        "fermionic attention",
        ["General background note", "Fermionic antisymmetric attention"],
        top_n=2,
    )
    assert isinstance(ranked, RerankResponse)
    assert ranked.model == "MatterWaveAI/grt-30m-reranker"
    assert len(ranked.results) == 2
    assert ranked.results[0].index == 1
    assert ranked.results[0].relevance_score == 0.965
    assert body_of(rerank_route) == {
        "model": "MatterWaveAI/grt-30m-reranker",
        "query": "fermionic attention",
        "documents": ["General background note", "Fermionic antisymmetric attention"],
        "return_documents": True,
        "top_n": 2,
    }

    tts_route = api.post("/v1/audio/speech").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "id": "tts-01",
                "model": "MatterWaveAI/grt-30m-tts",
                "voice": "aurora",
                "format": "json",
                "sample_rate": 24000,
                "duration_ms": 520,
                "audio_base64": "UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=",
                "phoneme_tokens": [12, 44, 108],
            },
        )
    )
    speech = client.synthesize_speech("Topological gauge invariance.")
    assert isinstance(speech, SpeechSynthesisResponse)
    assert speech.model == "MatterWaveAI/grt-30m-tts"
    assert speech.voice == "aurora"
    assert speech.sample_rate == 24000
    assert body_of(tts_route) == {
        "model": "MatterWaveAI/grt-30m-tts",
        "input": "Topological gauge invariance.",
        "voice": "aurora",
        "response_format": "json",
        "speed": 1.0,
    }

    api.get("/api/v1/models/grt-suite").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "suite": "grt-30m",
                "organization": "MatterWaveAI",
                "whitepaper_sha256": GRT_WHITEPAPER_SHA256,
                "total": 9,
                "models": [spec.__dict__ for spec in GRT_HF_MODELS],
            },
        )
    )
    suite = client.get_grt_suite()
    assert isinstance(suite, GrtSuiteManifest)
    assert suite.total == 9
    assert len(suite.models) == 9


@pytest.mark.asyncio()
async def test_async_rerank_tts_and_grt_suite(api, async_client: AsyncMatterWave) -> None:
    api.post("/v1/rerank").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "id": "rerank-async",
                "model": "MatterWaveAI/grt-30m-reranker",
                "results": [{"index": 0, "relevance_score": 0.99, "document": {"text": "doc"}}],
            },
        )
    )
    ranked = await async_client.rerank("query", ["doc"])
    assert ranked.results[0].relevance_score == 0.99

    api.post("/v1/audio/speech").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "id": "tts-async",
                "model": "MatterWaveAI/grt-30m-tts",
                "voice": "aurora",
                "format": "json",
                "sample_rate": 24000,
                "audio_base64": "AAAA",
            },
        )
    )
    speech = await async_client.synthesize_speech("Hello async")
    assert speech.sample_rate == 24000

    api.get("/api/v1/models/grt-suite").mock(
        return_value=httpx.Response(
            200,
            headers=H,
            json={
                "suite": "grt-30m",
                "organization": "MatterWaveAI",
                "whitepaper_sha256": GRT_WHITEPAPER_SHA256,
                "total": 9,
                "models": [spec.__dict__ for spec in GRT_HF_MODELS],
            },
        )
    )
    suite = await async_client.get_grt_suite()
    assert suite.total == 9
