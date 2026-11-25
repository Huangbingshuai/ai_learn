import logging
from time import monotonic, sleep

from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db import make_session_factory
from app.worker.ai import build_ai
from app.worker.queue import Worker

logger = logging.getLogger(__name__)
CLEANUP_INTERVAL_SECONDS = 60


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    try:
        ai = build_ai(settings)
    except ValueError as error:
        raise SystemExit(str(error)) from None

    worker = Worker(settings, make_session_factory(settings.database_url), ai)
    next_cleanup = 0.0
    try:
        while True:
            try:
                if monotonic() >= next_cleanup:
                    worker.cleanup()
                    next_cleanup = monotonic() + CLEANUP_INTERVAL_SECONDS
                if worker.process_once():
                    continue
            except SQLAlchemyError as error:
                logger.warning(
                    "Worker database operation failed exception_type=%s", type(error).__name__
                )
            sleep(settings.worker_poll_seconds)
    except KeyboardInterrupt:
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
