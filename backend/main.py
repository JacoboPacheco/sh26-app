import logging
import logging.handlers
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

load_dotenv()

# Mirror uvicorn's output (requests + tracebacks) to backend/server.log so it can be
# read by tools even when the server runs in someone else's terminal window.
_log_file = logging.handlers.RotatingFileHandler(
    Path(__file__).parent / os.getenv("LOG_FILE", "server.log"), maxBytes=2_000_000, backupCount=1, encoding="utf-8"
)
_log_file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
# "uvicorn.error" propagates to "uvicorn"; attaching to both would log each line twice
for _name in ("uvicorn", "uvicorn.access"):
    logging.getLogger(_name).addHandler(_log_file)

import auth
import items
import llm
import uploads
from database import Base, add_missing_columns, engine
from limiter import limiter

Base.metadata.create_all(bind=engine)
add_missing_columns()

app = FastAPI()

app.state.limiter = limiter


def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    # FastAPI errors carry `detail`, which the frontend shows; slowapi's default body
    # uses `error`, so a custom message (the AI daily cap's) never reached the UI.
    # A bare limit string like "30 per 1 minute" becomes a friendly line instead.
    message = exc.detail
    if re.match(r"^\d+ per \d+ ", message):
        message = "Too many requests — wait a minute and try again"
    response = JSONResponse({"detail": message}, status_code=429)
    return request.app.state.limiter._inject_headers(response, request.state.view_rate_limit)


app.add_exception_handler(RateLimitExceeded, rate_limit_handler)


# Runs before routing and body parsing, so a huge upload is refused without being
# downloaded. Defined before CORSMiddleware is added so the 413 still gets CORS headers.
@app.middleware("http")
async def reject_oversized_uploads(request: Request, call_next):
    if request.url.path == "/api/upload":
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > uploads.MAX_UPLOAD_BYTES + 4096:
            return JSONResponse({"detail": "File too large (max 5MB)"}, status_code=413)
    return await call_next(request)


allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(uploads.router)
app.include_router(llm.router)
app.include_router(items.router)  # EXAMPLE feature — remove with items.py


@app.get("/api/health")
def health():
    # touches the database so a deploy with a broken DB doesn't report green
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — any DB failure should surface here
        logging.getLogger("uvicorn.error").exception("health check: database unusable")
        return JSONResponse({"status": "db-error", "detail": "database unusable — see server.log"}, status_code=503)
    return {"status": "ok"}
