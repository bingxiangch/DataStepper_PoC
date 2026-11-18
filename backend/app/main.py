"""FastAPI application for the Data Agent Debugger."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import FRONTEND_DIST, live_status
from .runtime import DebuggerError, DebuggerService

logger = logging.getLogger("data_agent_debugger")


class CreateRunRequest(BaseModel):
    task: str = ""
    mode: Literal["scripted", "live"] = "scripted"
    dataset: str = "olist"


class CommandRequest(BaseModel):
    command_id: Optional[str] = None


class FeedbackRequest(BaseModel):
    step_id: str
    text: str
    command_id: Optional[str] = None


class ReplayRequest(BaseModel):
    revision_id: Optional[str] = None
    command_id: Optional[str] = None


class RejectApprovalRequest(BaseModel):
    reason: str = ""
    command_id: Optional[str] = None


class BreakpointInterpretRequest(BaseModel):
    text: str = ""


class BreakpointActivateRequest(BaseModel):
    text: str = ""
    command_id: Optional[str] = None


class BreakpointStateRequest(BaseModel):
    active: bool
    command_id: Optional[str] = None


def create_app(service: Optional[DebuggerService] = None) -> FastAPI:
    app = FastAPI(
        title="Data Agent Debugger",
        version="0.9.0",
        description="Interactive debugging for LLM data agents — inspect semantics, revise, replay, compare.",
    )
    app.state.service = service or DebuggerService()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def cache_control(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/assets/"):
            # Vite emits content-hashed filenames, so a URL never changes.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif not path.startswith("/api/"):
            # The entry HTML must be revalidated after a rebuild; otherwise the
            # browser keeps requesting a bundle hash that no longer exists.
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(DebuggerError)
    async def debugger_error_handler(request: Request, exc: DebuggerError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled API error")
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": f"The controller failed: {exc}",
                }
            },
        )

    def svc() -> DebuggerService:
        return app.state.service

    # ------------------------------------------------------------------ config
    @app.get("/api/health")
    def health():
        return {"ok": True, "live": live_status()}

    @app.get("/api/config")
    def config():
        return svc().config()

    @app.get("/api/runs")
    def list_runs():
        return {"runs": svc().list_runs()}

    # ------------------------------------------------------------------- runs
    @app.post("/api/runs", status_code=201)
    def create_run(request: CreateRunRequest):
        return svc().create_run(request.task, request.mode, request.dataset)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        return svc().get_view(run_id)

    @app.get("/api/runs/{run_id}/steps/{step_id}")
    def get_step(run_id: str, step_id: str):
        return svc().get_step_view(run_id, step_id)

    @app.get("/api/runs/{run_id}/compare")
    def compare(run_id: str, child_run_id: Optional[str] = Query(default=None)):
        return svc().compare_runs(run_id, child_run_id)

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: str, limit: int = Query(default=50, ge=1, le=500)):
        run = svc()._get(run_id)
        return {"events": svc().events.tail(run.id, limit), "count": run.event_count}

    # ------------------------------------------------------------ execution
    @app.post("/api/runs/{run_id}/step-into")
    def step_into(run_id: str, request: Optional[CommandRequest] = None):
        return svc().step_into(run_id, request.command_id if request else None)

    @app.post("/api/runs/{run_id}/step-over")
    def step_over(run_id: str, request: Optional[CommandRequest] = None):
        return svc().step_over(run_id, request.command_id if request else None)

    @app.post("/api/runs/{run_id}/step-out")
    def step_out(run_id: str, request: Optional[CommandRequest] = None):
        return svc().step_out(run_id, request.command_id if request else None)

    @app.post("/api/runs/{run_id}/continue")
    def continue_run(run_id: str, request: Optional[CommandRequest] = None):
        return svc().continue_run(run_id, request.command_id if request else None)

    @app.post("/api/runs/{run_id}/pause")
    def pause(run_id: str, request: Optional[CommandRequest] = None):
        return svc().pause(run_id, request.command_id if request else None)

    # -------------------------------------------------------------- feedback
    @app.post("/api/runs/{run_id}/feedback")
    def feedback(run_id: str, request: FeedbackRequest):
        return svc().feedback(run_id, request.step_id, request.text, request.command_id)

    @app.post("/api/runs/{run_id}/revisions/{revision_id}/accept")
    def accept_revision(run_id: str, revision_id: str, request: Optional[CommandRequest] = None):
        return svc().accept_revision(run_id, revision_id, request.command_id if request else None)

    @app.post("/api/runs/{run_id}/revisions/{revision_id}/reject")
    def reject_revision(run_id: str, revision_id: str, request: Optional[CommandRequest] = None):
        return svc().reject_revision(run_id, revision_id, request.command_id if request else None)

    @app.post("/api/runs/{run_id}/replay")
    def replay(run_id: str, request: Optional[ReplayRequest] = None):
        payload = request or ReplayRequest()
        return svc().replay(run_id, payload.revision_id, payload.command_id)

    # --------------------------------------------------------------- approval
    @app.post("/api/runs/{run_id}/approval/approve")
    def approve(run_id: str, request: Optional[CommandRequest] = None):
        return svc().approve(run_id, request.command_id if request else None)

    @app.post("/api/runs/{run_id}/approval/reject")
    def reject_approval(run_id: str, request: Optional[RejectApprovalRequest] = None):
        payload = request or RejectApprovalRequest()
        return svc().reject_approval(run_id, payload.reason, payload.command_id)

    # ------------------------------------------------------------ breakpoints
    @app.post("/api/runs/{run_id}/breakpoints/interpret")
    def interpret_breakpoint(run_id: str, request: BreakpointInterpretRequest):
        return svc().interpret_breakpoint(run_id, request.text)

    @app.post("/api/runs/{run_id}/breakpoints", status_code=201)
    def add_breakpoint(run_id: str, request: BreakpointActivateRequest):
        return svc().add_breakpoint(run_id, request.text, request.command_id)

    @app.delete("/api/runs/{run_id}/breakpoints/{breakpoint_id}")
    def remove_breakpoint(
        run_id: str,
        breakpoint_id: str,
        request: Optional[CommandRequest] = None,
    ):
        return svc().remove_breakpoint(
            run_id, breakpoint_id, request.command_id if request else None
        )

    @app.patch("/api/runs/{run_id}/breakpoints/{breakpoint_id}")
    def set_breakpoint_active(
        run_id: str,
        breakpoint_id: str,
        request: BreakpointStateRequest,
    ):
        return svc().set_breakpoint_active(
            run_id, breakpoint_id, request.active, request.command_id
        )

    # ----------------------------------------------------------------- static
    dist = Path(FRONTEND_DIST)
    if dist.exists() and (dist / "index.html").exists():
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_frontend(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API route not found.")
            if not full_path:
                return FileResponse(dist / "index.html")
            candidate = (dist / full_path).resolve()
            try:
                candidate.relative_to(dist.resolve())
            except ValueError:
                raise HTTPException(status_code=404, detail="Not found.")
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        def frontend_missing():
            return HTMLResponse(
                "<html><body style='font-family:system-ui;padding:2rem'>"
                "<h1>Data Agent Debugger API</h1>"
                "<p>The frontend bundle is not built. Run <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>, "
                "or use the Vite dev server at <code>http://localhost:5173</code>.</p>"
                "<p><a href='/docs'>API documentation</a></p>"
                "</body></html>"
            )

    return app


app = create_app()
