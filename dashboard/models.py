from typing import Literal

from pydantic import BaseModel


class CreateBotRequest(BaseModel):
    name: str
    soul: str | None = None
    extra_config: dict | None = None
    template: str = "default"
    network_isolation: bool = True


class DuplicateRequest(BaseModel):
    new_name: str


class ForkRequest(BaseModel):
    new_name: str


class RollbackRequest(BaseModel):
    timestamp: str


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: Literal["admin", "user"] = "user"
    bots: list[str] = []


class UpdateUserRequest(BaseModel):
    password: str | None = None
    role: Literal["admin", "user"] | None = None
    bots: list[str] | None = None


class CloneRequest(BaseModel):
    new_name: str
    track_fork: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
