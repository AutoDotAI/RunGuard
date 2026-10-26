from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from ..auth import current_user
from ..models import DashboardSummary

router = APIRouter(prefix="/api/v1/dashboard", tags=["Dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def summary(request: Request, window: Literal["24h"] = Query("24h"),
            owner: str = Depends(current_user)):
    return request.app.state.store.dashboard(owner)
