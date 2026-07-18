"""
AWS Lambda entrypoint.

Mangum adapts the FastAPI ASGI app so the same code runs under Lambda
Function URLs (and later under uvicorn on ECS/EC2 without this handler).
"""

from mangum import Mangum

from app.api.server import app

# Lambda invokes this handler; local/ECS use uvicorn on app.api.server:app instead.
handler = Mangum(app, lifespan="auto")
