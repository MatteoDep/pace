"""User profile model."""

from typing import Any

from pydantic import BaseModel


class Profile(BaseModel):
    full_name: str | None = None
    user_profile_number: int | None = None
    unit_system: str | None = None
    display_name: str | None = None

    @classmethod
    def from_api(
        cls,
        full_name: str | None,
        device_info: dict[str, Any] | None,
        unit_system: str | None,
        display_name: str | None = None,
    ) -> "Profile":
        upn = None
        if device_info:
            upn = device_info.get("userProfileNumber")
        return cls(
            full_name=full_name,
            user_profile_number=upn,
            unit_system=unit_system,
            display_name=display_name,
        )
