import asyncio
import logging
from pathlib import Path
from filelock import FileLock, Timeout
from sqlalchemy import select, update
from .config import settings
from .db import init_db, Session, Analysis, Run, ChatTask
from .chat import process_chat
from .engine import prepare, run_experiment

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("beseen.worker")


def recover():
    # Called only while holding an OS file lock. A second worker never resets active jobs.
    with Session.begin() as db:
        db.execute(update(Analysis).where(Analysis.status == "preparing").values(status="queued"))
        db.execute(update(Run).where(Run.status == "running").values(status="queued"))
        db.execute(update(ChatTask).where(ChatTask.status == "running").values(status="queued"))


async def tick():
    with Session() as db:
        chat = db.scalar(select(ChatTask).where(ChatTask.status == "queued").order_by(ChatTask.created_at))
    if chat:
        await process_chat(chat.id)
        return True
    with Session() as db:
        run = db.scalar(select(Run).where(Run.status == "queued").order_by(Run.created_at))
        analysis = None if run else db.scalar(select(Analysis).where(Analysis.status == "queued").order_by(Analysis.created_at))
    if run:
        await run_experiment(run.id)
        return True
    if analysis:
        await prepare(analysis.id)
        return True
    return False


async def loop():
    init_db()
    recover()
    log.info("Worker ready; persisted checkpoints will be reused.")
    while True:
        try:
            if not await tick():
                await asyncio.sleep(1)
        except Exception as e:
            # Do not log provider bodies or credentials.
            log.error("Job stopped with %s; safe error is saved in the database.", type(e).__name__)
            await asyncio.sleep(1)


if __name__ == "__main__":
    Path(settings().worker_lock).parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(settings().worker_lock, timeout=0):
            asyncio.run(loop())
    except Timeout:
        raise SystemExit("Another worker already owns this database lock.")
    except KeyboardInterrupt:
        pass
