"""Revoke pending identity proofs together with credential changes or erasure.

These helpers are synchronous and require their caller's atomic transaction. A social
browser is removed only when its verified provider/app/subject already maps to this
account. Unlinked flows cannot be attributed to an account and expire after ten minutes.
"""

import time

from .db import Database


def clear_transient_identity_state(db: Database) -> None:
    """A process restart never revives an OTP or an unfinished OAuth exchange."""
    with db.atomic():
        db.conn.execute("DELETE FROM phone_challenges")
        db.conn.execute("DELETE FROM social_browsers")
        db.conn.execute("DELETE FROM oidc_browsers")
        db.conn.execute("DELETE FROM phone_send_attempts WHERE createdAt <= ?", (time.time() - 86400,))


def revoke_identity_proofs(db: Database, user: str, *, erasing: bool = False) -> None:
    """Invalidate proofs without resetting SMS-abuse counters on a password change."""
    from .phone_identity import PhoneIdentityRepository

    if not db.conn.in_transaction:
        raise RuntimeError("Identity proof revocation requires the outer account transaction.")
    if erasing:
        PhoneIdentityRepository(db).purge_user_verifications(user)
    else:
        prefixes = ("bind:" + user + ":", "security:set_password:" + user + ":",
                    "security:erase:" + user + ":")
        db.conn.execute(
            "DELETE FROM phone_challenges WHERE phone IN "
            "(SELECT phone FROM phone_phones WHERE userId = ?) "
            "OR substr(subject, 1, ?) = ? OR substr(subject, 1, ?) = ? "
            "OR substr(subject, 1, ?) = ?",
            (user, len(prefixes[0]), prefixes[0], len(prefixes[1]), prefixes[1],
             len(prefixes[2]), prefixes[2]),
        )
    # Revision watermarks also reject a pre-revocation provider request that is
    # currently awaiting network I/O and has not produced a linked pending row yet.
    from .oidc_identity import revoke_user_oidc_flows
    from .social_identity import revoke_user_social_flows

    revoke_user_social_flows(db, user)
    revoke_user_oidc_flows(db, user)
