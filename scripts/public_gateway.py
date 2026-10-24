"""Single-port gateway for platforms such as Render.

Render exposes one public port. The coordinator speaks uAgents on 8101 while
the RSVP application runs on 8104, so this small gateway routes both public
surfaces without exposing internal agent ports.
"""

from __future__ import annotations

import argparse

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
import uvicorn


def create_gateway(coordinator_url: str, rsvp_url: str) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    coordinator_paths = {"/submit", "/agent_info"}

    @app.api_route(
        "/{path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def proxy(path: str, request: Request) -> Response:
        route = "/" + path
        # Agentverse/uAgents integrations have used both the transport URL
        # itself (/submit) and the public endpoint root during verification.
        # Accept POST at either location, while keeping GET / a health/status
        # response for Render and browsers.
        if route == "/" and request.method in {"GET", "HEAD"}:
            return JSONResponse(
                {"service": "eventpilot-gateway", "status": "ok", "routes": ["/submit", "/health"]}
            )
        upstream = coordinator_url if route in coordinator_paths or (route == "/" and request.method == "POST") else rsvp_url
        url = upstream + route
        body = await request.body()
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in {"host", "content-length"}
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                upstream_response = await client.request(
                    request.method, url, content=body, headers=headers
                )
        except httpx.HTTPError:
            return JSONResponse({"error": "upstream unavailable"}, status_code=503)
        response_headers = {
            key: value
            for key, value in upstream_response.headers.items()
            if key.lower() not in {"content-length", "transfer-encoding", "connection"}
        }
        return Response(
            content=upstream_response.content,
            status_code=upstream_response.status_code,
            headers=response_headers,
            media_type=upstream_response.headers.get("content-type"),
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(create_gateway("http://127.0.0.1:8101", "http://127.0.0.1:8104"), host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
