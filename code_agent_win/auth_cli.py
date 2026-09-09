"""Authentication commands run before application/workspace initialization."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import time
from collections.abc import Sequence

from code_agent.authentication.catalog import ModelCatalog
from code_agent.authentication.models import AuthError, Credential
from code_agent.authentication.registry import get_provider, list_providers
from code_agent.authentication.store import CredentialStore, default_auth_path


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="chaos-agent auth", description="模型平台登录与 API Key 配置")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("providers", help="列出平台和认证方式")
    commands.add_parser("status", help="查看已保存的登录状态")
    login = commands.add_parser("login", help="浏览器/设备码登录或保存 API Key")
    login.add_argument("provider")
    login.add_argument("--method")
    keys = login.add_mutually_exclusive_group()
    keys.add_argument("--api-key", action="store_true", help="通过隐藏输入读取 API Key")
    keys.add_argument("--api-key-env", metavar="NAME", help="从环境变量读取并保存 API Key")
    for flag in ("domain", "gateway", "account-id", "gateway-id"):
        login.add_argument("--" + flag)
    logout = commands.add_parser("logout", help="移除本地保存的登录凭据")
    logout.add_argument("provider")
    logout.add_argument("--auth", choices=("oauth", "api_key"))
    models = commands.add_parser("models", help="查看模型目录")
    models.add_argument("provider", nargs="?")
    models.add_argument("--refresh", action="store_true", help="联网更新公共模型目录")
    setup = commands.add_parser("configure", help="将模型添加为新的配置 profile")
    setup.add_argument("provider")
    setup.add_argument("model")
    setup.add_argument("--profile")
    setup.add_argument("--auth", choices=("oauth", "api_key"), help="同一平台保存两种凭据时选择认证方式")
    setup.add_argument("--context-window", type=int)
    setup.add_argument("--max-output-tokens", type=int)
    setup.add_argument("--base-url")
    setup.add_argument("--api")
    return root


async def run_auth(arguments: Sequence[str]) -> int:
    try:
        options = parser().parse_args(arguments)
    except SystemExit as error:
        return int(error.code or 0)
    try:
        store = CredentialStore()
        if options.command == "providers":
            for item in list_providers():
                suffix = " [实验性]" if item.experimental else ""
                print(f"{item.id:28} {', '.join(item.login_methods)}{suffix}")
        elif options.command == "status":
            status = await asyncio.to_thread(store.status)
            for provider, kind, expires in status:
                state = "到期；下次请求尝试刷新" if expires is not None and expires <= time.time() else "已保存"
                print(f"{provider:28} {kind:8} {state}")
            if not status:
                print("尚未登录。使用 chaos-agent auth login <provider>。")
        elif options.command == "login":
            await _login(options, store)
        elif options.command == "logout":
            removed = await asyncio.to_thread(store.remove, options.provider, options.auth)
            print("已移除本地登录凭据。" if removed else "未保存该平台的登录凭据。")
        elif options.command == "models":
            await _models(options)
        elif options.command == "configure":
            from .auth_profile_setup import configure
            await asyncio.to_thread(configure, options, store)
        return 0
    except (AuthError, ValueError) as error:
        print(f"authentication error: {error}", file=sys.stderr)
        return 2
    except (OSError, EOFError):
        print("authentication error: 无法读取输入或保存配置。", file=sys.stderr)
        return 2
    except Exception:
        print("authentication error: 登录未完成，既有凭据已保留。", file=sys.stderr)
        return 1


async def _login(options, store: CredentialStore) -> None:
    platform = get_provider(options.provider)
    use_key = options.api_key or options.api_key_env or not platform.oauth_methods
    if use_key:
        if not platform.offers_api_key or options.method:
            raise AuthError("该平台/方式不能使用 API Key")
        if options.api_key_env:
            key = os.environ.get(options.api_key_env, "")
        else:
            if not sys.stdin.isatty():
                raise AuthError("隐藏输入需要交互终端；也可使用 --api-key-env NAME")
            key = await asyncio.to_thread(getpass.getpass, "API Key（隐藏输入）: ")
        extra = _key_metadata(options)
        credential = Credential("api_key", key, extra=extra)
    else:
        from code_agent.authentication.oauth import login
        method = options.method or platform.oauth_methods[0]
        if method not in platform.oauth_methods:
            raise AuthError("该平台不支持此登录方式")
        extra = {key: value for key in ("domain", "gateway") if (value := getattr(options, key))}
        if platform.experimental:
            print("此平台与 URI Agent 一致，属于实验性接入。")
        credential = await login(platform.id, method=method, options=extra,
                                 display=print, read_input=_read_input)
    await asyncio.to_thread(store.set, platform.id, credential)
    print(f"{platform.id} 登录凭据已保存（{credential.kind}）。")
    print(f"使用 chaos-agent auth models {platform.id} 查看模型，随后 auth configure {platform.id} <model>。")


def _key_metadata(options) -> dict[str, object]:
    extra: dict[str, object] = {}
    if options.provider in {"cloudflare-ai-gateway", "cloudflare-workers-ai"}:
        account = options.account_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        gateway = options.gateway_id or os.environ.get("CLOUDFLARE_GATEWAY_ID", "default")
        if not account:
            raise AuthError("Cloudflare 登录需要 --account-id")
        import re
        if not all(re.fullmatch(r"[A-Za-z0-9_-]+", item) for item in (account, gateway)):
            raise AuthError("Invalid Cloudflare account/gateway identifier")
        extra.update(accountId=account, gatewayId=gateway)
    if options.gateway:
        extra["gateway"] = options.gateway
    return extra


async def _read_input(prompt: str) -> str:
    if not sys.stdin.isatty():
        raise AuthError("授权码回贴需要交互终端；可改用 device_code 登录")
    return await asyncio.to_thread(getpass.getpass, prompt)


async def _models(options) -> None:
    catalog = ModelCatalog(default_auth_path().with_name("models-catalog.json"))
    if options.provider:
        get_provider(options.provider)
    if options.refresh:
        report = await catalog.refresh()
        print(f"目录：{report.providers} 平台，{report.models} 模型；失败 {len(report.failed_providers)} 平台。")
        if report.failed_providers:
            print("更新失败：" + ", ".join(report.failed_providers))
    models = catalog.models(options.provider)
    for item in models:
        print(f"{item.provider}/{item.id}  {item.protocol}  context={item.context_window} output={item.max_output_tokens}")
    if not models:
        print("公共目录暂无模型。可用 auth configure 指定模型及 context-window/max-output-tokens。")
