from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from ..auth import current_user
from ..models import EnqueuedRun, Run, RunLogPage, RunPage, RunStatus

router = APIRouter(prefix="/api/v1/runs", tags=["Runs"])


@router.get("", response_model=RunPage)
def list_runs(request: Request, worker_id: UUID | None = None, status: RunStatus | None = None,
              page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
              owner: str = Depends(current_user)):
    return request.app.state.store.list_runs(owner, str(worker_id) if worker_id else None,
                                             status, page, page_size)


@router.get("/{run_id}", response_model=Run)
def get_run(run_id: UUID, request: Request, owner: str = Depends(current_user)):
    return request.app.state.store.get_run(owner, str(run_id))


@router.get("/{run_id}/logs", response_model=RunLogPage)
def get_run_logs(run_id: UUID, request: Request, after_id: int = Query(0, ge=0),
                 owner: str = Depends(current_user)):
    return request.app.state.store.get_logs(owner, str(run_id), after_id)


@router.post("/{run_id}/retry", response_model=EnqueuedRun, status_code=202)
def retry_run(run_id: UUID, request: Request, owner: str = Depends(current_user)):
    return request.app.state.store.retry_run(owner, str(run_id))
