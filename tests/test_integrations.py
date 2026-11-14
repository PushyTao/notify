import importlib
import json
import wave

import httpx
import pytest


@pytest.fixture
def adapters():
    try:
        return importlib.import_module('app.integrations')
    except ModuleNotFoundError as exc:
        if exc.name == 'app.integrations':
            pytest.fail('The integrations module is not implemented')
        raise


def mock_http(monkeypatch, adapters, handler):
    client = httpx.Client
    monkeypatch.setattr(adapters.httpx, 'Client', lambda **kwargs: client(
        transport=httpx.MockTransport(handler), **kwargs,
    ))


AUTHOR = {'source_id': 'author-1', 'name': '主播', 'provider': 'generic'}
COLLECTOR = {'base_url': 'https://provider.example/feed', 'api_key': 'secret-key'}
IDENTITY = '6a234656-45c2-45ea-bee7-279c57e116d8'
MODEL = {'base_url': 'https://model.example/v1', 'api_key': 'model-secret', 'model_id': 'example-model'}


def post(video_id='1', when=1700000000):
    return {'video_id': video_id, 'published_at': when, 'title': '<影片>', 'author_name': '主播',
            'url': f'https://www.douyin.com/video/{video_id}', 'media_url': 'https://media.example/file'}


def test_follows_generic_cursors_and_deduplicates(monkeypatch, adapters):
    def handler(request):
        assert request.url.path == '/feed'
        assert request.url.params['author_id'] == 'author-1'
        assert request.headers['Authorization'] == 'Bearer secret-key'
        if not request.url.params.get('cursor'):
            return httpx.Response(200, json={'items': [post('1')], 'next_cursor': 'opaque'})
        assert request.url.params['cursor'] == 'opaque'
        return httpx.Response(200, json={'items': [post('1'), post('2', 1690000000)], 'next_cursor': None})

    mock_http(monkeypatch, adapters, handler)
    assert adapters.fetch_posts(AUTHOR, COLLECTOR) == [post('1'), post('2', 1690000000)]


@pytest.mark.parametrize('body', [
    {'items': []}, {'items': [], 'next_cursor': None, 'error': {'message': 'secret-key'}},
    {'items': [{'video_id': '1'}], 'next_cursor': None},
    {'items': [post(when=1700000000000)], 'next_cursor': None},
    {'items': [post()], 'next_cursor': 1},
])
def test_rejects_invalid_generic_feed(monkeypatch, adapters, body):
    mock_http(monkeypatch, adapters, lambda _: httpx.Response(200, json=body))
    with pytest.raises(RuntimeError) as exc:
        adapters.fetch_posts(AUTHOR, COLLECTOR)
    assert 'secret-key' not in str(exc.value)


def test_rejects_cursor_loops_and_incomplete_page_budget(monkeypatch, adapters):
    mock_http(monkeypatch, adapters, lambda _: httpx.Response(200, json={'items': [post()], 'next_cursor': 'next'}))
    for max_pages in [1, 3]:
        with pytest.raises(RuntimeError):
            adapters.fetch_posts(AUTHOR, {**COLLECTOR, 'max_pages': max_pages})


def test_successful_empty_feed_can_establish_baseline(monkeypatch, adapters):
    mock_http(monkeypatch, adapters, lambda _: httpx.Response(200, json={'items': [], 'next_cursor': None}))
    assert adapters.fetch_posts({**AUTHOR, 'initialized': False}, COLLECTOR) == []


def test_unconfigured_third_party_never_receives_dtk_key(monkeypatch, adapters):
    mock_http(monkeypatch, adapters, lambda _: pytest.fail('Request should not have been sent'))
    settings = {'base_url': 'http://collector:8000', 'api_key': 'dtk-secret', 'third_party_url': ''}
    with pytest.raises(RuntimeError, match='第三方采集地址'):
        adapters.fetch_posts(AUTHOR, settings)


def test_poll_stops_only_after_passing_timestamp_cutoff(monkeypatch, adapters):
    def handler(request):
        cursor = request.url.params.get('cursor')
        if cursor is None:
            return httpx.Response(200, json={'items': [post()], 'next_cursor': 'boundary'})
        assert cursor == 'boundary'
        return httpx.Response(200, json={'items': [post('2'), post('3', 1699999999)], 'next_cursor': 'unused'})

    mock_http(monkeypatch, adapters, handler)
    author = {**AUTHOR, 'initialized': True, 'baseline_at': 1700000000}
    assert [p['video_id'] for p in adapters.fetch_posts(author, COLLECTOR)] == ['2', '1', '3']


def test_pinned_dtk_contract_normalizes_publish_time_and_media(monkeypatch, adapters):
    def handler(request):
        assert request.url.path == '/api/v1/douyin/user/posts'
        assert request.url.params['identity'] == IDENTITY
        assert request.url.params['url'] == 'https://www.douyin.com/user/MS4wLjAB_example'
        assert request.headers['X-API-Key'] == 'dtk-key'
        assert 'Cookie' not in request.headers
        return httpx.Response(200, json={'success': True, 'error': None, 'meta': {}, 'data': {
            'cursor': None, 'has_more': False, 'items': [
                {'kind': 'image_album'},
                {'content_id': '1', 'kind': 'video', 'created_at': '2023-11-14T22:13:20Z',
                 'title': '<影片>', 'web_url': 'https://www.douyin.com/video/1',
                 'author': {'nickname': '另一个名称'}, 'media': {'video': {'url': 'https://media.example/file'}}},
            ],
        }})

    mock_http(monkeypatch, adapters, handler)
    author = {**AUTHOR, 'provider': 'self_hosted', 'source_id': 'https://www.douyin.com/user/MS4wLjAB_example'}
    assert adapters.fetch_posts(author, {'base_url': 'http://dtk:8000', 'api_key': 'dtk-key', 'identity_id': IDENTITY}) == [post()]


def test_guest_dtk_response_cannot_establish_baseline(monkeypatch, adapters):
    mock_http(monkeypatch, adapters, lambda _: httpx.Response(200, json={
        'success': True, 'error': None, 'meta': {'guest_fallback': True},
        'data': {'items': [], 'cursor': None, 'has_more': False},
    }))
    with pytest.raises(RuntimeError, match='Cookie'):
        adapters.fetch_posts({**AUTHOR, 'provider': 'self_hosted'}, {'base_url': 'http://dtk:8000'})


def test_refresh_uses_uncached_dtk_video_detail(monkeypatch, adapters):
    def handler(request):
        assert request.url.path == '/api/v1/douyin/video'
        assert request.url.params['aweme_id'] == '1'
        assert request.url.params['identity'] == IDENTITY
        assert request.url.params['refresh'] == 'true'
        return httpx.Response(200, json={'success': True, 'error': None, 'meta': {}, 'data': {
            'kind': 'video', 'content_id': '1', 'media': {'video': {'url': 'https://media.example/new?sign=abcd'}},
        }})

    mock_http(monkeypatch, adapters, handler)
    author = {**AUTHOR, 'provider': 'self_hosted'}
    settings = {'base_url': 'http://dtk:8000', 'api_key': 'dtk-key', 'identity_id': IDENTITY}
    assert adapters.refresh_media(post(), author, settings) == 'https://media.example/new?sign=abcd'


def test_refresh_generic_uses_the_same_video_id(monkeypatch, adapters):
    original = post('1')
    def handler(request):
        cursor = request.url.params.get('cursor')
        previous = post('2', when=1700000001)
        if cursor is None:
            return httpx.Response(200, json={'items': [previous], 'next_cursor': 'more'})
        assert cursor == 'more'
        updated = {**original, 'media_url': 'https://media.example/new'}
        return httpx.Response(200, json={'items': [updated], 'next_cursor': 'yet-more'})

    mock_http(monkeypatch, adapters, handler)
    assert adapters.refresh_media(original, AUTHOR, COLLECTOR) == 'https://media.example/new'


def test_cookie_import_stores_signed_in_jar_upstream_only(monkeypatch, adapters):
    operations = []

    def handler(request):
        assert request.method == 'POST'
        assert request.url.path == '/api/v1/admin/identities/import'
        assert request.headers['X-API-Key'] == 'dtk-key'
        assert 'Cookie' not in request.headers
        body = json.loads(request.content)
        assert body['platform'] == 'douyin'
        assert body['user_agent'] == 'sample UA'
        assert body['cookies'] == 'ttwid=example; sessionid=private'
        assert body['reserved'] is True
        dry_run = body.get('dry_run', False)
        operations.append(dry_run)
        return httpx.Response(200 if dry_run else 201, json={
            'success': True, 'error': None, 'data': {'stored': not dry_run, 'identity_id': IDENTITY,
                                                     'report': {'authenticated': True, 'usable': True}},
        })

    mock_http(monkeypatch, adapters, handler)
    assert adapters.import_cookie({'base_url': 'http://dtk:8000', 'api_key': 'dtk-key',
                                   'cookie': 'ttwid=example; sessionid=private', 'user_agent': 'sample UA'}) == IDENTITY
    assert operations == [True, False]


def test_model_real_connectivity_and_long_text_merge(monkeypatch, adapters):
    calls = []

    def handler(request):
        assert request.url.path == '/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer model-secret'
        body = json.loads(request.content)
        assert body['model'] == 'example-model'
        calls.append(body['messages'][1]['content'])
        output = 'merged' if 'part-1' in calls[-1] and 'part-2' in calls[-1] else f'part-{len(calls)}'
        return httpx.Response(200, json={'choices': [{'message': {'content': output}}]})

    mock_http(monkeypatch, adapters, handler)
    assert adapters.summarize('文字' * 3500, MODEL, '归纳要点') == 'merged'
    assert len(calls) == 3
    assert adapters.test_model(MODEL) == 'part-4'


def test_model_rejects_blank_content_without_leaking_errors(monkeypatch, adapters):
    for body in ({'choices': [{'message': {'content': '  '}}]}, {'error': {'message': 'model-secret'}}):
        mock_http(monkeypatch, adapters, lambda _: httpx.Response(200, json=body))
        with pytest.raises(RuntimeError) as exc:
            adapters.summarize('正文', MODEL, '总结')
        assert 'model-secret' not in str(exc.value)
        monkeypatch.undo()


def test_model_uses_its_configured_long_timeout(monkeypatch, adapters):
    def handler(request):
        assert request.extensions['timeout']['read'] == 300
        return httpx.Response(200, json={'choices': [{'message': {'content': '成功'}}]})

    mock_http(monkeypatch, adapters, handler)
    assert adapters.test_model({**MODEL, 'timeout_seconds': 300}) == '成功'


@pytest.mark.parametrize('url', ['file:///tmp/forbidden', 'http://127.0.0.1/private',
                                  'http://[::ffff:192.168.1.2]/private'])
def test_media_rejects_non_public_destinations_before_request(monkeypatch, adapters, url):
    mock_http(monkeypatch, adapters, lambda _: pytest.fail('Unsafe address was contacted'))
    with pytest.raises(RuntimeError):
        adapters.transcribe(url, {})


def test_douyin_media_headers_only_sent_to_douyin_hosts(monkeypatch, adapters, tmp_path):
    monkeypatch.setattr(adapters.socket, 'getaddrinfo', lambda *_args, **_kwargs: [
        (2, 1, 6, '', ('8.8.8.8', 443)),
    ])

    def handler(request):
        assert 'Cookie' not in request.headers and 'Authorization' not in request.headers
        if request.headers['Host'] == 'sample.zjcdn.com':
            assert request.headers['Referer'] == 'https://www.douyin.com/'
            assert 'Chrome/' in request.headers['User-Agent']
            return httpx.Response(302, headers={'Location': 'https://notzjcdn.com/media'})
        assert request.headers['Host'] == 'notzjcdn.com'
        assert 'Referer' not in request.headers
        assert 'Chrome/' not in request.headers['User-Agent']
        return httpx.Response(200, stream=httpx.ByteStream(b'media bytes'))

    mock_http(monkeypatch, adapters, handler)
    media = tmp_path / 'media'
    adapters._download_media('https://sample.zjcdn.com/media', media)
    assert media.read_bytes() == b'media bytes'


def test_download_pins_public_ip_and_checks_redirect(monkeypatch, adapters):
    monkeypatch.setattr(adapters.socket, 'getaddrinfo', lambda *_a, **_kw: [
        (2, 1, 6, '', ('8.8.8.8', 443)),
    ])

    def handler(request):
        assert request.url.host == '8.8.8.8'
        assert request.headers['Host'] == 'media.example'
        assert request.extensions['sni_hostname'] == 'media.example'
        assert 'Cookie' not in request.headers and 'Authorization' not in request.headers
        return httpx.Response(302, headers={'Location': 'https://169.254.169.254/media'})

    mock_http(monkeypatch, adapters, handler)
    with pytest.raises(RuntimeError, match='公网'):
        adapters.transcribe('https://media.example/file', {**MODEL, 'backend': 'api'})


def test_transcription_rejects_blank_api_result_and_removes_media(monkeypatch, adapters):
    files = []

    def audio(media, wav):
        files.extend([media, wav])
        with wave.open(str(wav), 'wb') as output:
            output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            output.writeframes(b'\x01\x00' * 16)

    monkeypatch.setattr(adapters, '_extract_audio', audio)
    monkeypatch.setattr(adapters.socket, 'getaddrinfo', lambda *_a, **_kw: [(2, 1, 6, '', ('8.8.8.8', 443))])

    def handler(request):
        if request.method == 'GET':
            assert 'Authorization' not in request.headers
            return httpx.Response(200, stream=httpx.ByteStream(b'video'))
        assert request.url.path == '/v1/audio/transcriptions'
        assert request.headers['Authorization'] == 'Bearer model-secret'
        assert b'filename="audio.wav"' in request.read()
        return httpx.Response(200, json={'text': '\n  '})

    mock_http(monkeypatch, adapters, handler)
    with pytest.raises(RuntimeError, match='语音'):
        adapters.transcribe('https://media.example/file', {**MODEL, 'backend': 'api'})
    assert files and all(not path.exists() for path in files)


def test_email_formats_are_escaped_and_contain_fourteen_hour_local_time(monkeypatch, adapters):
    deliveries = []

    class SMTP:
        def __init__(self, host, port, timeout):
            assert host == 'mail.example' and port == 25 and timeout > 0

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def send_message(self, message, from_addr, to_addrs):
            assert from_addr == 'sender@example.com' and to_addrs == ['reader@example.com']
            deliveries.append(message)

    monkeypatch.setattr(adapters.smtplib, 'SMTP', SMTP)
    record = {**post(when=1700028000), 'transcript': '<script>原文</script>', 'summary': '要点'}
    settings = {'host': 'mail.example', 'port': 25, 'from_email': 'sender@example.com', 'tls': 'none'}
    adapters.send_email('reader@example.com', record, settings)
    adapters.send_email('reader@example.com', record, settings)
    plain, html = [part.get_content() for part in deliveries[0].iter_parts()]
    for body in [plain, html]:
        positions = [body.index(label) for label in ['更新时间', '博主名称', '总结内容', '内容全文', '原视频标题', '原视频链接']]
        assert positions == sorted(positions)
        assert '2023-11-15 14:00:00' in body
    assert '<script>' in plain and '<script>' not in html and '&lt;script&gt;' in html
    assert deliveries[0]['Message-ID'] == deliveries[1]['Message-ID']
