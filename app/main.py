from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import hmac
import json
import secrets
import sqlite3
import time

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import Settings, DEFAULT_PROMPT, environment
from app.db import Store
from app.market import MarketService
from app.middleware import RequestSizeLimit
from app.schemas import Author, Subscriber, ModelConfig, Login, PasswordChange, EmailInput, CookieImport, validate_settings
from app.security import hash_password, verify_password, token_hash, safe_error

ROOT = Path(__file__).parent


def create_app(db_path=None, secret_key=None, admin_password=None, secure_cookie=None):
    env = environment()
    store = Store(db_path or env['db_path'])
    settings = Settings(store, secret_key if secret_key is not None else env['secret_key'])
    secure_cookie = env['secure_cookie'] if secure_cookie is None else secure_cookie
    if not store.one('SELECT id FROM auth'):
        password = admin_password if admin_password is not None else env['admin_password']
        if len(password) < 12:
            raise ValueError('首次启动请设置至少 12 字符的 ADMIN_PASSWORD')
        with store.connect() as db:
            db.execute('INSERT OR IGNORE INTO auth(id,password_hash) VALUES(1,?)', (hash_password(password),))
    app = FastAPI(title='Douyin Notify', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.settings = store, settings
    templates = Jinja2Templates(directory=ROOT / 'templates')
    templates.env.filters['datetime'] = lambda value: datetime.fromtimestamp(value, ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S') if value else '—'
    # check_dir=False permits independent template implementation during development.
    app.mount('/static', StaticFiles(directory=ROOT / 'static', check_dir=False), name='static')

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default response includes raw input (including passwords/keys).
        fields = ', '.join('.'.join(map(str, e['loc'][1:])) for e in exc.errors())
        return JSONResponse({'detail': f'请检查输入字段：{fields}'}, status_code=422)

    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict(request, exc):
        return JSONResponse({'detail': '数据重复或仍被记录引用，请修改输入或停用该项'}, status_code=409)

    @app.middleware('http')
    async def headers(request, call_next):
        if request.url.path.startswith('/api/'):
            # Same-origin browser writes only, including login to prevent login CSRF.
            origin = request.headers.get('origin')
            if request.method not in ('GET', 'HEAD') and origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail':'请求来源无效'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        if request.url.path.startswith(('/admin', '/api/')):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def session(request):
        raw = request.cookies.get('notify_session', '')
        if not raw:
            return None
        return store.one('SELECT * FROM sessions WHERE token=? AND expires_at>?', (token_hash(raw), time.time()))

    def admin(request: Request):
        found = session(request)
        if not found:
            raise HTTPException(401, '请先登录后台')
        if request.method not in ('GET', 'HEAD') and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), found['csrf']):
            raise HTTPException(403, '请求校验失败，请刷新页面')
        return found

    def require_row(table, id):
        row = store.one(f'SELECT * FROM {table} WHERE id=?', (id,))
        if not row:
            raise HTTPException(404, '记录不存在')
        return row

    @app.get('/healthz')
    def health():
        store.one('SELECT 1')
        return {'ok': True}

    @app.get('/api/market')
    def market():
        return MarketService(store).snapshot()

    @app.get('/')
    def index(request: Request, page: int = 1, author_id: str = ''):
        if author_id:
            try:
                author_id = int(author_id)
            except ValueError:
                raise HTTPException(422, '博主编号无效') from None
        else:
            author_id = None
        page = max(1, page)
        condition, params = "status='complete'", []
        if author_id is not None:
            condition += ' AND author_id=?'
            params.append(author_id)
        total = store.one(f'SELECT COUNT(*) n FROM videos WHERE {condition}', params)['n']
        pages = max(1, (total + 9) // 10)
        page = min(page, pages)
        records = store.all(f'SELECT id,author_name,published_at,title,url,transcript,summary FROM videos WHERE {condition} ORDER BY published_at DESC,id DESC LIMIT 10 OFFSET ?', params + [(page - 1)*10])
        return templates.TemplateResponse(request, 'index.html', {'records':records,'authors':store.all('SELECT id,name FROM authors ORDER BY name'), 'page':page,'pages':pages,'total':total,'author_id':author_id or ''})

    @app.get('/records/{id}')
    def record(request: Request, id: int):
        row = store.one("SELECT id,author_name,published_at,title,url,transcript,summary FROM videos WHERE id=? AND status='complete'", (id,))
        if not row:
            raise HTTPException(404, '记录不存在')
        return templates.TemplateResponse(request, 'record.html', {'record':row})

    @app.get('/admin')
    def dashboard(request: Request):
        found = session(request)
        return templates.TemplateResponse(request, 'admin.html' if found else 'login.html', {'csrf':found['csrf'] if found else '', 'default_prompt':DEFAULT_PROMPT})

    @app.post('/api/login')
    def login(body: Login, request: Request):
        address = request.client.host if request.client else 'unknown'
        now = time.time()
        # Increment before expensive hashing to bound concurrent attempts as well.
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM login_attempts WHERE address=?', (address,)).fetchone()
            if row and row['reset_at'] > now and row['attempts'] >= 5:
                raise HTTPException(429, '登录尝试过多，请 15 分钟后重试')
            attempts = row['attempts'] + 1 if row and row['reset_at'] > now else 1
            reset_at = row['reset_at'] if row and row['reset_at'] > now else now + 900
            db.execute('INSERT INTO login_attempts VALUES(?,?,?) ON CONFLICT(address) DO UPDATE SET attempts=excluded.attempts,reset_at=excluded.reset_at', (address,attempts,reset_at))
        auth = store.one('SELECT password_hash FROM auth WHERE id=1')
        if not verify_password(body.password, auth['password_hash']):
            raise HTTPException(401, '密码不正确')
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with store.connect() as db:
            db.execute('DELETE FROM login_attempts WHERE address=?', (address,))
            db.execute('DELETE FROM sessions WHERE expires_at<?', (now,))
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (token_hash(token),csrf,now + 43200))
        response = JSONResponse({'ok':True,'csrf':csrf})
        response.set_cookie('notify_session', token, max_age=43200, httponly=True, samesite='strict', secure=secure_cookie)
        return response

    @app.post('/api/logout')
    def logout(found=Depends(admin)):
        with store.connect() as db:
            db.execute('DELETE FROM sessions WHERE token=?', (found['token'],))
        response = JSONResponse({'ok':True})
        response.delete_cookie('notify_session')
        return response

    @app.get('/api/admin/state')
    def state(found=Depends(admin)):
        models = store.all('SELECT * FROM models ORDER BY id')
        for model in models:
            model['api_key_set'] = bool(model.pop('api_key'))
        subscribers = store.all('SELECT * FROM subscribers ORDER BY id')
        for sub in subscribers:
            sub['author_ids'] = json.loads(sub['author_ids'])
        heartbeat = store.one("SELECT value FROM runtime WHERE key='worker_heartbeat'")
        counts = {table:store.one(f'SELECT COUNT(*) n FROM {table}')['n'] for table in ('authors','videos','subscribers')}
        counts['pending'] = store.one("SELECT COUNT(*) n FROM videos WHERE status IN ('pending','retry','processing')")['n']
        counts['failed'] = store.one("SELECT COUNT(*) n FROM videos WHERE status='failed'")['n']
        return {'csrf':found['csrf'],'authors':store.all('SELECT * FROM authors ORDER BY id'),
                'subscribers':subscribers,'models':models,'settings':settings.public(),'default_prompt':DEFAULT_PROMPT,
                'videos':store.all('SELECT id,video_id,author_name,published_at,title,status,attempts,error FROM videos ORDER BY published_at DESC,id DESC LIMIT 200'),
                'deliveries':store.all('SELECT id,video_id,email,status,attempts,error FROM deliveries ORDER BY id DESC LIMIT 200'),
                'status':{'worker_heartbeat':heartbeat['value'] if heartbeat else None,'counts':counts}}

    @app.post('/api/admin/authors', dependencies=[Depends(admin)])
    def add_author(body: Author):
        with store.connect() as db:
            row = db.execute('INSERT INTO authors(name,source_id,provider,enabled,interval_seconds,backfill,created_at) VALUES(?,?,?,?,?,?,?) RETURNING id',
                             (*body.model_dump().values(), time.time())).fetchone()
        return {'ok':True,'id':row['id']}

    @app.put('/api/admin/authors/{id}', dependencies=[Depends(admin)])
    def update_author(id: int, body: Author):
        old = require_row('authors', id)
        if old['source_id'] != body.source_id and store.one('SELECT id FROM videos WHERE author_id=? LIMIT 1', (id,)):
            raise HTTPException(409, '已有记录的博主不能更换账号，请新增博主')
        with store.connect() as db:
            db.execute('UPDATE authors SET name=?,source_id=?,provider=?,enabled=?,interval_seconds=?,backfill=?,initialized=CASE WHEN source_id!=? THEN 0 ELSE initialized END WHERE id=?', (*body.model_dump().values(),body.source_id,id))
        return {'ok':True}

    @app.delete('/api/admin/authors/{id}', dependencies=[Depends(admin)])
    def delete_author(id: int):
        require_row('authors', id)
        with store.connect() as db:
            # Explicit subscriptions must not silently become "all authors" after deletion.
            for sub in db.execute('SELECT author_ids FROM subscribers'):
                if id in json.loads(sub['author_ids']):
                    raise HTTPException(409, '请先修改该博主的订阅关系，或将博主停用')
            db.execute('DELETE FROM authors WHERE id=?', (id,))
        return {'ok':True}

    @app.post('/api/admin/authors/{id}/poll', dependencies=[Depends(admin)])
    def poll_now(id: int):
        row = require_row('authors', id)
        if not row['enabled']:
            raise HTTPException(409, '请先启用博主')
        with store.connect() as db:
            db.execute('UPDATE authors SET next_run=0 WHERE id=?', (id,))
        return {'ok':True,'message':'已加入检查队列'}

    def validate_subscriptions(body):
        valid = {row['id'] for row in store.all('SELECT id FROM authors')}
        if not set(body.author_ids) <= valid:
            raise HTTPException(422, '选择了不存在的博主')

    @app.post('/api/admin/subscribers', dependencies=[Depends(admin)])
    def add_subscriber(body: Subscriber):
        validate_subscriptions(body)
        with store.connect() as db:
            row = db.execute('INSERT INTO subscribers(email,enabled,author_ids) VALUES(?,?,?) RETURNING id', (body.email,body.enabled,json.dumps(sorted(set(body.author_ids))))).fetchone()
        return {'ok':True,'id':row['id']}

    @app.put('/api/admin/subscribers/{id}', dependencies=[Depends(admin)])
    def update_subscriber(id: int, body: Subscriber):
        require_row('subscribers', id)
        validate_subscriptions(body)
        with store.connect() as db:
            db.execute('UPDATE subscribers SET email=?,enabled=?,author_ids=? WHERE id=?', (body.email,body.enabled,json.dumps(sorted(set(body.author_ids))),id))
            db.execute("UPDATE deliveries SET email=? WHERE subscriber_id=? AND status IN ('pending','retry','failed')", (body.email,id))
        return {'ok':True}

    @app.delete('/api/admin/subscribers/{id}', dependencies=[Depends(admin)])
    def delete_subscriber(id: int):
        require_row('subscribers', id)
        with store.connect() as db:
            db.execute('DELETE FROM subscribers WHERE id=?', (id,))
        return {'ok':True}

    def save_model(body, id=None):
        old = require_row('models', id) if id else None
        key = settings.encrypt(body.api_key) if body.api_key else (old['api_key'] if old else '')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if body.enabled:
                db.execute('UPDATE models SET enabled=0')
            values = (body.name,body.base_url,body.model_id,key,body.enabled,body.timeout_seconds)
            if id:
                db.execute('UPDATE models SET name=?,base_url=?,model_id=?,api_key=?,enabled=?,timeout_seconds=? WHERE id=?', (*values,id))
            else:
                id = db.execute('INSERT INTO models(name,base_url,model_id,api_key,enabled,timeout_seconds) VALUES(?,?,?,?,?,?) RETURNING id', values).fetchone()['id']
        return {'ok':True,'id':id}

    @app.post('/api/admin/models', dependencies=[Depends(admin)])
    def add_model(body: ModelConfig):
        return save_model(body)

    @app.put('/api/admin/models/{id}', dependencies=[Depends(admin)])
    def update_model(id: int, body: ModelConfig):
        return save_model(body,id)

    @app.delete('/api/admin/models/{id}', dependencies=[Depends(admin)])
    def delete_model(id: int):
        require_row('models', id)
        with store.connect() as db:
            db.execute('DELETE FROM models WHERE id=?', (id,))
        return {'ok':True}

    @app.post('/api/admin/models/{id}/test', dependencies=[Depends(admin)])
    def model_test(id: int):
        from app.integrations import test_model
        model = require_row('models', id)
        model['api_key'] = settings.decrypt(model['api_key'])
        start = time.monotonic()
        try:
            test_model(model)
        except Exception as exc:
            raise HTTPException(502, safe_error(exc)) from None
        return {'ok':True,'message':'模型连接成功','elapsed_ms':round((time.monotonic()-start)*1000)}

    @app.put('/api/admin/settings/{group}', dependencies=[Depends(admin)])
    def update_settings(group: str, body: dict):
        try:
            validate_settings(group,body)
            if group == 'collector' and body.get('cookie'):
                raise ValueError('请使用独立的 Cookie 导入操作')
            settings.save(group,body)
        except ValueError as exc:
            raise HTTPException(422, '设置无效，请检查地址、字段、端口或输入范围') from None
        return {'ok':True}

    @app.post('/api/admin/collector/cookie', dependencies=[Depends(admin)])
    def collector_cookie(body: CookieImport):
        from app.integrations import import_cookie
        try:
            identity_id = import_cookie({**settings.get('collector'),**body.model_dump()})
            settings.save('collector', {'identity_id':identity_id})
        except Exception as exc:
            raise HTTPException(502, safe_error(exc)) from None
        return {'ok':True,'message':'Cookie 已导入采集服务','identity_id':identity_id}

    @app.post('/api/admin/test-email', dependencies=[Depends(admin)])
    def email_test(body: EmailInput):
        from app.integrations import send_email
        record = {'video_id':'test','id':0,'published_at':time.time(),'author_name':'通知测试','summary':'邮件服务连接测试',
                  'transcript':'这是一封配置测试邮件。','title':'邮件测试','url':'https://www.douyin.com/'}
        try:
            send_email(body.email,record,settings.get('smtp'))
        except Exception as exc:
            raise HTTPException(502, safe_error(exc)) from None
        return {'ok':True,'message':'测试邮件已提交 SMTP 服务器'}

    @app.post('/api/admin/password', dependencies=[Depends(admin)])
    def change_password(body: PasswordChange):
        if not verify_password(body.current_password, store.one('SELECT password_hash FROM auth')['password_hash']):
            raise HTTPException(400, '当前密码不正确')
        with store.connect() as db:
            db.execute('UPDATE auth SET password_hash=? WHERE id=1', (hash_password(body.new_password),))
            db.execute('DELETE FROM sessions')
        return {'ok':True,'message':'密码已修改，请重新登录'}

    def retry(table, id):
        row = require_row(table, id)
        if row['status'] not in ('failed','retry'):
            raise HTTPException(409, '仅可重试失败的任务')
        with store.connect() as db:
            db.execute(f"UPDATE {table} SET status='pending',attempts=0,error='',next_run=0,lease_owner=NULL,lease_until=0 WHERE id=? AND status IN ('failed','retry')", (id,))
        return {'ok':True,'message':'已加入重试队列'}

    @app.post('/api/admin/videos/{id}/retry', dependencies=[Depends(admin)])
    def retry_video(id: int):
        return retry('videos',id)

    @app.post('/api/admin/deliveries/{id}/retry', dependencies=[Depends(admin)])
    def retry_delivery(id: int):
        return retry('deliveries',id)

    app.add_middleware(RequestSizeLimit)
    return app
