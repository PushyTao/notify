import base64
import io
import json
from types import SimpleNamespace
import wave

import httpx
import pytest

from app import integrations as adapters


@pytest.fixture
def audio(monkeypatch):
    paths = []
    def prepare(seconds):
        pcm = b'\x01\x00' * (16000 * seconds)
        monkeypatch.setattr(adapters, '_download_media', lambda url, path: path.write_bytes(b'video'))
        def extract(media, wav):
            paths.extend([media, wav])
            with wave.open(str(wav), 'wb') as out:
                out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                out.writeframes(pcm)
        monkeypatch.setattr(adapters, '_extract_audio', extract)
        return pcm, paths
    return prepare


def transport(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(adapters.httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(handler), **kw))


MIMO = {'backend': 'mimo', 'model': 'mimo-v2.5-asr', 'api_key': 'mimo-secret', 'language': 'zh'}


def completion(text='转写结果', reason='stop'):
    return {'choices': [{'message': {'content': text}, 'finish_reason': reason}]}


def test_mimo_splits_audio_without_losing_frames_and_joins_in_order(monkeypatch, audio):
    pcm, paths = audio(125)
    pieces = []
    def handler(req):
        assert str(req.url) == 'https://api.xiaomimimo.com/v1/chat/completions'
        assert req.headers['Authorization'] == 'Bearer mimo-secret'
        body = json.loads(req.content)
        assert body['model'] == 'mimo-v2.5-asr'
        assert body['asr_options'] == {'language': 'zh'}
        assert body['stream'] is False
        assert len(body['messages']) == 1
        message = body['messages'][0]
        assert message['role'] == 'user' and len(message['content']) == 1
        part = message['content'][0]
        assert part['type'] == 'input_audio'
        data = part['input_audio']['data']
        assert data.startswith('data:audio/wav;base64,')
        encoded = data.split(',', 1)[1]
        assert len(encoded) < 10_000_000
        with wave.open(io.BytesIO(base64.b64decode(encoded)), 'rb') as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
            assert wav.getnframes() <= 60 * 16000
            pieces.append(wav.readframes(wav.getnframes()))
        return httpx.Response(200, json=completion(f'第{len(pieces)}段'))
    transport(monkeypatch, handler)
    assert adapters.transcribe('https://media.example/video', MIMO) == '第1段\n第2段\n第3段'
    assert b''.join(pieces) == pcm
    assert paths and all(not p.exists() for p in paths)


@pytest.mark.parametrize('result', [completion('', 'stop'), completion('部分', 'length'),
    completion('部分', 'content_filter'), completion('部分', None), {},
    {'error': {'message': 'mimo-secret'}}, {'choices': [{'message': {'content': ['bad']}, 'finish_reason': 'stop'}]}])
def test_mimo_rejects_incomplete_later_segment_and_cleans_files(monkeypatch, audio, result):
    _, paths = audio(61)
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=completion() if len(calls) == 1 else result)
    transport(monkeypatch, handler)
    with pytest.raises(adapters.IntegrationError) as exc:
        adapters.transcribe('https://media.example/video', MIMO)
    assert len(calls) == 2
    assert 'mimo-secret' not in str(exc.value)
    assert all(not p.exists() for p in paths)


@pytest.mark.parametrize('status', [401, 429, 500])
def test_mimo_http_failure_is_safe(monkeypatch, audio, status):
    audio(1)
    transport(monkeypatch, lambda req: httpx.Response(status, json={'error': {'message': 'mimo-secret'}}))
    with pytest.raises(adapters.IntegrationError, match=f'HTTP {status}') as exc:
        adapters.transcribe('https://media.example/video', MIMO)
    assert 'mimo-secret' not in str(exc.value)


@pytest.mark.parametrize('change', [{'api_key': ''}, {'language': 'ja'}, {'model': 'small'}])
def test_mimo_validates_configuration_before_downloading(monkeypatch, change):
    monkeypatch.setattr(adapters, '_download_media', lambda *a: pytest.fail('Invalid configuration downloaded media'))
    with pytest.raises(adapters.IntegrationError):
        adapters.transcribe('https://media.example/video', {**MIMO, **change})


def test_mimo_custom_url_and_auto_language(monkeypatch, audio):
    audio(1)
    def handler(req):
        assert str(req.url) == 'https://gateway.example/v1/chat/completions'
        assert json.loads(req.content)['asr_options']['language'] == 'auto'
        return httpx.Response(200, json=completion())
    transport(monkeypatch, handler)
    assert adapters.transcribe('https://media.example/video', {**MIMO, 'base_url': 'https://gateway.example/v1', 'language': ''}) == '转写结果'


def test_local_and_generic_asr_still_work(monkeypatch, audio):
    audio(1)
    class Whisper:
        def transcribe(self, path, **kw):
            assert kw == {'language': 'zh', 'vad_filter': True}
            return iter([SimpleNamespace(text='本地全文')]), None
    monkeypatch.setattr(adapters, '_whisper', lambda model, cache: Whisper())
    assert adapters.transcribe('https://media.example/video', {'backend': 'local', 'model': 'small', 'language': 'zh'}) == '本地全文'
    def handler(req):
        assert req.url.path == '/v1/audio/transcriptions'
        assert 'multipart/form-data' in req.headers['Content-Type']
        assert b'filename="audio.wav"' in req.content
        return httpx.Response(200, json={'text': '通用 API 全文'})
    transport(monkeypatch, handler)
    assert adapters.transcribe('https://media.example/video', {'backend': 'api', 'base_url': 'https://api.example/v1', 'model': 'whisper-1'}) == '通用 API 全文'
