"""Offline tests for conditional answer verification (no API calls)."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents import Agent
from agents.items import ToolCallOutputItem

import main as app
import run_samples


THREE_SOURCE_QUERY = (
    "I paid for a domestic hotel with my corporate card. What is the hotel "
    "limit, when must I upload the card receipt, and when is the expense claim due?"
)
THREE_SOURCE_EVIDENCE = """\
[Snippet 1 | chunk 8 | bm25=14.93 cosine=0.576 | via bm25+semantic]
Corporate Card Policy: Cardholders must upload receipts to the finance portal
within 7 calendar days of each transaction.

[Snippet 2 | chunk 2 | bm25=7.36 cosine=0.463 | via bm25+semantic]
Expense Reimbursement Policy: All expense claims must be submitted through the
finance portal within 30 days of the expense date.

[Snippet 3 | chunk 1 | bm25=11.09 cosine=0.383 | via bm25+semantic]
Domestic Travel Policy: Hotel reimbursement is capped at 2,000 THB per night
for domestic stays."""
FAILED_DRAFT = (
    "The knowledge base does not contain information regarding when an expense "
    "claim is due, but the hotel limit is 2,000 THB and card receipts are due "
    "within 7 calendar days."
)
CORRECTED_ANSWER = (
    "The hotel limit is 2,000 THB per night, the card receipt is due within "
    "7 calendar days, and the expense claim is due within 30 days."
)
PRIVACY_QUERY = (
    "I accidentally sent a customer's account number through my personal "
    "messaging app. What rules did I violate, and what must I do now?"
)
PRIVACY_EVIDENCE = """\
[Snippet 1 | chunk 9 | bm25=15.27 cosine=0.390 | via bm25+semantic]
Customer Data Privacy Policy: Files containing account numbers must be encrypted
before internal sharing. Customer data must not be sent to personal email
accounts or consumer messaging applications.

[Snippet 2 | chunk 10 | bm25=1.46 cosine=0.346 | via bm25+semantic]
Incident Response Policy: Security incidents such as accidental data disclosure
must be reported to the IT Security team within 2 hours of discovery. Employees
should preserve evidence and avoid deleting suspicious files."""


class RecordingThrottle:
    def __init__(self):
        self.reservations = []

    async def acquire(self, calls=None):
        self.reservations.append(calls)


def _tool_output(agent: Agent, output: str) -> ToolCallOutputItem:
    return ToolCallOutputItem(
        agent=agent,
        raw_item={"type": "function_call_output", "call_id": "retrieve-1"},
        output=output,
    )


def test_three_source_failure_is_sent_through_grounded_verifier(monkeypatch):
    """Regression: a retrieved 30-day deadline must reach the repair pass."""
    report_agent = Agent(name="Test Report Generator", model="test-model")
    throttle = RecordingThrottle()
    calls = []

    async def fake_run(agent, request):
        calls.append((agent, request))
        if len(calls) == 1:
            return SimpleNamespace(
                final_output=FAILED_DRAFT,
                new_items=[_tool_output(report_agent, THREE_SOURCE_EVIDENCE)],
            )
        return SimpleNamespace(final_output=CORRECTED_ANSWER, new_items=[])

    monkeypatch.setattr(app, "THROTTLE", throttle)
    monkeypatch.setattr(app.Runner, "run", fake_run)

    answer = asyncio.run(app.answer(report_agent, THREE_SOURCE_QUERY))

    assert answer == CORRECTED_ANSWER
    assert len(calls) == 2
    assert calls[1][0].name == "Grounded Answer Verifier"
    verifier_request = calls[1][1]
    assert THREE_SOURCE_QUERY in verifier_request
    assert THREE_SOURCE_EVIDENCE in verifier_request
    assert FAILED_DRAFT in verifier_request
    assert "within 30 days" in verifier_request
    assert throttle.reservations == [None, 1]


def test_single_fact_answer_skips_verifier(monkeypatch):
    report_agent = Agent(name="Test Report Generator", model="test-model")
    throttle = RecordingThrottle()
    calls = []

    async def fake_run(agent, request):
        calls.append((agent, request))
        return SimpleNamespace(
            final_output="Upload the receipt within 7 calendar days.",
            new_items=[],
        )

    monkeypatch.setattr(app, "THROTTLE", throttle)
    monkeypatch.setattr(app.Runner, "run", fake_run)

    answer = asyncio.run(
        app.answer(
            report_agent,
            "When should I upload a corporate-card receipt?",
        )
    )

    assert answer == "Upload the receipt within 7 calendar days."
    assert len(calls) == 1
    assert throttle.reservations == [None]


def test_negative_knowledge_claim_triggers_verification():
    assert app.claims_knowledge_gap(
        "The knowledge base does not contain information about parking."
    )
    assert app.should_verify(
        "What is the parking reimbursement policy?",
        "The knowledge base does not contain information about parking.",
    )


def test_supported_multi_part_answer_skips_verification():
    assert not app.should_verify(
        THREE_SOURCE_QUERY,
        CORRECTED_ANSWER,
    )


def test_definitive_no_answer_skips_verifier(monkeypatch):
    report_agent = Agent(name="Test Report Generator", model="test-model")
    throttle = RecordingThrottle()
    calls = []
    draft = "The knowledge base does not contain this information."

    async def fake_run(agent, request):
        calls.append((agent, request))
        return SimpleNamespace(
            final_output=draft,
            new_items=[
                _tool_output(report_agent, app.retrieval.NO_ANSWER_MARKER),
            ],
        )

    monkeypatch.setattr(app, "THROTTLE", throttle)
    monkeypatch.setattr(app.Runner, "run", fake_run)

    answer = asyncio.run(app.answer(report_agent, "Who won the World Cup in 2022?"))

    assert answer == draft
    assert len(calls) == 1
    assert throttle.reservations == [None]


def test_internal_marker_from_verifier_is_rejected(monkeypatch):
    report_agent = Agent(name="Test Report Generator", model="test-model")
    throttle = RecordingThrottle()
    calls = []

    async def fake_run(agent, request):
        calls.append((agent, request))
        if len(calls) == 1:
            return SimpleNamespace(
                final_output=FAILED_DRAFT,
                new_items=[_tool_output(report_agent, THREE_SOURCE_EVIDENCE)],
            )
        return SimpleNamespace(
            final_output="The deadline is 30 days [Snippet 2].",
            new_items=[],
        )

    monkeypatch.setattr(app, "THROTTLE", throttle)
    monkeypatch.setattr(app.Runner, "run", fake_run)

    answer = asyncio.run(app.answer(report_agent, THREE_SOURCE_QUERY))

    assert answer == FAILED_DRAFT
    assert len(calls) == 2
    assert not app.verifier_output_is_safe("Supported by [Snippet 2].")


@pytest.mark.parametrize(
    "failure",
    [
        "Error code: 429 - quota exhausted",
        "Error code: 503 - model is currently experiencing high demand",
    ],
)
def test_verifier_provider_failure_returns_original_draft(monkeypatch, failure):
    """Any provider-side failure degrades to the draft, not to an exception.

    The draft is already grounded and complete by this point, so a 429, a 503
    or a transport error must never destroy it — an earlier version caught only
    rate limits, and a 503 from the verifier took down the whole answer.
    """
    report_agent = Agent(name="Test Report Generator", model="test-model")
    throttle = RecordingThrottle()
    calls = []

    class FakeAPIError(Exception):
        pass

    async def fake_run(agent, request):
        calls.append((agent, request))
        if len(calls) == 1:
            return SimpleNamespace(
                final_output=FAILED_DRAFT,
                new_items=[_tool_output(report_agent, THREE_SOURCE_EVIDENCE)],
            )
        raise FakeAPIError(failure)

    monkeypatch.setattr(app, "THROTTLE", throttle)
    monkeypatch.setattr(app, "APIError", FakeAPIError)
    monkeypatch.setattr(app.Runner, "run", fake_run)

    answer = asyncio.run(app.answer(report_agent, THREE_SOURCE_QUERY))

    assert answer == FAILED_DRAFT
    assert len(calls) == 2


def test_verifier_timeout_returns_original_draft(monkeypatch):
    report_agent = Agent(name="Test Report Generator", model="test-model")
    throttle = RecordingThrottle()
    calls = []

    async def fake_run(agent, request):
        calls.append((agent, request))
        if len(calls) == 1:
            return SimpleNamespace(
                final_output=FAILED_DRAFT,
                new_items=[_tool_output(report_agent, THREE_SOURCE_EVIDENCE)],
            )
        await asyncio.sleep(0.05)
        return SimpleNamespace(final_output=CORRECTED_ANSWER, new_items=[])

    monkeypatch.setenv("VERIFIER_TIMEOUT_SECONDS", "0.001")
    monkeypatch.setattr(app, "THROTTLE", throttle)
    monkeypatch.setattr(app.Runner, "run", fake_run)

    answer = asyncio.run(app.answer(report_agent, THREE_SOURCE_QUERY))

    assert answer == FAILED_DRAFT
    assert len(calls) == 2


def test_structured_violation_review_removes_unsupported_claims(monkeypatch):
    report_agent = Agent(name="Test Report Generator", model="test-model")
    throttle = RecordingThrottle()
    calls = []
    unsafe_draft = (
        "You violated the messaging rule and failed to encrypt the account number."
    )
    review = app.ViolationReview(
        findings=[
            app.ViolationFinding(
                category="violation",
                scenario_quote=(
                    "sent a customer's account number through my personal "
                    "messaging app"
                ),
                policy_quote=(
                    "Customer data must not be sent to personal email accounts "
                    "or consumer messaging applications."
                ),
                source_policy="Customer Data Privacy Policy",
            ),
            app.ViolationFinding(
                category="violation",
                scenario_quote="shared a file internally",
                policy_quote=(
                    "Files containing account numbers must be encrypted before "
                    "internal sharing."
                ),
                source_policy="Customer Data Privacy Policy",
            ),
            app.ViolationFinding(
                category="required_action",
                scenario_quote=(
                    "sent a customer's account number through my personal "
                    "messaging app"
                ),
                policy_quote=(
                    "Security incidents such as accidental data disclosure must "
                    "be reported to the IT Security team within 2 hours of discovery."
                ),
                source_policy="Incident Response Policy",
            ),
            app.ViolationFinding(
                category="required_action",
                scenario_quote=(
                    "sent a customer's account number through my personal "
                    "messaging app"
                ),
                policy_quote=(
                    "Employees should preserve evidence and avoid deleting "
                    "suspicious files."
                ),
                source_policy="Incident Response Policy",
            ),
        ]
    )

    async def fake_run(agent, request):
        calls.append((agent, request))
        if len(calls) == 1:
            return SimpleNamespace(
                final_output=unsafe_draft,
                new_items=[_tool_output(report_agent, PRIVACY_EVIDENCE)],
            )
        return SimpleNamespace(final_output=review, new_items=[])

    monkeypatch.setattr(app, "THROTTLE", throttle)
    monkeypatch.setattr(app.Runner, "run", fake_run)

    answer = asyncio.run(app.answer(report_agent, PRIVACY_QUERY))

    assert "consumer messaging applications" in answer
    assert "IT Security team within 2 hours" in answer
    assert "preserve evidence" in answer
    assert "encrypted before internal sharing" not in answer
    assert calls[1][0].name == "Violation Claim Verifier"


def test_generator_requires_direct_evidence_for_claimed_violations():
    report_agent = app.build_agents("test-model")
    instructions = str(report_agent.instructions)

    assert "scenario directly establishes that rule's conditions" in instructions
    assert "do not substitute a related fact" in instructions
    assert "cannot be performed in the scenario described" in instructions
    assert "Do not explain the omission" in instructions


def test_verifier_removes_consolation_facts_from_gap_answers():
    verifier = app.build_verifier("test-model")
    instructions = str(verifier.instructions)

    assert "remove replacement or consolation facts" in instructions
    assert "another explicit part of the question" in instructions


def test_google_model_name_is_written_to_transcript(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_MODEL", "gemini-test-model")
    monkeypatch.setenv("GOOGLE_VERIFIER_MODEL", "gemini-test-verifier")
    output = tmp_path / "transcript.md"
    records = [
        {
            "label": "Exact keyword match",
            "query": "When is the receipt due?",
            "trace": "",
            "answer": (
                "Upload it to the finance portal within 7 calendar days "
                "(Corporate Card Policy)."
            ),
            "elapsed": 1.0,
        }
    ]

    run_samples.write_markdown(records, output)

    assert (
        "- Provider / model: `google` / `gemini-test-model`"
        in output.read_text(encoding="utf-8")
    )
    assert "- Verifier model: `gemini-test-verifier`" in output.read_text(
        encoding="utf-8"
    )
    assert "- Factual checks: 1 passed, 0 failed, 0 API errors" in output.read_text(
        encoding="utf-8"
    )


def test_factual_evaluator_passes_complete_three_source_answer():
    result = run_samples.evaluate_answer(
        "Three-source synthesis",
        (
            "Domestic stays are capped at 2,000 THB (Domestic Travel Policy). "
            "Upload within 7 calendar days (Corporate Card Policy). Submit the "
            "claim within 30 days (Expense Reimbursement Policy)."
        ),
    )

    assert result["status"] == "pass"


def test_factual_evaluator_flags_privacy_overreach_and_api_errors():
    overreach = run_samples.evaluate_answer(
        "Incident + privacy cross-policy",
        (
            "Customer data must not be sent to consumer messaging applications. "
            "You also violated the Information Security Policy by failing to "
            "encrypt it. Report to IT Security within 2 hours, preserve evidence, "
            "and avoid deleting suspicious files."
        ),
    )
    api_error = run_samples.evaluate_answer(
        "Near-miss no-answer",
        "ERROR: RateLimitError: quota exhausted",
    )

    assert overreach["status"] == "fail"
    assert "information security policy" in overreach["forbidden"]
    assert api_error["status"] == "error"


def test_wrap_answer_preserves_markdown_structure():
    wrapped = app.wrap_answer(
        "### Hotel Limit\n"
        "\n"
        "- Hotel reimbursement is capped at 2,000 THB per night for domestic "
        "stays, and claims must be submitted within 30 days (Domestic Travel "
        "Policy).\n",
        width=60,
    )
    lines = wrapped.splitlines()

    assert "### Hotel Limit" in lines  # headings pass through unwrapped
    assert "" in lines  # blank lines survive
    assert all(len(line) <= 60 for line in lines)
    # A bullet's continuation hangs under its text instead of resetting left.
    continuations = [ln for ln in lines if ln.startswith("  ") and ln.strip()]
    assert continuations, "expected an indented continuation line"


def test_wrap_answer_does_not_split_hyphenated_terms_or_urls():
    wrapped = app.wrap_answer(
        "- Provide a conflict-of-interest declaration and see "
        "https://example.com/a/deliberately/long/policy/url for details "
        "(Vendor Onboarding Policy).",
        width=40,
    )

    assert "conflict-of-interest" in wrapped
    assert "https://example.com/a/deliberately/long/policy/url" in wrapped
