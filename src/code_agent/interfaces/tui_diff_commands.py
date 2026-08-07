from __future__ import annotations

from ._diff_parser import DiffScope
from .diff_interaction import DiffAction


async def show_diff(interactions: object, app: object) -> None:
    scope = DiffScope.WORKING_TREE
    recorded = app.state.diff
    try:
        view = await interactions.diff.load(scope, recorded)
    except Exception as error:
        interactions.diff_interaction.open(
            None,
            scope=scope,
            recorded_diff=recorded,
            error=_diff_error("load", error),
        )
        return
    interactions.diff_interaction.open(
        view,
        scope=scope,
        recorded_diff=recorded,
    )


async def handle_diff_key(interactions: object, app: object, key: str) -> bool:
    action = interactions.diff_interaction.handle_key(key)
    if action is DiffAction.REFRESH:
        await _refresh_diff(interactions, app)
    elif action is DiffAction.SEND:
        await _send_diff(interactions, app)
    return True


async def _refresh_diff(interactions: object, app: object) -> None:
    modal = interactions.diff_interaction
    if modal.has_unsent_comments:
        modal.set_error("send or discard comments before refreshing")
        return
    recorded = app.state.diff
    try:
        view = await interactions.diff.load(modal.scope, recorded, modal.paths)
        modal.replace_view(view)
        modal.recorded_diff = recorded
    except Exception as error:
        modal.set_error(_diff_error("refresh", error))


async def _send_diff(interactions: object, app: object) -> None:
    try:
        feedback = interactions.diff_interaction.feedback()
    except ValueError as error:
        interactions.diff_interaction.set_error(str(error))
        return
    try:
        accepted = await app.submit(feedback, attachments=())
    except Exception as error:
        interactions.diff_interaction.set_error(
            f"diff feedback submit failed ({type(error).__name__})"
        )
        return
    if accepted:
        interactions.diff_interaction.finish_send()
    else:
        interactions.diff_interaction.set_error(
            "diff feedback was not accepted; comments retained"
        )


def _diff_error(action: str, error: Exception) -> str:
    return f"diff {action} failed ({type(error).__name__})"
