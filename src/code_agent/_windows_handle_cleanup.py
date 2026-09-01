from __future__ import annotations

from collections.abc import Callable, Iterable


def close_all(
    handles: Iterable[int | None], close: Callable[[int], None]
) -> BaseException | None:
    """Close every owned handle and return the first close failure."""
    first: BaseException | None = None
    for handle in handles:
        if handle is None:
            continue
        try:
            close(handle)
        except BaseException as error:
            first = first or error
    return first


def attach_cleanup(primary: BaseException, cleanup: BaseException) -> None:
    if getattr(primary, "cleanup_error", None) is None:
        setattr(primary, "cleanup_error", cleanup)
    add_note = getattr(primary, "add_note", None)
    if callable(add_note):
        add_note(f"Windows handle finalization failed: {cleanup}")
