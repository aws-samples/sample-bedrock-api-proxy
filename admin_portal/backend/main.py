"""
Admin Portal Backend - FastAPI Application

Independent FastAPI server for the admin portal running on port 8005.
Serves both the API and the static frontend files.
"""
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Add parent directory to path to import from app
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Load environment variables from .env files
from dotenv import load_dotenv

# Load root .env first (DynamoDB, AWS settings)
root_env_path = Path(__file__).parent.parent.parent / ".env"
if root_env_path.exists():
    load_dotenv(root_env_path)
    print(f"Loaded environment from: {root_env_path}")

# Load local .env second (Cognito settings - overrides root)
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path, override=True)
    print(f"Loaded environment from: {env_path}")

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from admin_portal.backend.api import auth, api_keys, pricing, dashboard, model_mapping
from admin_portal.backend.api import provider_keys, providers, routing, failover
from admin_portal.backend.middleware.cognito_auth import CognitoAuthMiddleware
from admin_portal.backend.services.usage_aggregator import start_aggregator, stop_aggregator

# Configuration
ADMIN_PORT = 8005
API_PREFIX = "/api"
USAGE_AGGREGATION_INTERVAL = 300  # 5 minutes

# Static files directory (frontend build output)
FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"
SERVE_STATIC = os.environ.get("SERVE_STATIC_FILES", "true").lower() == "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    print(f"Admin Portal starting on port {ADMIN_PORT}...")
    print(f"Frontend directory: {FRONTEND_DIR}")
    print(f"Serve static files: {SERVE_STATIC}")

    # Start usage aggregator background task
    start_aggregator(interval_seconds=USAGE_AGGREGATION_INTERVAL)

    yield

    # Stop usage aggregator
    stop_aggregator()
    print("Admin Portal shutting down...")


# Create FastAPI application
app = FastAPI(
    title="Anthropic API Proxy - Admin Portal",
    description="Administration interface for managing API keys and model pricing",
    version="1.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development; restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add authentication middleware (Cognito JWT validation)
app.add_middleware(CognitoAuthMiddleware)

# Include API routers
app.include_router(auth.router, prefix=f"{API_PREFIX}/auth", tags=["Authentication"])
app.include_router(dashboard.router, prefix=f"{API_PREFIX}/dashboard", tags=["Dashboard"])
app.include_router(api_keys.router, prefix=f"{API_PREFIX}/keys", tags=["API Keys"])
app.include_router(pricing.router, prefix=f"{API_PREFIX}/pricing", tags=["Model Pricing"])
app.include_router(model_mapping.router, prefix=f"{API_PREFIX}/model-mapping", tags=["Model Mapping"])
app.include_router(provider_keys.router, prefix=f"{API_PREFIX}/provider-keys", tags=["Provider Keys"])
app.include_router(providers.router, prefix=f"{API_PREFIX}/providers", tags=["Providers"])
app.include_router(routing.router, prefix=f"{API_PREFIX}/routing", tags=["Routing"])
app.include_router(failover.router, prefix=f"{API_PREFIX}/failover", tags=["Failover"])


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "admin-portal"}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger = logging.getLogger(__name__)
    logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "An internal error occurred. Please try again later.",
        },
    )


# Static file serving for frontend (when bundled in production)
if SERVE_STATIC and FRONTEND_DIR.exists():
    print(f"Mounting static files from {FRONTEND_DIR}")

    # Mount static assets (js, css, images) at /admin/assets/
    assets_dir = FRONTEND_DIR / "assets"
    if assets_dir.exists():
        app.mount("/admin/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    # Serve index.html for /admin and /admin/ routes (SPA catch-all)
    @app.get("/admin")
    @app.get("/admin/")
    @app.get("/admin/{path:path}")
    async def serve_spa(request: Request, path: str = ""):
        """
        Serve the SPA frontend.
        For any /admin/* route that doesn't match an API endpoint,
        return index.html and let React Router handle the routing.
        """
        # Skip API routes
        if path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "message": f"API endpoint not found: /admin/{path}"}
            )

        # Check if requesting a specific static file
        static_file = FRONTEND_DIR / path
        if path and static_file.exists() and static_file.is_file():
            return FileResponse(static_file)

        # Otherwise, return index.html for SPA routing
        index_file = FRONTEND_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)

        return HTMLResponse(
            content="<html><body><h1>Admin Portal</h1><p>Frontend not built. Run 'npm run build' in frontend directory.</p></body></html>",
            status_code=200
        )
else:
    # Redirect /admin to message when static files not available
    @app.get("/admin")
    @app.get("/admin/")
    @app.get("/admin/{path:path}")
    async def admin_not_available(path: str = ""):
        """Return message when frontend is not bundled."""
        return HTMLResponse(
            content="<html><body><h1>Admin Portal</h1><p>Frontend not available. In development, run the frontend dev server separately.</p></body></html>",
            status_code=200
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=ADMIN_PORT,
        reload=True,
    )
