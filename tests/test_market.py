import json
import socket
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Store
from app.main import create_app
from app import market
from app import market_provider as provider
from app.worker import Worker


def at(day, minute):
    return datetime.fromisoformat(f'{day}T{minute}').replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()


FRIDAY = '2026-11-13'
MONDAY = '2026-11-16'
THURSDAY = '2026-11-12'


def tt_payload(code, name, days):
    return {'code': 0, 'data': {code: {
        'qt': {code: ['1', name, code[2:]]},
        'data': [{'date': d.replace('-', ''), 'prec': '100', 'data': [
            f'{hm} 101 0 {amount}' for hm, amount in rows
        ]} for d, rows in days.items()]
    }}}


def em_payload(days):
    return {'rc': 0, 'data': {'code': 'BK0473', 'market': 90, 'name': '证券Ⅱ', 'prePrice': '100', 'trends': [
        f'{d} {hm},100,101,102,99,20,{amount},101' for d, rows in days.items() for hm, amount in rows
    ]}}


def calculate(payload, code, now=None):
    instrument = provider.INSTRUMENTS[code]
    return market.calculate_item(instrument, provider.parse_series(instrument, payload),
                                 now or at(MONDAY, '09:31'))


@pytest.mark.parametrize('code,payload', [
    ('90.BK0473', em_payload({FRIDAY: [('09:30', '100000000'), ('09:31', '200000000')],
                              MONDAY: [('09:30', '200000000'), ('09:31', '400000000')]})),
    ('sh000001', tt_payload('sh000001', '上证指数', {
        FRIDAY: [('0930', '100000000'), ('0931', '300000000')],
        MONDAY: [('0930', '200000000'), ('0931', '600000000')],
    })),
])
def test_compare_with_the_same_minute_on_previous_session(code, payload):
    result = calculate(payload, code)
    assert (result['amount_yi'], result['delta_yi'], result['delta_pct']) == (6, 3, 100)
    assert (result['trade_date'], result['as_of'], result['previous_date']) == (MONDAY, '09:31', FRIDAY)
    assert (result['price'], result['change_pct']) == (101, 1)


@pytest.mark.parametrize('previous,current,minute,delta,pct', [
    ([('0930', 100_000_000), ('0931', 300_000_000), ('1500', 900_000_000)],
     [('0930', 100_000_000), ('0931', 300_000_000)], '09:31', 0, 0),
    ([('0930', 0), ('0931', 0)], [('0930', 0), ('0931', 600_000_000)], '09:31', 6, None),
    ([('0930', 100_000_000), ('0932', 400_000_000)], [('0930', 0), ('0931', 1), ('0932', 2)],
     '09:32', None, None),
    ([('0930', 100_000_000), ('0931', 300_000_000)], [('0930', 100_000_000), ('0932', 300_000_000)],
     '09:32', None, None),
    ([('0930', 100_000_000)], [('0930', 0), ('0931', 1)], '09:31', None, None),
    (None, [('0930', 0), ('0931', 1)], '09:31', None, None),
])
def test_comparison_refuses_to_invent_the_baseline(previous, current, minute, delta, pct):
    days = ({FRIDAY: previous} if previous is not None else {}) | {MONDAY: current}
    result = calculate(tt_payload('sh000001', '上证指数', days), 'sh000001', at(MONDAY, minute))
    assert (result['delta_yi'], result['delta_pct']) == (delta, pct)


@pytest.mark.parametrize('rows', [
    [('0930', 'NaN')], [('0930', 'Inf')], [('0930', -1)], [('0930', 1), ('0931', 0)],
    [('0930', 1), ('0930', 2)], [('1200', 0)], [],
])
def test_abnormal_latest_session_is_rejected_instead_of_falling_back(rows):
    payload = tt_payload('sh000001', '上证指数', {FRIDAY: [('0930', 0)], MONDAY: rows})
    with pytest.raises(ValueError):
        calculate(payload, 'sh000001')


@pytest.mark.parametrize('variant', ['wrong exchange', 'wrong code', 'wrong name', 'missing latest'])
def test_payload_identity_and_latest_session_are_validated(variant):
    payload = tt_payload('sh000001', '上证指数', {FRIDAY: [('0930', 0)], MONDAY: [('0930', 1)]})
    node = payload['data']['sh000001']
    if variant == 'wrong exchange':
        payload['data'] = {'sz000001': node}
    elif variant == 'wrong code':
        node['qt']['sh000001'][2] = '000002'
    elif variant == 'wrong name':
        node['qt']['sh000001'][1] = '上证180'
    else:
        del node['data'][-1]['data']
    with pytest.raises(ValueError):
        calculate(payload, 'sh000001')


def test_after_close_rejects_a_truncated_daily_comparison():
    payload = tt_payload('sh000001', '上证指数', {FRIDAY: [('0930', 0)], MONDAY: [('0930', 1)]})
    quote = calculate(payload, 'sh000001', at(MONDAY, '15:01'))
    assert quote['as_of'] == '09:30' and quote['delta_yi'] is None


def test_lunch_rejects_a_comparison_when_the_morning_did_not_reach_1130():
    payload = tt_payload('sh000001', '上证指数', {FRIDAY: [('0930', 0)], MONDAY: [('0930', 1)]})
    quote = calculate(payload, 'sh000001', at(MONDAY, '12:00'))
    assert quote['as_of'] == '09:30' and quote['delta_yi'] is None


def test_tencent_day_change_uses_the_previous_close_of_the_latest_session():
    payload = tt_payload('sh000001', '上证指数', {MONDAY: [('0930', 1)], FRIDAY: [('0930', 0)]})
    latest, previous = payload['data']['sh000001']['data']
    latest['prec'], previous['prec'] = '101', '90'
    assert calculate(payload, 'sh000001')['change_pct'] == 0


def test_nonzero_reopen_marker_is_not_treated_as_turnover():
    payload = tt_payload('sh000001', '上证指数', {MONDAY: [('1130', 4), ('1300', 5)]})
    with pytest.raises(ValueError):
        calculate(payload, 'sh000001', at(MONDAY, '13:00'))


def test_missing_eastmoney_minute_cannot_be_counted_as_zero_turnover():
    payload = em_payload({MONDAY: [('09:30', 100_000_000), ('09:32', 200_000_000)]})
    quote = calculate(payload, '90.BK0473', at(MONDAY, '09:32'))
    assert quote['price'] == 101 and quote['amount_yi'] is None


def test_provider_uses_fixed_eastmoney_endpoint_and_retries_transport_once(monkeypatch):
    responses = iter([httpx.RemoteProtocolError('simulated disconnect'),
                      em_payload({MONDAY: [('09:30', 100_000_000)]})])
    sent = []

    def respond(request):
        sent.append(request)
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return httpx.Response(200, stream=Stream(item))

    class Stream(httpx.AsyncByteStream):
        def __init__(self, data):
            self.data = data

        async def __aiter__(self):
            yield json.dumps(self.data).encode()

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    instrument = provider.INSTRUMENTS['90.BK0473']
    sessions = provider.PublicMarketProvider().fetch(instrument)
    assert sessions[0].minutes[570].amount == 100_000_000
    assert len(sent) == 2
    for request in sent:
        assert str(request.url).split('?')[0] == 'https://push2.eastmoney.com/api/qt/stock/trends2/get'
        assert (request.url.params['secid'], request.url.params['ndays']) == ('90.BK0473', '2')
        assert request.headers['referer'] == 'https://quote.eastmoney.com/bk/90.BK0473.html'


def test_provider_deadline_does_not_wait_for_stuck_dns_or_create_unbounded_threads(monkeypatch):
    release = Event()
    two_completed = Event()
    started_calls = []
    completed_calls = []
    threads = []
    thread_errors = []

    def record_thread(**kwargs):
        thread = Thread(**kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(provider, 'Thread', record_thread)
    monkeypatch.setattr('threading.excepthook', thread_errors.append)

    def blocked_lookup(host, *args, **kwargs):
        first = not started_calls
        started_calls.append(host)
        try:
            release.wait(2)
            if first:
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('127.0.0.1', 443))]
            raise socket.gaierror(socket.EAI_AGAIN, 'test DNS lookup')
        finally:
            completed_calls.append(host)
            if len(completed_calls) == 2:
                two_completed.set()

    monkeypatch.setattr(socket, 'getaddrinfo', blocked_lookup)
    monkeypatch.setattr(provider, 'REQUEST_SECONDS', 0.1)
    source = provider.PublicMarketProvider()
    try:
        started = time.monotonic()
        # Only socket.getaddrinfo is replaced. This uses the actual httpx/AnyIO path.
        with pytest.raises(TimeoutError):
            source.fetch(provider.INSTRUMENTS['sh000001'])
        assert len(started_calls) == 2
        assert not completed_calls

        # The two earlier lookups are still blocked. More fetches must neither
        # wait for one of them nor start and accumulate additional resolver threads.
        for _ in range(3):
            with pytest.raises(httpx.ConnectError):
                source.fetch(provider.INSTRUMENTS['sh000001'])
        assert len(started_calls) == 2
        assert not completed_calls
        assert time.monotonic() - started < 1.5
    finally:
        release.set()
        assert two_completed.wait(2)
        for thread in threads:
            thread.join(2)
    assert len(threads) == 2 and all(thread.daemon and not thread.is_alive() for thread in threads)
    assert not thread_errors

    def recovered_lookup(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, 'DNS capacity recovered')

    monkeypatch.setattr(socket, 'getaddrinfo', recovered_lookup)
    with pytest.raises(httpx.ConnectError, match='DNS capacity recovered'):
        source.fetch(provider.INSTRUMENTS['sh000001'])


class Feed:
    def __init__(self, responses=None):
        self.responses = responses or {}

    def fetch(self, instrument):
        if instrument.code not in self.responses:
            raise httpx.RemoteProtocolError('private request diagnostic')
        responses = self.responses[instrument.code]
        return provider.parse_series(instrument, responses)


def both_markets(sh, sz):
    return {'sh000002': tt_payload('sh000002', 'Ａ股指数', sh),
            'sz399107': tt_payload('sz399107', '深证Ａ指', sz)}


@pytest.mark.parametrize('sz_date,sz_minute', [(THURSDAY, '0931'), (FRIDAY, '0930')])
def test_total_never_combines_different_trade_dates_or_cutoffs(tmp_path, sz_date, sz_minute):
    sh = {FRIDAY: [('0930', 0), ('0931', 100_000_000)]}
    sz = {sz_date: [('0930', 0), ('0931', 200_000_000)] if sz_minute == '0931' else [('0930', 200_000_000)]}
    service = market.MarketService(Store(tmp_path/'db'), Feed(both_markets(sh, sz)))
    assert service.refresh(at(FRIDAY, '09:31'))
    total = service.snapshot(at(FRIDAY, '09:31'))['total']
    assert total['status'] == 'unavailable'
    assert total['amount_yi'] is None


def test_current_total_ready_without_aligned_previous_dates(tmp_path):
    sh = {FRIDAY: [('0930', 100_000_000)], MONDAY: [('0930', 200_000_000)]}
    sz = {THURSDAY: [('0930', 200_000_000)], MONDAY: [('0930', 300_000_000)]}
    service = market.MarketService(Store(tmp_path/'db'), Feed(both_markets(sh, sz)))
    assert service.refresh(at(MONDAY, '09:30'))
    total = service.snapshot(at(MONDAY, '09:30'))['total']
    assert total['amount_yi'] == 5 and total['delta_yi'] is None and total['previous_date'] is None


def test_fresh_series_cannot_regress_to_an_older_minute_or_date(tmp_path):
    response = {'sh000001': tt_payload('sh000001', '上证指数', {
        FRIDAY: [('0930', 0), ('0931', 400_000_000)],
        MONDAY: [('0930', 0), ('0931', 600_000_000)],
    })}
    feed = Feed(response)
    service = market.MarketService(Store(tmp_path/'db'), feed)
    assert service.refresh(at(MONDAY, '09:31'))
    for now, days in [(at(MONDAY, '09:32'), {MONDAY: [('0930', 0)]}),
                      (at(MONDAY, '09:33'), {FRIDAY: [('0930', 0), ('0931', 400_000_000)]})]:
        feed.responses['sh000001'] = tt_payload('sh000001', '上证指数', days)
        assert service.refresh(now)
        item = service.snapshot(now)['items'][0]
        assert (item['trade_date'], item['as_of'], item['amount_yi'], item['stale']) == (MONDAY, '09:31', 6, True)
        assert item['fetched_at'] == at(MONDAY, '09:31')


def test_cache_migration_and_access_from_separate_web_store(tmp_path, monkeypatch):
    path = tmp_path/'existing.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE runtime (key TEXT PRIMARY KEY, value REAL NOT NULL)')
        db.execute("INSERT INTO runtime VALUES ('worker_heartbeat', 123)")
    store = Store(path)
    assert store.one('SELECT value FROM runtime')['value'] == 123
    app = create_app(db_path=path, secret_key='s'*40, admin_password='strong-password-123')
    client = TestClient(app)
    response = client.get('/api/market')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['status'] == 'unavailable'
    assert [x['name'] for x in response.json()['items']] == [
        '上证指数', '深成指', '创业板指', '科创综指', '科创50', '沪深300', '中证1000', '证券板块', '银行板块',
    ]
    feed = Feed({'sh000001': tt_payload('sh000001', '上证指数', {FRIDAY: [('0930', 100_000_000)]})})
    service = market.MarketService(store, feed)
    assert service.refresh(at(FRIDAY, '09:30'))
    feed.responses.clear()
    assert service.refresh(at(FRIDAY, '09:31'))
    attempts = store.all('SELECT * FROM market_refresh')

    def forbidden(*args, **kwargs):
        pytest.fail('API must not fetch from the provider')

    monkeypatch.setattr(provider.PublicMarketProvider, 'fetch', forbidden)
    data = client.get('/api/market').json()
    assert data['status'] == 'partial' and data['stale']
    assert data['items'][0]['amount_yi'] == 1 and data['items'][0]['stale']
    assert data['items'][0]['fetched_at'] == at(FRIDAY, '09:30')
    assert data['items'][7]['status'] == 'unavailable' and data['items'][7]['price'] is None
    assert 'private request diagnostic' not in json.dumps(data)
    assert store.all('SELECT * FROM market_refresh') == attempts


def test_market_lease_fences_an_expired_owner_and_replacement(tmp_path):
    store = Store(tmp_path/'db')
    assert hasattr(store, 'claim_market'), 'market refresh needs its own lease'
    assert store.claim_market('old', now=100, lease_seconds=10)
    assert not store.claim_market('duplicate', now=109, lease_seconds=10)
    assert not store.finish_market('old', {'probe': {'price': 111}}, now=110, next_run=200)
    assert store.claim_market('replacement', now=110, lease_seconds=10)
    assert not store.finish_market('old', {'probe': {'price': 111}}, now=111, next_run=200)
    assert store.finish_market('replacement', {'probe': {'price': 222}}, now=111, next_run=200)
    assert not store.claim_market('premature', now=199, lease_seconds=10)
    assert json.loads(store.market_rows()[0]['payload']) == {'price': 222}


def test_lease_is_checked_against_current_clock_before_commit(tmp_path, monkeypatch):
    store = Store(tmp_path/'db')
    reads = []

    def now():
        reads.append(1)
        return 111

    monkeypatch.setattr('app.db.time.time', now)
    assert store.claim_market('expired', now=100, lease_seconds=10)
    assert not store.finish_market('expired', {'probe': {'price': 222}}, now=None, next_run=200)
    assert reads
    assert not store.market_rows()


def test_reopen_marker_does_not_create_a_large_publish_lag_at_1301(tmp_path):
    rows = [(f'{minute//60:02d}{minute%60:02d}', 0) for minute in range(570, 691)]
    feed = Feed({'sh000001': tt_payload('sh000001', '上证指数', {FRIDAY: rows, MONDAY: rows})})
    service = market.MarketService(Store(tmp_path/'db'), feed)
    assert service.refresh(at(MONDAY, '13:00'))
    assert not service.snapshot(at(MONDAY, '13:01'))['items'][0]['stale']


def test_worker_market_wait_does_not_block_author_lane_or_open_a_db_transaction(tmp_path):
    store = Store(tmp_path/'db')
    fetching, release, polled = Event(), Event(), Event()

    class SlowSource:
        def fetch(self, instrument):
            fetching.set()
            assert release.wait(3)
            raise httpx.RemoteProtocolError('provider not reachable')

    class Integrations:
        @staticmethod
        def fetch_posts(author, settings):
            polled.set()
            return []

    with store.connect() as db:
        db.execute("INSERT INTO authors(name,source_id,created_at) VALUES('a','a',1)")
    worker = Worker(store, Settings(store, 'x'*40), Integrations, market=market.MarketService(store, SlowSource()))
    thread = Thread(target=worker.run, daemon=True)
    thread.start()
    try:
        assert fetching.wait(2)
        assert polled.wait(2)
    finally:
        worker.stop.set()
        release.set()
        thread.join(4)
    assert not thread.is_alive()


@pytest.mark.parametrize('date,clock,seconds', [
    (MONDAY, '09:45', 60), (MONDAY, '13:10', 60), (MONDAY, '12:00', 900),
    (MONDAY, '15:01', 900), ('2026-11-14', '10:00', 900), (MONDAY, '09:25', 300),
])
def test_refresh_cadence_respects_sessions_and_restarts_at_the_open(date, clock, seconds):
    assert market.refresh_interval(at(date, clock)) == seconds


@pytest.mark.parametrize('hm,clock,amount,delta,pct', [
    ('10:00', '10:00', 5421.8929337881, -533.1558432360, -8.953005478192447),
    ('11:30', '12:00', 9792.1953516214, -2419.6233218490, -19.813783569400007),
    ('15:00', '15:00', 16553.3990070518, -2762.9241170495, -14.303571644036920),
])
def test_real_tencent_replay_against_independent_decimal_totals(tmp_path, hm, clock, amount, delta, pct):
    source = Path(__file__).parent/'fixtures'/'market'
    responses = {
        code: json.loads((source/f'{code}.json').read_bytes()) for code in ('sh000002', 'sz399107')
    }
    service = market.MarketService(Store(tmp_path/'db'), Feed(responses))
    assert service.refresh(at(FRIDAY, clock))
    saturday = at('2026-11-14', '10:00')
    snapshot = service.snapshot(saturday if hm == '15:00' else at(FRIDAY, clock))
    assert snapshot['status'] == 'partial'
    assert [item['status'] for item in snapshot['items']] == ['unavailable']*9
    result = snapshot['total']
    assert (result['trade_date'], result['previous_date'], result['as_of']) == (FRIDAY, THURSDAY, hm)
    assert result['amount_yi'] == pytest.approx(amount, abs=1e-10, rel=0)
    assert result['delta_yi'] == pytest.approx(delta, abs=1e-10, rel=0)
    assert result['delta_pct'] == pytest.approx(pct, abs=1e-10, rel=0)
    if hm == '15:00':
        assert '休市' in snapshot['message']
