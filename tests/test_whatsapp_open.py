from app.utils.whatsapp_open import (
    chat_id_from_source_url,
    dm_phone_from_chat_id,
    enrich_whatsapp_sources,
    invite_code_from_link,
    whatsapp_open_urls,
)


def test_group_invite_becomes_deep_link_and_https():
    urls = whatsapp_open_urls(
        chat_id="120363025123456789@g.us",
        invite_link="https://chat.whatsapp.com/AbCdEfGhIjKlMn",
    )
    assert urls["open_url"] == "whatsapp://chat?code=AbCdEfGhIjKlMn"
    assert urls["fallback_url"] == "https://chat.whatsapp.com/AbCdEfGhIjKlMn"


def test_group_invite_strips_trailing_slash_and_query():
    urls = whatsapp_open_urls(
        chat_id="120363025123456789@g.us",
        invite_link="https://chat.whatsapp.com/AbCdEfGhIjKlMn/?utm=x",
    )
    assert urls["open_url"] == "whatsapp://chat?code=AbCdEfGhIjKlMn"
    assert urls["fallback_url"] == "https://chat.whatsapp.com/AbCdEfGhIjKlMn"


def test_dm_chat_id_becomes_wa_me():
    urls = whatsapp_open_urls(
        chat_id="919876543210@c.us",
        invite_link=None,
    )
    assert urls["open_url"] == "https://wa.me/919876543210"
    assert urls["fallback_url"] == "https://wa.me/919876543210"


def test_junk_chat_id_rejected():
    urls = whatsapp_open_urls(chat_id="not-a-chat", invite_link="https://chat.whatsapp.com/AbCdEfGhIjKlMn")
    assert urls["open_url"] is None
    assert urls["fallback_url"] is None


def test_group_without_invite_has_no_open_urls():
    urls = whatsapp_open_urls(chat_id="120363025123456789@g.us", invite_link=None)
    assert urls["open_url"] is None
    assert urls["fallback_url"] is None


def test_invite_code_rejects_non_whatsapp_host():
    assert invite_code_from_link("https://evil.example/AbCdEfGhIjKlMn") is None
    assert invite_code_from_link("https://chat.whatsapp.com/no") is None


def test_dm_phone_rejects_group_id():
    assert dm_phone_from_chat_id("120363025123456789@g.us") is None
    assert dm_phone_from_chat_id("919876543210@c.us") == "919876543210"


def test_chat_id_from_source_url():
    cid = "120363025123456789@g.us"
    url = f"whatsapp://chat/{cid}/episode/abc123"
    assert chat_id_from_source_url(url) == cid
    assert chat_id_from_source_url("https://example.com") is None


def test_enrich_whatsapp_sources_attaches_invite():
    cid = "120363025123456789@g.us"
    invite = "https://chat.whatsapp.com/AbCdEfGhIjKlMn"
    sources = [
        {
            "source_index": 1,
            "source_url": f"whatsapp://chat/{cid}/episode/ep1",
            "doc_title": "Ops Group",
            "source_type": "whatsapp",
            "snippet": "summary",
        },
        {
            "source_index": 2,
            "source_url": "https://arnifi.com/blog/x",
            "doc_title": "Blog",
            "source_type": "blog",
        },
    ]
    out = enrich_whatsapp_sources(
        sources,
        get_invite_link=lambda chat_id: invite if chat_id == cid else None,
    )
    assert out[0]["chat_id"] == cid
    assert out[0]["group_invite_link"] == invite
    assert out[0]["open_url"] == "whatsapp://chat?code=AbCdEfGhIjKlMn"
    assert out[0]["fallback_url"] == invite
    assert "group_invite_link" not in out[1]


def test_enrich_whatsapp_sources_missing_invite():
    cid = "120363025123456789@g.us"
    sources = [
        {
            "source_index": 1,
            "source_url": f"whatsapp://chat/{cid}/episode/ep1",
            "source_type": "whatsapp",
        }
    ]
    out = enrich_whatsapp_sources(sources, get_invite_link=lambda _: None)
    assert out[0]["chat_id"] == cid
    assert out[0]["group_invite_link"] is None
    assert out[0]["open_url"] is None
