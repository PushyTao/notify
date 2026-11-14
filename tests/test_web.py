import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(db_path=tmp_path/'web.db', secret_key='s'*40, admin_password='strong-password-123'))


def login(client):
    response = client.post('/api/login', json={'password':'strong-password-123'})
    assert response.status_code == 200
    return {'X-CSRF-Token': response.json()['csrf']}


def test_auth_csrf_and_secret_redaction(client):
    assert client.get('/api/admin/state').status_code == 401
    headers = login(client)
    assert client.post('/api/admin/models', json={'name':'X','base_url':'https://example.com','model_id':'x','api_key':'secret'}).status_code == 403
    result = client.post('/api/admin/models', headers=headers, json={'name':'X','base_url':'https://example.com','model_id':'x','api_key':'super-secret','enabled':True})
    assert result.status_code == 200
    state = client.get('/api/admin/state').json()
    assert state['models'][0]['api_key_set'] is True
    assert 'super-secret' not in str(state)
    assert client.post('/api/logout', headers=headers).status_code == 200
    assert client.get('/api/admin/state').status_code == 401


def test_multiple_models_exactly_one_active(client):
    h = login(client)
    for n in range(2):
        assert client.post('/api/admin/models', headers=h, json={'name':str(n),'base_url':'https://example.com/v1','model_id':'m','enabled':True}).status_code == 200
    models = client.get('/api/admin/state').json()['models']
    assert sum(x['enabled'] for x in models) == 1
    assert models[-1]['enabled'] == 1


def test_author_and_subscriber_validation(client):
    h = login(client)
    r = client.post('/api/admin/authors', headers=h, json={'name':'作者','source_id':'https://www.douyin.com/user/abc','interval_seconds':600})
    assert r.status_code == 200
    a = client.get('/api/admin/state').json()['authors'][0]
    assert a['source_id'] == 'abc'
    assert client.post('/api/admin/subscribers', headers=h, json={'email':'bad'}).status_code == 422
    assert client.post('/api/admin/subscribers', headers=h, json={'email':'a@example.com','author_ids':[999]}).status_code == 422
    assert client.post('/api/admin/subscribers', headers=h, json={'email':'a@example.com','author_ids':[a['id']]}).status_code == 200
    assert client.put('/api/admin/settings/smtp', headers=h, json={'port':-1}).status_code == 422
    assert client.put('/api/admin/settings/collector', headers=h, json={'base_url':'file:///etc/passwd'}).status_code == 422


def test_login_throttles_and_password_change_revokes_all_sessions(client):
    h = login(client)
    r = client.post('/api/admin/password', headers=h, json={'current_password':'strong-password-123','new_password':'changed-password-123'})
    assert r.status_code == 200
    assert client.get('/api/admin/state').status_code == 401
    for _ in range(5):
        client.post('/api/login', json={'password':'wrong'})
    assert client.post('/api/login', json={'password':'wrong'}).status_code == 429


def test_public_order_sort_and_no_admin_link(client):
    store = client.app.state.store
    with store.connect() as db:
        db.execute("INSERT INTO authors(name,source_id,created_at) VALUES('作者','abc',1)")
        for vid, published in [('old',100),('new',200)]:
            db.execute("INSERT INTO videos(video_id,author_id,author_name,published_at,title,url,media_url,transcript,summary,status) VALUES(?,1,'作者',?,?,'https://www.douyin.com/video/1','https://example.com/v','全文','摘要','complete')", (vid,published,vid))
    response = client.get('/')
    assert response.status_code == 200
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(response.text, 'html.parser')
    assert not soup.select('a[href="/admin"]')
    assert response.text.index('new') < response.text.index('old')
    text = soup.get_text()
    labels = ['更新时间','博主名称','总结内容','内容全文','原视频标题','原视频链接']
    positions = [text.index(label) for label in labels]
    assert positions == sorted(positions)


def test_finance_home_has_fixed_market_cards_and_independent_total(client):
    from bs4 import BeautifulSoup
    page = BeautifulSoup(client.get('/').text, 'html.parser')
    assert '长虹' in page.title.text
    panel = page.select_one('[data-market-panel]')
    assert panel is not None
    assert [card.select_one('h3').text for card in panel.select('[data-market-item]')] == [
        '上证指数', '深成指', '创业板指', '科创综指', '科创50',
        '沪深300', '中证1000', '证券板块', '银行板块', '半导体板块',
    ]
    total = panel.select_one('[data-market-total]')
    assert '沪深成交额' in total.get_text()
    assert total.find_parent('details') is None
    assert '等待行情更新' in panel.get_text()
    assert '暂无可比数据' in panel.get_text()
    assert not page.select('a[href="/admin"]')
    assert page.select_one('script[src^="/static/market.js?v="][defer]')
    assert page.select_one('script[src^="/static/market-strip.js?v="][defer]')
    assert page.select_one('main').find(recursive=False) == panel
    assert panel.select_one('[data-market-rail]')['tabindex'] == '0'
    assert len(panel.select('dialog')) == 11
    assert len(panel.select('[data-market-drag]')) == 10
    assert not total.select('[data-market-drag]')


def test_static_assets_use_content_versions_to_avoid_mixed_deployments(client):
    import hashlib
    from pathlib import Path
    from urllib.parse import urlsplit, parse_qs
    from bs4 import BeautifulSoup
    page = BeautifulSoup(client.get('/').text, 'html.parser')
    urls = [node.get('src') or node.get('href') for node in page.select('script[src], link[rel=stylesheet]')]
    for url in urls:
        parts = urlsplit(url)
        path = Path(__file__).parents[1] / 'app' / parts.path.lstrip('/')
        assert parse_qs(parts.query).get('v') == [hashlib.sha256(path.read_bytes()).hexdigest()[:12]]


def test_chunked_body_limit_without_content_length(client):
    chunks = (part for part in [b'{"password":"', b'x'*70000, b'"}'])
    response = client.post('/api/login', content=chunks, headers={'Content-Type':'application/json'})
    assert response.status_code == 413
    small = (part for part in [b'{"password":', b'"strong-password-123"}'])
    assert client.post('/api/login', content=small, headers={'Content-Type':'application/json'}).status_code == 200
