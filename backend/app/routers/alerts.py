from fastapi import APIRouter, Depends, Request

from ..auth import current_user
from ..models import AlertPage

router = APIRouter(prefix="/api/v1/alerts", tags=["Alerts"])


@router.get("", response_model=AlertPage)
def list_alerts(request: Request, owner: str = Depends(current_user)):
    request.app.state.alerts.evaluate()
    return request.app.state.alerts.list_alerts(owner)
