import os

from celery import Celery  # type: ignore[import-untyped]
from dotenv import load_dotenv

from core.email.manager import EmailManager
from core.queue.tasks import execute_registered_task
from core import task as _task_registration  # noqa: F401
from core.queue import registrations as _queue_registrations  # noqa: F401
from core.queue.celery_provider import CeleryQueueProvider
from core.queue.manager import QueueManager

load_dotenv()

broker_url = os.getenv("CELERY_BROKER_URL")
backend_url = os.getenv("CELERY_RESULT_BACKEND")

celery_app = Celery("worker", broker=broker_url, backend=backend_url)
celery_app.conf.update(task_track_started=True)

# Route each wrapper task to its own queue so specialised workers can
# subscribe selectively (see docker-compose.yml for the four worker
# services: writes, precompute, gates, default).
celery_app.conf.task_routes = {
    "celery_worker.run_write_task": {"queue": "writes"},
    "celery_worker.run_precompute_task": {"queue": "precompute"},
    "celery_worker.run_gate_task": {"queue": "gates"},
    "celery_worker.run_async_task": {"queue": "celery"},
    "celery_worker.test_scheduler": {"queue": "celery"},
}

# Writers enqueue follow-up precompute tasks via QueueManager. Without this
# the worker process raises "QueueManager is not configured" the moment any
# writer tries to refresh a cache after committing a mutation.
QueueManager.configure(CeleryQueueProvider(celery_app=celery_app))

# Mount email templates at worker boot. Without this, the first send_email
# task lazily builds an EmailManager; if template import fails the
# exception is swallowed by configure_from_settings' try/except and the
# singleton is cached template-less, making every subsequent render raise
# "Available: <none>".
EmailManager.configure_from_settings()


@celery_app.task(name="celery_worker.test_scheduler")
async def test_scheduler(message: str) -> str:
    return message


@celery_app.task(name="celery_worker.run_async_task")
async def run_async_task(task_key: str, kwargs: dict):
    # Default catch-all queue for legacy fire-and-forget work (emails,
    # invoice PDFs, plan cache fanout, etc.).
    return await execute_registered_task(task_key=task_key, payload=kwargs)


@celery_app.task(name="celery_worker.run_write_task")
async def run_write_task(task_key: str, kwargs: dict):
    # Mutations routed through core.queue.write_pipeline land here. The
    # `db.write` handler in core/queue/tasks.py dispatches to the
    # writer registered under `kwargs["writer_key"]`.
    return await execute_registered_task(task_key=task_key, payload=kwargs)


@celery_app.task(name="celery_worker.run_gate_task")
async def run_gate_task(task_key: str, kwargs: dict):
    # Smart gate-cache refreshes run on the `gates` queue so a flood of
    # re-checks does not starve the write path.
    return await execute_registered_task(task_key=task_key, payload=kwargs)


@celery_app.task(name="celery_worker.run_precompute_task")
async def run_precompute_task(task_key: str, kwargs: dict):
    # Per-tenant GET precompute work runs on its own queue so heavy list
    # / aggregate computations can be tuned independently of the rest.
    return await execute_registered_task(task_key=task_key, payload=kwargs)
