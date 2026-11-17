"""The chat's Claude and OpenAI providers, without a real call.

The Claude tests run the real Anthropic SDK against a fake HTTP transport that
answers with the Messages API's own stream format, so the request the SDK
builds (thinking, caching, the web search tool, document blocks) and the
events it gives back are the SDK's, not a stand-in's. They need the add-on's
packages (requirements-studio.txt) and are skipped without them, as upstream's
test run does not install those; the image's test run does.
"""

import json
from types import SimpleNamespace

import pytest

from src.studio import providers

anthropic = pytest.importorskip('anthropic')
httpx2 = pytest.importorskip('httpx2')

from src.studio.providers import claude  # noqa: E402


def _sse(*events):
    return ''.join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _message_start(model='claude-opus-5-5'):
    return {'type': 'message_start', 'message': {
        'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'model': model, 'content': [],
        'stop_reason': None, 'stop_sequence': None,
        'usage': {'input_tokens': 10, 'output_tokens': 1, 'cache_creation_input_tokens': 0,
                  'cache_read_input_tokens': 0}}}


def _text_turn(text, stop_reason='end_turn', citations=()):
    events = [_message_start(),
              {'type': 'content_block_start', 'index': 0,
               'content_block': {'type': 'text', 'text': '', 'citations': [] if citations else None}},
              {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text}}]
    for c in citations:
        events.append({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'citations_delta', 'citation': {
            'type': 'web_search_result_location', 'url': c[0], 'title': c[1], 'encrypted_index': 'x',
            'cited_text': 'quoted'}}})
    events += [{'type': 'content_block_stop', 'index': 0},
               {'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None},
                'usage': {'output_tokens': 7}},
               {'type': 'message_stop'}]
    return _sse(*events)


class Server:
    """The Messages API, faked: answers each request with the next scripted response."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def handler(self, request):
        self.requests.append(json.loads(request.content.decode()))
        status, body = self.responses.pop(0)
        if status != 200:
            return httpx2.Response(status, json=body, headers={'content-type': 'application/json'})
        return httpx2.Response(200, content=body, headers={'content-type': 'text/event-stream'})


@pytest.fixture
def server(monkeypatch):
    holder = {}

    def factory(**options):
        transport = httpx2.MockTransport(holder['server'].handler)
        return anthropic.Anthropic(api_key='test-key-not-real', max_retries=0,
                                   http_client=anthropic.DefaultHttpxClient(transport=transport), **options)

    monkeypatch.setattr(claude, '_client_factory', factory)
    monkeypatch.setattr(providers, 'model', lambda name: 'claude-opus-5-5')

    def use(*responses):
        holder['server'] = Server(*responses)
        return holder['server']
    return use


def _run(**kwargs):
    messages = kwargs.pop('messages', [{'role': 'user', 'text': 'hi', 'attachments': []}])
    return list(claude.stream_chat('system text', messages, **kwargs))


def test_the_request_the_sdk_sends(server):
    srv = server((200, _text_turn('OK')))
    pdf = {'kind': 'pdf', 'name': 'deck.pdf', 'content_type': 'application/pdf', 'data': b'%PDF-1.4'}
    image = {'kind': 'image', 'name': 's.png', 'content_type': 'image/png', 'data': b'\x89PNG'}
    deck = {'kind': 'text', 'name': 'deck.pptx', 'text': 'Contents of attached slide deck'}
    events = _run(messages=[{'role': 'user', 'text': 'first', 'attachments': [pdf, image, deck]},
                            {'role': 'assistant', 'text': 'answer', 'attachments': []},
                            {'role': 'user', 'text': 'second', 'attachments': []}], use_web_search=True)
    sent = srv.requests[0]
    assert sent['model'] == 'claude-opus-5-5' and sent['stream'] is True
    assert sent['max_tokens'] == providers.MAX_OUTPUT_TOKENS
    assert sent['thinking'] == {'type': 'adaptive'}
    assert sent['cache_control'] == {'type': 'ephemeral'}
    assert sent['system'] == 'system text'
    assert sent['tools'] == [{'type': 'web_search_20260209', 'name': 'web_search'}]
    assert 'temperature' not in sent and 'top_p' not in sent
    first = sent['messages'][0]['content']
    assert [b['type'] for b in first] == ['document', 'image', 'text', 'text']
    assert first[0]['source'] == {'type': 'base64', 'media_type': 'application/pdf', 'data': 'JVBERi0xLjQ='}
    assert first[0]['title'] == 'deck.pdf'
    assert first[3]['text'] == 'first'
    assert sent['messages'][1] == {'role': 'assistant', 'content': 'answer'}
    assert events[0] == {'type': 'text', 'text': 'OK'}
    assert events[-1]['type'] == 'usage' and events[-1]['usage']['output_tokens'] == 7


def test_web_citations_become_sources_once_each(server):
    server((200, _text_turn('Found it.', citations=[('https://example.com/a', 'A'), ('https://example.com/a', 'A'),
                                                    ('https://example.org/b', None)])))
    events = _run(use_web_search=True)
    sources = next(e for e in events if e['type'] == 'sources')['sources']
    assert sources == [{'title': 'A', 'url': 'https://example.com/a'},
                       {'title': 'https://example.org/b', 'url': 'https://example.org/b'}]


def test_a_paused_turn_is_sent_back_and_resumes(server):
    srv = server((200, _text_turn('Part one. ', stop_reason='pause_turn')), (200, _text_turn('Part two.')))
    events = _run(use_web_search=True)
    assert ''.join(e['text'] for e in events if e['type'] == 'text') == 'Part one. Part two.'
    assert len(srv.requests) == 2
    resumed = srv.requests[1]['messages']
    assert resumed[-1]['role'] == 'assistant'
    assert resumed[-1]['content'][0]['type'] == 'text'
    usage = next(e for e in events if e['type'] == 'usage')['usage']
    assert usage['output_tokens'] == 14 and usage['input_tokens'] == 20


def test_a_refusal_is_said_plainly(server):
    server((200, _text_turn('', stop_reason='refusal')))
    events = _run()
    assert {'type': 'error', 'message': 'Claude declined this request for safety reasons. Try rephrasing.'} in events


def test_a_model_that_rejects_thinking_is_asked_again_without_it(server):
    srv = server((400, {'type': 'error', 'error': {'type': 'invalid_request_error',
                                                   'message': 'adaptive thinking is not supported on this model'}}),
                 (200, _text_turn('OK')))
    events = _run()
    assert {'type': 'text', 'text': 'OK'} in events
    assert not any(e['type'] == 'error' for e in events)
    assert 'thinking' in srv.requests[0] and 'thinking' not in srv.requests[1]


def test_another_400_is_not_retried_away(server):
    srv = server((400, {'type': 'error', 'error': {'type': 'invalid_request_error',
                                                   'message': 'some other validation error'}}))
    events = _run()
    assert any(e['type'] == 'error' and '400' in e['message'] for e in events)
    assert len(srv.requests) == 1


def test_a_refused_key_says_what_to_check_and_never_the_key(server):
    server((401, {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}}))
    events = _run()
    assert events == [{'type': 'error', 'message': 'The Claude API key was refused. An administrator needs to '
                                                   "check the server's STUDIO_ANTHROPIC_API_KEY."}]
    assert 'test-key-not-real' not in json.dumps(events)


def test_closing_the_stream_mid_answer_closes_the_sdk_stream(server, monkeypatch):
    server((200, _text_turn('Hello')))
    gen = claude.stream_chat('s', [{'role': 'user', 'text': 'hi', 'attachments': []}])
    assert next(gen) == {'type': 'text', 'text': 'Hello'}
    gen.close()  # what the chat does when the browser goes away
    with pytest.raises(StopIteration):
        next(gen)


# --- OpenAI, faked at the client --------------------------------------------------

class FakeResponses:
    def __init__(self, events):
        self.events = events
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return iter(self.events)


def test_openai_streams_text_sources_and_usage(monkeypatch):
    pytest.importorskip('openai')
    from src.studio.providers import openai_client
    events = [SimpleNamespace(type='response.output_text.delta', delta='Hi'),
              SimpleNamespace(type='response.output_text.annotation.added',
                              annotation={'url': 'https://example.com', 'title': 'Ex'}),
              SimpleNamespace(type='response.completed',
                              response=SimpleNamespace(usage=SimpleNamespace(input_tokens=5, output_tokens=2)))]
    fake = FakeResponses(events)
    monkeypatch.setattr(openai_client, '_client_factory', lambda **o: SimpleNamespace(responses=fake))
    pdf = {'kind': 'pdf', 'name': 'd.pdf', 'content_type': 'application/pdf', 'data': b'%PDF'}
    out = list(openai_client.stream_chat('sys', [{'role': 'user', 'text': 'q', 'attachments': [pdf]}],
                                         use_web_search=True, model='gpt-test'))
    assert out[0] == {'type': 'text', 'text': 'Hi'}
    assert {'type': 'sources', 'sources': [{'title': 'Ex', 'url': 'https://example.com'}]} in out
    assert out[-1] == {'type': 'usage', 'usage': {'input_tokens': 5, 'output_tokens': 2}, 'model': 'gpt-test'}
    assert fake.kwargs['instructions'] == 'sys' and fake.kwargs['tools'] == [{'type': 'web_search'}]
    parts = fake.kwargs['input'][0]['content']
    assert parts[0]['type'] == 'input_file' and parts[0]['file_data'].startswith('data:application/pdf;base64,')
    assert parts[-1] == {'type': 'input_text', 'text': 'q'}
