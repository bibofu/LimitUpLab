"""One async graph host per API process, owned by FastAPI lifespan.

The dedicated SelectorEventLoop also supports psycopg on Windows, independent
of Uvicorn's HTTP event loop. Existing synchronous tools run in bounded threads.
"""

import asyncio
from concurrent.futures import Future
import threading

from app.agents.runner import durable_runtime


class DurableRuntimeHost:
    def __init__(self, database_url):
        self.database_url = database_url
        self.ready = Future()
        self.thread = threading.Thread(target=self._serve, name="agent-graph", daemon=True)

    def _serve(self):
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        try:
            runtime = loop.run_until_complete(durable_runtime.DurableRuntime.open(self.database_url))
            self.runtime = runtime
            durable_runtime._runtime = runtime
            self.ready.set_result(runtime)
            loop.run_forever()
        except BaseException as error:
            if not self.ready.done():
                self.ready.set_exception(error)
        finally:
            durable_runtime._runtime = None
            # Let the pool stop its own workers. Cancelling them first causes
            # pool.close() to re-raise CancelledError during application shutdown.
            if hasattr(self, "runtime"):
                running = list(self.runtime.active_tasks)
                for task in running:
                    task.cancel()
                if running:
                    loop.run_until_complete(asyncio.gather(*running, return_exceptions=True))
                loop.run_until_complete(self.runtime.pool.close())
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def start(self):
        self.thread.start()
        self.ready.result(timeout=35)
        return self

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=15)
        if self.thread.is_alive():
            raise RuntimeError("Durable runtime did not shut down")
