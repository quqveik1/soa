class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def require_roles(actor: dict, *roles: str) -> None:
    if actor.get("role") not in roles:
        raise ApiError(403, "ACCESS_DENIED", "Недостаточно прав для этой операции")
