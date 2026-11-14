"""Business transitions; external adapters are injected at the boundary."""
import json
import time

from app.db import assert_owner


class Service:
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings

    def poll_author(self, author, collector, now=None):
        now = time.time() if now is None else now
        posts = collector(author, self.settings.get('collector'))
        if not isinstance(posts, list):
            raise ValueError('采集接口必须返回列表')
        for post in posts:
            if not post.get('video_id') or not isinstance(post.get('published_at'), (int, float)) or not post.get('media_url'):
                raise ValueError('视频数据不完整')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            assert_owner(db, 'authors', author)
            current = dict(db.execute('SELECT * FROM authors WHERE id=?', (author['id'],)).fetchone())
            if not current['initialized']:
                selected = sorted(posts, key=lambda p: p['published_at'], reverse=True)[:current['backfill']]
            else:
                selected = [post for post in posts if post['published_at'] >= current['baseline_at']]
            db.executemany(
                """UPDATE videos SET media_url=? WHERE video_id=? AND author_id=?
                   AND status IN ('pending','retry','failed') AND lease_owner IS NULL""",
                ((post['media_url'], post['video_id'], current['id']) for post in posts),
            )
            for post in selected:
                db.execute('''INSERT OR IGNORE INTO videos(video_id,author_id,author_name,published_at,title,url,media_url)
                              VALUES(?,?,?,?,?,?,?)''', (post['video_id'], author['id'], post.get('author_name') or current['name'],
                              post['published_at'], post['title'], post['url'], post['media_url']))
            db.execute('''UPDATE authors SET initialized=1,baseline_at=CASE WHEN initialized=0 THEN ? ELSE baseline_at END,
                          last_polled=?,next_run=?,attempts=0,error='',lease_owner=NULL,lease_until=0 WHERE id=?''',
                       (now, now, now + current['interval_seconds'], author['id']))

    def process_video(self, video, integrations):
        transcript = video['transcript']
        if not transcript:
            media_url = video['media_url']
            if video['attempts'] > 1:
                author = self.store.one('SELECT * FROM authors WHERE id=?', (video['author_id'],))
                media_url = integrations.refresh_media(video, author, self.settings.get('collector'))
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    assert_owner(db, 'videos', video)
                    db.execute('UPDATE videos SET media_url=? WHERE id=?', (media_url, video['id']))
            transcript = integrations.transcribe(media_url, self.settings.get('asr'))
            if not transcript or not transcript.strip():
                raise ValueError('没有可识别的语音')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                assert_owner(db, 'videos', video)
                db.execute('UPDATE videos SET transcript=? WHERE id=?', (transcript, video['id']))
        model = self.store.one('SELECT * FROM models WHERE enabled=1')
        if not model:
            raise ValueError('请先启用总结模型')
        model['api_key'] = self.settings.decrypt(model['api_key'])
        prompt = self.settings.get('prompt')['text']
        summary = integrations.summarize(transcript, model, prompt)
        if not summary or not summary.strip():
            raise ValueError('模型未返回总结')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            assert_owner(db, 'videos', video)
            db.execute("UPDATE videos SET summary=?,model_name=?,prompt_used=?,status='complete',error='',lease_owner=NULL,lease_until=0 WHERE id=?",
                       (summary, model['model_id'], prompt, video['id']))
            for subscriber in db.execute('SELECT * FROM subscribers WHERE enabled=1').fetchall():
                authors = json.loads(subscriber['author_ids'])
                if not authors or video['author_id'] in authors:
                    db.execute('INSERT OR IGNORE INTO deliveries(video_id,subscriber_id,email) VALUES(?,?,?)',
                               (video['id'], subscriber['id'], subscriber['email']))

    def deliver(self, delivery, sender):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            subscriber = db.execute('SELECT * FROM subscribers WHERE id=?', (delivery['subscriber_id'],)).fetchone()
            if not subscriber:
                return  # Deletion cascades to the delivery.
            assert_owner(db, 'deliveries', delivery)
            if not subscriber['enabled']:
                db.execute("UPDATE deliveries SET status='pending',lease_owner=NULL,lease_until=0 WHERE id=?", (delivery['id'],))
                return
            record = db.execute('SELECT * FROM videos WHERE id=?', (delivery['video_id'],)).fetchone()
            if not record or record['status'] != 'complete':
                raise ValueError('记录尚未完成')
            authors = json.loads(subscriber['author_ids'])
            if authors and record['author_id'] not in authors:
                db.execute("""UPDATE deliveries SET status='cancelled',error=?,lease_owner=NULL,lease_until=0
                              WHERE id=?""", ('订阅者已取消此博主的通知', delivery['id']))
                return
            email = subscriber['email']
            db.execute('UPDATE deliveries SET email=? WHERE id=?', (email, delivery['id']))
        sender(email, dict(record), self.settings.get('smtp'))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            assert_owner(db, 'deliveries', delivery)
            db.execute("UPDATE deliveries SET status='sent',error='',lease_owner=NULL,lease_until=0 WHERE id=?", (delivery['id'],))
