"""Offline tests for the retrieval layer (no LLM calls, no API keys).

Run:  pytest tests/ -v
Note: the first run downloads the MiniLM embedding model (~80 MB).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import retrieval
from retrieval import NO_ANSWER_MARKER, SemanticIndex, hybrid_search, load_chunks

KB_PATH = Path(__file__).parent.parent / "knowledge_base.txt"


@pytest.fixture(scope="module")
def chunks():
    return load_chunks(str(KB_PATH))


@pytest.fixture(scope="module")
def semantic_index(chunks):
    return SemanticIndex(chunks)


def _texts(results):
    return " ".join(r.chunk.text for r in results)


def test_exact_keyword_query(chunks, semantic_index):
    """Their sample query must rank the international travel policy first."""
    results = hybrid_search(
        "What is the policy on international travel?", chunks, semantic_index
    )
    assert results != NO_ANSWER_MARKER
    assert results[0].chunk.text.startswith("International Travel Policy")


def test_semantic_paraphrase(chunks, semantic_index):
    """Paraphrase with no keyword overlap on 'international travel'."""
    if not semantic_index.available:
        pytest.skip("embedding model unavailable")
    results = hybrid_search(
        "What are the rules for an overseas business trip?", chunks, semantic_index
    )
    assert results != NO_ANSWER_MARKER
    assert "International Travel Policy" in _texts(results)


def test_multi_policy_query(chunks, semantic_index):
    """A question spanning two policies should retrieve both."""
    results = hybrid_search(
        "How do I claim expenses for a hotel on a domestic trip?",
        chunks,
        semantic_index,
    )
    assert results != NO_ANSWER_MARKER
    text = _texts(results)
    assert "Domestic Travel Policy" in text
    assert "Expense Reimbursement Policy" in text


def test_three_source_hotel_card_query(chunks, semantic_index):
    """The failed synthesis case must receive all three supporting policies."""
    results = hybrid_search(
        (
            "I paid for a domestic hotel with my corporate card. What is the "
            "hotel limit, receipt upload deadline, and expense claim deadline?"
        ),
        chunks,
        semantic_index,
    )
    assert results != NO_ANSWER_MARKER
    text = _texts(results)
    assert "Domestic Travel Policy" in text
    assert "Corporate Card Policy" in text
    assert "Expense Reimbursement Policy" in text


def test_unrelated_query_returns_no_answer(chunks, semantic_index):
    """A query with no relation to any policy must trigger the marker."""
    results = hybrid_search(
        "quantum entanglement spaghetti recipe", chunks, semantic_index
    )
    assert results == NO_ANSWER_MARKER


def test_bm25_only_fallback(chunks):
    """With no semantic index, BM25 alone must still answer keyword queries."""
    results = hybrid_search(
        "What is the policy on international travel?", chunks, semantic_index=None
    )
    assert results != NO_ANSWER_MARKER
    assert results[0].chunk.text.startswith("International Travel Policy")


def test_expanded_corporate_card_policy_keyword_query(chunks):
    """New corporate-card content should be retrievable without embeddings."""
    results = hybrid_search(
        "When should corporate card receipts be uploaded?",
        chunks,
        semantic_index=None,
    )
    assert results != NO_ANSWER_MARKER
    assert results[0].chunk.text.startswith("Corporate Card Policy")


def test_expanded_data_privacy_policy_keyword_query(chunks):
    """New privacy content should answer customer-data handling questions."""
    results = hybrid_search(
        "Can I send customer account numbers to a personal email account?",
        chunks,
        semantic_index=None,
    )
    assert results != NO_ANSWER_MARKER
    assert results[0].chunk.text.startswith("Customer Data Privacy Policy")


def test_expanded_multi_policy_security_query(chunks):
    """A security question should retrieve both equipment and incident policies."""
    results = hybrid_search(
        "What should I do if I lose a company laptop or security token?",
        chunks,
        semantic_index=None,
    )
    assert results != NO_ANSWER_MARKER
    text = _texts(results)
    assert "Office Equipment Policy" in text
    assert "Incident Response Policy" in text


def test_noisy_vendor_purchase_query_retrieves_both_policies(chunks):
    """A realistic, wordy query should still find the two relevant policies."""
    results = hybrid_search(
        (
            "Before we sign a new technology vendor and issue a purchase order, "
            "do we need quotes or due diligence?"
        ),
        chunks,
        semantic_index=None,
    )
    assert results != NO_ANSWER_MARKER
    text = _texts(results)
    assert "Procurement Policy" in text
    assert "Vendor Onboarding Policy" in text


def test_question_filler_words_do_not_dominate_ranking(chunks):
    """Question wording should not outrank the actual laptop/security terms."""
    results = hybrid_search(
        "What should I do if I lose a company laptop or security token?",
        chunks,
        semantic_index=None,
    )
    assert results != NO_ANSWER_MARKER
    assert results[0].chunk.text.startswith("Office Equipment Policy")


def test_specific_single_term_still_retrieves_after_relevance_guard(chunks):
    """The relevance guard should not block concise but valid questions."""
    results = hybrid_search("password length", chunks, semantic_index=None)
    assert results != NO_ANSWER_MARKER
    assert results[0].chunk.text.startswith("Information Security Policy")


def test_policy_sounding_near_miss_returns_no_answer(chunks):
    """Generic policy words alone should not force a false positive answer."""
    results = hybrid_search(
        "What is the parking policy for employees at headquarters?",
        chunks,
        semantic_index=None,
    )
    assert results == NO_ANSWER_MARKER


def test_top_k_limits_returned_results(chunks):
    """Retrieval should return only the requested number of snippets."""
    results = hybrid_search(
        "purchase order vendor approval quotes",
        chunks,
        semantic_index=None,
        top_k=2,
    )
    assert results != NO_ANSWER_MARKER
    assert len(results) == 2


def test_format_results_no_answer_passthrough():
    assert retrieval.format_results(NO_ANSWER_MARKER) == NO_ANSWER_MARKER
