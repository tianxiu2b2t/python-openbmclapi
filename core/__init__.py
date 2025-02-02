import asyncio
import os
import time

from core import web
from . import scheduler
from .logger import logger
from . import utils
import atexit
from . import cluster
from . import dashboard
from . import database
from . import storages

_WAITLOCK = utils.CountLock()
_START_RUNTIME = time.monotonic()

async def call(module, func: str):
    try:
        init = getattr(module, func)
        if asyncio.iscoroutinefunction(init):
            await init()
        else:
            await asyncio.get_event_loop().run_in_executor(None, init)
    except:
        logger.traceback()

async def main():
    start = time.monotonic_ns()
    await asyncio.gather(*[
        call(m, "init") for m in (
            scheduler,
            storages,
            database,
            dashboard,
            cluster,
            web
        )
    ])
    _WAITLOCK.acquire()
    end = time.monotonic_ns()
    logger.tsuccess("main.success.start_service_done", time=f"{((end-start) / 1e9):.2f}")
    try:
        await _WAITLOCK.wait()
    except:
        logger.tdebug("main.debug.service_unfinish")
    finally:
        await asyncio.gather(*[
            call(m, "unload") for m in (
                scheduler,
                cluster,
                database,
                dashboard,
                web
            )
        ])

def init():
    atexit.register(main_exit)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.debug("KeyboardInterrupt")
    finally:
        atexit.unregister(main_exit)
    logger.tsuccess("main.success.service_exit")

def main_exit():
    _WAITLOCK.release()