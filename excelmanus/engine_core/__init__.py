"""Engine core 组件。

请从子模块直接导入（例如 ``excelmanus.engine_core.session_state``），
不要经本包 ``__init__`` 预加载整图——工具层会引用 ``tool_result``，
预加载会把 dispatcher / hooks / skillpacks 再绕回 ``excelmanus.tools``。
"""
