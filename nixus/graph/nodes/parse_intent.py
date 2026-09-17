from datetime import datetime

from dotenv import load_dotenv

from nixus.config import settings

load_dotenv()

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel

from nixus.graph.state import SQLAgentState
from nixus.utils.retry import llm_retry

llm = ChatAnthropic(
    model="claude-haiku-4-5",
    anthropic_api_key=(settings.anthropic_api_key or ""),
    temperature=0.1,
    max_tokens=512,
)


class IntentResult(BaseModel):
    intent_class: str
    extracted_entities: list
    reasoning: str


# Write requests are refused upstream at the scope gate (NIXUS is read-only), so
# parse_intent only distinguishes the read-side intents that inform generation.
PARSE_PROMPT = """Classify this natural language database query.

intent_class options:
- READ: wants to SELECT / retrieve data
- SCHEMA_QUESTION: asking about table structure, column names
- AMBIGUOUS: unclear intent

extracted_entities: key concepts mentioned — table hints, filters, aggregations, time ranges, column hints.

Query: {user_query}

Respond ONLY with valid JSON matching this schema. No markdown, no backticks:
{{
  "intent_class": "READ|SCHEMA_QUESTION|AMBIGUOUS",
  "extracted_entities": ["entity1", "entity2"],
  "reasoning": "one sentence"
}}"""


def now():
    return datetime.now().astimezone().strftime("%H:%M:%S")


async def parse_intent_node(state: SQLAgentState) -> SQLAgentState:
    state["current_node"] = "parse_intent"
    structured_llm = llm.with_structured_output(IntentResult)

    @llm_retry
    async def _call_llm(prompt: str):
        return await structured_llm.ainvoke(prompt)

    result = await _call_llm(PARSE_PROMPT.format(user_query=state["user_query"]))

    state["intent_class"] = result.intent_class
    state["extracted_entities"] = result.extracted_entities
    state["completed_nodes"].append("parse_intent")
    state["stream_updates"].append({
        "timestamp": now(), "node": "parse_intent",
        "message": f"Intent: {result.intent_class} | Entities: {result.extracted_entities}",
        "status": "done",
    })
    return state
