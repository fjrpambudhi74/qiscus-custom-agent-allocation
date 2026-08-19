import logging

from fastapi import FastAPI

from app.database import create_db_and_tables
from app.routers import health, webhooks
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Qiscus Custom Agent Allocation")

app.include_router(health.router)
app.include_router(webhooks.router)


@app.on_event("startup")
def on_startup() -> None:
    create_db_and_tables()
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown() -> None:
    stop_scheduler()
