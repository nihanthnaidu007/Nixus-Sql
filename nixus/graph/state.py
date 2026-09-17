from typing import TypedDict

from pydantic import BaseModel


class SchemaTable(BaseModel):
    table_name: str
    description: str
    columns_json: str
    sample_values_json: str | None
    relevance_score: float


class FewShotExample(BaseModel):
    natural_language: str
    sql_query: str
    tables_used: list
    query_type: str
    similarity_score: float


class CacheResult(BaseModel):
    hit: bool
    similarity: float
    cached_sql: str | None = None
    result_preview: list | None = None
    chart_type: str | None = None
    explanation: str | None = None
    cache_id: int | None = None


class ValidationResult(BaseModel):
    is_valid: bool
    errors: list
    warnings: list
    normalized_sql: str


class ExecutionResult(BaseModel):
    success: bool
    rows: list
    columns: list
    row_count: int
    execution_time_ms: float
    error: str | None = None


class ResultQuality(BaseModel):
    status: str
    reasoning: str
    is_acceptable: bool


class CorrectionRecord(BaseModel):
    attempt: int
    failed_sql: str
    error_message: str
    fix_reasoning: str
    corrected_sql: str


class ChartConfig(BaseModel):
    chart_type: str
    x_column: str | None = None
    y_column: str | None = None
    color_column: str | None = None
    title: str
    reasoning: str
    plotly_json: str | None = None


class StreamUpdate(BaseModel):
    timestamp: str
    node: str
    message: str
    status: str


class SQLAgentState(TypedDict):
    user_query: str
    session_id: str
    # Stateless clarification round-trip inputs (Option B): carried in with the
    # request, not persisted server-side. Absent/0 for a normal single-turn query.
    clarification_context: dict | None
    clarification_round: int
    scope_category: str | None
    scope_message: str | None
    # Response-outcome discriminator + the text the client surfaces for each.
    outcome: str | None
    clarifying_question: str | None
    reason: str | None
    intent_class: str
    extracted_entities: list
    cache_result: dict | None
    served_from_cache: bool
    relevant_schemas: list
    schema_context: str
    tables_identified: list
    similar_examples: list
    fewshot_context: str
    generated_sql: str
    validation_result: dict | None
    grounding_result: dict | None
    execution_result: dict | None
    result_quality: dict | None
    correction_attempts: int
    correction_history: list
    chart_config: dict | None
    explanation: str
    confidence_score: float
    # Categorical confidence (5.2): the verdict plus its legible reasoning, so the
    # API/UI can show WHY confidence is what it is rather than a bare number.
    confidence: str | None
    confidence_reasons: list
    confidence_signals: dict
    current_node: str
    completed_nodes: list
    is_complete: bool
    trace_id: str | None
    trace_url: str | None
    error: str | None
    stream_updates: list
