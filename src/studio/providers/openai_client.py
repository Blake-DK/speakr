"""GPT, through OpenAI's Responses API (the ``openai`` package upstream already installs).

Carried over from the standalone app the studio replaces, made synchronous
for Flask: streaming, the web search tool, PDFs as input files, images as
input images, slide decks and transcripts as text.
"""

import base64
import time

from src.studio import providers

_client_factory = None


def _client(**options):
    from openai import OpenAI
    if _client_factory is not None:  # tests
        return _client_factory(**options)
    return OpenAI(api_key=providers.api_key('openai'), **options)


def _input_items(messages):
    items = []
    for m in messages:
        if m["role"] == "assistant":
            items.append({"role": "assistant", "content": m["text"]})
            continue
        attachments = m.get("attachments") or []
        if not attachments:
            items.append({"role": "user", "content": m["text"]})
            continue
        parts = []
        for att in attachments:
            if att["kind"] == "text":
                parts.append({"type": "input_text", "text": att["text"]})
                continue
            data = base64.b64encode(att["data"]).decode()
            if att["kind"] == "pdf":
                parts.append({
                    "type": "input_file",
                    "filename": att["name"],
                    "file_data": f"data:application/pdf;base64,{data}",
                })
            else:
                parts.append({"type": "input_image", "image_url": f"data:{att['content_type']};base64,{data}"})
        parts.append({"type": "input_text", "text": m["text"]})
        items.append({"role": "user", "content": parts})
    return items


def _get(obj, name):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def stream_chat(system, messages, use_web_search=False, model=None):
    import openai

    model = model or providers.model('openai')
    client = _client()
    kwargs = {
        "model": model,
        "instructions": system,
        "input": _input_items(messages),
        "stream": True,
    }
    if use_web_search:
        kwargs["tools"] = [{"type": "web_search"}]

    sources = []
    seen_urls = set()
    usage = {}
    last_beat = time.monotonic()

    try:
        stream = client.responses.create(**kwargs)
        try:
            for event in stream:
                etype = getattr(event, "type", "")
                if etype == "response.output_text.delta":
                    yield {"type": "text", "text": event.delta}
                    last_beat = time.monotonic()
                elif etype == "response.output_text.annotation.added":
                    ann = event.annotation
                    url, title = _get(ann, "url"), _get(ann, "title")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        sources.append({"title": title or url, "url": url})
                elif etype == "response.failed":
                    yield {"type": "error", "message": "OpenAI reported the response failed. Try again."}
                elif etype == "response.completed":
                    found = _get(_get(event, "response"), "usage")
                    for field in ("input_tokens", "output_tokens"):
                        value = _get(found, field) if found is not None else None
                        if isinstance(value, int):
                            usage[field] = value
                elif time.monotonic() - last_beat >= providers.KEEPALIVE_SECONDS:
                    last_beat = time.monotonic()
                    yield {"type": "keepalive"}
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if sources:
            yield {"type": "sources", "sources": sources, "kind": "cited"}
        yield {"type": "usage", "usage": usage, "model": model}
    except openai.AuthenticationError:
        yield {"type": "error", "message": "The OpenAI API key was refused. An administrator needs to check the "
                                           "server's STUDIO_OPENAI_API_KEY."}
    except openai.RateLimitError:
        yield {"type": "error", "message": "OpenAI is rate-limited right now. Wait a moment and try again."}
    except openai.APIStatusError as e:
        yield {"type": "error",
               "message": f"OpenAI API error ({e.status_code}): {str(getattr(e, 'message', '') or e)[:500]}"}
    except openai.APIConnectionError:
        yield {"type": "error", "message": "Could not reach the OpenAI API. Check the server's internet connection."}


EXCLUDED_MODEL_WORDS = ("audio", "realtime", "image", "transcribe", "tts", "embed", "moderation", "dall-e", "whisper")


def list_models():
    """[{"id", "label"}] of OpenAI's chat models, newest first. Raises on failure."""
    client = _client(timeout=15.0)
    found = [{"id": m.id, "label": m.id} for m in client.models.list()
             if m.id.startswith(("gpt-", "o")) and not any(x in m.id for x in EXCLUDED_MODEL_WORDS)]
    return sorted(found, key=lambda x: x["id"], reverse=True)


def test(model):
    """One tiny live request with this model; returns a line for the settings page. Raises on failure."""
    client = _client(timeout=60.0)
    resp = client.responses.create(model=model, input="Reply with the single word: OK")
    text = (getattr(resp, "output_text", "") or "").strip()
    return f"{model} replied: {text[:60] or '(empty)'}"
