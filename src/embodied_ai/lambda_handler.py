"""AWS Lambda entrypoint for the FastAPI application."""

from __future__ import annotations

from mangum import Mangum

from .web_app import create_app


app = create_app()
handler = Mangum(app, lifespan="on")
