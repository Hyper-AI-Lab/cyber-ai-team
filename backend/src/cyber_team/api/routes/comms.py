"""Communication routes — telephony, SMS, email, messaging."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from typing import Optional
from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal

router = APIRouter()


class CallRequest(BaseModel):
    to_number: str
    agent_id: Optional[str] = None
    context: str = ""
    from_number: Optional[str] = None


class SMSRequest(BaseModel):
    to_number: str
    message: str
    agent_id: Optional[str] = None
    from_number: Optional[str] = None


class EmailRequest(BaseModel):
    to_address: str
    subject: str
    body: str
    agent_id: Optional[str] = None
    cc: list[str] = Field(default_factory=list)


class MessageRequest(BaseModel):
    platform: str  # telegram, whatsapp, slack
    recipient: str
    message: str
    agent_id: Optional[str] = None


@router.post("/call")
async def make_call(
    data: CallRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "send",
        "communication",
        context={"channel": "voice", "agent_id": data.agent_id},
    )
    comms = request.app.state.comms_gateway
    result = await comms.make_call(data)
    return result


@router.post("/sms")
async def send_sms(
    data: SMSRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "send",
        "communication",
        context={"channel": "sms", "agent_id": data.agent_id},
    )
    comms = request.app.state.comms_gateway
    result = await comms.send_sms(data)
    return result


@router.post("/email")
async def send_email(
    data: EmailRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "send",
        "communication",
        context={"channel": "email", "agent_id": data.agent_id},
    )
    comms = request.app.state.comms_gateway
    result = await comms.send_email(data)
    return result


@router.post("/message")
async def send_message(
    data: MessageRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "send",
        "communication",
        context={"channel": data.platform, "agent_id": data.agent_id},
    )
    comms = request.app.state.comms_gateway
    result = await comms.send_message(data)
    return result


@router.get("/logs")
async def get_comm_logs(
    request: Request,
    channel: Optional[str] = None,
    limit: int = 50,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "communication_log",
        context={"channel": channel, "limit": limit},
    )
    comms = request.app.state.comms_gateway
    return await comms.get_logs(channel, limit)
