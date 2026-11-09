"""Sign-up, sign-in and sessions."""

from typing import Annotated, Any

from fastapi import APIRouter, Cookie, HTTPException, Response

from apps.api.auth.security import (
    hash_password,
    hash_token,
    new_session_token,
    verify_password,
)
from apps.api.schemas.api import LoginIn, SignupIn, UserOut
from apps.api.services import SESSION_COOKIE, ApiServices, CurrentUser, Services

router = APIRouter(prefix="/api/auth", tags=["auth"])
_SESSION_DAYS = 14


async def _start_session(services: ApiServices, user: dict[str, Any], response: Response) -> None:
    token = new_session_token()
    await services.repository.create_session(user["id"], hash_token(token), _SESSION_DAYS)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=services.cookie_secure,
        max_age=_SESSION_DAYS * 86_400,
    )


@router.post("/signup", response_model=UserOut, status_code=201)
async def signup(body: SignupIn, response: Response, services: Services) -> dict[str, Any]:
    try:
        user = await services.repository.create_user(
            body.email, hash_password(body.password), body.persona
        )
    except ValueError:
        raise HTTPException(409, "An account with this email already exists.") from None
    await _start_session(services, user, response)
    return user


@router.post("/login", response_model=UserOut)
async def login(body: LoginIn, response: Response, services: Services) -> dict[str, Any]:
    user = await services.repository.user_by_email(body.email)
    if user is None or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Incorrect email or password.")
    await _start_session(services, user, response)
    return user


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    user: CurrentUser,
    services: Services,
    session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> None:
    if session:
        await services.repository.delete_session(hash_token(session))
    response.delete_cookie(SESSION_COOKIE)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> dict[str, Any]:
    return user
