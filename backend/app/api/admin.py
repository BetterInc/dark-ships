"""Admin panel API: user management for superusers.

List/search accounts, create accounts, suspend/reactivate (is_active),
grant/revoke admin (is_superuser), and inspect any user's watchlist.

All endpoints require a superuser (current_superuser). Suspension takes
effect immediately: fastapi-users re-loads the user from the DB on every
request, so a suspended user's next call 401s regardless of JWT lifetime.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi_users import exceptions
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import UserCreate, UserManager, current_superuser, get_user_manager
from ..db import get_session
from ..models import User, UserVessel
from .user_watchlist import WatchlistItemOut, _enrich, _user_entries

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---- response shapes -----------------------------------------------------

class AdminUserOut(BaseModel):
    id: int
    email: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    watchlist_count: int


class AdminUserCreate(BaseModel):
    email: EmailStr
    password: str


class AdminUserPatch(BaseModel):
    is_active: bool | None = None
    is_superuser: bool | None = None


def _user_out(user: User, watchlist_count: int) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        watchlist_count=watchlist_count,
    )


async def _watchlist_count(session: AsyncSession, user_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(UserVessel).where(UserVessel.user_id == user_id)
    ) or 0


# ---- endpoints -----------------------------------------------------------

@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    q: str | None = None,
    admin: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(User).order_by(User.id)
    if q:
        stmt = stmt.where(func.lower(User.email).contains(q.lower()))
    # .unique() is required: the OAuthAccount relationship is joined-eager-loaded
    users = (await session.execute(stmt)).scalars().unique().all()

    counts = dict(
        (await session.execute(
            select(UserVessel.user_id, func.count()).group_by(UserVessel.user_id)
        )).all()
    )
    return [_user_out(u, counts.get(u.id, 0)) for u in users]


@router.post("/users", response_model=AdminUserOut, status_code=201)
async def create_user(
    payload: AdminUserCreate,
    admin: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_session),
    user_manager: UserManager = Depends(get_user_manager),
):
    # safe=False so is_verified sticks: admin-created accounts skip the
    # email-verification round trip (and on_after_register sends no mail)
    try:
        user = await user_manager.create(
            UserCreate(email=payload.email, password=payload.password, is_verified=True),
            safe=False,
        )
    except exceptions.UserAlreadyExists:
        raise HTTPException(409, f"A user with email {payload.email} already exists")
    except exceptions.InvalidPasswordException as e:
        raise HTTPException(400, str(e.reason))
    return _user_out(user, 0)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: int,
    payload: AdminUserPatch,
    admin: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, f"No user with id {user_id}")

    fields = payload.model_dump(exclude_unset=True)
    if user.id == admin.id and (
        fields.get("is_active") is False or fields.get("is_superuser") is False
    ):
        raise HTTPException(400, "You cannot suspend or de-admin your own account")

    for key, value in fields.items():
        if value is not None:
            setattr(user, key, value)
    await session.commit()
    await session.refresh(user)
    return _user_out(user, await _watchlist_count(session, user.id))


@router.get("/users/{user_id}/watchlist", response_model=list[WatchlistItemOut])
async def user_watchlist(
    user_id: int,
    admin: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_session),
):
    if await session.get(User, user_id) is None:
        raise HTTPException(404, f"No user with id {user_id}")
    return await _enrich(session, await _user_entries(session, user_id))
