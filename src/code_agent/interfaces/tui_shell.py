"""User shell input occupies the same foreground slot as model generation."""
import asyncio
import time

from code_agent.core.cancellation import CancellationToken
from .terminal_display import DisplayKind
from .tui_run import finish_run


async def submit_shell(app, text):
    control = getattr(app, "user_commands", None)
    if control is None:
        app._append(DisplayKind.ERROR, "当前宿主不支持用户命令")
        app.input.replace(text)
        return False
    if app._run_task is not None and not app._run_task.done():
        app._append(DisplayKind.ERROR, "先暂停当前执行，再运行命令；已保留输入")
        app.input.replace(text)
        return False
    include = not text.startswith("!!")
    command = text[1 if include else 2:].strip()
    if not command:
        app._append(DisplayKind.ERROR, "请在 ! 后输入命令")
        app.input.replace(text)
        return False
    app._token = CancellationToken()
    app._user_command_running = True
    app._run_started_at = time.monotonic()
    app.state.status, app.state.active_action = "running", "用户命令"
    app._append(DisplayKind.USER, text)

    async def run():
        try:
            thread, result = await control.run(command, app.current_thread_id, app._token, include=include)
            app.current_thread_id = thread
            app.state.thread_id = thread
            output = result.output
            app._append(DisplayKind.TOOL, control.display(result))
            app.state.status = "cancelled" if output.get("reason") == "cancelled" else "error" if result.is_error else "idle"
        except Exception as error:
            app.state.status = "error"
            app._append(DisplayKind.ERROR, f"用户命令失败：{type(error).__name__}")
        finally:
            app._user_command_running = False
            app.state.active_action = None

    app._run_task = asyncio.create_task(run())
    app._run_task.add_done_callback(lambda task: finish_run(app, task))
    app._start_animation()
    app.redraw()
    await asyncio.sleep(0)
    return True
