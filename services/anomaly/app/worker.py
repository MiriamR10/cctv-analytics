"""Background scheduler: rules every N seconds, scoring every window, nightly retrain.

Run as a separate process: ``python -m app.worker``. Run exactly one instance.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from app.config import settings
from app.db import SessionLocal, init_db
from app.services import alerts, forensic_sync, modeling
from app.services.rules import run_rules
from app.timeutils import local_tz, utcnow

logger = logging.getLogger("worker")


async def _rules_loop(channels) -> None:
    while True:
        try:
            async with SessionLocal() as session:
                created = await run_rules(session)
                await session.commit()
            await alerts.dispatch(created, channels)
        except Exception:
            logger.exception("Rules cycle failed")
        await asyncio.sleep(settings.rules_poll_seconds)


async def _scoring_loop(channels) -> None:
    while True:
        try:
            async with SessionLocal() as session:
                created = await modeling.score_recent(session)
                await session.commit()
            await alerts.dispatch(created, channels)
        except Exception:
            logger.exception("Scoring cycle failed")
        await asyncio.sleep(settings.scoring_interval_minutes * 60)


def _seconds_until_retrain(now: datetime) -> float:
    local = now.astimezone(local_tz())
    target = local.replace(
        hour=settings.retrain_hour, minute=0, second=0, microsecond=0
    )
    if target <= local:
        target += timedelta(days=1)
    return (target - local).total_seconds()


async def _retrain_loop() -> None:
    while True:
        await asyncio.sleep(_seconds_until_retrain(utcnow()))
        try:
            async with SessionLocal() as session:
                await modeling.train(session)
                await session.commit()
        except modeling.TrainingError as error:
            logger.warning("Retrain skipped: %s", error)
        except Exception:
            logger.exception("Retrain failed; previous model stays active")


async def _sync_loop() -> None:
    while True:
        try:
            async with SessionLocal() as session:
                imported = await forensic_sync.sync_all(session)
            if imported:
                logger.info("Imported %d events from forensic", imported)
        except Exception:
            logger.exception("Forensic sync failed")
        await asyncio.sleep(settings.forensic_sync_seconds)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if settings.auto_create_tables:
        await init_db()
    channels = alerts.default_channels()
    loops = [_rules_loop(channels), _scoring_loop(channels), _retrain_loop()]
    if settings.forensic_sync:
        loops.append(_sync_loop())
    await asyncio.gather(*loops)


if __name__ == "__main__":
    asyncio.run(main())
