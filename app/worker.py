"""Three independent lanes keep slow ASR from blocking polling or mail."""
import argparse
import logging
import signal
import threading
import time
import uuid

from app.config import Settings, environment
from app.db import Store
from app.security import safe_error
from app.service import Service

logger = logging.getLogger('notify.worker')


class Worker:
    def __init__(self, store, settings, integrations):
        self.store, self.settings, self.integrations = store, settings, integrations
        self.owner = uuid.uuid4().hex
        self.service = Service(store,settings)
        self.stop = threading.Event()

    def step(self, table):
        task = self.store.claim(table,self.owner)
        if not task:
            return False
        try:
            if table == 'authors':
                self.service.poll_author(task,self.integrations.fetch_posts)
            elif table == 'videos':
                self.service.process_video(task,self.integrations)
            else:
                self.service.deliver(task,self.integrations.send_email)
        except Exception as exc:
            error = safe_error(exc)
            logger.warning('%s task %s: %s',table,task['id'],error)
            self.store.fail(table,task,error)
        return True

    def lane(self, table):
        while not self.stop.is_set():
            try:
                busy = self.step(table)
            except Exception as exc:
                logger.error('Worker storage error: %s',type(exc).__name__)
                busy = False
            if not busy:
                self.stop.wait(2)

    def run(self):
        lanes = [threading.Thread(target=self.lane,args=(table,),name=table,daemon=True) for table in ('authors','videos','deliveries')]
        for lane in lanes:
            lane.start()
        try:
            while not self.stop.is_set():
                if not all(lane.is_alive() for lane in lanes):
                    raise RuntimeError('Worker lane stopped unexpectedly')
                self.store.heartbeat(self.owner)
                self.stop.wait(15)
        finally:
            self.stop.set()
            deadline = time.monotonic() + 20
            for lane in lanes:
                lane.join(max(0,deadline-time.monotonic()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--healthcheck',action='store_true')
    args = parser.parse_args()
    env = environment()
    store = Store(env['db_path'])
    if args.healthcheck:
        heartbeat = store.one("SELECT value FROM runtime WHERE key='worker_heartbeat'")
        raise SystemExit(0 if heartbeat and time.time()-heartbeat['value'] < 90 else 1)
    from app import integrations
    settings = Settings(store,env['secret_key'])
    # Detect a mismatched encryption key immediately rather than corrupting task state.
    for group in ('collector','asr','smtp','prompt'):
        settings.get(group)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    # httpx INFO includes full request URLs (including signed media parameters).
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    worker = Worker(store,settings,integrations)
    signal.signal(signal.SIGTERM,lambda *_: worker.stop.set())
    signal.signal(signal.SIGINT,lambda *_: worker.stop.set())
    worker.run()


if __name__ == '__main__':
    main()
