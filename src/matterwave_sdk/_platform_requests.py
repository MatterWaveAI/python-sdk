"""Pure request builders for the `/api/v1/*` OIDC, OAuth, universal connector
catalog, decision trail, replay, checkpoint, reliability, telemetry, HITL,
guardrails, and tool planning routes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ._base import Request, _segment

OIDC_CONFIG_PATH = "/api/v1/oidc/config"
OIDC_ENFORCE_CLAIMS_PATH = "/api/v1/oidc/enforce-claims"
CONNECTOR_CATALOG_PATH = "/api/v1/connectors/catalog"
OAUTH_REGISTER_PATH = "/api/v1/oauth/register"
OAUTH_PROXY_PATH = "/api/v1/oauth/proxy"
RUNS_PATH = "/api/v1/runs"
RELIABILITY_METRICS_PATH = "/api/v1/reliability/metrics"
RELIABILITY_SLO_PATH = "/api/v1/reliability/slo"
RELIABILITY_TRENDS_PATH = "/api/v1/reliability/trends"
TELEMETRY_STATUS_PATH = "/api/v1/telemetry/status"
TELEMETRY_EXECUTIONS_PATH = "/api/v1/telemetry/executions"
HITL_INTERACTIONS_PATH = "/api/v1/hitl/interactions"
GUARDRAILS_EVALUATE_PATH = "/api/v1/guardrails/evaluate"
TOOLS_PLAN_PATH = "/api/v1/tools/plan"


def _get(path: str, params: Mapping[str, str] | None = None) -> Request:
    return Request("GET", path, None, keyed=True, stream=False, params=dict(params or {}))


def _post(path: str, body: Mapping[str, Any] | None = None) -> Request:
    payload = dict(body) if body is not None else None
    return Request("POST", path, payload, keyed=True, stream=False)


def _required(name: str, value: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


def get_oidc_config_request() -> Request:
    return _get(OIDC_CONFIG_PATH)


def sync_agent_key_oidc_request(key_id: str) -> Request:
    path = f"/api/v1/agent-keys/{_segment('key_id', key_id)}/oidc-sync"
    return _post(path)


def enforce_oidc_claims_request(payload: Mapping[str, Any]) -> Request:
    if not payload:
        raise ValueError("payload must not be empty")
    return _post(OIDC_ENFORCE_CLAIMS_PATH, dict(payload))


def list_connector_catalog_request(
    *,
    category: str | None = None,
    q: str | None = None,
) -> Request:
    params: dict[str, str] = {}
    if category is not None:
        params["category"] = category
    if q is not None:
        params["q"] = q
    return _get(CONNECTOR_CATALOG_PATH, params)


def register_oauth_authenticator_request(payload: Mapping[str, Any]) -> Request:
    if not payload:
        raise ValueError("payload must not be empty")
    return _post(OAUTH_REGISTER_PATH, dict(payload))


def proxy_oauth_request_request(
    credential_id: str,
    endpoint: str,
    *,
    method: str = "GET",
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    body: Any = None,
    dry_run: bool | None = None,
) -> Request:
    path = f"{OAUTH_PROXY_PATH}/{_segment('credential_id', credential_id)}"
    payload: dict[str, Any] = {
        "endpoint": _required("endpoint", endpoint),
        "method": _required("method", method).upper(),
    }
    if params is not None:
        payload["params"] = dict(params)
    if headers is not None:
        payload["headers"] = dict(headers)
    if body is not None:
        payload["body"] = body
    if dry_run is not None:
        payload["dry_run"] = dry_run
    return _post(path, payload)


def get_run_decision_trail_request(run_id: str) -> Request:
    path = f"{RUNS_PATH}/{_segment('run_id', run_id)}/decision-trail"
    return _get(path)


def replay_run_request(
    run_id: str,
    *,
    mode: str = "simulated",
    overrides: Mapping[str, Any] | None = None,
    from_step_id: str | None = None,
) -> Request:
    path = f"{RUNS_PATH}/{_segment('run_id', run_id)}/replay"
    payload: dict[str, Any] = {
        "mode": _required("mode", mode),
        "overrides": dict(overrides) if overrides is not None else {},
    }
    if from_step_id is not None:
        payload["from_step_id"] = from_step_id
    return _post(path, payload)


def list_run_checkpoints_request(run_id: str) -> Request:
    path = f"{RUNS_PATH}/{_segment('run_id', run_id)}/checkpoints"
    return _get(path)


def resume_run_request(
    run_id: str,
    checkpoint_id: str | None = None,
    *,
    state_overrides: Mapping[str, Any] | None = None,
) -> Request:
    path = f"{RUNS_PATH}/{_segment('run_id', run_id)}/resume"
    payload: dict[str, Any] = {}
    if checkpoint_id is not None:
        payload["checkpoint_id"] = _required("checkpoint_id", checkpoint_id)
    if state_overrides is not None:
        payload["state_overrides"] = dict(state_overrides)
    return _post(path, payload)


def get_reliability_metrics_request(*, agent_id: str | None = None) -> Request:
    params: dict[str, str] = {}
    if agent_id is not None:
        params["agent_id"] = agent_id
    return _get(RELIABILITY_METRICS_PATH, params)


def get_reliability_slo_request() -> Request:
    return _get(RELIABILITY_SLO_PATH)


def get_reliability_trends_request() -> Request:
    return _get(RELIABILITY_TRENDS_PATH)


def get_telemetry_status_request() -> Request:
    return _get(TELEMETRY_STATUS_PATH)


def get_execution_telemetry_request(execution_id: str) -> Request:
    path = f"{TELEMETRY_EXECUTIONS_PATH}/{_segment('execution_id', execution_id)}/metrics"
    return _get(path)


def list_hitl_interactions_request(*, status: str | None = None) -> Request:
    params: dict[str, str] = {}
    if status is not None:
        params["status"] = status
    return _get(HITL_INTERACTIONS_PATH, params)


def respond_hitl_interaction_request(
    interaction_id: str,
    decision: str,
    *,
    response_data: Mapping[str, Any] | None = None,
    responded_by: str | None = None,
) -> Request:
    path = f"{HITL_INTERACTIONS_PATH}/{_segment('interaction_id', interaction_id)}/respond"
    payload: dict[str, Any] = {"decision": _required("decision", decision)}
    if response_data is not None:
        payload["response_data"] = dict(response_data)
    if responded_by is not None:
        payload["responded_by"] = responded_by
    return _post(path, payload)


def evaluate_guardrails_request(
    rail_type: str,
    content: str,
    *,
    tool_name: str | None = None,
    tool_args: Mapping[str, Any] | None = None,
    policies: Sequence[str] | None = None,
) -> Request:
    payload: dict[str, Any] = {
        "rail_type": _required("rail_type", rail_type),
        "content": content,
    }
    if tool_name is not None:
        payload["tool_name"] = tool_name
    if tool_args is not None:
        payload["tool_args"] = dict(tool_args)
    if policies is not None:
        payload["policies"] = list(policies)
    return _post(GUARDRAILS_EVALUATE_PATH, payload)


def plan_tools_request(
    goal: str,
    available_tools: Sequence[str] | None = None,
    *,
    constraints: Mapping[str, Any] | None = None,
) -> Request:
    cleaned_goal = _required("goal", goal)
    payload: dict[str, Any] = {
        "task": cleaned_goal,
        "goal": cleaned_goal,
    }
    if available_tools is not None:
        payload["available_tools"] = list(available_tools)
    if constraints is not None:
        payload["constraints"] = dict(constraints)
    return _post(TOOLS_PLAN_PATH, payload)
