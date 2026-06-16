"""
Slack reaction_added handler.

Triggered when a user adds a reaction to a message. Filters for ✅ by the configured
approver in the configured channel, on a message authored by Transcript Bot itself.
Then parses the TRANSCRIPT_PAYLOAD block and reposts the cleaned transcript to the
output channel with the PDF attached.
"""

import os
import re
import logging
import httpx

log = logging.getLogger("transcript-bot.handler")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
APPROVE_CHANNEL_ID = os.getenv("APPROVE_CHANNEL_ID")
OUTPUT_CHANNEL_ID = os.getenv("OUTPUT_CHANNEL_ID")
APPROVER_USER_ID = os.getenv("APPROVER_USER_ID")
APPROVE_REACTION = os.getenv("APPROVE_REACTION", "white_check_mark")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Transcript Bot")

SLACK_API_BASE = "https://slack.com/api"

# HTML entities Slack inserts when fetching messages via API
HTML_ENTITIES = {
    "&lt;": "<", "&gt;": ">", "&amp;": "&",
    "&quot;": '"', "&#39;": "'",
}


def decode_html_entities(text: str) -> str:
    for entity, char in HTML_ENTITIES.items():
        text = text.replace(entity, char)
    return text


def parse_transcript_payload(text: str) -> dict | None:
    """
    Extract fields from the embedded TRANSCRIPT_PAYLOAD block.
    Returns None if no payload found.
    """
    text = decode_html_entities(text)

    payload_match = re.search(
        r"<!-- TRANSCRIPT_PAYLOAD([\s\S]*?)END_PAYLOAD -->", text
    )
    if not payload_match:
        log.warning("No TRANSCRIPT_PAYLOAD block found in message")
        return None

    block = payload_match.group(1)

    def extract_single(field: str) -> str:
        m = re.search(rf"^{field}: (.*)$", block, re.MULTILINE)
        return m.group(1).strip() if m else ""

    # Summary is multiline: from "SUMMARY:" to "END_PAYLOAD" or end
    summary_match = re.search(
        r"^SUMMARY: ([\s\S]*?)(?=\nEND_PAYLOAD|$)", block, re.MULTILINE
    )
    summary = summary_match.group(1).strip() if summary_match else ""

    # Keywords may be multiline (Fireflies splits them on newlines) → join with commas
    keywords_match = re.search(
        r"^KEYWORDS: ([\s\S]*?)(?=\n[A-Z_]+: |\nEND_PAYLOAD)", block, re.MULTILINE
    )
    keywords = (
        keywords_match.group(1).strip().replace("\n", ", ")
        if keywords_match
        else ""
    )

    return {
        "title": extract_single("TITLE"),
        "date": extract_single("DATE"),
        "host": extract_single("HOST"),
        "attendees": extract_single("ATTENDEES"),
        "keywords": keywords,
        "audio_url": extract_single("AUDIO_URL"),
        "transcript_url": extract_single("TRANSCRIPT_URL"),
        "summary": summary,
    }


async def get_message_text(channel: str, ts: str) -> dict | None:
    """Fetch a specific message from Slack by channel + timestamp."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{SLACK_API_BASE}/conversations.history",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            params={"channel": channel, "latest": ts, "inclusive": "true", "limit": 1},
        )
    data = r.json()
    if not data.get("ok"):
        log.error(f"conversations.history failed: {data.get('error')}")
        return None
    messages = data.get("messages", [])
    if not messages:
        log.warning(f"No message found at ts={ts} in channel={channel}")
        return None
    return messages[0]


async def post_to_output_channel(payload: dict) -> bool:
    """Post the cleaned transcript to the output channel."""
    text = (
        f"🎙️ *Nuovo transcript — {payload['title']}*\n\n"
        f"*Data:* {payload['date']}\n"
        f"*Host:* {payload['host']}\n"
        f"*Partecipanti:* {payload['attendees']}\n"
        f"*Keywords:* {payload['keywords']}\n"
        f"*Audio:* {payload['audio_url']}\n\n"
        f"*Summary:*\n{payload['summary']}"
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{SLACK_API_BASE}/chat.postMessage",
            headers={
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "channel": OUTPUT_CHANNEL_ID,
                "text": text,
                "username": BOT_USERNAME,
                "unfurl_links": False,
                "unfurl_media": False,
            },
        )
    data = r.json()
    if not data.get("ok"):
        log.error(f"chat.postMessage failed: {data.get('error')}")
        return False

    posted_ts = data.get("ts")
    log.info(f"Posted message to output channel, ts={posted_ts}")

    # If we have a transcript_url, upload it as a thread reply
    transcript_url = payload.get("transcript_url")
    if transcript_url:
        await attach_transcript_pdf(transcript_url, payload["title"], posted_ts)

    return True


async def attach_transcript_pdf(pdf_url: str, title: str, thread_ts: str) -> bool:
    """
    Download the transcript PDF from Fireflies and upload it to the output channel
    as a reply in the thread of the just-posted summary message.

    Uses Slack's files.upload (legacy but still functional and simpler than the new
    upload_external flow for our use case).
    """
    log.info(f"Downloading transcript PDF from {pdf_url[:80]}...")
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            pdf_resp = await client.get(pdf_url)
            pdf_resp.raise_for_status()
            pdf_bytes = pdf_resp.content
    except Exception as e:
        log.error(f"Failed to download PDF: {e}")
        return False

    filename = (
        "".join(c if c.isalnum() or c in "-_ " else "_" for c in title)[:80]
        + ".pdf"
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        # New Slack upload flow: getUploadURLExternal → upload → completeUploadExternal
        r1 = await client.get(
            f"{SLACK_API_BASE}/files.getUploadURLExternal",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            params={"filename": filename, "length": str(len(pdf_bytes))},
        )
        d1 = r1.json()
        if not d1.get("ok"):
            log.error(f"getUploadURLExternal failed: {d1.get('error')}")
            return False

        upload_url = d1["upload_url"]
        file_id = d1["file_id"]

        r2 = await client.post(upload_url, content=pdf_bytes)
        if r2.status_code != 200:
            log.error(f"Upload to {upload_url} failed: {r2.status_code}")
            return False

        r3 = await client.post(
            f"{SLACK_API_BASE}/files.completeUploadExternal",
            headers={
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "files": [{"id": file_id, "title": title}],
                "channel_id": OUTPUT_CHANNEL_ID,
                "thread_ts": thread_ts,
            },
        )
        d3 = r3.json()
        if not d3.get("ok"):
            log.error(f"completeUploadExternal failed: {d3.get('error')}")
            return False

    log.info(f"PDF attached to thread {thread_ts}")
    return True


async def handle_reaction_added(event: dict) -> None:
    """
    Main reaction_added handler. Filters and dispatches.
    """
    reaction = event.get("reaction")
    user = event.get("user")
    item = event.get("item", {})
    channel = item.get("channel")
    ts = item.get("ts")

    log.info(
        f"reaction_added: reaction={reaction} user={user} channel={channel} ts={ts}"
    )

    # Filter 1: correct reaction emoji
    if reaction != APPROVE_REACTION:
        log.info(f"Skipping: reaction {reaction} != {APPROVE_REACTION}")
        return

    # Filter 2: correct approve channel
    if channel != APPROVE_CHANNEL_ID:
        log.info(f"Skipping: channel {channel} != {APPROVE_CHANNEL_ID}")
        return

    # Filter 3: correct approver user
    if user != APPROVER_USER_ID:
        log.info(f"Skipping: user {user} != {APPROVER_USER_ID}")
        return

    # Fetch the message to extract the TRANSCRIPT_PAYLOAD
    message = await get_message_text(channel, ts)
    if not message:
        log.warning("Could not fetch message — skipping")
        return

    # Filter 4: must be a message authored by our own bot (sanity check)
    # The bot's posts come from the Zap 1 (Zapier app), so they have bot_id set.
    # Loose check: must contain the TRANSCRIPT_PAYLOAD marker.
    text = message.get("text", "")
    if "TRANSCRIPT_PAYLOAD" not in text and "TRANSCRIPT_PAYLOAD" not in decode_html_entities(text):
        log.info("Skipping: message does not contain TRANSCRIPT_PAYLOAD")
        return

    payload = parse_transcript_payload(text)
    if not payload:
        log.warning("Could not parse TRANSCRIPT_PAYLOAD")
        return

    log.info(f"Parsed payload for: {payload['title']}")

    success = await post_to_output_channel(payload)
    if success:
        log.info(f"Transcript published: {payload['title']}")
    else:
        log.error(f"Failed to publish transcript: {payload['title']}")
