"""桌面云效认证的独立 AG-UI 路由。"""
from typing import Any
from fastapi import APIRouter, Body, Header
from fastapi.responses import StreamingResponse
from app.protocols.authentication import build_authentication_stream

router = APIRouter(tags=["authentication"])


@router.post("/authentication/run")
async def run_authentication(payload: dict[str, Any] = Body(...), accept: str | None = Header(default="text/event-stream")) -> StreamingResponse:
    """运行不依赖 LLM 或登录前用户 token 的桌面认证动作。"""
    return StreamingResponse(build_authentication_stream(payload, accept), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
