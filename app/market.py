"""Calculate and persist provider quotes; serve a read-only cache view."""
import json
import logging
import time
import uuid
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.market_provider import (
    EASTMONEY, THS, ITEMS, TENCENT, TENCENT_URL, TOTAL_INPUTS, TRADING_MINUTES, Instrument,
    PublicMarketProvider,
)

logger = logging.getLogger('notify.market')
SHANGHAI = ZoneInfo('Asia/Shanghai')
YI = Decimal(100_000_000)
LEASE_SECONDS = 120  # twelve fixed instruments, at most two four-second attempts each
OPEN, LUNCH, AFTERNOON, CLOSE = 570, 690, 780, 900
TOTAL = Instrument('total', '沪深成交额', 'sh000002+sz399107', TENCENT,
                   '腾讯财经沪深 A 股成交额，不含 B 股和北交所')


def _clock(now):
    local = datetime.fromtimestamp(now, SHANGHAI)
    return local, local.hour*60 + local.minute


def _open(local, minute):
    return local.weekday() < 5 and (OPEN <= minute <= LUNCH or AFTERNOON <= minute <= CLOSE)


def refresh_interval(now):
    local, minute = _clock(now)
    if _open(local, minute):
        return 60
    if local.weekday() >= 5:
        return 900
    second = local.second + local.microsecond/1_000_000
    next_open = OPEN if minute < OPEN else AFTERNOON if minute < AFTERNOON else None
    return min(900, (next_open - minute)*60-second) if next_open else 900


def _format_minute(minute):
    return f'{minute//60:02d}:{minute%60:02d}'


def _empty(instrument):
    return {
        'key': instrument.key, 'name': instrument.name, 'code': instrument.code,
        'price': None, 'change_pct': None, 'amount_yi': None, 'delta_yi': None, 'delta_pct': None,
        'trade_date': None, 'as_of': None, 'previous_date': None, 'points': [],
        'status': 'unavailable', 'message': '等待行情更新', 'source': instrument.source,
        'scope': instrument.scope, 'fetched_at': None, 'stale': False,
    }


def _window(sessions, now):
    current = sessions[-1]
    previous = sessions[-2] if len(sessions) >= 2 else None
    local, minute = _clock(now)
    if current.day > local.date():
        raise ValueError('Trading session is in the future')
    today = current.day == local.date()
    limit = minute if today else CLOSE
    cutoff = max((ts for ts in current.minutes if ts <= limit), default=None)
    if cutoff is None:
        raise ValueError('Trading session has no usable minutes')
    required_cutoff = (CLOSE if not today or minute >= CLOSE else
                       LUNCH if LUNCH <= minute <= AFTERNOON else None)
    return current, previous, cutoff, required_cutoff


def _complete(session, cutoff):
    return session is not None and all(minute in session.minutes
                                       for minute in TRADING_MINUTES if minute <= cutoff)


def _comparison(window):
    current, previous, cutoff, required_cutoff = window
    # Afternoon reopening has no new trading minute at 13:00. After close,
    # never compare a partial file as if it were the full daily turnover.
    if ((required_cutoff is None or cutoff == required_cutoff) and _complete(current, cutoff)
            and _complete(previous, cutoff)):
        return previous.minutes[cutoff].amount
    return None


def _amount_fields(amount, baseline):
    if baseline is None:
        return {'amount_yi': float(amount / YI), 'delta_yi': None, 'delta_pct': None,
                'message': '分钟历史缺失，暂无可比数据'}
    return {'amount_yi': float(amount / YI), 'delta_yi': float((amount - baseline) / YI),
            'delta_pct': float((amount - baseline) / baseline * 100) if baseline else None,
            'message': '' if baseline else '基准成交额为零，百分比不可用'}


def calculate_item(instrument, sessions, now):
    window = _window(sessions, now)
    current, previous, cutoff, _ = window
    point = current.minutes[cutoff]
    amount_fields = (
        {'amount_yi': None, 'delta_yi': None, 'delta_pct': None, 'message': '当日分钟缺失，累计成交额不可用'}
        if instrument.source in (EASTMONEY, THS) and not _complete(current, cutoff)
        else _amount_fields(point.amount, _comparison(window))
    )
    return {
        **_empty(instrument), **amount_fields,
        'price': float(point.price),
        'change_pct': (float((point.price - current.previous_close) / current.previous_close * 100)
                       if current.previous_close else None),
        'trade_date': current.day.isoformat(), 'as_of': _format_minute(cutoff),
        'previous_date': previous.day.isoformat() if previous else None,
        'points': [{'time': _format_minute(ts), 'price': float(bar.price)}
                   for ts, bar in sorted(current.minutes.items()) if ts <= cutoff],
        'status': 'ready',
    }


def _calculate_total(windows):
    first, second = windows
    left, left_prior, cutoff, required = first
    right, right_prior, right_cutoff, right_required = second
    if (left.day != right.day or cutoff != right_cutoff
            or any(limit is not None and cutoff != limit for limit in (required, right_required))
            or not (_complete(left, cutoff) and _complete(right, cutoff))):
        return None
    baseline = None
    common_previous = left_prior and right_prior and left_prior.day == right_prior.day
    if common_previous:
        left_amount, right_amount = map(_comparison, windows)
        if left_amount is not None and right_amount is not None:
            baseline = left_amount + right_amount
    return {
        **_empty(TOTAL), **_amount_fields(left.minutes[cutoff].amount + right.minutes[cutoff].amount, baseline),
        'trade_date': left.day.isoformat(), 'as_of': _format_minute(cutoff),
        'previous_date': left_prior.day.isoformat() if common_previous else None, 'status': 'ready',
    }


def _age_message(item, now):
    local, minute = _clock(now)
    today = local.date().isoformat()
    traded_today = item['trade_date'] == today
    if local.weekday() >= 5:
        market_message = '周末休市，显示最近交易日'
    elif minute < OPEN:
        market_message = '尚未开盘，显示最近交易日'
    elif not traded_today:
        market_message = '今日无行情，可能休市或数据源延迟'
    elif LUNCH < minute <= AFTERNOON:
        market_message = '午间休市'
    elif minute >= CLOSE:
        market_message = '已收盘'
    else:
        market_message = '盘中行情'
    active_or_closed = local.weekday() < 5 and minute >= OPEN
    # Absence of a same-day quote after opening could also mean a holiday;
    # without an exchange calendar mark it uncertain, never claim it is live.
    incomplete = active_or_closed and not traded_today
    if traded_today and active_or_closed:
        expected = LUNCH if LUNCH < minute <= AFTERNOON else min(minute, CLOSE)
        cutoff = item['as_of']
        actual = int(cutoff[:2])*60 + int(cutoff[3:])
        lag = sum(actual < ts <= expected for ts in TRADING_MINUTES)
        incomplete = lag > 2 or (minute > CLOSE+2 and actual != CLOSE)
    # A complete closing quote remains meaningful during breaks, but a
    # stopped worker should still be distinguishable from a live refresh.
    lagged = now - item['fetched_at'] > (180 if _open(local, minute) else 1800)
    if incomplete:
        market_message += '，行情尚未更新到预期分钟'
    if lagged:
        market_message += '，缓存较旧'
    return incomplete or lagged, market_message


class MarketService:
    def __init__(self, store, provider=None):
        self.store = store
        self.provider = PublicMarketProvider() if provider is None else provider

    def refresh(self, now=None, *, stopped=None):
        started = time.time() if now is None else now
        owner = uuid.uuid4().hex
        if not self.store.claim_market(owner, started, LEASE_SECONDS):
            return False

        def fetch(instrument):
            try:
                return self.provider.fetch(instrument)
            except Exception as exc:
                logger.debug('Market fetch failed for %s: %s', instrument.code, type(exc).__name__)
                return None

        results = {}
        total_inputs = []
        for instrument in (*ITEMS, *TOTAL_INPUTS):
            if stopped is not None and stopped():
                self.store.release_market(owner)
                return False
            sessions = fetch(instrument)
            observed = time.time() if now is None else now
            try:
                if instrument in TOTAL_INPUTS:
                    total_inputs.append(_window(sessions, observed) if sessions else None)
                else:
                    results[instrument.key] = calculate_item(instrument, sessions, observed) if sessions else None
            except ValueError:
                if instrument in TOTAL_INPUTS:
                    total_inputs.append(None)
                else:
                    results[instrument.key] = None
        if stopped is not None and stopped():
            self.store.release_market(owner)
            return False
        results[TOTAL.key] = _calculate_total(total_inputs) if all(total_inputs) else None
        finished = time.time() if now is None else now
        return self.store.finish_market(owner, results, now=now,
                                        next_run=max(finished+1, started+refresh_interval(started)))

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        rows = {row['key']: row for row in self.store.market_rows()}

        def view(instrument):
            row = rows.get(instrument.key)
            item = _empty(instrument) if not row or not row['payload'] else json.loads(row['payload'])
            if item['code'] != instrument.code or item['source'] != instrument.source:
                # Source changes must not relabel or reuse another classification's cache.
                item = _empty(instrument)
                item['message'] = '数据源已切换，等待新行情'
                return item
            if row:
                item['fetched_at'] = row['fetched_at']
                if item['status'] == 'ready':
                    old, age = _age_message(item, now)
                    item['stale'] = old or bool(row['failed'])
                    if row['failed']:
                        age = f'更新失败，保留上次数据；{age}'
                    item['message'] = '；'.join(filter(None, (age, item['message'])))
                elif row['failed']:
                    item['message'] = ('沪深序列缺失或不同步，合计不可用' if instrument == TOTAL
                                       else '数据源暂不可用')
            return item

        *items, total = [view(instrument) for instrument in (*ITEMS, TOTAL)]
        ready = [item for item in (*items, total) if item['status'] == 'ready']
        local, minute = _clock(now)
        if local.weekday() >= 5:
            message = '周末休市'
        elif minute < OPEN:
            message = '尚未开盘'
        elif not any(item['trade_date'] == local.date().isoformat() for item in ready):
            message = '今日无行情，可能休市或更新延迟' if ready else '等待行情 worker 更新'
        elif LUNCH < minute <= AFTERNOON:
            message = '午间休市'
        elif minute >= CLOSE:
            message = '已收盘'
        else:
            message = '盘中缓存行情'
        if not ready and rows:
            message += '；数据源暂不可用'
        return {
            'source': '腾讯财经 / 同花顺', 'source_url': TENCENT_URL,
            'fetched_at': max((item['fetched_at'] for item in ready), default=None),
            'stale': any(item['stale'] for item in ready),
            'status': 'ready' if len(ready) == len(items)+1 else 'partial' if ready else 'unavailable',
            'message': message, 'items': items, 'total': total,
        }
