from typing import Any, Optional

from fastapi import HTTPException, Request, status

from cyber_team.api.security import Principal


async def require_authorization(
    request: Request,
    principal: Principal,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    context: Optional[dict[str, Any]] = None,
) -> None:
    authorization = request.app.state.authorization_service
    decision = await authorization.authorize(
        principal=principal,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        context=context,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Action is not authorized",
        )
