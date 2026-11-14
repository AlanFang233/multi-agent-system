"""Coordinator bootstrap only: discovery and health, not yet task orchestration."""

import httpx
from fastapi import FastAPI

from agent_ecosystem.settings import get_settings

app = FastAPI(title="Agent Coordinator - Starter")


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
