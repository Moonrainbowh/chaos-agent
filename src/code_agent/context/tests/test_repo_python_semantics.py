from __future__ import annotations

import unittest

from code_agent.context.repo_python_semantics import extract_python_semantics


class PythonSemanticFactsTests(unittest.TestCase):
    def test_extracts_ranges_signatures_docstrings_and_structured_uses(self) -> None:
        facts = extract_python_semantics(
            "pkg/child.py",
            "from .service import Client as C\n"
            "from .types import *\n\n"
            "@decorate\n"
            "class Child(Base):\n"
            "    \"\"\"Child docs.\"\"\"\n"
            "    def run(self, value: C) -> Result:\n"
            "        return C()\n",
        )

        child = next(item for item in facts.symbols if item.name == "Child")
        run = next(item for item in facts.symbols if item.name == "Child.run")
        self.assertEqual((child.line, child.end_line), (5, 8))
        self.assertIn("class Child(Base)", child.signature)
        self.assertEqual(child.docstring, "Child docs.")
        self.assertEqual((run.line, run.end_line), (7, 8))
        self.assertIn("def run(self, value: C) -> Result", run.signature)
        self.assertEqual(
            [(item.module, item.level, item.names, item.aliases, item.star) for item in facts.imports],
            [
                ("service", 1, ("Client",), (("Client", "C"),), False),
                ("types", 1, (), (), True),
            ],
        )
        uses = {(item.kind, item.expression, item.owner) for item in facts.uses}
        self.assertIn(("decorator", "decorate", "Child"), uses)
        self.assertIn(("inherits", "Base", "Child"), uses)
        self.assertIn(("annotation", "C", "Child.run"), uses)
        self.assertIn(("annotation", "Result", "Child.run"), uses)
        self.assertIn(("call", "C", "Child.run"), uses)

    def test_parameters_locals_comprehensions_and_shadowed_imports_are_not_external(self) -> None:
        facts = extract_python_semantics(
            "module.py",
            "from dep import run\n\n"
            "def execute(run, values):\n"
            "    local = run\n"
            "    mapped = [run(item) for item in values]\n"
            "    return local, mapped\n\n"
            "def use_import():\n"
            "    return run()\n",
        )

        uses = {(item.kind, item.expression, item.owner) for item in facts.uses}
        self.assertNotIn(("call", "run", "execute"), uses)
        self.assertNotIn(("reference", "run", "execute"), uses)
        self.assertNotIn(("reference", "item", "execute"), uses)
        self.assertNotIn(("reference", "local", "execute"), uses)
        self.assertIn(("call", "run", "use_import"), uses)

    def test_config_accesses_keep_namespace_and_provenance(self) -> None:
        facts = extract_python_semantics(
            "settings.py",
            "import os\n\n"
            "def load(config, options):\n"
            "    env_timeout = os.getenv('TIMEOUT')\n"
            "    app_timeout = config.get('timeout')\n"
            "    cli_timeout = options['timeout']\n"
            "    return env_timeout, app_timeout, cli_timeout\n",
        )

        accesses = {
            (item.namespace, item.key, item.provenance, item.operation)
            for item in facts.config_accesses
        }
        self.assertIn(("env", "TIMEOUT", "os.getenv", "read"), accesses)
        self.assertIn(
            ("mapping:config", "timeout", "local:settings.py:load:config", "read"),
            accesses,
        )
        self.assertIn(
            ("mapping:options", "timeout", "local:settings.py:load:options", "read"),
            accesses,
        )

    def test_lambda_reassignment_and_nested_comprehension_bindings_are_local(self) -> None:
        facts = extract_python_semantics(
            "app.py",
            "from dep import run\n"
            "run = lambda value: value\n"
            "callback = lambda run: run()\n"
            "items = [child for parent in parents for child in parent.children]\n"
            "run()\n",
        )
        expressions = {(item.kind, item.expression) for item in facts.uses}
        self.assertNotIn(("call", "run"), expressions)
        self.assertNotIn(("reference", "parent"), expressions)

    def test_symbol_skeletons_have_a_per_file_byte_cap(self) -> None:
        source = "\n".join(
            f'def f{i}(value: "{"x" * 400}"):\n    """{"d" * 900}"""\n    return value'
            for i in range(100)
        )
        facts = extract_python_semantics("large.py", source)
        size = sum(
            len((item.signature + item.docstring).encode("utf-8"))
            for item in facts.symbols
        )
        self.assertLessEqual(size, 32 * 1024)


if __name__ == "__main__":
    unittest.main()
