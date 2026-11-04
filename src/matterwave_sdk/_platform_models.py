"""Response models for the platform OIDC, OAuth, universal connector catalog,
decision trail, replay, checkpoint, reliability, telemetry, HITL, guardrails,
and tool planning routes (`/api/v1/*`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._wire import Wire

# --- OIDC & A2A permissions ---------------------------------------------------


@dataclass(frozen=True)
class OidcConfig(Wire):
    """`GET /api/v1/oidc/config`: the deployment's OIDC identity provider
    configuration and connection status."""

    adapter: str
    issuer_url: str
    token_endpoint: str
    jwks_uri: str
    admin_url: str
    project_id: str
    connected: bool
    status: str
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class A2ALimits(Wire):
    """Execution bounds carried in an agent's A2A permission envelope."""

    max_tokens: int
    max_duration_seconds: int
    max_cost_usd: float


@dataclass(frozen=True)
class A2APermissionEnvelope(Wire):
    """Agent-to-agent permission claims provisioned for an agent key."""

    allowed_tools: list[str]
    denied_tools: list[str]
    allowed_models: list[str]
    cwd_scope: str
    limits: A2ALimits


@dataclass(frozen=True)
class OidcSyncResult(Wire):
    """`POST /api/v1/agent-keys/{key_id}/oidc-sync`: the OIDC client binding
    and A2A permission claims synchronized for an agent key."""

    key_id: str
    idp_client_id: str
    oidc_issuer: str
    adapter: str
    synced_at: str
    scopes: list[str]
    resource_pattern: str
    a2a: A2APermissionEnvelope
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class EnforceClaimsResult(Wire):
    """`POST /api/v1/oidc/enforce-claims`: the decision and effective execution
    bounds after evaluating an agent key's A2A claims against a run request."""

    allowed: bool
    violation_claim: str | None
    detail: str
    effective_tools: list[str]
    resolved_cwd: str
    limits: A2ALimits
    request_id: str | None = field(default=None, compare=False)


# --- Universal connector catalog & OAuth broker -------------------------------


@dataclass(frozen=True)
class ConnectorCatalogEntry(Wire):
    """One provider in the universal connector catalog."""

    provider_id: str
    name: str
    category: str
    auth_types: list[str]
    authorize_url: str
    token_url: str
    api_base_url: str
    default_scopes: list[str]
    pkce_supported: bool
    refresh_supported: bool
    revoke_url: str
    docs_url: str
    icon: str


@dataclass(frozen=True)
class ConnectorCatalogList(Wire):
    """`GET /api/v1/connectors/catalog`: the matching connector catalog
    entries and total count."""

    data: list[ConnectorCatalogEntry]
    total: int
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class OAuthAuthenticator(Wire):
    """`POST /api/v1/oauth/register`: a registered OAuth authenticator with its
    client secret masked."""

    authenticator_id: str
    name: str
    provider_id: str
    org_id: str
    client_id: str
    client_secret_masked: str
    authorize_url: str
    token_url: str
    redirect_uri: str
    scopes: list[str]
    api_base_url: str
    tenant_id: str | None
    extra_authorize_params: dict[str, str]
    created_at: str
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class CredentialProxyResponse(Wire):
    """`POST /api/v1/oauth/proxy/{credential_id}`: the result of proxying a
    request through the server-side credential broker."""

    credential_id: str
    provider_id: str
    method: str
    target_url: str
    status_code: int
    auto_refreshed: bool
    broker: str
    headers_injected: list[str]
    data: dict[str, Any]
    request_id: str | None = field(default=None, compare=False)


# --- Decision trail, replay & checkpoints -------------------------------------


@dataclass(frozen=True)
class DecisionNode(Wire):
    """One node in a run's execution decision graph."""

    id: str
    type: str
    label: str
    step_id: str
    timestamp: str
    duration_ms: int
    confidence: float
    tokens_used: int
    cost_usd: float
    outcome: str
    rationale: str
    evidence: list[dict[str, Any]]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DecisionEdge(Wire):
    """A directed edge between two nodes in a run's decision graph."""

    from_node: str
    to_node: str
    edge_type: str
    label: str


@dataclass(frozen=True)
class DecisionTrailStatistics(Wire):
    """Aggregate statistics across a run's decision trail."""

    total_nodes: int
    total_edges: int
    tool_calls_count: int
    total_tokens: int
    total_cost_usd: float
    duration_ms: int
    success: bool


@dataclass(frozen=True)
class DecisionTrail(Wire):
    """`GET /api/v1/runs/{run_id}/decision-trail`: the complete decision trail
    graph and statistics for a run."""

    run_id: str
    org: str
    nodes: list[DecisionNode]
    edges: list[DecisionEdge]
    statistics: DecisionTrailStatistics
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ReplayDifference(Wire):
    """One field-level difference between the original run and a replay."""

    step_id: str
    field: str
    original: Any
    replayed: Any
    significance: str


@dataclass(frozen=True)
class ReplayRunResult(Wire):
    """`POST /api/v1/runs/{run_id}/replay`: the outcome and diff report from
    replaying a run."""

    replay_id: str
    run_id: str
    mode: str
    success: bool
    original_outcome: dict[str, Any]
    replayed_outcome: dict[str, Any]
    differences: list[ReplayDifference]
    duration_ms: int
    created_at: str
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class CheckpointRecord(Wire):
    """One persisted execution checkpoint for a run."""

    checkpoint_id: str
    thread_id: str
    run_id: str
    step_id: str
    step_index: int
    timestamp: str
    state: dict[str, Any]
    metadata: dict[str, Any]
    parent_checkpoint_id: str | None


@dataclass(frozen=True)
class CheckpointList(Wire):
    """`GET /api/v1/runs/{run_id}/checkpoints`: the checkpoints recorded for a
    run."""

    run_id: str
    data: list[CheckpointRecord]
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class ResumeRunResult(Wire):
    """`POST /api/v1/runs/{run_id}/resume`: the new run spawned from a
    checkpoint."""

    run_id: str
    source_run_id: str
    resumed_from_checkpoint_id: str
    resumed_from_step: str
    state: str
    restored_state: dict[str, Any]
    created_at: str
    request_id: str | None = field(default=None, compare=False)


# --- Reliability, telemetry, HITL, guardrails & tool planning -----------------


@dataclass(frozen=True)
class ReliabilityMetrics(Wire):
    """`GET /api/v1/reliability/metrics`: aggregate reliability indicators for
    the organisation or a specific agent."""

    org: str
    agent_id: str | None
    sr: float
    cr: float
    pc: float
    hir: float
    ma: float
    tcl: float
    wct: float
    total_runs: int
    window: str
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ReliabilitySloReport(Wire):
    """`GET /api/v1/reliability/slo`: service-level objective compliance for
    the organisation."""

    org: str
    mtte_p95_ms: float
    mtte_target_ms: float
    mtte_compliant: bool
    rsr: float
    rsr_target: float
    rsr_compliant: bool
    overall_compliant: bool
    total_measurements: int
    total_replays: int
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class AnomalyAlert(Wire):
    """An anomaly detected across reliability metric trends."""

    metric: str
    severity: str
    current_value: float
    baseline_value: float
    threshold: float
    message: str
    detected_at: str


@dataclass(frozen=True)
class ReliabilityTrendBucket(Wire):
    """One time-bucketed slice of reliability metrics."""

    bucket: str
    sr: float
    cr: float
    pc: float
    hir: float
    ma: float
    tcl: float
    wct: float
    runs: int


@dataclass(frozen=True)
class ReliabilityTrends(Wire):
    """`GET /api/v1/reliability/trends`: historical reliability buckets and
    detected anomalies."""

    org: str
    buckets: list[ReliabilityTrendBucket]
    anomalies: list[AnomalyAlert]
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class TelemetryServiceEndpoint(Wire):
    """One endpoint in the platform's observability stack."""

    service: str
    url: str
    healthy: bool
    protocol: str


@dataclass(frozen=True)
class TelemetryStackStatus(Wire):
    """`GET /api/v1/telemetry/status`: status of the OpenTelemetry, Prometheus,
    Grafana, Tempo, and Loki stack."""

    org: str
    otel_enabled: bool
    prometheus_scraped: bool
    grafana_dashboards: list[str]
    tempo_tracing_enabled: bool
    loki_logging_enabled: bool
    endpoints: list[TelemetryServiceEndpoint]
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class StepTelemetryMetrics(Wire):
    """Telemetry metrics for a single step within an execution."""

    step_id: str
    step_name: str
    status: str
    duration_ms: float
    tokens_used: int
    cost_usd: float
    retries: int


@dataclass(frozen=True)
class TelemetryExecutionMetrics(Wire):
    """`GET /api/v1/telemetry/executions/{execution_id}/metrics`: per-step and
    total telemetry metrics for an execution."""

    execution_id: str
    pipeline_name: str
    status: str
    total_duration_ms: float
    total_tokens: int
    total_cost_usd: float
    trace_id: str
    steps: list[StepTelemetryMetrics]
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class HitlInteraction(Wire):
    """One human-in-the-loop interaction request and its resolution state."""

    interaction_id: str
    execution_id: str
    run_id: str
    step_id: str
    org: str
    title: str
    prompt: str
    channels: list[str]
    status: str
    ui_schema: dict[str, Any]
    context: dict[str, Any]
    decision: str | None
    response_data: dict[str, Any] | None
    responded_by: str | None
    created_at: str
    responded_at: str | None
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class HitlInteractionList(Wire):
    """`GET /api/v1/hitl/interactions`: human-in-the-loop interactions for the
    organisation."""

    data: list[HitlInteraction]
    total: int
    request_id: str | None = field(default=None, compare=False)

    def __iter__(self) -> Any:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class GuardrailViolation(Wire):
    """One guardrail violation triggered during content evaluation."""

    rail_id: str
    rail_type: str
    category: str
    severity: str
    message: str
    matched_text: str | None


@dataclass(frozen=True)
class GuardrailEvaluation(Wire):
    """`POST /api/v1/guardrails/evaluate`: the verdict, modified content, and
    triggered violations from evaluating content against guardrails."""

    rail_type: str
    action: str
    allowed: bool
    modified_content: str
    refusal_message: str | None
    triggered_rails: list[str]
    violations: list[GuardrailViolation]
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ToolPlanStep(Wire):
    """One planned tool invocation step with dependencies and cost/time estimates."""

    step_id: str
    tool_name: str
    description: str
    arguments: dict[str, Any]
    depends_on: list[str]
    estimated_cost_usd: float
    estimated_time_ms: int
    fallback_tool: str | None


@dataclass(frozen=True)
class ToolPlan(Wire):
    """`POST /api/v1/tools/plan`: an execution plan of tool calls ordered into
    parallelizable stages."""

    plan_id: str
    task: str
    steps: list[ToolPlanStep]
    execution_order: list[list[str]]
    total_estimated_cost_usd: float
    total_estimated_time_ms: int
    confidence: float
    request_id: str | None = field(default=None, compare=False)
