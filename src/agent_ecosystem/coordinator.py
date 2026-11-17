"""Coordinator: agent discovery + Phase 2 workflow orchestration REST API.

Original discovery endpoints (/health, /agents, /agents/discover) are kept
unchanged. New endpoints (docs/PHASE1_API_AND_DATA_DESIGN §6):

    POST /workflows                  {"goal": ...}          -> 202 {workflow_id}
    GET  /workflows                  ?limit=&status=        -> list
    GET  /workflows/{id}                                    -> full snapshot
    GET  /workflows/{id}/result                             -> 200 | 409
    POST /workflows/{id}/cancel                             -> 200 {status}

Run:  python -m agent_ecosystem.coordinator --port 8000
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field

from agent_ecosystem.settings import get_settings


class GoalRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=8000)


def create_app(service: Any = None) -> FastAPI:
    """Build the Coordinator app; ``service`` (WorkflowService) is injectable
    for tests. The real service is constructed lazily on first use so importing
    this module stays cheap and offline-safe."""
    app = FastAPI(title="Agent Coordinator")
    app.state.service = service
    app.state.background_tasks = set()

    def get_service() -> Any:
        if app.state.service is None:
            from agent_ecosystem.orchestration.service import build_default_service

            app.state.service = build_default_service()
        return app.state.service

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "coordinator"}

    @app.get("/agents")
    async def agents():
        settings = get_settings()
        return {
            "research": settings.research_agent_url,
            "coding": settings.coding_agent_url,
            "analysis": settings.analysis_agent_url,
        }

    @app.get("/agents/discover")
    async def discover():
        settings = get_settings()
        urls = {
            "research": settings.research_agent_url,
            "coding": settings.coding_agent_url,
            "analysis": settings.analysis_agent_url,
        }
        result = {}
        async with httpx.AsyncClient(timeout=5.0) as client:
            for name, base_url in urls.items():
                try:
                    response = await client.get(f"{base_url}/.well-known/agent-card.json")
                    response.raise_for_status()
                    result[name] = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    result[name] = {"error": str(exc)}
        return result

    @app.post("/workflows", status_code=202)
    async def submit_workflow(request: GoalRequest):
        svc = get_service()
        workflow = svc.create_workflow(request.goal)
        task = asyncio.create_task(svc.run_workflow(workflow), name=workflow.workflow_id)
        app.state.background_tasks.add(task)
        task.add_done_callback(app.state.background_tasks.discard)
        return {
            "workflow_id": workflow.workflow_id,
            "status": workflow.status.value,
        }

    @app.get("/workflows")
    async def list_workflows(limit: int = 50, status: str | None = None):
        svc = get_service()
        workflows = svc.store.list_workflows(limit=limit)
        if status:
            workflows = [w for w in workflows if w.status.value == status]
        return [
            {
                "workflow_id": w.workflow_id,
                "status": w.status.value,
                "goal": w.goal[:120],
                "created_at": w.created_at.isoformat(),
                "error": w.error,
            }
            for w in workflows
        ]

    @app.get("/workflows/{workflow_id}")
    async def workflow_status(workflow_id: str):
        from fastapi.responses import JSONResponse

        svc = get_service()
        snapshot = svc.status(workflow_id)
        if snapshot is None:
            return JSONResponse({"error": "unknown workflow"}, status_code=404)
        return snapshot

    @app.get("/workflows/{workflow_id}/result")
    async def workflow_result(workflow_id: str):
        from fastapi.responses import JSONResponse

        svc = get_service()
        workflow = svc.store.get_workflow(workflow_id)
        if workflow is None:
            return JSONResponse({"error": "unknown workflow"}, status_code=404)
        if workflow.status.value != "completed" or not workflow.final_result:
            return JSONResponse(
                {"status": workflow.status.value, "error": workflow.error}, status_code=409
            )
        return {"status": "completed", "final_result": workflow.final_result}

    @app.post("/workflows/{workflow_id}/cancel")
    async def cancel_workflow(workflow_id: str):
        from fastapi.responses import JSONResponse

        svc = get_service()
        if not svc.cancel(workflow_id):
            return JSONResponse({"error": "unknown workflow"}, status_code=404)
        return {"status": "cancel_requested", "workflow_id": workflow_id}

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Coordinator with DAG workflow orchestration")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
