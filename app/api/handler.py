"""
AWS Lambda entrypoint (legacy Mangum path).

Production Lambda now uses AWS Lambda Web Adapter + uvicorn
(see Dockerfile.lambda). Mangum remains available for BUFFERED
invoke mode experiments only — it cannot stream SSE.
"""

from mangum import Mangum

from app.api.server import app

# Not used by current Dockerfile.lambda (uvicorn via LWA).
handler = Mangum(app, lifespan="auto")
