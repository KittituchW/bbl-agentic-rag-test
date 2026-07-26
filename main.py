"""BBL AI Engineer Programming Test — Agentic AI with RAG.

Two agents orchestrated with the OpenAI Agents SDK (agent-as-tool pattern):

  1. Data Retriever  — forced to call the custom `search_knowledge_base`
     tool (hybrid BM25 + semantic + RRF over knowledge_base.txt) and,
     via `stop_on_first_tool`, returns the raw snippets verbatim.
  2. Report Generator — entry agent; forced to call the Data Retriever
     (exposed as the `retrieve_information` tool), then synthesizes the
     snippets into a grounded, non-redundant answer.

Providers (set LLM_PROVIDER in .env):
  * bbl    — BBL's Azure API Management gateway (api-key header)
  * openai — standard OpenAI API key
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import (
    Agent,
    ModelSettings,
    Runner,
    function_tool,
    set_tracing_disabled,
)

try:  # exported at top level in current SDK versions
    from agents import OpenAIResponsesModel
except ImportError:  # fallback for older layouts
    from agents.models.openai_responses import OpenAIResponsesModel

import retrieval

logging.basicConfig(level=logging.WARNING)

KB_PATH = Path(__file__).parent / "knowledge_base.txt"

# ---------------------------------------------------------------------------
# Retrieval setup (chunk + embed once at startup)
# ---------------------------------------------------------------------------
CHUNKS = retrieval.load_chunks(str(KB_PATH))
SEMANTIC_INDEX = retrieval.SemanticIndex(CHUNKS)


@function_tool
def search_knowledge_base(query: str) -> str:
    """Search the company policy knowledge base for text snippets relevant
    to the query. Returns raw snippets with retrieval scores, or
    NO_RELEVANT_INFORMATION if nothing in the knowledge base matches.

    Args:
        query: The user's information need, e.g. "international travel policy".
    """
    results = retrieval.hybrid_search(query, CHUNKS, SEMANTIC_INDEX)
    return retrieval.format_results(results)


# ---------------------------------------------------------------------------
# Model / provider configuration
# ---------------------------------------------------------------------------
def build_model() -> OpenAIResponsesModel:
    provider = os.getenv("LLM_PROVIDER", "openai").lower()

    if provider == "bbl":
        api_key = os.environ["BBL_API_KEY"]  
        client = AsyncOpenAI(
            base_url=os.getenv(
                "BBL_BASE_URL", "https://apimsdbxcandidate01.azure-api.net/llm"
            ),
            api_key="unused",  
            default_headers={"api-key": api_key},
        )
        model_name = os.getenv("BBL_MODEL", "gpt-5-mini")
    elif provider == "openai":
        client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        model_name = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (use 'bbl' or 'openai')")

    return OpenAIResponsesModel(model=model_name, openai_client=client)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------
def build_agents(model: OpenAIResponsesModel) -> Agent:
    data_retriever = Agent(
        name="Data Retriever",
        model=model,
        instructions=(
            "You are an expert in information retrieval. Your ONLY job is to "
            "call the search_knowledge_base tool with a well-formed search "
            "query derived from the request. You never answer questions "
            "yourself and never add commentary — the tool output is returned "
            "verbatim as your result."
        ),
        tools=[search_knowledge_base],
        # Force the tool call — the retriever must never answer from memory.
        model_settings=ModelSettings(tool_choice="required"),
        # Return the tool's raw output directly: guarantees verbatim
        # snippets, prevents paraphrasing, and saves one LLM round-trip.
        tool_use_behavior="stop_on_first_tool",
    )

    report_generator = Agent(
        name="Report Generator",
        model=model,
        instructions=(
            "You are an expert writer. Answer the user's question using ONLY "
            "the snippets returned by the retrieve_information tool, which "
            "you must call first for every question.\n"
            "Rules:\n"
            "1. Ground every statement in the retrieved snippets. Never use "
            "outside knowledge or invent details.\n"
            "2. If the tool returns NO_RELEVANT_INFORMATION, or the snippets "
            "do not actually address the question, reply that the knowledge "
            "base does not contain this information. Do not guess.\n"
            "3. Synthesize overlapping snippets into one cohesive answer — "
            "no redundancy, no copied snippet headers or scores.\n"
            "4. If the question has several parts and the snippets cover only "
            "some of them, answer those parts and say which parts are not "
            "addressed. Before stating that something is missing, re-read "
            "every snippet — never claim the knowledge base lacks "
            "information that appears in a snippet.\n"
            "5. Include only facts that bear on the question. A snippet may "
            "be retrieved without being relevant — leave out loosely related "
            "rules from other policies rather than padding the answer.\n"
            "6. Format: use '-' for bullets and end each with the source "
            "policy name in parentheses, e.g. '(Corporate Card Policy)'. "
            "Open with the direct answer as a plain sentence — do not write "
            "the words 'Direct answer'. Use headings only when the question "
            "has three or more distinct parts; a one- or two-fact answer is "
            "just bullets. If the knowledge base does not cover the "
            "question, or cannot be used the way the question asks, say so "
            "in the FIRST line, not the last.\n"
            "7. Do not offer to produce further work. You may state what the "
            "knowledge base does not cover, or that a request could not be "
            "followed as asked — that is part of the answer, not a closing "
            "remark.\n"
            "8. Write for an employee who has never heard of this system. "
            "Never mention retrieval, snippets, chunks, tools, or internal "
            "markers such as NO_RELEVANT_INFORMATION. Refer to the source "
            "as 'the knowledge base'."
        ),
        tools=[
            data_retriever.as_tool(
                tool_name="retrieve_information",
                tool_description=(
                    "Retrieve raw policy snippets from the company knowledge "
                    "base relevant to a question."
                ),
            )
        ],
        # Force retrieval on the first turn; the SDK resets tool_choice
        # after the call, letting the model then write the final answer.
        model_settings=ModelSettings(tool_choice="required"),
    )
    return report_generator


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def answer(agent: Agent, query: str) -> str:
    result = await Runner.run(agent, query)
    return result.final_output


def main() -> None:
    load_dotenv()
    set_tracing_disabled(True)  
    agent = build_agents(build_model())

    if len(sys.argv) > 1:
        queries = [" ".join(sys.argv[1:])]
    else:
        print("Enter a question (blank line to exit).")
        queries = iter(lambda: input("\nQ: ").strip(), "")

    for query in queries:
        print(f"\n=== Query: {query}")
        print(asyncio.run(answer(agent, query)))


if __name__ == "__main__":
    main()
