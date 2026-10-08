"""Wire conversation features once for both application construction paths."""
from code_agent.capabilities.catalog import progressive_tools, CapabilityStrategy
from .user_command_control import UserCommandControl
from .conversation_tree_control import ConversationTreeControl


def configure_conversation_controls(tui,sessions,dispatcher,profile_supplier=None):
    memory = getattr(tui, "project_memory", None)
    tui.user_commands = UserCommandControl(sessions, dispatcher,
        on_thread_created=memory.bind_thread if memory is not None else None)
    tui.conversation_tree = ConversationTreeControl(sessions,tui.tasks)
    tui.usage_profile = profile_supplier or (lambda:None)
    tui.tool_catalog = lambda: tool_catalog(tui.controller)


def tool_catalog(controller):
    engine = controller._engine
    actions = getattr(engine,"_actions",None)
    tools = tuple(actions.tools()) if actions is not None else ()
    visible = getattr(engine,"last_advertised_tool_names",None)
    if visible is None:
        visible = {t.name for t in progressive_tools(tools,{},strategy=getattr(engine,"_capability_strategy",CapabilityStrategy.HYBRID))}
        label = "首次请求默认可见"
    else:
        label = "最近请求实际可见"
    names = {t.name for t in tools}
    return (f"当前注册且启用：{len(names)} · {label}：{len(visible)}\n" +
            "\n".join(("● " if t.name in visible else "· ")+t.name for t in tools))
