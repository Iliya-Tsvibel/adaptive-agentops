from fastapi import FastAPI

app = FastAPI()


@app.get("/services/demo-api/health")
def demo_api_health() -> dict[str, str]:
    return {
        "service": "demo-api",
        "status": "unhealthy",
        "reason": "stalled_worker",
    }
