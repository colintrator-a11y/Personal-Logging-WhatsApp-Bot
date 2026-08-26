"""HTTP brain. The Baileys bridge POSTs a message here and sends back the reply."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from app import config, handlers, sheets, store

log = logging.getLogger("brain")


class Incoming(BaseModel):
    message_id: str
    chat_jid: str
    text: str
    ts: int | None = None


class Outgoing(BaseModel):
    reply: str | None = None


async def flush_queue_forever() -> None:
    """Rows that failed to write sit on disk until Sheets takes them."""
    interval = max(config.QUEUE_FLUSH_MS, 5000) / 1000
    while True:
        await asyncio.sleep(interval)
        batches = store.drain_queue()
        if not batches:
            continue
        for i, rows in enumerate(batches):
            try:
                await asyncio.to_thread(sheets.append_with_retry, rows)
                store.log("queue_flushed", rows=rows)
                # These rows are now the sheet's last ones, and they are not what
                # `undo` was pointing at. Drop the pointer rather than delete the
                # wrong rows.
                store.clear_write()
            except Exception as err:  # noqa: BLE001 - put it all back, try next tick
                for unwritten in batches[i:]:
                    store.enqueue(unwritten)
                store.log("queue_flush_failed", error=str(err), requeued=len(batches) - i)
                break


@asynccontextmanager
async def lifespan(_: FastAPI):
    gaps = config.missing()
    if gaps:
        log.warning("missing config: %s", ", ".join(gaps))
    else:
        try:
            await asyncio.to_thread(sheets.ensure_headers)
            log.info("sheet ready: %s / %s", config.SHEET_ID, config.SHEET_NAME)
        except Exception as err:  # noqa: BLE001 - keep serving, writes will queue
            log.warning("could not verify the sheet: %s", err)

    task = asyncio.create_task(flush_queue_forever())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="personal log bot", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": not config.missing(),
        "missing_config": config.missing(),
        "queued_writes": store.queue_depth(),
    }


@app.post("/message", response_model=Outgoing)
async def message(body: Incoming, x_bridge_token: str = Header(default="")) -> Outgoing:
    if config.BRIDGE_TOKEN and x_bridge_token != config.BRIDGE_TOKEN:
        raise HTTPException(status_code=401, detail="bad bridge token")

    # Nothing malformed should ever take the process down.
    try:
        reply = await handlers.handle(body.text, body.chat_jid)
    except Exception as err:  # noqa: BLE001
        log.exception("handler blew up")
        store.log("handler_error", chat=body.chat_jid, text=body.text, error=str(err))
        reply = "⚠️ Something broke handling that. It was not logged."
    return Outgoing(reply=reply)


def run() -> None:
    import uvicorn

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    uvicorn.run(app, host=config.BRAIN_HOST, port=config.BRAIN_PORT, log_level="info")


if __name__ == "__main__":
    run()
