"""Authentication foundation built on FastAPI-Users.

Exports:
  - fastapi_users:        the FastAPIUsers instance (router factories)
  - auth_backend:         JWT + Bearer transport (token in Authorization header)
  - current_active_user:  dependency other routers import to require a login
  - current_superuser:    same, but additionally requires is_superuser (admin)
  - google_oauth_client:  GoogleOAuth2 client or None when creds are unset
  - UserRead/UserCreate/UserUpdate: pydantic schemas for the routers
"""

import logging
from collections.abc import AsyncGenerator
from typing import Optional

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, IntegerIDMixin, schemas
from fastapi_users.exceptions import InvalidPasswordException
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from httpx_oauth.clients.google import GoogleOAuth2
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from .mail import send_reset_password_email, send_verification_email
from .models import OAuthAccount, User

logger = logging.getLogger(__name__)

SECRET = get_settings().auth_secret


# ---- schemas -------------------------------------------------------------

class UserRead(schemas.BaseUser[int]):
    role: str = "user"  # user | partner | admin


class UserCreate(schemas.BaseUserCreate):
    pass


class UserUpdate(schemas.BaseUserUpdate):
    pass


# ---- user database + manager --------------------------------------------

async def get_user_db(
    session: AsyncSession = Depends(get_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)


class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    # distinct per-purpose secrets so a leaked/guessed token of one kind can't
    # be replayed as another (all still derived from the one configured secret)
    reset_password_token_secret = SECRET + ":reset"
    verification_token_secret = SECRET + ":verify"

    async def validate_password(self, password: str, user) -> None:
        # fastapi-users ships a no-op validator; enforce a real minimum policy.
        # Applies to both self-registration and admin-created accounts.
        if len(password) < 8:
            raise InvalidPasswordException("Password must be at least 8 characters")
        email = getattr(user, "email", "") or ""
        if email and email.lower() in password.lower():
            raise InvalidPasswordException("Password must not contain your email address")

    async def on_after_register(
        self, user: User, request: Optional[Request] = None
    ) -> None:
        logger.info("User %s registered", user.id)
        # bootstrap: emails listed in ADMIN_EMAILS become admins on signup
        if user.email.lower() in get_settings().admin_email_set:
            user = await self.user_db.update(
                user, {"is_superuser": True, "role": "admin", "is_verified": True})
            logger.info("User %s promoted to admin via ADMIN_EMAILS", user.id)
        if user.is_verified:
            return
        # fire off a verification email (best effort - never block register)
        try:
            await self.request_verify(user, request)
        except Exception:
            logger.exception("Could not send verification email to %s", user.email)

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ) -> None:
        await send_reset_password_email(user.email, token)

    async def on_after_request_verify(
        self, user: User, token: str, request: Optional[Request] = None
    ) -> None:
        await send_verification_email(user.email, token)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)


# ---- authentication backend (JWT in Authorization: Bearer header) -------

bearer_transport = BearerTransport(tokenUrl="api/auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=SECRET, lifetime_seconds=3600)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, int](get_user_manager, [auth_backend])

# the reusable dependency the watchlist agent (and other routers) will import
current_active_user = fastapi_users.current_user(active=True)
# for endpoints that are public but richer when logged in (guest teasers)
current_user_optional = fastapi_users.current_user(active=True, optional=True)
# admin-only routes: 401 when unauthenticated, 403 when not a superuser
current_superuser = fastapi_users.current_user(active=True, superuser=True)


# ---- Google OAuth (only enabled when creds are configured) --------------

_settings = get_settings()
google_oauth_client: Optional[GoogleOAuth2] = None
if _settings.google_oauth_client_id and _settings.google_oauth_client_secret:
    google_oauth_client = GoogleOAuth2(
        _settings.google_oauth_client_id,
        _settings.google_oauth_client_secret,
    )
