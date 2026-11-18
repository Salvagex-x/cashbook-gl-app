"""
Login and roles.

Users live in the app's SECRETS (never in the code or on GitHub). Passwords are
stored only as salted PBKDF2 hashes, so even the person who reads the secrets
cannot see anybody's password.

Roles
  admin       everything, including chart of accounts and the audit log
  accountant  post cashbook / journal entries and reverse vouchers
  viewer      read-only
"""
import hashlib
import hmac
import os

ITERATIONS = 600_000  # slow on purpose, so guessing passwords is expensive

ROLE_PERMISSIONS = {
    "admin": {"view", "post", "reverse", "coa", "audit"},
    "accountant": {"view", "post", "reverse"},
    "viewer": {"view"},
}


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


_DUMMY_HASH = hash_password("not-a-real-password", 1000)


def clean_users(raw) -> dict:
    """Turn the [users.*] section of the secrets into {username: {name, role, password_hash}}."""
    users = {}
    for username, info in (raw or {}).items():
        try:
            info = dict(info)
            role = str(info.get("role", "viewer")).strip().lower()
            if role not in ROLE_PERMISSIONS or not info.get("password_hash"):
                continue
            users[str(username).strip().lower()] = {
                "name": str(info.get("name") or username),
                "role": role,
                "password_hash": str(info["password_hash"]),
            }
        except Exception:
            continue
    return users


def authenticate(users: dict, username: str, password: str):
    """Return {'username','name','role'} when the login is correct, otherwise None."""
    username = (username or "").strip().lower()
    record = users.get(username)
    # always do the slow hash check, so a wrong username takes as long as a wrong password
    ok = verify_password(password or "", record["password_hash"] if record else _DUMMY_HASH)
    if record and ok:
        return {"username": username, "name": record["name"], "role": record["role"]}
    return None


def can(user: dict, permission: str) -> bool:
    return bool(user) and permission in ROLE_PERMISSIONS.get(user.get("role"), set())
