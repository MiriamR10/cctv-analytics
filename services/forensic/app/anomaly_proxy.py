"""Reverse proxy to the anomaly service, so the UI talks to a single origin."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from .config import settings

router = APIRouter(prefix="/anomaly-api", include_in_schema=False)

# Training and back-scoring 14 days can take a while on a small server.
TIMEOUT = httpx.Timeout(300.0, connect=5.0)
FORWARDED_HEADERS = ("content-type", "accept")


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(path: str, request: Request) -> Response:
    if not settings.anomaly_url:
        raise HTTPException(503, "anomaly service is not configured (ANOMALY_URL)")
    url = f"{settings.anomaly_url.rstrip('/')}/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARDED_HEADERS}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            upstream = await client.request(
                request.method,
                url,
                params=request.query_params,
                content=await request.body(),
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"anomaly service unreachable: {exc}") from exc
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )
