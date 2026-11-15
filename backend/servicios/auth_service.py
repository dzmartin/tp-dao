import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from backend.dominio.exceptions import BusinessRuleError
from backend.persistencia.sqlite_repository import SQLiteRepository
from backend.datos.demo_accounts import DEMO_ACCOUNTS


_PASSWORD_ITERATIONS = 310_000
_PASSWORD_MIN_LENGTH = 10
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")
_SESSION_DURATION = timedelta(hours=8)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_password(password: str) -> str:
    if not isinstance(password, str):
        raise BusinessRuleError("La contraseña es obligatoria.")
    if len(password) < _PASSWORD_MIN_LENGTH:
        raise BusinessRuleError(f"La contraseña debe tener al menos {_PASSWORD_MIN_LENGTH} caracteres.")
    if len(password) > 256:
        raise BusinessRuleError("La contraseña no puede superar los 256 caracteres.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${_PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def _password_matches(password: str, encoded: str) -> bool:
    if len(password) > 256:
        return False
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        ).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


class AuthService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self.repository = repository

    def setup_status(self) -> dict:
        locations = self.repository.all(
            "SELECT id,code,name,type FROM locations WHERE type='LOCAL' ORDER BY code"
        )
        public_locations = [dict(row) for row in locations]
        demo_shortcuts = self._demo_login_shortcuts()
        if self.repository.one("SELECT 1 FROM app_users LIMIT 1"):
            return {
                "setup_required": False,
                "locations": public_locations,
                "demo_shortcuts": demo_shortcuts,
            }
        initial_locations = self.repository.all(
            "SELECT id,code,name,type FROM locations ORDER BY type DESC,code"
        )
        return {
            "setup_required": True,
            "locations": [dict(row) for row in initial_locations],
            "demo_shortcuts": demo_shortcuts,
        }

    def _demo_login_shortcuts(self) -> list[dict]:
        rows = self.repository.all(
            """SELECT m.username,u.role FROM demo_account_markers m
               JOIN app_users u ON u.username=m.username AND u.active=1"""
        )
        roles = {row["username"]: row["role"] for row in rows}
        if any(roles.get(account["username"]) != account["role"] for account in DEMO_ACCOUNTS):
            return []
        return [
            {
                "username": account["username"],
                "password": account["password"],
                "label": account["label"],
            }
            for account in DEMO_ACCOUNTS
        ]

    def _validate_username(self, username: str) -> str:
        if not isinstance(username, str):
            raise BusinessRuleError("El usuario es obligatorio.")
        normalized = username.strip()
        if not _USERNAME_PATTERN.fullmatch(normalized):
            raise BusinessRuleError("El usuario debe tener entre 3 y 50 caracteres (letras, números, punto, guion o guion bajo).")
        return normalized

    def _insert_user(self, username: str, password: str, role: str, location_id: int | None) -> int:
        normalized = self._validate_username(username)
        if role not in ("ADMIN", "CD", "LOCAL"):
            raise BusinessRuleError("El perfil debe ser Administrador, Centro de Distribución o Local.")
        if role in ("ADMIN", "CD") and location_id is not None:
            raise BusinessRuleError("Las cuentas Administrador y Centro de Distribución no se vinculan a una sucursal.")
        if role == "LOCAL":
            location = self.repository.one(
                "SELECT id FROM locations WHERE id=? AND type='LOCAL'", (location_id,)
            )
            if not location:
                raise BusinessRuleError("La cuenta Local debe estar vinculada a una sucursal existente.")
        cursor = self.repository.execute(
            """INSERT INTO app_users(username,password_hash,role,location_id,created_at)
               VALUES(?,?,?,?,?)""",
            (normalized, _hash_password(password), role, location_id, _now().isoformat(timespec="seconds")),
        )
        return int(cursor.lastrowid)

    def initialize(
        self,
        cd_username: str,
        cd_password: str,
        local_username: str,
        local_password: str,
        local_location_id: int | None,
        create_cd_location: dict | None = None,
        create_local_location: dict | None = None,
    ) -> None:
        with self.repository.transaction():
            if self.repository.one("SELECT 1 FROM app_users LIMIT 1"):
                raise BusinessRuleError("La configuración inicial ya fue completada.")
            cd_location = self.repository.one("SELECT id FROM locations WHERE type='CD' LIMIT 1")
            if not cd_location:
                if not isinstance(create_cd_location, dict):
                    raise BusinessRuleError("Ingresá los datos del Centro de Distribución inicial.")
                cd_code = str(create_cd_location.get("code", "")).strip()
                cd_name = str(create_cd_location.get("name", "")).strip()
                if not cd_code or not cd_name:
                    raise BusinessRuleError("El código y nombre del Centro de Distribución son obligatorios.")
                cursor = self.repository.execute(
                    "INSERT INTO locations(code,name,type) VALUES(?,?,'CD')",
                    (cd_code, cd_name),
                )
                cd_location = {"id": cursor.lastrowid}

            local_location = None
            if local_location_id is not None:
                local_location = self.repository.one(
                    "SELECT id FROM locations WHERE id=? AND type='LOCAL'", (local_location_id,)
                )
            elif isinstance(create_local_location, dict):
                local_code = str(create_local_location.get("code", "")).strip()
                local_name = str(create_local_location.get("name", "")).strip()
                if not local_code or not local_name:
                    raise BusinessRuleError("El código y nombre de la sucursal Local son obligatorios.")
                cursor = self.repository.execute(
                    "INSERT INTO locations(code,name,type) VALUES(?,?,'LOCAL')",
                    (local_code, local_name),
                )
                local_location = {"id": cursor.lastrowid}
            if not local_location:
                raise BusinessRuleError("Elegí una sucursal para la cuenta Local o ingresá una nueva.")

            self._insert_user(cd_username, cd_password, "CD", None)
            self._insert_user(local_username, local_password, "LOCAL", int(local_location["id"]))

    def initialize_profile(
        self,
        username: str,
        password: str,
        role: str,
        location_id: int | None,
        create_location: dict | None = None,
    ) -> None:
        if role not in ("ADMIN", "CD", "LOCAL"):
            raise BusinessRuleError("El perfil debe ser Administrador, Centro de Distribución o Local.")
        with self.repository.transaction():
            if self.repository.one("SELECT 1 FROM app_users LIMIT 1"):
                raise BusinessRuleError("La configuración inicial ya fue completada.")

            if role == "ADMIN":
                if location_id is not None or create_location is not None:
                    raise BusinessRuleError("La cuenta Administrador no se vincula a una ubicación.")
                if not self.repository.one("SELECT 1 FROM locations WHERE type='CD' LIMIT 1"):
                    code = "CD"
                    suffix = 1
                    while self.repository.one("SELECT 1 FROM locations WHERE code=?", (code,)):
                        code = f"CD-{suffix:02d}"
                        suffix += 1
                    self.repository.execute(
                        "INSERT INTO locations(code,name,type) VALUES(?,?,'CD')",
                        (code, "Centro de Distribución"),
                    )
                self._insert_user(username, password, "ADMIN", None)
                return

            location = None
            if location_id is not None:
                location = self.repository.one(
                    "SELECT id FROM locations WHERE id=? AND type=?", (location_id, role)
                )
                if not location:
                    raise BusinessRuleError("La ubicación seleccionada no corresponde al perfil.")
            elif isinstance(create_location, dict):
                code = str(create_location.get("code", "")).strip()
                name = str(create_location.get("name", "")).strip()
                if not code or not name:
                    raise BusinessRuleError("El código y nombre de la ubicación son obligatorios.")
                cursor = self.repository.execute(
                    "INSERT INTO locations(code,name,type) VALUES(?,?,?)",
                    (code, name, role),
                )
                location = {"id": cursor.lastrowid}
            else:
                label = "sucursal" if role == "LOCAL" else "Centro de Distribución"
                raise BusinessRuleError(f"Elegí una {label} existente o ingresá una nueva.")

            if role == "LOCAL" and not self.repository.one(
                "SELECT 1 FROM locations WHERE type='CD' LIMIT 1"
            ):
                self.repository.execute(
                    "INSERT INTO locations(code,name,type) VALUES('CD','Centro de Distribución','CD')"
                )
            self._insert_user(
                username,
                password,
                role,
                int(location["id"]) if role == "LOCAL" else None,
            )

    def authenticate(self, username: str, password: str) -> dict | None:
        row = self.repository.one(
            "SELECT * FROM app_users WHERE username=? COLLATE NOCASE AND active=1",
            (username.strip(),),
        )
        if not row or not _password_matches(password, row["password_hash"]):
            return None
        return self._public_user(row)

    def create_session(self, user_id: int) -> tuple[str, datetime]:
        token = secrets.token_urlsafe(32)
        expires_at = _now() + _SESSION_DURATION
        with self.repository.transaction():
            self.repository.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (_now().isoformat(),))
            self.repository.execute(
                "INSERT INTO auth_sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                (
                    hashlib.sha256(token.encode("ascii")).hexdigest(),
                    user_id,
                    expires_at.isoformat(timespec="seconds"),
                ),
            )
        return token, expires_at

    def session_user(self, token: str | None) -> dict | None:
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode("ascii", errors="ignore")).hexdigest()
        row = self.repository.one(
            """SELECT u.* FROM auth_sessions s JOIN app_users u ON u.id=s.user_id
               WHERE s.token_hash=? AND s.expires_at>? AND u.active=1""",
            (token_hash, _now().isoformat()),
        )
        return self._public_user(row) if row else None

    def revoke_session(self, token: str | None) -> None:
        if token:
            token_hash = hashlib.sha256(token.encode("ascii", errors="ignore")).hexdigest()
            with self.repository.transaction():
                self.repository.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))

    def list_users(self) -> list[dict]:
        rows = self.repository.all(
            """SELECT u.id,u.username,u.role,u.active,l.code AS location_code,l.name AS location_name
               FROM app_users u LEFT JOIN locations l ON l.id=u.location_id
               ORDER BY u.role,u.username"""
        )
        return [dict(row) for row in rows]

    def create_user(self, username: str, password: str, role: str, location_id: int | None) -> int:
        with self.repository.transaction():
            try:
                return self._insert_user(username, password, role, location_id)
            except sqlite3.IntegrityError as error:
                if "UNIQUE constraint failed: app_users.username" in str(error):
                    raise BusinessRuleError("Ya existe una cuenta con ese usuario.") from error
                raise

    def register_user(
        self, username: str, password: str, role: str, location_id: int | None
    ) -> int:
        if self.setup_status()["setup_required"]:
            raise BusinessRuleError("Completá la configuración inicial antes de registrar usuarios.")
        if role not in ("LOCAL", "CD"):
            raise BusinessRuleError("El perfil debe ser Centro de Distribución o Local.")
        return self.create_user(username, password, role, location_id)

    def reset_password(
        self, user_id: int, password: str, current_user_id: int, current_token: str | None
    ) -> None:
        password_hash = _hash_password(password)
        with self.repository.transaction():
            self.repository.execute(
                """DELETE FROM demo_account_markers WHERE username=(
                       SELECT username FROM app_users WHERE id=?)""",
                (user_id,),
            )
            cursor = self.repository.execute(
                "UPDATE app_users SET password_hash=? WHERE id=? AND active=1",
                (password_hash, user_id),
            )
            if cursor.rowcount == 0:
                raise BusinessRuleError("La cuenta no existe o está desactivada.")
            current_hash = (
                hashlib.sha256(current_token.encode("ascii", errors="ignore")).hexdigest()
                if user_id == current_user_id and current_token
                else None
            )
            if current_hash:
                self.repository.execute(
                    "DELETE FROM auth_sessions WHERE user_id=? AND token_hash<>?",
                    (user_id, current_hash),
                )
            else:
                self.repository.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))

    def delete_user(self, user_id: int, current_user_id: int) -> None:
        with self.repository.transaction():
            user = self.repository.one("SELECT id,role FROM app_users WHERE id=? AND active=1", (user_id,))
            if not user:
                raise BusinessRuleError("La cuenta no existe o ya está desactivada.")
            if user_id == current_user_id:
                raise BusinessRuleError("No podés desactivar la cuenta con la que iniciaste sesión.")
            if user["role"] == "CD":
                count = self.repository.one(
                    "SELECT COUNT(*) AS count FROM app_users WHERE role='CD' AND active=1"
                )["count"]
                if count <= 1:
                    raise BusinessRuleError("Debe quedar al menos una cuenta activa de Centro de Distribución.")
            if user["role"] == "ADMIN":
                count = self.repository.one(
                    "SELECT COUNT(*) AS count FROM app_users WHERE role='ADMIN' AND active=1"
                )["count"]
                if count <= 1:
                    raise BusinessRuleError("Debe quedar al menos una cuenta activa de Administrador.")
            self.repository.execute(
                "DELETE FROM demo_account_markers WHERE username=(SELECT username FROM app_users WHERE id=?)",
                (user_id,),
            )
            self.repository.execute("UPDATE app_users SET active=0 WHERE id=?", (user_id,))
            self.repository.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))

    def _public_user(self, row) -> dict:
        user = {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "location_id": row["location_id"],
            "location_code": None,
            "location_name": None,
        }
        if row["location_id"] is not None:
            location = self.repository.one(
                "SELECT code,name FROM locations WHERE id=?", (row["location_id"],)
            )
            if location:
                user["location_code"] = location["code"]
                user["location_name"] = location["name"]
        return user
