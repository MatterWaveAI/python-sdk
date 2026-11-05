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


GRT_WHITEPAPER_SHA256 = (
    "79219257df919cc598a75f7ae0968988ed0d33ffc1777469f9a9da39a558819d"
)


@dataclass(frozen=True)
class GrtHfModelSpec(Wire):
    """Metadata and SHA256 verification digests for one official
    `MatterWaveAI/grt-30m*` HuggingFace model."""

    id: str
    alias: str
    name: str
    hf_repo_id: str
    hf_url: str
    path: str
    architecture: str
    model_type: str
    pipeline_tag: str
    input: list[str]
    reasoning: bool
    vocab_size: int
    hidden_size: int
    context_window: int
    weight_sha256: str | None
    config_sha256: str
    whitepaper_sha256: str = GRT_WHITEPAPER_SHA256

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


GRT_HF_MODELS: tuple[GrtHfModelSpec, ...] = (
    GrtHfModelSpec(
        id="MatterWaveAI/grt-30m",
        alias="grt-30m",
        name="MatterWave GRT-30M (Base Causal LM)",
        hf_repo_id="MatterWaveAI/grt-30m",
        hf_url="https://huggingface.co/MatterWaveAI/grt-30m",
        path="release/30m",
        architecture="GRTForCausalLM",
        model_type="grt",
        pipeline_tag="text-generation",
        input=["text"],
        reasoning=False,
        vocab_size=151669,
        hidden_size=256,
        context_window=2048,
        weight_sha256="205f76f3ed59fee0ed54bb8133ea1b1a64527994c68776066057b4707f1b2456",
        config_sha256="5ffa4facb3cfba2b39512bbc54c7b7ecda637c70f40fd18ede13396cd8e5a86b",
    ),
    GrtHfModelSpec(
        id="MatterWaveAI/grt-30m-chat",
        alias="grt-30m-chat",
        name="MatterWave GRT-30M Chat (Instruction & Tool Tuned)",
        hf_repo_id="MatterWaveAI/grt-30m-chat",
        hf_url="https://huggingface.co/MatterWaveAI/grt-30m-chat",
        path="release/30m-chat",
        architecture="GRTForCausalLM",
        model_type="grt",
        pipeline_tag="text-generation",
        input=["text"],
        reasoning=False,
        vocab_size=151669,
        hidden_size=256,
        context_window=2048,
        weight_sha256="ee613803cb7b2c618def7e6c79476e1aab4e887cc08e234204b6bad12ad772bc",
        config_sha256="c2b264a05e11914a8e41c1610f3c81e15d38151c459a1c66caaaa0381d804685",
    ),
    GrtHfModelSpec(
        id="MatterWaveAI/grt-30m-mor",
        alias="grt-30m-mor",
        name="MatterWave GRT-30M MoR (Mixture-of-Recursions)",
        hf_repo_id="MatterWaveAI/grt-30m-mor",
        hf_url="https://huggingface.co/MatterWaveAI/grt-30m-mor",
        path="release/30m-mor",
        architecture="GRTForCausalLM",
        model_type="grt",
        pipeline_tag="text-generation",
        input=["text"],
        reasoning=True,
        vocab_size=151669,
        hidden_size=256,
        context_window=2048,
        weight_sha256="5633a578073e808d18fe3e52fb0fbf0005dc015b6bb10a1bff339f5d83225a07",
        config_sha256="bf652df2d4107c7e7ef3c23d0854436aedb0030f0821f97939ed6ed858254013",
    ),
    GrtHfModelSpec(
        id="MatterWaveAI/grt-30m-embedding",
        alias="grt-30m-embedding",
        name="MatterWave GRT-30M Embedding (Dense Vector Encoder)",
        hf_repo_id="MatterWaveAI/grt-30m-embedding",
        hf_url="https://huggingface.co/MatterWaveAI/grt-30m-embedding",
        path="release/30m-embedding",
        architecture="GRTModel",
        model_type="grt",
        pipeline_tag="feature-extraction",
        input=["text"],
        reasoning=False,
        vocab_size=151669,
        hidden_size=256,
        context_window=2048,
        weight_sha256="5cdab387c5e2ee9bb3907acaccfccfaa7cc10060c21e3f265ce981a4a7d38226",
        config_sha256="c987cec1b71deba4a1749a939032309921872ae2a6c21378f8160edb29b210df",
    ),
    GrtHfModelSpec(
        id="MatterWaveAI/grt-30m-reranker",
        alias="grt-30m-reranker",
        name="MatterWave GRT-30M Reranker (Cross-Encoder Reranking)",
        hf_repo_id="MatterWaveAI/grt-30m-reranker",
        hf_url="https://huggingface.co/MatterWaveAI/grt-30m-reranker",
        path="release/30m-reranker",
        architecture="GRTForSequenceClassification",
        model_type="grt",
        pipeline_tag="text-classification",
        input=["text"],
        reasoning=False,
        vocab_size=151669,
        hidden_size=256,
        context_window=2048,
        weight_sha256="6d090decc4426a1c21a372c1bd718a6b80626e2c7b460c9ab79b713d84320ef9",
        config_sha256="6f401b7ed6af93ed7fc93eedc6de44ad67f2f19ecdc5b3c96dcc92ee564750fc",
    ),
    GrtHfModelSpec(
        id="MatterWaveAI/grt-30m-vl",
        alias="grt-30m-vl",
        name="MatterWave GRT-30M Vision-Language (Multimodal VL)",
        hf_repo_id="MatterWaveAI/grt-30m-vl",
        hf_url="https://huggingface.co/MatterWaveAI/grt-30m-vl",
        path="release/30m-vl",
        architecture="GRTVLForConditionalGeneration",
        model_type="grt_vl",
        pipeline_tag="image-text-to-text",
        input=["text", "image"],
        reasoning=False,
        vocab_size=151669,
        hidden_size=256,
        context_window=2048,
        weight_sha256="b21667c9d39c67513671af4fe4480551f26ac738b23821902fd78ae55a4f1dfe",
        config_sha256="0fd2f34d415723412d54055ecb174bcbb60cccaf43235af52b6c13826e6bb859",
    ),
    GrtHfModelSpec(
        id="MatterWaveAI/grt-30m-tts",
        alias="grt-30m-tts",
        name="MatterWave GRT-30M TTS (Neural Speech Synthesis)",
        hf_repo_id="MatterWaveAI/grt-30m-tts",
        hf_url="https://huggingface.co/MatterWaveAI/grt-30m-tts",
        path="release/30m-tts",
        architecture="GRTTTSForConditionalGeneration",
        model_type="grt_tts",
        pipeline_tag="text-to-speech",
        input=["text"],
        reasoning=False,
        vocab_size=151669,
        hidden_size=256,
        context_window=2048,
        weight_sha256="9df117cf2d9fdd14a972495293628bfea4ab503ee4fbcb270ffb667763e314f4",
        config_sha256="74dca6d26f2726ae96033446d5d418a152aef8368a8e49aa49bb15f6364ca75a",
    ),
    GrtHfModelSpec(
        id="MatterWaveAI/grt-30m-omni",
        alias="grt-30m-omni",
        name="MatterWave GRT-30M Omni (Any-to-Any Multimodal)",
        hf_repo_id="MatterWaveAI/grt-30m-omni",
        hf_url="https://huggingface.co/MatterWaveAI/grt-30m-omni",
        path="release/30m-omni",
        architecture="GRTOmniForConditionalGeneration",
        model_type="grt_omni",
        pipeline_tag="any-to-any",
        input=["text", "image"],
        reasoning=True,
        vocab_size=151669,
        hidden_size=256,
        context_window=2048,
        weight_sha256="b314996ce883a0c875de18da67f5d23103c507c79b7421b8b8c52cef95d2fbf7",
        config_sha256="5bc88f708534280522872f046e89deade537f286a78bc261c1e039d03fa6d0cf",
    ),
    GrtHfModelSpec(
        id="MatterWaveAI/grt-30m-tokenizer",
        alias="grt-30m-tokenizer",
        name="MatterWave GRT-30M Tokenizer (151,669-Token Vocabulary)",
        hf_repo_id="MatterWaveAI/grt-30m-tokenizer",
        hf_url="https://huggingface.co/MatterWaveAI/grt-30m-tokenizer",
        path="release/30m-tokenizer",
        architecture="GRTTokenizer",
        model_type="grt",
        pipeline_tag="feature-extraction",
        input=["text"],
        reasoning=False,
        vocab_size=151669,
        hidden_size=256,
        context_window=2048,
        weight_sha256=None,
        config_sha256="4c7ed24fcbaf5b1c62ec160296e682eb7b700ea19d88f0a8e01e363b125d505f",
    ),
)


@dataclass(frozen=True)
class RerankResult(Wire):
    """One ranked document from `POST /v1/rerank`."""

    index: int
    relevance_score: float
    document: dict[str, Any] | str | None = None


@dataclass(frozen=True)
class RerankResponse(Wire):
    """`POST /v1/rerank`: cross-encoder document reranking response."""

    model: str
    results: list[RerankResult]
    id: str | None = None
    usage: dict[str, int] | None = None
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class SpeechSynthesisResponse(Wire):
    """`POST /v1/audio/speech`: neural speech synthesis response from `MatterWaveAI/grt-30m-tts`."""

    model: str
    voice: str
    format: str
    sample_rate: int
    audio_base64: str
    id: str | None = None
    duration_ms: int | None = None
    phoneme_tokens: list[int] | None = None
    request_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class GrtSuiteManifest(Wire):
    """`GET /api/v1/models/grt-suite`: the 9-model `MatterWaveAI/grt-30m*` suite manifest."""

    models: list[dict[str, Any]]
    suite: str = "grt-30m"
    organization: str = "MatterWaveAI"
    whitepaper_sha256: str = GRT_WHITEPAPER_SHA256
    total: int = 9
    request_id: str | None = field(default=None, compare=False)
