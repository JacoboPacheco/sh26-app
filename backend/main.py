import logging
import logging.handlers
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

# explicit path: under --reload the worker can't locate its caller, so a bare load_dotenv()
# searches the current directory and misses backend/.env when started from the repo root
load_dotenv(Path(__file__).parent / ".env")

# Mirror uvicorn's output (requests + tracebacks) to backend/server.log so it can be
# read by tools even when the server runs in someone else's terminal window.
_log_file = logging.handlers.RotatingFileHandler(
    Path(__file__).parent / os.getenv("LOG_FILE", "server.log"), maxBytes=2_000_000, backupCount=1, encoding="utf-8"
)
_log_file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
# "uvicorn.error" propagates to "uvicorn"; attaching to both would log each line twice
for _name in ("uvicorn", "uvicorn.access"):
    logging.getLogger(_name).addHandler(_log_file)

import analyst
import agreement
import ask
import auth
import baked
import briefing
import bulletin
import catalog
import comment
import costs
import danger
import evidence
import fixit
import forecast
import grid
import gridlock
import gridreader
import harden
import hospitals
import hurricane
import leadtimes
import llm
import narrate
import negotiate
import plan_agents
import planner
import plants
import scenarios
import service_rules
import sitereport
import show
import sources
import timelapse
import towns
import unlock
import uploads
import views
import vote
import voice
from database import Base, add_missing_columns, engine
from limiter import limiter

Base.metadata.create_all(bind=engine)
add_missing_columns()
baked.load()  # precomputed Strengthen studies (backend/demo/strengthen): a deploy opens with answers

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


def validation_handler(request: Request, exc: RequestValidationError):
    # FastAPI's default echoes the input back; a bare NaN/Infinity body can't be serialized and
    # became a 500. Keep FastAPI's shape (a list the frontend already formats) minus "input".
    errors = [{k: v for k, v in e.items() if k in ("loc", "msg", "type")} for e in exc.errors()]
    return JSONResponse({"detail": errors or "The request body is not valid for this endpoint."}, status_code=422)


app.add_exception_handler(RequestValidationError, validation_handler)


# Runs before routing and body parsing, so a huge upload is refused without being
# downloaded. Defined before CORSMiddleware is added so the 413 still gets CORS headers.
@app.middleware("http")
async def reject_oversized_uploads(request: Request, call_next):
    if request.url.path == "/api/upload":
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > uploads.MAX_UPLOAD_BYTES + 4096:
            return JSONResponse({"detail": "File too large (max 5MB)"}, status_code=413)
    return await call_next(request)


# /api/grid/headroom builds a whole model per load level, and grid.py is fingerprinted (a change
# there means a rebake), so its per-visitor limit lives here: 120 a minute, like the cascade's.
_HEADROOM_LIMIT = 120
_headroom_hits: dict[str, list[float]] = {}


@app.middleware("http")
async def limit_headroom(request: Request, call_next):
    if request.url.path == "/api/grid/headroom":
        now = time.monotonic()
        ip = request.client.host if request.client else "?"
        hits = [t for t in _headroom_hits.get(ip, ()) if now - t < 60]
        if len(hits) >= _HEADROOM_LIMIT:
            return JSONResponse({"detail": "Too many requests — wait a minute and try again"}, status_code=429)
        hits.append(now)
        _headroom_hits[ip] = hits
        if len(_headroom_hits) > 5000:  # forget visitors idle for a minute
            for k in [k for k, v in _headroom_hits.items() if not v or now - v[-1] >= 60]:
                del _headroom_hits[k]
    return await call_next(request)


allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

app.add_middleware(GZipMiddleware, minimum_size=1000)  # the grid and Build plans payloads shrink ~10x
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(uploads.router)
app.include_router(llm.router)
app.include_router(grid.router)
app.include_router(evidence.router)
app.include_router(scenarios.router)
app.include_router(hurricane.router)
app.include_router(harden.router)
app.include_router(fixit.router)
app.include_router(bulletin.router)
app.include_router(towns.router)
app.include_router(forecast.router)
app.include_router(planner.router)
app.include_router(catalog.router)
app.include_router(costs.router)
app.include_router(hospitals.router)
app.include_router(briefing.router)
app.include_router(voice.router)
app.include_router(plants.router)
app.include_router(ask.router)
app.include_router(gridlock.router)
app.include_router(gridreader.router)
app.include_router(agreement.router)
app.include_router(negotiate.router)
app.include_router(danger.router)
app.include_router(vote.router)
app.include_router(comment.router)
app.include_router(analyst.router)
app.include_router(unlock.router)
app.include_router(plan_agents.router)
app.include_router(leadtimes.router)
app.include_router(narrate.router)
app.include_router(views.router)
app.include_router(sitereport.router)
app.include_router(show.router)
app.include_router(sources.router)
app.include_router(timelapse.router)
app.include_router(service_rules.router)


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
