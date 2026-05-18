"""FastAPI app for the orchestrator. Use uvicorn --factory mode against build_app."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from .config import Config
from .state import StateStore
from .worktree import WorktreeManager
from .dispatcher import WorkerDispatcher
from .memory import MemoryEmitter
from .router import TaskRouter
from .types import ApprovalDecision, InboundTaskRequest, TaskRecord, TaskState
from .workflows import load_workflows_dir
from .adapters.channels.telegram import TelegramChannelAdapter
from .adapters.notify.stdout import StdoutNotifyAdapter
from .adapters.pm.linear import LinearPMAdapter
from .adapters import ChannelRegistry, NotifyRegistry, PMRegistry


# Default: where the test repo lives. Configurable via FLYN_DEFAULT_TEST_REPO env.
import os as _os
DEFAULT_TEST_REPO_ENV = "FLYN_DEFAULT_TEST_REPO"


def _default_repo_for_workflow(workflow: str) -> Path:
    p = _os.environ.get(DEFAULT_TEST_REPO_ENV)
    if p:
        return Path(p)
    # Fall back to the orchestrator's home/test-repo
    home = _os.environ.get("FLYN_ORCHESTRATOR_HOME",
                            str(Path.home() / ".flyn" / "orchestrator"))
    return Path(home) / "test-repo"


def build_app(
    *,
    http_client: Optional[Any] = None,
    dispatcher: Optional[WorkerDispatcher] = None,
    reviewer_invoker=None,
    repo_path_for_workflow=None,
) -> FastAPI:
    """Factory. Test-friendly — accepts optional overrides for the http client + dispatcher + reviewer."""
    cfg = Config.from_env()
    cfg.home.mkdir(parents=True, exist_ok=True)
    (cfg.home / "data").mkdir(parents=True, exist_ok=True)
    cfg.workspaces_dir.mkdir(parents=True, exist_ok=True)
    cfg.captures_dir.mkdir(parents=True, exist_ok=True)

    store = StateStore(db_path=cfg.db_path)
    wt_mgr = WorktreeManager(workspaces_dir=cfg.workspaces_dir)
    dispatcher = dispatcher or WorkerDispatcher()  # default registry: claude-p pre-registered
    memory = MemoryEmitter(
        router_url=cfg.router_url,
        http=http_client or httpx.Client(timeout=httpx.Timeout(30.0)),
    )
    builder_prompt = Path(__file__).parent / "prompts" / "builder.md"
    repo_fn = repo_path_for_workflow or _default_repo_for_workflow

    # Load workflow policies from disk
    workflows_dir = Path(__file__).parent / "workflows"
    workflows = load_workflows_dir(workflows_dir)

    # Adapter registries (built before router so we can pass channels through)
    channels = ChannelRegistry()
    channels.register(TelegramChannelAdapter())
    notifies = NotifyRegistry()
    notifies.register(StdoutNotifyAdapter())
    pms = PMRegistry()
    pms.register(LinearPMAdapter())

    task_router = TaskRouter(
        store=store, dispatcher=dispatcher, worktree_mgr=wt_mgr,
        memory=memory, repo_path_for_workflow=repo_fn,
        builder_prompt_path=builder_prompt,
        reviewer_invoker=reviewer_invoker,
        channel_registry=channels,  # NEW: wires outbound notify
        workflows=workflows,
        config=cfg,
    )

    app = FastAPI(title="flyn-orchestrator", version="0.1.0")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": "flyn-orchestrator", "port": cfg.port,
                "router_url": cfg.router_url, "default_backend": cfg.default_backend}

    @app.post("/api/tasks/inbound")
    def inbound(req: InboundTaskRequest, background: BackgroundTasks) -> dict[str, Any]:
        task_id = task_router.accept(req)
        # Run task in background so the HTTP response returns immediately.
        background.add_task(task_router.run_task, task_id)
        return {"task_id": task_id, "state": "inbound", "accepted": True}

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> TaskRecord:
        t = store.get_task(task_id)
        if not t:
            raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
        return t

    @app.post("/api/tasks/{task_id}/run")
    def run_task_route(task_id: str) -> TaskRecord:
        """Explicit run trigger — used by tests; production also routes through this."""
        return task_router.run_task(task_id)

    @app.post("/api/tasks/{task_id}/approve")
    def approve(task_id: str, decision: ApprovalDecision) -> TaskRecord:
        """Accept or reject a task pending human approval."""
        t = store.get_task(task_id)
        if not t:
            raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
        try:
            return task_router.handle_approval(task_id, decision)
        except NotImplementedError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel(task_id: str) -> dict[str, Any]:
        t = store.get_task(task_id)
        if not t:
            raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
        store.transition(t.task_id, t.state, TaskState.CANCELLED,
                         actor="user", reason="cancel via REST")
        return {"ok": True, "task_id": task_id, "state": "cancelled"}

    return app
