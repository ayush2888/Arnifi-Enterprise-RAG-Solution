"""Tests for question → source intent auto-detect."""

from __future__ import annotations

from app.services.retrieval.source_intent import (
    detect_source_families,
    resolve_source_intent,
)


def test_whatsapp_only_follow_up() -> None:
    assert (
        resolve_source_intent(
            "does it have any whatsapp chat related to it?",
            client_source="all",
        )
        == "whatsapp"
    )


def test_whatsapp_variants() -> None:
    assert resolve_source_intent("check periskope for golden visa") == "whatsapp"
    assert resolve_source_intent("any WA group discussion on this?") == "whatsapp"


def test_docs_and_whatsapp_searches_both() -> None:
    # Explicit Drive + WhatsApp → all
    assert (
        resolve_source_intent("check from drive and from whatsapp")
        == "all"
    )
    # Softened: bare "docs" is not a Drive cue, so only WhatsApp hits
    assert resolve_source_intent("check from docs and from whatsapp") == "whatsapp"


def test_blog_and_website_cues_resolve_to_website() -> None:
    assert resolve_source_intent("what does the blog say about UAE visa?") == "website"
    assert resolve_source_intent("from the website, company setup costs") == "website"


def test_drive_only_with_framing() -> None:
    assert resolve_source_intent("check from drive about KYC") == "drive"
    assert resolve_source_intent("search google drive for freezone rules") == "drive"
    assert resolve_source_intent("from the drive, what is required?") == "drive"


def test_generic_docs_pdfs_files_do_not_force_drive() -> None:
    # Softened auto-detect: vague "docs/pdfs/files" must not lock to Drive-only.
    assert resolve_source_intent("search our docs for freezone rules") == "all"
    assert resolve_source_intent("from the documents, what is required?") == "all"
    assert resolve_source_intent("check the PDFs for IQAMA fees") == "all"
    assert resolve_source_intent("look in the files for pricing") == "all"


def test_documents_word_alone_does_not_force_drive() -> None:
    # Must not treat "KYC documents required" as Drive-only intent.
    assert (
        resolve_source_intent(
            "What KYC documents are required for Cayman Island company setup?"
        )
        == "all"
    )
    assert detect_source_families(
        "What KYC documents are required for Cayman Island company setup?"
    ) == set()


def test_no_cue_stays_all() -> None:
    assert resolve_source_intent("What is a UAE golden visa and who qualifies?") == "all"


def test_explicit_client_source_wins() -> None:
    assert (
        resolve_source_intent(
            "any whatsapp chat about this?",
            client_source="blog",
        )
        == "website"
    )
    assert (
        resolve_source_intent(
            "ignore keywords",
            client_source="drive",
        )
        == "drive"
    )


def test_force_all_phrases() -> None:
    assert resolve_source_intent("whatsapp and blogs too please") == "all"
    assert resolve_source_intent("search all sources for golden visa") == "all"


def test_short_follow_up_uses_current_whatsapp_cue() -> None:
    history = [
        {"role": "user", "content": "What is UAE golden visa?"},
        {"role": "assistant", "content": "A long-term residency program."},
    ]
    assert (
        resolve_source_intent("and WhatsApp?", client_source="all", history=history)
        == "whatsapp"
    )
