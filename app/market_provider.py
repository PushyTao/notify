"""Fixed public quote endpoints and their distinct minute turnover formats."""
import asyncio
import json
import socket
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from threading import BoundedSemaphore, Thread

import httpx

TENCENT_URL = 'https://web.ifzq.gtimg.cn/appstock/app/day/query'
EASTMONEY_URL = 'https://push2.eastmoney.com/api/qt/stock/trends2/get'
TENCENT = '腾讯财经'
EASTMONEY = '东方财富'
THS = '同花顺'
THS_URL = 'https://d.10jqka.com.cn'
INDEX_SCOPE = '腾讯财经指数行情统计口径'
BOARD_SCOPE = '东方财富行业板块行情统计口径'
THS_SCOPE = '同花顺行业板块统计口径，与东方财富行业分类不同'
MAX_RESPONSE_BYTES = 256 * 1024
MAX_HISTORY_BYTES = 4 * 1024 * 1024
REQUEST_SECONDS = 4
DNS_SLOTS = BoundedSemaphore(2)


@dataclass(frozen=True)
class Instrument:
    key: str
    name: str
    code: str
    source: str
    scope: str
    aliases: tuple[str, ...] = ()


ITEMS = (
    Instrument('shanghai', '上证指数', 'sh000001', TENCENT, INDEX_SCOPE),
    Instrument('shenzhen', '深成指', 'sz399001', TENCENT, INDEX_SCOPE, ('深证成指',)),
    Instrument('chinext', '创业板指', 'sz399006', TENCENT, INDEX_SCOPE),
    Instrument('star_composite', '科创综指', 'sh000680', TENCENT, INDEX_SCOPE),
    Instrument('star50', '科创50', 'sh000688', TENCENT, INDEX_SCOPE),
    Instrument('csi300', '沪深300', 'sh000300', TENCENT, INDEX_SCOPE),
    Instrument('csi1000', '中证1000', 'sh000852', TENCENT, INDEX_SCOPE),
    Instrument('securities', '证券板块', '881157', THS, THS_SCOPE, ('证券',)),
    Instrument('banks', '银行板块', '881155', THS, THS_SCOPE, ('银行',)),
    Instrument('semiconductors', '半导体板块', '881121', THS, THS_SCOPE, ('半导体',)),
)
TOTAL_INPUTS = (
    Instrument('shanghai_a', 'A股指数', 'sh000002', TENCENT, '上海 A 股'),
    Instrument('shenzhen_a', '深证A指', 'sz399107', TENCENT, '深圳 A 股'),
)
INSTRUMENTS = {item.code: item for item in (*ITEMS, *TOTAL_INPUTS)}

TRADING_MINUTES = (*range(9*60+30, 11*60+31), *range(13*60+1, 15*60+1))
REOPEN = 13*60
ALLOWED_MINUTES = {*TRADING_MINUTES, REOPEN}


@dataclass(frozen=True)
class Minute:
    price: Decimal
    amount: Decimal  # cumulative yuan for this session, normalized across providers


@dataclass(frozen=True)
class Session:
    day: date
    previous_close: Decimal | None
    minutes: dict[int, Minute]


def _number(value, *, positive=False):
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        raise ValueError('Invalid market number')
    number = Decimal(str(value))
    # Bound magnitudes as well as NaN/Infinity before computing JSON numbers.
    if (not number.is_finite() or number < 0 or number > Decimal('1e18')
            or (number != 0 and number < Decimal('1e-12')) or (positive and number == 0)):
        raise ValueError('Invalid market number')
    return number


def _minute(value):
    if len(value) != 4 or not value.isascii() or not value.isdigit() or int(value[2:]) >= 60:
        raise ValueError('Invalid market minute')
    minute = int(value[:2])*60 + int(value[2:])
    if minute not in ALLOWED_MINUTES:
        raise ValueError('Minute outside market hours')
    return minute


def _date(value, *, compact=False):
    day = date.fromisoformat(value)
    expected = day.strftime('%Y%m%d') if compact else day.isoformat()
    if value != expected or day.weekday() >= 5:
        raise ValueError('Invalid trading date')
    return day


def _session(day, previous_close, rows, *, increments):
    minutes = {}
    amount = Decimal(0)
    if not 0 < len(rows) <= len(ALLOWED_MINUTES):
        raise ValueError('Invalid market session length')
    for minute, price, raw_amount in sorted(rows):
        if minute in minutes:
            raise ValueError('Duplicate market minute')
        value = _number(raw_amount)
        if increments:
            amount += value
        else:
            if value < amount:
                raise ValueError('Decreasing cumulative turnover')
            amount = value
        minutes[minute] = Minute(_number(price, positive=True), amount)
    # The optional reopen marker is not a trading minute.
    marker = minutes.pop(REOPEN, None)
    if marker and 11*60+30 in minutes and marker.amount != minutes[11*60+30].amount:
        raise ValueError('Unexpected turnover at the reopen marker')
    if not minutes:
        raise ValueError('Session has no trading minutes')
    return Session(day, previous_close, minutes)


def _identity(instrument, code, name):
    allowed = {unicodedata.normalize('NFKC', n) for n in (instrument.name, *instrument.aliases)}
    if code != instrument.code or unicodedata.normalize('NFKC', name.strip()) not in allowed:
        raise ValueError('Mismatched market instrument')


def _tencent(instrument, payload):
    if payload['code'] != 0:
        raise ValueError('Tencent quote unavailable')
    code = instrument.code
    node = payload['data'][code]
    identity = node['qt'][code]
    _identity(instrument, code[:2] + identity[2], identity[1])
    days = node['data']
    if not isinstance(days, list) or not 1 <= len(days) <= 5:
        raise ValueError('Invalid Tencent session count')
    sessions = []
    for day in days:
        rows = []
        if not isinstance(day['data'], list) or len(day['data']) > len(ALLOWED_MINUTES):
            raise ValueError('Invalid Tencent minute array')
        for line in day['data']:
            clock, price, _volume, amount = line.split()
            rows.append((_minute(clock), price, amount))
        sessions.append(_session(_date(day['date'], compact=True), _number(day['prec'], positive=True),
                                 rows, increments=False))
    if len({session.day for session in sessions}) != len(sessions):
        raise ValueError('Duplicate trading session')
    return sorted(sessions, key=lambda session: session.day)


def _eastmoney(instrument, payload):
    if payload['rc'] != 0:
        raise ValueError('Eastmoney quote unavailable')
    node = payload['data']
    _identity(instrument, f"{node['market']}.{node['code']}", node['name'])
    rows = node['trends']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2*len(ALLOWED_MINUTES):
        raise ValueError('Invalid Eastmoney minute array')
    by_day = {}
    for line in rows:
        stamp, _opening, price, _high, _low, _volume, amount, _avg = line.split(',')
        day, clock = stamp.split()
        if len(clock) != 5 or clock[2] != ':':
            raise ValueError('Invalid Eastmoney timestamp')
        by_day.setdefault(_date(day), []).append((_minute(clock.replace(':', '')), price, amount))
    if len(by_day) > 2:
        raise ValueError('Invalid Eastmoney session count')
    latest = max(by_day)
    previous_close = _number(node['prePrice'], positive=True) if node.get('prePrice') is not None else None
    return [_session(day, previous_close if day == latest else None, vals, increments=True)
            for day, vals in sorted(by_day.items())]


def _ths(instrument, payload):
    node = payload['time']['bk_' + instrument.code]
    _identity(instrument, instrument.code, node['name'])
    day = _date(node['date'], compact=True)
    previous_close = _number(node['pre'], positive=True)
    rows = []
    for line in node['data'].split(';'):
        clock, price, amount, _average, _volume = line.split(',')
        rows.append((_minute(clock), price, amount))
    current = _session(day, previous_close, rows, increments=True)
    by_day = {}
    history = payload['history']['data']
    if not isinstance(history, str) or history.count(';') >= 100_000:
        raise ValueError('Invalid THS history')
    for line in history.split(';') if history else []:
        fields = line.split(',')
        stamp = fields[0]
        if len(stamp) != 12:
            raise ValueError('Invalid THS history timestamp')
        session_day = _date(stamp[:8], compact=True)
        if session_day < day:
            by_day.setdefault(session_day, []).append((_minute(stamp[8:]), fields[4], fields[6]))
    if not by_day:
        return [current]
    prior = _session(max(by_day), None, by_day[max(by_day)], increments=True)
    # A stale/mismatched historical file must never supply a comparison day.
    if 900 not in prior.minutes or prior.minutes[900].price != previous_close:
        return [current]
    return [prior, current]


def parse_series(instrument, payload):
    try:
        return {TENCENT: _tencent, EASTMONEY: _eastmoney, THS: _ths}[instrument.source](instrument, payload)
    except (KeyError, TypeError, IndexError, AttributeError, InvalidOperation) as exc:
        raise ValueError('Malformed market payload') from exc


async def _resolve(host, port, *, family=0, type=0, proto=0, flags=0):
    # The socket resolver is blocking and cannot be cancelled. Use a bounded
    # set of daemon threads so the request loop can close without joining them.
    if not DNS_SLOTS.acquire(blocking=False):
        raise socket.gaierror(socket.EAI_AGAIN, 'Market DNS slots are busy')
    loop = asyncio.get_running_loop()
    result = loop.create_future()

    def completed(addresses, error):
        if result.done():
            return
        if error is None:
            result.set_result(addresses)
        else:
            result.set_exception(error)

    def resolve():
        try:
            try:
                addresses, error = socket.getaddrinfo(host, port, family, type, proto, flags), None
            except Exception as exc:
                addresses, error = None, exc
            if not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(completed, addresses, error)
                except RuntimeError:
                    if not loop.is_closed():
                        raise
        finally:
            DNS_SLOTS.release()

    try:
        Thread(target=resolve, name='market-dns', daemon=True).start()
    except Exception:
        DNS_SLOTS.release()
        raise
    return await result


def _market_loop():
    loop = asyncio.new_event_loop()
    loop.getaddrinfo = _resolve
    return loop


class PublicMarketProvider:
    """One retry for transport errors; malformed data is rejected without retry."""

    def __init__(self):
        self._ths_history = {}  # at most one previous-day file per fixed board

    def fetch(self, instrument):
        # Bound DNS, headers, and body as a single attempt, in addition to the
        # HTTP I/O timeout. Called only by the dedicated synchronous worker.
        return asyncio.run(self._fetch(instrument), loop_factory=_market_loop)

    async def _fetch(self, instrument):
        if INSTRUMENTS.get(instrument.code) != instrument:
            raise ValueError('Unknown market instrument')
        if instrument.source == TENCENT:
            url, params, headers = TENCENT_URL, {'code': instrument.code}, {}
        elif instrument.source == EASTMONEY:
            url = EASTMONEY_URL
            params = {
                'secid': instrument.code, 'ndays': 2, 'iscr': 0, 'iscca': 0,
                'fields1': ','.join(f'f{i}' for i in range(1, 14)),
                'fields2': ','.join(f'f{i}' for i in range(51, 59)),
                'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
            }
            headers = {'Referer': f'https://quote.eastmoney.com/bk/{instrument.code}.html'}
        for attempt in range(2):
            try:
                if instrument.source == THS:
                    return await self._fetch_ths(instrument)
                async with asyncio.timeout(REQUEST_SECONDS):
                    payload = await self._request(url, params, headers)
                break
            except (TimeoutError, httpx.TransportError):
                if attempt:
                    raise
        return parse_series(instrument, payload)

    async def _fetch_ths(self, instrument):
        deadline = asyncio.get_running_loop().time() + REQUEST_SECONDS
        code = instrument.code
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': f'https://q.10jqka.com.cn/thshy/detail/code/{code}/'}

        async def request(path, limit=MAX_RESPONSE_BYTES):
            callback = 'quotebridge_' + path.removesuffix('.js').replace('/', '_')
            return await self._request(f'{THS_URL}/{path}', {}, headers, callback=callback, limit=limit)

        async with asyncio.timeout_at(deadline):
            current = await request(f'v6/time/bk_{code}/last.js')
        payload = {'time': current, 'history': {'data': ''}}
        sessions = parse_series(instrument, payload)
        day = sessions[-1].day
        cached = self._ths_history.get(code)
        if cached and cached[0] == day:
            payload['history'] = cached[1]
            return parse_series(instrument, payload)
        try:
            async with asyncio.timeout_at(deadline):
                payload['history'] = await request(f'v6/line/bk_{code}/60/{day.year}.js', MAX_HISTORY_BYTES)
                sessions = parse_series(instrument, payload)
                # The first trading day of a year needs the preceding year's file.
                if len(sessions) == 1 and day.month == 1:
                    payload['history'] = await request(f'v6/line/bk_{code}/60/{day.year-1}.js', MAX_HISTORY_BYTES)
                    sessions = parse_series(instrument, payload)
        except (httpx.HTTPError, ValueError, TimeoutError):
            # Current turnover remains useful even when history is unavailable.
            return parse_series(instrument, {'time': current, 'history': {'data': ''}})
        if len(sessions) == 2 and all(m in sessions[0].minutes for m in TRADING_MINUTES):
            prior = sessions[0].day.strftime('%Y%m%d')
            trimmed = {'data': ';'.join(row for row in payload['history']['data'].split(';') if row.startswith(prior))}
            self._ths_history[code] = (day, trimmed)
        return sessions

    @staticmethod
    async def _request(url, params, headers, *, callback=None, limit=MAX_RESPONSE_BYTES):
        async with httpx.AsyncClient(timeout=REQUEST_SECONDS, follow_redirects=False, trust_env=False) as client:
            async with client.stream('GET', url, params=params,
                                     headers={'Accept-Encoding': 'identity', **headers}) as response:
                response.raise_for_status()
                if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    raise ValueError('Unexpected market response encoding')
                body = bytearray()
                async for chunk in response.aiter_raw():
                    if len(body) + len(chunk) > limit:
                        raise ValueError('Market response too large')
                    body.extend(chunk)
        if callback:
            # Validate the exact requested symbol/path; never execute JSONP.
            text = body.decode('utf-8').strip().removesuffix(';').rstrip()
            prefix = callback + '('
            if not text.startswith(prefix) or not text.endswith(')'):
                raise ValueError('Mismatched THS JSONP callback')
            body = text[len(prefix):-1]
        return json.loads(body, parse_float=Decimal)
