"""Non-secret channel discovery and purpose/session-bound phone security actions.

A verified phone does not replace the immutable internal account ID. OTPs for login,
phone association, password setting and erasure are distinct single-use proofs. No
SQLite transaction spans SMS delivery or a password worker await.
"""

import secrets
from collections.abc import Callable
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field

from . import accounts
from .accounts import Password, _Accounts, reauthorize_account
from .admin_db import token_hash
from .browser_sessions import clear_cookie
from .config import Settings
from .data_controls import AccountDataRepository
from .db import APIError
from .phone_identity import (
    PhoneChallenge,
    PhoneCode,
    PhoneIdentityRepository,
    issue_phone_challenge,
)
from .schemas import DTO, OK
from .sms import DisabledSmsSender


class PhoneChannel(DTO):
    enabled: bool
    simulated: bool
    reason: str | None
    countryCallingCodes: list[str]


class SocialChannel(DTO):
    provider: Literal["wechat", "qq"]
    enabled: bool
    simulated: bool
    reason: str | None


class OidcChannel(DTO):
    enabled: bool
    provider: Literal["logto"] = "logto"
    simulated: bool
    reason: str | None
    authorizationOrigin: str | None


class AuthOptions(DTO):
    backend: Literal["legacy", "oidc"]
    oidc: OidcChannel
    phone: PhoneChannel
    social: list[SocialChannel]
    legacyPasswordEnabled: bool
    registrationEnabled: bool


class PhoneSecurityChallengeInput(DTO):
    purpose: Literal["set_password", "erase"]


class PhoneSetPasswordInput(DTO):
    challengeId: UUID
    code: PhoneCode
    newPassword: Password = Field(repr=False)


class PhoneEraseInput(DTO):
    challengeId: UUID
    code: PhoneCode
    confirmation: Literal["DELETE MY ACCOUNT"]


def _subject(request: Request, user: str, purpose: str) -> str:
    return "security:" + purpose + ":" + user + ":" + token_hash(request.state.session_token)


def _verified_phone(request: Request, user: str) -> str:
    _Accounts(request.app.state.db).require(user)
    row = request.app.state.db.conn.execute(
        "SELECT phone FROM phone_phones WHERE userId = ?", (user,),
    ).fetchone()
    if row is None:
        raise APIError(403, "phone_binding_required", "A verified phone is required.")
    return row["phone"]


def identity_security_router(settings: Settings, current_user: Callable[..., str]) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["identity security"])
    User = Annotated[str, Depends(current_user)]

    @router.get("/auth/options", response_model=AuthOptions)
    async def options(request: Request):
        legacy = settings.auth_backend == "legacy"
        configured = all((settings.oidc_issuer_url, settings.oidc_client_id,
                          settings.oidc_client_secret, settings.oidc_redirect_uri))
        oidc_enabled = not legacy and settings.oidc_enabled and configured
        parsed = urlsplit(settings.oidc_issuer_url) if configured else None
        sender = getattr(request.app.state, "sms_sender", None)
        phone_enabled = legacy and sender is not None and not isinstance(sender, DisabledSmsSender)
        providers = getattr(request.app.state, "social_providers", {}) if legacy else {}
        return AuthOptions(
            backend=settings.auth_backend,
            oidc=OidcChannel(
                enabled=oidc_enabled, simulated=oidc_enabled and settings.oidc_simulated,
                reason=None if oidc_enabled else "oidc_disabled" if legacy or not settings.oidc_enabled else "oidc_unconfigured",
                authorizationOrigin=(parsed.scheme + "://" + parsed.netloc) if parsed else None,
            ),
            phone=PhoneChannel(
                enabled=phone_enabled,
                simulated=phone_enabled and settings.sms_provider == "test-file",
                reason=None if phone_enabled else "sms_unavailable", countryCallingCodes=["+86"],
            ),
            social=[SocialChannel(
                provider=name, enabled=name in providers and phone_enabled,
                simulated=bool(name in providers and getattr(providers[name], "simulated", False)),
                reason=(None if name in providers and phone_enabled else
                        "phone_binding_unavailable" if name in providers else "social_unavailable"),
            ) for name in ("wechat", "qq")],
            legacyPasswordEnabled=legacy, registrationEnabled=settings.registration_enabled,
        )

    @router.post("/account/phone/security/challenges", response_model=PhoneChallenge, status_code=202)
    async def challenge(body: PhoneSecurityChallengeInput, request: Request, user: User):
        reauthorize_account(request, user)
        previous = _Accounts(request.app.state.db).require(user)
        phone = _verified_phone(request, user)
        result = await issue_phone_challenge(request, phone, body.purpose,
                                             _subject(request, user, body.purpose))
        try:
            reauthorize_account(request, user)
            _Accounts(request.app.state.db).unchanged(previous)
            if _verified_phone(request, user) != phone:
                raise APIError(401, "unauthorized", "The account identity changed.")
        except BaseException:
            PhoneIdentityRepository(request.app.state.db, settings).invalidate_challenge(result.challengeId)
            raise
        return result

    @router.post("/account/phone/password", response_model=OK)
    async def set_password(body: PhoneSetPasswordInput, request: Request, response: Response, user: User):
        reauthorize_account(request, user)
        db = request.app.state.db
        previous = _Accounts(db).require(user)
        phone = _verified_phone(request, user)
        tools = accounts._auth_tools(request)
        tools.attempts.check("sensitive_peer", accounts._peer(request), accounts.SENSITIVE_ATTEMPTS)
        tools.attempts.check("sensitive_user", user, accounts.SENSITIVE_ATTEMPTS)
        repo = PhoneIdentityRepository(db, settings)
        proof = repo.inspect_challenge(str(body.challengeId), body.code.get_secret_value(),
                                       purpose="set_password", subject=_subject(request, user, "set_password"))
        if proof.phone != phone:
            raise APIError(401, "invalid_phone_code", "The verification code is invalid or expired.")
        salt = secrets.token_bytes(accounts.SALT_BYTES)
        digest = await tools.workers.run(lambda: accounts._derive_password(body.newPassword, salt))
        reauthorize_account(request, user)
        _Accounts(db).unchanged(previous)
        if _verified_phone(request, user) != phone:
            raise APIError(401, "unauthorized", "The account identity changed.")
        with db.atomic():
            repo.consume_challenge(proof)
            _Accounts(db).change_password(previous, salt, digest)
        clear_cookie(response, secure=request.url.scheme == "https")
        return OK()

    @router.post("/account/phone/erase", response_model=OK)
    async def erase(body: PhoneEraseInput, request: Request, response: Response, user: User):
        reauthorize_account(request, user)
        db = request.app.state.db
        previous = _Accounts(db).require(user)
        phone = _verified_phone(request, user)
        tools = accounts._auth_tools(request)
        tools.attempts.check("sensitive_peer", accounts._peer(request), accounts.SENSITIVE_ATTEMPTS)
        tools.attempts.check("sensitive_user", user, accounts.SENSITIVE_ATTEMPTS)
        repo = PhoneIdentityRepository(db, settings)
        proof = repo.inspect_challenge(str(body.challengeId), body.code.get_secret_value(),
                                       purpose="erase", subject=_subject(request, user, "erase"))
        if proof.phone != phone:
            raise APIError(401, "invalid_phone_code", "The verification code is invalid or expired.")
        # No await from authentication/proof checks through consume + complete erasure.
        with db.atomic():
            repo.consume_challenge(proof)
            AccountDataRepository(db, settings).erase(
                user, previous, request.app.state.runtime.in_flight_run_ids(),
            )
        clear_cookie(response, secure=request.url.scheme == "https")
        return OK()

    return router
