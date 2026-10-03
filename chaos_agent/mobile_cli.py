"""Persistent project entry for an Android SSH terminal."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from code_agent.interfaces.project_launcher_view import choose_project, noninteractive_projects
from code_agent.interfaces.terminal_layout import LayoutMode
from code_agent.project_launcher.store import ProjectStore
from code_agent.sessions.repository import SQLiteSessionRepository
from .app_paths import product_state_root, session_path
from .mobile_catalog import MobileCatalog, ProjectSessionView
from .stdio import configure_windows_utf8_stdio


def create_application(root):
    from .app import create_application as create
    return create(workspace_root=root, restore_model_selection=True)


async def run_mobile(store, catalog, *, factory=create_application, chooser=choose_project):
    """Rebuild the entire application for each explicit cross-project selection."""
    requested = None
    while True:
        root = requested or await chooser(store, current_root=await asyncio.to_thread(store.last_root))
        if root is None:
            return 0
        requested = None
        application = None
        failed = False
        try:
            root = await asyncio.to_thread(store.select, root)
            application = await asyncio.to_thread(factory, root)
            tui = application.tui
            tui.project_store = store
            tui.requested_project = None
            tui.layout_mode = LayoutMode.COMPACT
            tui.composer_expanded = False
            tui._announced = True
            tui.sessions = tui.history = ProjectSessionView(catalog, root)
            await application.startup()
            # Opening a project creates no model turn; saved history is explicitly
            # selected through the scoped conversation button.
            await tui.run()
            requested = tui.requested_project
        except Exception as error:
            failed = True
            # Avoid returning to an accidental cwd or showing credential-bearing errors.
            sys.stderr.write(f"项目启动失败 ({type(error).__name__})；返回项目列表。\n")
        finally:
            if application is not None:
                try:
                    await application.aclose()
                except Exception as error:
                    sys.stderr.write(f"项目资源关闭失败 ({type(error).__name__})；手机入口退出，请重新连接。\n")
                    return 2
        if requested is None:
            # An explicit TUI exit ends this SSH entry; failures return to the list.
            if not failed:
                return 0


async def run(arguments):
    parser = argparse.ArgumentParser(description="手机 SSH 项目入口；保存项目，无需 cd")
    parser.add_argument("--seed-project", action="append", type=Path, default=[])
    parser.add_argument("--check", action="store_true", help="只列出保存的项目，不创建模型或运行任务")
    args = parser.parse_args(arguments)
    store = ProjectStore(product_state_root() / "projects.json")
    repository = SQLiteSessionRepository(session_path())
    try:
        catalog = MobileCatalog(repository)
        roots = await catalog.project_roots()
        await asyncio.to_thread(store.seed, (*args.seed_project, *roots))
        if args.check:
            await noninteractive_projects(store)
            return 0
        if not sys.stdin.isatty():
            sys.stderr.write("手机项目入口需要交互终端；SSH 请启用终端/PTY。\n")
            return 2
        return await run_mobile(store, catalog)
    finally:
        repository.close()


def main():
    configure_windows_utf8_stdio()
    try:
        return asyncio.run(run(sys.argv[1:]))
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        sys.stderr.write(f"手机入口不可用 ({type(error).__name__})。\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
