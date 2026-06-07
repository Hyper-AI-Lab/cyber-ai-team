"""Communication routes — telephony, SMS, email, messaging."""


from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from cyber_team.api.authorization import require_authorization
from cyber_team.api.security import Principal, get_current_principal

router = APIRouter()


class CallRequest(BaseModel):
    to_number: str
    agent_id: str | None = None
    context: str = ""
    from_number: str | None = None
    idempotency_key: str | None = None


class SMSRequest(BaseModel):
    to_number: str
    message: str
    agent_id: str | None = None
    from_number: str | None = None
    idempotency_key: str | None = None


class EmailRequest(BaseModel):
    to_address: str
    subject: str
    body: str
    agent_id: str | None = None
    cc: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


class MessageRequest(BaseModel):
    platform: str  # telegram, whatsapp, slack
    recipient: str
    message: str
    agent_id: str | None = None
    idempotency_key: str | None = None


class InboundEmailStatusUpdate(BaseModel):
    status: str = Field(pattern="^(new|triaged|archived|spam|closed)$")


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
    channel: str | None = None,
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


@router.get("/inbound-email")
async def list_inbound_email(
    request: Request,
    status: str | None = Query(None, pattern="^(new|triaged|archived|spam|closed)$"),
    limit: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "communication",
        context={"channel": "inbound_email", "status": status, "limit": limit},
    )
    return await request.app.state.inbound_email_service.list_messages(status=status, limit=limit)


@router.get("/inbound-email/{message_id}")
async def get_inbound_email(
    message_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "read",
        "communication",
        context={"channel": "inbound_email", "message_id": message_id},
    )
    message = await request.app.state.inbound_email_service.get_message(message_id)
    if not message:
        raise HTTPException(404, "Inbound email message not found")
    return message


@router.patch("/inbound-email/{message_id}/status")
async def update_inbound_email_status(
    message_id: str,
    data: InboundEmailStatusUpdate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "update",
        "communication",
        context={"channel": "inbound_email", "message_id": message_id, "status": data.status},
    )
    message = await request.app.state.inbound_email_service.update_status(message_id, data.status)
    if not message:
        raise HTTPException(404, "Inbound email message not found")
    return message


@router.post("/inbound-email/{message_id}/triage-reply")
async def triage_inbound_email_and_prepare_reply(
    message_id: str,
    request: Request,
    _body: dict | None = Body(default=None),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "update",
        "communication",
        context={"channel": "inbound_email", "message_id": message_id},
    )
    result = await request.app.state.email_triage_service.triage_and_prepare_reply(
        message_id,
        requester=principal.email or principal.subject,
    )
    if not result:
        raise HTTPException(404, "Inbound email message not found")
    return result


@router.post("/inbound-email/poll")
async def poll_inbound_email(
    request: Request,
    _body: dict | None = Body(default=None),
    principal: Principal = Depends(get_current_principal),
):
    await require_authorization(
        request,
        principal,
        "validate",
        "integration",
        "imap",
        context={"channel": "inbound_email"},
    )
    return await request.app.state.inbound_email_service.poll_once()
