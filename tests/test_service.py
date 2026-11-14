import pytest

from app.db import Store
from app.config import Settings
from app.integrations import IntegrationError
from app.security import hash_password, safe_error, verify_password
from app.service import Service


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / 'test.db')
    settings = Settings(store, 'a' * 40)
    return store, settings, Service(store, settings)


def author(store, backfill=0):
    with store.connect() as db:
        row = db.execute('INSERT INTO authors(name,source_id,backfill,created_at) VALUES(?,?,?,?) RETURNING *',
                         ('测试博主', 'user-1', backfill, 100)).fetchone()
    return dict(row)


def post(n, published_at):
    return dict(video_id=str(n), author_name='博主', published_at=published_at,
                title=f'标题{n}', url=f'https://www.douyin.com/video/{n}', media_url='https://media.example/video.mp4')


def test_initial_baseline_then_new_video_only_and_dedup(setup):
    store, settings, service = setup
    a = author(store)
    service.poll_author(a, lambda a, s: [post(1, 90)], now=200)
    a = store.one('SELECT * FROM authors WHERE id=?', (a['id'],))
    assert a['initialized'] == 1
    service.poll_author(a, lambda a, s: [post(2, 210), post(1, 90)], now=220)
    service.poll_author(a, lambda a, s: [post(2, 210)], now=230)
    assert [(v['video_id'], v['status']) for v in store.all('SELECT * FROM videos')] == [('2', 'pending')]


def test_backfill_selects_newest_and_failed_poll_does_not_initialize(setup):
    store, _, service = setup
    a = author(store, 1)
    def fail(a, s):
        raise RuntimeError('unavailable')
    with pytest.raises(RuntimeError):
        service.poll_author(a, fail, now=200)
    assert store.one('SELECT initialized FROM authors')['initialized'] == 0
    service.poll_author(a, lambda a, s: [post(1, 90), post(2, 99)], now=200)
    assert store.one('SELECT * FROM videos')['video_id'] == '2'


def test_settings_secrets_encrypted_and_blank_preserves(setup):
    store, settings, _ = setup
    settings.save('smtp', {'password': 'mail-secret', 'host': 'mail.example'})
    settings.save('smtp', {'password': '', 'port': 465})
    assert settings.get('smtp')['password'] == 'mail-secret'
    assert 'mail-secret' not in store.one('SELECT value FROM settings')['value']
    assert settings.public()['smtp']['password_set'] is True
    assert 'password' not in settings.public()['smtp']


def test_password_hash_and_invalid_password():
    hashed = hash_password('a secure password')
    assert verify_password('a secure password', hashed)
    assert not verify_password('wrong', hashed)
    assert not verify_password('wrong', 'invalid')


def test_claim_exclusive_expired_lease_recovered(setup):
    store, _, service = setup
    a = author(store, 1)
    service.poll_author(a, lambda a, s: [post(1, 90)], now=200)
    claimed = store.claim('videos', 'worker-a', now=300, lease_seconds=10)
    assert claimed['attempts'] == 1
    assert store.claim('videos', 'worker-b', now=305) is None
    recovered = store.claim('videos', 'worker-b', now=311)
    assert recovered['id'] == claimed['id']
    assert recovered['lease_owner'] == 'worker-b'


def test_process_and_independent_recipient_delivery(setup):
    store, settings, service = setup
    a = author(store, 1)
    with store.connect() as db:
        db.execute("INSERT INTO subscribers(email) VALUES('one@example.com'),('two@example.com')")
        db.execute('INSERT INTO models(name,base_url,model_id,api_key,enabled) VALUES(?,?,?,?,1)',
                   ('Model', 'https://ai.example', 'model', settings.encrypt('key')))
    service.poll_author(a, lambda a, s: [post(1, 90)], now=200)
    video = store.claim('videos', 'w', now=300)
    class Integrations:
        @staticmethod
        def transcribe(url, config):
            return '真实转写全文'
        @staticmethod
        def summarize(text, model, prompt):
            assert text == '真实转写全文'
            assert model['api_key'] == 'key'
            return '总结'
    service.process_video(video, Integrations)
    assert store.one('SELECT status FROM videos')['status'] == 'complete'
    assert len(store.all('SELECT * FROM deliveries')) == 2
    sent = []
    delivery = store.claim('deliveries', 'w')
    service.deliver(delivery, lambda email, record, config: sent.append((email, record['transcript'])))
    assert sent == [('one@example.com', '真实转写全文')]
    other = store.claim('deliveries', 'w')
    assert other['email'] == 'two@example.com'
    assert store.one("SELECT COUNT(*) n FROM deliveries WHERE status='sent'")['n'] == 1


def test_blank_transcript_never_published(setup):
    store, _, service = setup
    a = author(store, 1)
    service.poll_author(a, lambda a, s: [post(1, 90)], now=200)
    class EmptyASR:
        @staticmethod
        def transcribe(url, config):
            return ' '
    with pytest.raises(ValueError):
        service.process_video(store.claim('videos', 'w', now=300), EmptyASR)
    assert store.one('SELECT status FROM videos')['status'] != 'complete'


def test_sanitized_integration_diagnostic_is_preserved():
    diagnostic = 'DTK 需要登录 Cookie，请重新导入'
    assert safe_error(IntegrationError(diagnostic)) == diagnostic
    for other_error in (RuntimeError, ValueError):
        assert 'secret-token' not in safe_error(other_error('Request failed with secret-token'))


@pytest.mark.parametrize('status', ['pending', 'retry', 'failed', 'processing', 'complete'])
def test_poll_refreshes_existing_backfill_media_only_for_unleased_unfinished_videos(setup, status):
    store, _, service = setup
    a = author(store, 1)
    service.poll_author(a, lambda a, s: [post(1, 90)], now=200)
    with store.connect() as db:
        db.execute("UPDATE videos SET status=?,transcript='保存的转写',summary='保存的总结'", (status,))
    original_url = post(1, 90)['media_url']
    new_url = 'https://media.example/fresh.mp4'
    service.poll_author(a, lambda a, s: [dict(post(1, 90), media_url=new_url), post(2, 100)], now=210)
    assert store.all('SELECT video_id,media_url,status,transcript,summary FROM videos') == [{
        'video_id': '1',
        'media_url': new_url if status in ('pending', 'retry', 'failed') else original_url,
        'status': status,
        'transcript': '保存的转写',
        'summary': '保存的总结',
    }]


@pytest.mark.parametrize('cached_transcript', ['', '保存的转写'])
def test_video_retry_refreshes_before_transcribing_and_reuses_cached_text(setup, cached_transcript):
    store, settings, service = setup
    a = author(store, 1)
    service.poll_author(a, lambda a, s: [post(1, 90)], now=200)
    with store.connect() as db:
        db.execute("UPDATE videos SET transcript=?,attempts=1,status='retry'", (cached_transcript,))
        db.execute("INSERT INTO models(name,base_url,model_id,api_key,enabled) VALUES('M','https://ai.example','m','',1)")
    video = store.claim('videos', 'w', now=300)
    calls = []
    fresh_url = 'https://media.example/fresh.mp4'

    class Integrations:
        @staticmethod
        def refresh_media(video, source_author, collector_settings):
            assert video['video_id'] == '1'
            assert source_author['id'] == a['id']
            assert collector_settings == settings.get('collector')
            calls.append('refresh')
            return fresh_url

        @staticmethod
        def transcribe(url, config):
            calls.append(('transcribe', url, store.one('SELECT media_url FROM videos')['media_url']))
            return '新的转写'

        @staticmethod
        def summarize(text, model, prompt):
            calls.append(('summarize', text))
            return '总结'

    service.process_video(video, Integrations)
    if cached_transcript:
        assert calls == [('summarize', '保存的转写')]
    else:
        assert calls == ['refresh', ('transcribe', fresh_url, fresh_url), ('summarize', '新的转写')]


@pytest.fixture
def pending_delivery(setup):
    store, _, service = setup
    a = author(store, 1)
    service.poll_author(a, lambda a, s: [post(1, 90)], now=200)
    with store.connect() as db:
        db.execute("UPDATE videos SET status='complete'")
        subscriber = db.execute("INSERT INTO subscribers(email) VALUES('old@example.com') RETURNING id").fetchone()
        db.execute("INSERT INTO deliveries(video_id,subscriber_id,email) SELECT id,?,'old@example.com' FROM videos",
                   (subscriber['id'],))
    return store, service, store.claim('deliveries', 'w')


def test_subscriber_excluding_author_cancels_claimed_delivery(pending_delivery):
    store, service, delivery = pending_delivery
    with store.connect() as db:
        db.execute("UPDATE subscribers SET author_ids='[999]' WHERE id=?", (delivery['subscriber_id'],))
    sent = []
    service.deliver(delivery, lambda email, *_: sent.append(email))
    assert sent == []
    state = store.one('SELECT status,error,lease_owner FROM deliveries')
    assert state['status'] == 'cancelled'
    assert state['error']
    assert state['lease_owner'] is None


def test_disabled_subscriber_leaves_claimed_delivery_pending(pending_delivery):
    store, service, delivery = pending_delivery
    with store.connect() as db:
        db.execute('UPDATE subscribers SET enabled=0 WHERE id=?', (delivery['subscriber_id'],))
    sent = []
    service.deliver(delivery, lambda email, *_: sent.append(email))
    assert sent == []
    assert store.one('SELECT status,lease_owner FROM deliveries') == {'status': 'pending', 'lease_owner': None}


def test_delivery_uses_current_subscriber_address(pending_delivery):
    store, service, delivery = pending_delivery
    with store.connect() as db:
        db.execute("UPDATE subscribers SET email='new@example.com' WHERE id=?", (delivery['subscriber_id'],))
    sent = []
    service.deliver(delivery, lambda email, *_: sent.append(email))
    assert sent == ['new@example.com']
    assert store.one('SELECT status,email FROM deliveries') == {'status': 'sent', 'email': 'new@example.com'}
