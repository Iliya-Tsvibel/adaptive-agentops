"""Isolated local simulation, not a production mutation API.

Network reachability is not authorization. This target does not enforce AgentOps
approval policy or replay protection. Its restart counter is process-local and
resets when the application is restarted.
"""

from fastapi import FastAPI, HTTPException, Request

app = FastAPI()
app.state.restart_count = 0


@app.get("/services/demo-api/health")
def demo_api_health() -> dict[str, str]:
    return {
        "service": "demo-api",
        "status": "unhealthy",
        "reason": "stalled_worker",
    }


@app.post("/services/demo-api/restart")
async def demo_api_restart(request: Request) -> dict[str, str | bool]:
    if request.query_params:
        raise HTTPException(status_code=400, detail="Restart query parameters are not supported")
    async for chunk in request.stream():
        if chunk:
            raise HTTPException(status_code=400, detail="Restart request body must be empty")
    request.app.state.restart_count += 1
    return {"service": "demo-api", "status": "restart_requested", "simulated": True}
