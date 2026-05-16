"""Auth status model."""

from pydantic import BaseModel


class AuthStatus(BaseModel):
    authenticated: bool
    token_path: str
