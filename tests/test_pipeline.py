import time

from app.config import Settings
from app.db import Store
from app.worker import Worker


def test_pipeline_fail_retry_and_resume_without_retranscribing(tmp_path):
    store = Store(tmp_path/'pipeline.db')
    settings = Settings(store,'x'*40)
    with store.connect() as db:
        db.execute("INSERT INTO authors(name,source_id,backfill,created_at) VALUES('作者','abc',1,1)")
        db.execute("INSERT INTO subscribers(email) VALUES('reader@example.com')")
        db.execute('INSERT INTO models(name,base_url,model_id,api_key,enabled) VALUES(?,?,?,?,1)', ('AI','https://ai.example','m',settings.encrypt('k')))
    class Controlled:
        transcripts = 0
        summaries = 0
        emails = []
        @staticmethod
        def fetch_posts(a, s):
            return [{'video_id':'1','author_name':'作者','published_at':100,'title':'标题','url':'https://www.douyin.com/video/1','media_url':'https://media.example/1'}]
        @classmethod
        def transcribe(cls, url, s):
            cls.transcripts += 1
            return '语音全文'
        @classmethod
        def summarize(cls, text, model, prompt):
            cls.summaries += 1
            if cls.summaries == 1:
                raise RuntimeError('never-store-the-api-key')
            return '总结内容'
        @classmethod
        def send_email(cls, email, record, s):
            cls.emails.append((email,record['summary'],record['transcript']))
    worker = Worker(store,settings,Controlled)
    assert worker.step('authors')
    assert worker.step('videos')
    row = store.one('SELECT * FROM videos')
    assert row['status'] == 'retry'
    assert 'never-store' not in row['error']
    assert row['transcript'] == '语音全文'
    with store.connect() as db:
        db.execute('UPDATE videos SET next_run=0')
    restarted = Worker(store,settings,Controlled)
    assert restarted.step('videos')
    assert Controlled.transcripts == 1
    assert restarted.step('deliveries')
    assert Controlled.emails == [('reader@example.com','总结内容','语音全文')]
    assert not restarted.step('deliveries')
    assert store.one('SELECT status FROM videos')['status'] == 'complete'


def test_disabled_subscriber_not_claimed_and_final_crash_visible(tmp_path):
    store = Store(tmp_path/'db')
    with store.connect() as db:
        db.execute("INSERT INTO authors(name,source_id,created_at) VALUES('a','a',1)")
        db.execute("INSERT INTO videos(video_id,author_id,author_name,published_at,title,url,media_url,status,attempts,lease_until) VALUES('1',1,'a',1,'t','https://example.com','https://example.com','processing',5,1)")
    assert store.claim('videos','next',now=20) is None
    assert store.one('SELECT status FROM videos')['status'] == 'failed'
