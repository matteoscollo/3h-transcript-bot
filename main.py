"""
3h-transcript-bot — Slack App per pre-approvazione transcript Fireflies.

Riceve eventi Slack tramite Events API:
- reaction_added → se ✅ messa da RQ in #rq-approve-transcripts su un messaggio del
  Transcript Bot, parsa il TRANSCRIPT_PAYLOAD embedato e ripubblica il transcript
  pulito in #transcripts-fetch-bot con il PDF allegato.

Architettura:
- Zap 1 (Zapier) resta: Fireflies → Post in #rq-approve-transcripts con payload nascosto
- Questa app sostituisce il vecchio Zap 2 (reaction trigger via Slack App workspace-wide)
"""

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from slack_handler import handle_reaction_added

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
)
log = logging.getLogger("transcript-bot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Transcript Bot starting up")
    # Validate required env vars at startup
    required = ["SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "APPROVE_CHANNEL_ID",
                "OUTPUT_CHANNEL_ID", "APPROVER_USER_ID"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        log.error(f"Missing env vars: {missing}")
        raise RuntimeError(f"Missing required env vars: {missing}")
    log.info("All required env vars present")
    yield
    log.info("Transcript Bot shutting down")


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def health():
    """Health check endpoint for Railway / uptime monitoring."""
    return {"status": "ok", "service": "3h-transcript-bot"}


@app.post("/slack/events")
async def slack_events(request: Request):
    """
    Slack Events API endpoint.
    Handles URL verification challenge and event callbacks.
    """
    body = await request.json()

    # Step 1: URL verification (one-time, when configuring Slack App)
    if body.get("type") == "url_verification":
        log.info("Slack URL verification challenge received")
        return PlainTextResponse(body.get("challenge", ""))

    # Step 2: event callback
    if body.get("type") == "event_callback":
        event = body.get("event", {})
        event_type = event.get("type")
        log.info(f"Event received: {event_type}")

        if event_type == "reaction_added":
            # Run handler async-safe; respond 200 to Slack within 3s either way
            try:
                await handle_reaction_added(event)
            except Exception as e:
                log.exception(f"Error handling reaction_added: {e}")
            return JSONResponse({"ok": True})

        # Other event types: log and ignore
        log.info(f"Ignoring event type: {event_type}")
        return JSONResponse({"ok": True})

    log.warning(f"Unknown Slack body type: {body.get('type')}")
    raise HTTPException(status_code=400, detail="Unknown event type")
