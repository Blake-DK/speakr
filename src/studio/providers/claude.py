"""Claude, through Anthropic's own Python SDK (``anthropic``, pinned in requirements-studio.txt).

Carried over from the standalone app the studio replaces, made synchronous
for Flask: streaming with adaptive thinking, the web search tool, PDFs as
document blocks, images as image blocks, slide decks and transcripts as text,
automatic prompt caching, the continuation of a paused server-tool turn, the
refusal message, and one retry without ``thinking`` when an older model
rejects it. Added: when an answer that used the web search cites nothing
(the search tool's dynamic filtering gives no citations), the pages the
search found are listed instead, marked as such.
"""

import base64
import logging
import time

from src.studio import providers

logger = logging.getLogger(__name__)

MAX_PAUSE_TURN_CONTINUATIONS = 5
MAX_SEARCHED_PAGES = 8
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}

# The client is made per call: the key is read from the environment each time,
# so a changed key needs no restart of anything but the container.
_client_factory = None


def _client(**options):
    import anthropic
    if _client_factory is not None:  # tests
        return _client_factory(**options)
    return anthropic.Anthropic(api_key=providers.api_key('claude'), **options)


def _content_blocks(message):
    attachments = message.get("attachments") or []
    if not attachments or message["role"] != "user":
        return message["text"]
    blocks = []
    for att in attachments:
        if att["kind"] == "text":
            blocks.append({"type": "text", "text": att["text"]})
            continue
        data = base64.standard_b64encode(att["data"]).decode()
        if att["kind"] == "pdf":
            blocks.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": data},
                "title": att["name"],
            })
        else:
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": att["content_type"], "data": data},
            })
    blocks.append({"type": "text", "text": message["text"]})
    return blocks


def _add_usage(total, usage):
    if usage is None:
        return
    for field in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        value = getattr(usage, field, None)
        if isinstance(value, int):
            total[field] = total.get(field, 0) + value
    server = getattr(usage, "server_tool_use", None)
    searches = getattr(server, "web_search_requests", None) if server is not None else None
    if isinstance(searches, int):
        total["web_search_requests"] = total.get("web_search_requests", 0) + searches


def stream_chat(system, messages, use_web_search=False, model=None):
    import anthropic

    model = model or providers.model('claude')
    client = _client()
    api_messages = [{"role": m["role"], "content": _content_blocks(m)} for m in messages]

    kwargs = {
        "model": model,
        "max_tokens": providers.MAX_OUTPUT_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": system,
        # Automatic caching of the last cacheable block, so long decks and
        # transcripts are not billed in full on every follow-up turn.
        "cache_control": {"type": "ephemeral"},
    }
    if use_web_search:
        kwargs["tools"] = [WEB_SEARCH_TOOL]

    sources = []
    seen_urls = set()
    searched = []          # the pages the web search returned, for when the answer cites none
    searched_urls = set()
    yielded_text = False
    usage = {}
    last_beat = time.monotonic()

    try:
        for attempt in (1, 2):
            try:
                for _ in range(MAX_PAUSE_TURN_CONTINUATIONS + 1):
                    with client.messages.stream(messages=api_messages, **kwargs) as stream:
                        for event in stream:
                            if event.type == "content_block_delta" and event.delta.type == "text_delta":
                                yielded_text = True
                                yield {"type": "text", "text": event.delta.text}
                                last_beat = time.monotonic()
                            elif time.monotonic() - last_beat >= providers.KEEPALIVE_SECONDS:
                                # Thinking or searching: no text yet, but show signs of life.
                                last_beat = time.monotonic()
                                yield {"type": "keepalive"}
                        final = stream.get_final_message()

                    _add_usage(usage, getattr(final, "usage", None))
                    for block in final.content:
                        for citation in getattr(block, "citations", None) or []:
                            url = getattr(citation, "url", None)
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                sources.append({"title": getattr(citation, "title", None) or url, "url": url})
                        if getattr(block, "type", "") == "web_search_tool_result":
                            results = getattr(block, "content", None)
                            for result in results if isinstance(results, list) else []:
                                url = getattr(result, "url", None)
                                if url and url not in searched_urls:
                                    searched_urls.add(url)
                                    searched.append({"title": getattr(result, "title", None) or url, "url": url})

                    if final.stop_reason == "pause_turn":
                        # The server-side tool loop paused; send the turn back so it resumes.
                        api_messages = api_messages + [{"role": "assistant", "content": final.content}]
                        continue
                    if final.stop_reason == "refusal":
                        yield {"type": "error",
                               "message": "Claude declined this request for safety reasons. Try rephrasing."}
                    break
                break
            except anthropic.BadRequestError as e:
                # The model list is live but a model's capabilities are not, so an
                # older model can reject adaptive thinking with a 400. Drop it and
                # retry once: a parameter 400 comes before any text is streamed.
                if attempt == 1 and not yielded_text and "thinking" in kwargs and "thinking" in str(e).lower():
                    kwargs.pop("thinking")
                    continue
                raise

        if sources:
            yield {"type": "sources", "sources": sources, "kind": "cited"}
        elif searched:
            # The web search tool with dynamic filtering (web_search_20260209) reads the
            # results in its own code step, and the answer then carries no citations.
            # List the pages the search found instead, under a heading that says so.
            yield {"type": "sources", "sources": searched[:MAX_SEARCHED_PAGES], "kind": "searched"}
        yield {"type": "usage", "usage": usage, "model": model}
    except anthropic.AuthenticationError:
        yield {"type": "error", "message": "The Claude API key was refused. An administrator needs to check the "
                                           "server's STUDIO_ANTHROPIC_API_KEY."}
    except anthropic.RateLimitError:
        yield {"type": "error", "message": "Claude is rate-limited right now. Wait a moment and try again."}
    except anthropic.APIStatusError as e:
        yield {"type": "error", "message": f"Claude API error ({e.status_code}): {str(e.message)[:500]}"}
    except anthropic.APIConnectionError:
        yield {"type": "error", "message": "Could not reach the Claude API. Check the server's internet connection."}


def list_models():
    """[{"id", "label"}] from Anthropic's live model list. Raises on failure."""
    client = _client(timeout=15.0)
    return [{"id": m.id, "label": getattr(m, "display_name", None) or m.id} for m in client.models.list()]


def test(model):
    """One tiny live request with this model; returns a line for the settings page. Raises on failure."""
    client = _client(timeout=60.0)
    resp = client.messages.create(
        model=model,
        max_tokens=512,
        messages=[{"role": "user", "content": "Reply with the single word: OK"}],
    )
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "").strip()
    return f"{model} replied: {text[:60] or '(empty)'}"
