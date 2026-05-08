"""Chat routes — interact with agents via chat."""

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Optional
import json
import asyncio
from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal

router = APIRouter()


class ChatMessage(BaseModel):
    agent_id: Optional[str] = None
    message: str
    conversation_id: Optional[str] = None


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
    await require_authorization(request, principal, "send", "chat", data.agent_id)
    mgr = request.app.state.agent_manager
    result = await mgr.chat(data.agent_id, data.message, data.conversation_id)
    return result


@router.websocket("/ws")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()
    conversation_id = None
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            agent_id = msg.get("agent_id")
            message = msg.get("message", "")
            conversation_id = msg.get("conversation_id", conversation_id)

            # Get agent manager from app state
            mgr = websocket.app.state.agent_manager
            result = await mgr.chat(agent_id, message, conversation_id)

            await websocket.send_text(json.dumps({
                "agent_id": result["agent_id"],
                "agent_name": result["agent_name"],
                "message": result["message"],
                "conversation_id": result["conversation_id"],
            }))
    except WebSocketDisconnect:
        pass
