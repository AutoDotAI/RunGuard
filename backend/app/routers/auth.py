from fastapi import APIRouter, Request

from ..auth import hash_password, verify_password
from ..models import Credentials, TokenResponse
from ..store import Problem

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


def token_response(request: Request, user: dict) -> TokenResponse:
    return TokenResponse(access_token=request.app.state.tokens.issue(user["id"]),
                         user={"id": user["id"], "email": user["email"]})


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(payload: Credentials, request: Request):
    user = request.app.state.store.create_user(payload.email, hash_password(payload.password))
    return token_response(request, user)


@router.post("/login", response_model=TokenResponse)
def login(payload: Credentials, request: Request):
    user = request.app.state.store.user_by_email(payload.email)
    if user is None or not verify_password(payload.password, user["password_hash"]):
        raise Problem(401, "INVALID_CREDENTIALS", "Invalid email or password")
    return token_response(request, user)
