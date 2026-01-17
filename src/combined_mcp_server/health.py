"""
ECS health check HTTP server.

Provides liveness, readiness, and status endpoints for container orchestration.
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from aiohttp import web

from combined_mcp_server.config import get_settings
from combined_mcp_server.utils.logging import get_logger

logger = get_logger(__name__)


class HealthCheckServer:
    """
    HTTP health check server for ECS.

    Runs in a background thread and provides:
    - /health - Liveness probe (always returns 200 if process alive)
    - /ready - Readiness probe (returns 200 when fully initialized)
    - /status - Detailed status JSON with component health
    """

    def __init__(self) -> None:
        """Initialize health check server."""
        settings = get_settings()
        self._host = settings.health_check.host
        self._port = settings.health_check.port
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._start_time = time.time()

        # Component status tracking
        self._components: dict[str, dict[str, Any]] = {
            "postgres": {"ready": False, "error": None},
            "vectorstore": {"ready": False, "document_count": 0, "error": None},
            "redshift": {"ready": False, "error": None},
        }

        logger.info(
            "Health check server initialized",
            host=self._host,
            port=self._port,
        )

    def set_component_status(
        self,
        component: str,
        ready: bool,
        error: str | None = None,
        **extra: Any,
    ) -> None:
        """
        Update component status.

        Args:
            component: Component name
            ready: Whether the component is ready
            error: Optional error message
            **extra: Additional status fields
        """
        self._components[component] = {
            "ready": ready,
            "error": error,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
        logger.debug(
            "Component status updated",
            component=component,
            ready=ready,
            error=error,
        )

    async def _handle_health(self, _request: web.Request) -> web.Response:
        """
        Handle liveness probe.

        Always returns 200 if the process is running.
        """
        return web.json_response(
            {
                "status": "alive",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def _handle_ready(self, _request: web.Request) -> web.Response:
        """
        Handle readiness probe.

        Returns 200 if all required components are ready.
        """
        vectorstore_ready = self._components.get("vectorstore", {}).get("ready", False)
        postgres_ready = self._components.get("postgres", {}).get("ready", False)

        # Consider ready if postgres is connected and vectorstore is initialized
        is_ready = postgres_ready and vectorstore_ready

        status_code = 200 if is_ready else 503

        return web.json_response(
            {
                "ready": is_ready,
                "components": {
                    name: comp.get("ready", False)
                    for name, comp in self._components.items()
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            status=status_code,
        )

    async def _handle_status(self, _request: web.Request) -> web.Response:
        """
        Handle detailed status request.

        Returns comprehensive status information.
        """
        uptime_seconds = time.time() - self._start_time

        return web.json_response(
            {
                "status": "running",
                "version": "1.0.0",
                "uptime_seconds": uptime_seconds,
                "components": self._components,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def start(self) -> None:
        """Start the health check HTTP server."""
        self._app = web.Application()
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/ready", self._handle_ready)
        self._app.router.add_get("/status", self._handle_status)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()

        logger.info(
            "Health check server started",
            host=self._host,
            port=self._port,
        )

    async def stop(self) -> None:
        """Stop the health check HTTP server."""
        if self._runner:
            await self._runner.cleanup()
            logger.info("Health check server stopped")


# Singleton instance
_health_server: HealthCheckServer | None = None


def get_health_server() -> HealthCheckServer:
    """Get health check server singleton."""
    global _health_server
    if _health_server is None:
        _health_server = HealthCheckServer()
    return _health_server
