"""Chat routes — interact with agents via chat."""

import json

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel

from cyber_team.api.authorization import require_authorization
from cyber_team.api.rate_limit import enforce_rate_limit, rate_limiter
from cyber_team.api.security import (
    Principal,
    consume_websocket_ticket,
    decode_token,
    get_current_principal,
)
from cyber_team.config import settings

router = APIRouter()


class ChatMessage(BaseModel):
    agent_id: str | None = None
    message: str
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    agent_id: str
    agent_name: str
    message: str
    conversation_id: str


@router.post("/send", response_model=ChatResponse)
async def send_chat_message(
    data: ChatMessage,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await enforce_rate_limit(
        request,
        "chat.send",
        settings.rate_limit_chat_per_minute,
        subject=principal.subject,
    )
    await require_authorization(request, principal, "send", "chat", data.agent_id)
    mgr = request.app.state.agent_manager
    try:
        return await mgr.chat(data.agent_id, data.message, data.conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.websocket("/ws")
async def websocket_chat(websocket: WebSocket):
    principal = _principal_from_websocket(websocket)
    if not principal:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    conversation_id = None
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"error": "Invalid JSON message"}))
                continue
            agent_id = msg.get("agent_id")
            message = msg.get("message", "")
            conversation_id = msg.get("conversation_id", conversation_id)
            try:
                rate_limiter.check(
                    f"chat.ws:{principal.subject}",
                    settings.rate_limit_chat_per_minute,
                )
            except HTTPException as exc:
                await websocket.send_text(json.dumps({"error": exc.detail}))
                continue

            decision = await websocket.app.state.authorization_service.authorize(
                principal=principal,
                action="send",
                resource_type="chat",
                resource_id=agent_id,
            )
            if not decision.allowed:
                await websocket.send_text(json.dumps({"error": "Action is not authorized"}))
                continue

            # Get agent manager from app state
            mgr = websocket.app.state.agent_manager
            try:
                result = await mgr.chat(agent_id, message, conversation_id)
            except ValueError as exc:
                await websocket.send_text(json.dumps({"error": str(exc)}))
                continue

            await websocket.send_text(
                json.dumps({
                    "agent_id": result["agent_id"],
                    "agent_name": result["agent_name"],
                    "message": result["message"],
                    "conversation_id": result["conversation_id"],
                })
            )
    except WebSocketDisconnect:
        pass


def _principal_from_websocket(websocket: WebSocket) -> Principal | None:
    ticket = websocket.query_params.get("ticket")
    if ticket:
        return consume_websocket_ticket(ticket)

    auth_header = websocket.headers.get("authorization", "")
    token = None
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    if not token:
        return None
    try:
        return decode_token(token, expected_type="access")
    except HTTPException:
        return None
