from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from ..auth import current_user
from ..models import EnqueuedRun, Worker, WorkerCreate, WorkerPage, WorkerUpdate

router = APIRouter(prefix="/api/v1/workers", tags=["Workers"])


@router.get("", response_model=WorkerPage)
def list_workers(request: Request, limit: int = Query(20, ge=1, le=100),
                 offset: int = Query(0, ge=0), owner: str = Depends(current_user)):
    return request.app.state.store.list_workers(owner, limit, offset)


@router.post("", response_model=Worker, status_code=201)
def create_worker(payload: WorkerCreate, request: Request, owner: str = Depends(current_user)):
    return request.app.state.store.create_worker(owner, payload)


@router.get("/{worker_id}", response_model=Worker)
def get_worker(worker_id: UUID, request: Request, owner: str = Depends(current_user)):
    return request.app.state.store.get_worker(owner, str(worker_id))


@router.patch("/{worker_id}", response_model=Worker)
def update_worker(worker_id: UUID, payload: WorkerUpdate, request: Request,
                  owner: str = Depends(current_user)):
    return request.app.state.store.update_worker(owner, str(worker_id), payload)


@router.delete("/{worker_id}", status_code=204)
def archive_worker(worker_id: UUID, request: Request, owner: str = Depends(current_user)):
    request.app.state.store.archive_worker(owner, str(worker_id))
    return Response(status_code=204)


@router.post("/{worker_id}/runs", response_model=EnqueuedRun, status_code=202)
def run_worker(worker_id: UUID, request: Request, owner: str = Depends(current_user)):
    request.app.state.rate_limiter.enforce("run", owner)
    return request.app.state.store.run_worker(owner, str(worker_id))
