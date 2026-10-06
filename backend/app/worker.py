"""Worker : prend les items en attente et les traite (threads, dans l'API ou en process séparé)."""

from __future__ import annotations

import logging
import threading
import time

from . import db, notion, pipeline
from .config import get_settings

log = logging.getLogger(__name__)

wake = threading.Event()


class Worker:
    def __init__(self, concurrency: int | None = None):
        self.concurrency = concurrency or get_settings().worker_concurrency
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._notion: notion.Syncer | None = None
        self._digest = None

    def start(self) -> None:
        for i in range(self.concurrency):
            t = threading.Thread(target=self._loop, name=f"kb-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("Worker démarré (%d threads)", self.concurrency)
        if notion.enabled():
            self._notion = notion.Syncer()
            self._notion.start()
        if get_settings().digest_enabled:
            from .digest.agent import Scheduler   # imported lazily: the digest pulls in feedparser

            self._digest = Scheduler()
            self._digest.start()

    def stop(self) -> None:
        self._stop.set()
        wake.set()
        if self._notion:
            self._notion.stop()
        if self._digest:
            self._digest.stop()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                did_work = run_once()
            except Exception:
                log.exception("Erreur inattendue du worker")
                did_work = False
            if not did_work:
                wake.wait(timeout=5)
                wake.clear()


def run_once() -> bool:
    item = db.fetchone("select * from claim_next_item()")
    if not item:
        return False
    started = time.monotonic()
    log.info("Traitement %s (%s)", item["id"], item.get("input_url") or item.get("file_name") or "note")
    try:
        pipeline.process(item)
        log.info("Prêt %s en %.1fs", item["id"], time.monotonic() - started)
    except Exception as exc:
        log.warning("Échec %s : %s", item["id"], exc, exc_info=True)
        pipeline.fail(item, exc)
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    w = Worker()
    w.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        w.stop()
