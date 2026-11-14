import asyncio
import time
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import httpx

from app import market, market_provider as provider
from app.db import Store


def fixture(code='881157'):
    return json.loads((Path(__file__).parent/'fixtures'/'ths'/f'{code}.json').read_text(encoding='utf-8'))


def at(clock):
    return datetime.fromisoformat('2026-11-13T'+clock).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()


@pytest.mark.parametrize('code', ['881157', '881155', '881121'])
@pytest.mark.parametrize('clock', ['10:00', '11:30', '15:00'])
def test_real_ths_minutes_match_independent_same_minute_sum(code, clock):
    payload = fixture(code)
    instrument = provider.INSTRUMENTS[code]
    result = market.calculate_item(instrument, provider.parse_series(instrument, payload), at(clock))
    cutoff = clock.replace(':', '')
    current = sum(Decimal(r.split(',')[2]) for r in payload['time']['bk_'+code]['data'].split(';')
                  if r[:4] <= cutoff)
    previous = sum(Decimal(r.split(',')[6]) for r in payload['history']['data'].split(';')
                   if r[:8] == '20261112' and r[8:12] <= cutoff)
    assert result['amount_yi'] == float(current/100_000_000)
    assert result['delta_yi'] == float((current-previous)/100_000_000)
    assert result['previous_date'] == '2026-11-12'
    assert result['source'] == '同花顺'
    assert result['change_pct'] is not None


@pytest.mark.parametrize('variant', ['name', 'date', 'duplicate', 'negative', 'missing'])
def test_ths_rejects_bad_identity_and_incomplete_current(variant):
    payload = fixture()
    node = payload['time']['bk_881157']
    if variant == 'name': node['name'] = '银行'
    if variant == 'date': node['date'] = '20261114'
    if variant == 'duplicate': node['data'] += ';'+node['data'].split(';')[0]
    if variant == 'negative': node['data'] = node['data'].replace('52441953', '-1', 1)
    if variant == 'missing': node['data'] = ';'.join(node['data'].split(';')[1:])
    instrument = provider.INSTRUMENTS['881157']
    if variant == 'missing':
        result = market.calculate_item(instrument, provider.parse_series(instrument, payload), at('15:00'))
        assert result['amount_yi'] is None and result['delta_yi'] is None
    else:
        with pytest.raises(ValueError): provider.parse_series(instrument, payload)


def test_missing_history_keeps_current_quote_without_invented_baseline():
    payload = fixture()
    payload['history'] = {'data': ''}
    instrument = provider.INSTRUMENTS['881157']
    result = market.calculate_item(instrument, provider.parse_series(instrument, payload), at('15:00'))
    assert result['amount_yi'] == 144.05147003
    assert result['delta_yi'] is None


def test_source_switch_does_not_display_cached_eastmoney_quote(tmp_path):
    store = Store(tmp_path/'db')
    old = market._empty(provider.ITEMS[7]) | {'code':'90.BK0473', 'source':'东方财富', 'status':'ready',
        'price':100, 'amount_yi':1, 'trade_date':'2026-11-13', 'as_of':'15:00'}
    assert store.claim_market('old', at('15:00'), 120)
    assert store.finish_market('old', {'securities':old}, now=at('15:00'), next_run=at('15:00')+1)
    item = market.MarketService(store).snapshot(at('15:00'))['items'][7]
    assert item['source'] == '同花顺' and item['status'] == 'unavailable'
    assert item['amount_yi'] is None


def mock_ths(monkeypatch, response):
    original = httpx.AsyncClient
    class Stream(httpx.AsyncByteStream):
        def __init__(self, body): self.body = body
        async def __aiter__(self): yield self.body
    def respond(request):
        status, body = response(request)
        return httpx.Response(status, stream=Stream(body))
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))


def jsonp(request, payload, wrong=False):
    path = request.url.path.strip('/').removesuffix('.js')
    callback = 'wrong' if wrong else 'quotebridge_' + path.replace('/', '_')
    return (callback + '(' + json.dumps(payload) + ');').encode()


def test_provider_fetches_history_once_per_trade_day_and_retries_transport(monkeypatch):
    sent = []
    payload = fixture()
    def response(request):
        sent.append(request)
        if len(sent) == 1: raise httpx.RemoteProtocolError('disconnect')
        kind = 'time' if '/time/' in request.url.path else 'history'
        return 200, jsonp(request, payload[kind])
    mock_ths(monkeypatch, response)
    source = provider.PublicMarketProvider()
    instrument = provider.INSTRUMENTS['881157']
    for _ in range(2):
        sessions = source.fetch(instrument)
        assert sessions[0].day.isoformat() == '2026-11-12'
        assert sessions[-1].minutes[900].amount == Decimal('14405147003')
    assert len(sent) == 4
    assert sum('/line/' in r.url.path for r in sent) == 1
    assert all(r.url.host == 'd.10jqka.com.cn' for r in sent)


@pytest.mark.parametrize('failure', ['unavailable', 'bad callback', 'too large'])
def test_history_failure_preserves_quote_and_retries_next_refresh(monkeypatch, failure):
    payload = fixture()
    calls = []
    def response(request):
        if '/time/' in request.url.path: return 200, jsonp(request, payload['time'])
        calls.append(request)
        if failure == 'unavailable': return 503, b''
        if failure == 'bad callback': return 200, jsonp(request, payload['history'], wrong=True)
        return 200, b'x'*(provider.MAX_HISTORY_BYTES+1)
    mock_ths(monkeypatch, response)
    source = provider.PublicMarketProvider()
    for _ in range(2):
        sessions = source.fetch(provider.INSTRUMENTS['881157'])
        assert len(sessions) == 1 and sessions[0].minutes[900].amount == 14405147003
    assert len(calls) == 2


def test_current_callback_cannot_impersonate_another_board(monkeypatch):
    mock_ths(monkeypatch, lambda request: (200, jsonp(request, fixture()['time'], wrong=True)))
    with pytest.raises(ValueError, match='callback'):
        provider.PublicMarketProvider().fetch(provider.INSTRUMENTS['881157'])


def test_first_trading_day_fetches_previous_year_history(monkeypatch):
    payload = fixture()
    payload['time']['bk_881157']['date'] = '20260105'
    prior = ';'.join(row.replace('20261112', '20251231', 1)
                     for row in payload['history']['data'].split(';') if row.startswith('20261112'))
    sent = []
    def response(request):
        sent.append(request.url.path)
        data = payload['time'] if '/time/' in request.url.path else {
            'data': prior if request.url.path.endswith('2025.js') else ''}
        return 200, jsonp(request, data)
    mock_ths(monkeypatch, response)
    sessions = provider.PublicMarketProvider().fetch(provider.INSTRUMENTS['881157'])
    assert [s.day.isoformat() for s in sessions] == ['2025-12-31', '2026-01-05']
    assert len(sent) == 3 and sent[-1].endswith('2025.js')


def test_source_switch_can_replace_a_newer_old_source_snapshot(tmp_path):
    store = Store(tmp_path/'db')
    stamp = at('15:00')
    old = market._empty(provider.ITEMS[7]) | {'code':'90.BK0473', 'source':'东方财富',
        'status':'ready', 'trade_date':'2026-11-13', 'as_of':'15:00'}
    assert store.claim_market('old', stamp, 120)
    assert store.finish_market('old', {'securities':old}, now=stamp, next_run=stamp+1)
    new = market.calculate_item(provider.ITEMS[7], provider.parse_series(provider.ITEMS[7], fixture()), at('11:30'))
    assert store.claim_market('new', stamp+1, 120)
    assert store.finish_market('new', {'securities':new}, now=stamp+1, next_run=stamp+2)
    item = market.MarketService(store).snapshot(stamp+1)['items'][7]
    assert item['source'] == '同花顺' and item['status'] == 'ready'


def test_slow_history_keeps_current_within_total_attempt_deadline(monkeypatch):
    payload = fixture()
    calls = []
    async def request(url, *args, **kwargs):
        calls.append(url)
        if '/time/' in url: return payload['time']
        await asyncio.sleep(5)
        return payload['history']
    monkeypatch.setattr(provider, 'REQUEST_SECONDS', 0.05)
    monkeypatch.setattr(provider.PublicMarketProvider, '_request', staticmethod(request))
    started = time.monotonic()
    sessions = provider.PublicMarketProvider().fetch(provider.INSTRUMENTS['881157'])
    assert len(sessions) == 1 and sessions[0].minutes[900].amount == 14405147003
    assert time.monotonic()-started < 0.3
    assert len(calls) == 2


def test_history_cache_rolls_on_new_trading_day(monkeypatch):
    payload = fixture()
    history_calls = []
    def response(request):
        kind = 'time' if '/time/' in request.url.path else 'history'
        if kind == 'history': history_calls.append(request)
        return 200, jsonp(request, payload[kind])
    mock_ths(monkeypatch, response)
    source = provider.PublicMarketProvider()
    source.fetch(provider.INSTRUMENTS['881157'])
    payload['time']['bk_881157']['date'] = '20261116'
    payload['time']['bk_881157']['pre'] = '1315.895'
    sessions = source.fetch(provider.INSTRUMENTS['881157'])
    assert [s.day.isoformat() for s in sessions] == ['2026-11-13', '2026-11-16']
    assert len(history_calls) == 2
