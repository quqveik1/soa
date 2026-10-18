from contextvars import ContextVar


current_actor: ContextVar[dict | None] = ContextVar('current_actor', default=None)
