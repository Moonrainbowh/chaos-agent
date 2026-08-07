from __future__ import annotations

from collections.abc import Mapping

from .models import VerifierOracle


def bugfix_assets(
    language: str,
    number: int,
) -> tuple[dict[str, str], dict[str, str], tuple[VerifierOracle, ...]]:
    builders = {"python": _python_bugfix, "node": _node_bugfix, "dotnet": _dotnet_bugfix}
    return builders[language](number)


def multifile_assets(
    language: str,
    number: int,
) -> tuple[dict[str, str], dict[str, str], tuple[VerifierOracle, ...]]:
    builders = {"python": _python_multifile, "node": _node_multifile, "dotnet": _dotnet_multifile}
    return builders[language](number)


def _python_bugfix(number: int) -> tuple[dict[str, str], dict[str, str], tuple[VerifierOracle, ...]]:
    if number % 2 == 0:
        return _python_boundary_bugfix(number)
    bad = f"OFFSET = {number}\n\ndef normalize(value):\n    return value - OFFSET\n"
    fixed = bad.replace("value - OFFSET", "value + OFFSET")
    public = _python_test("from calculator import normalize", f"self.assertEqual(normalize(2), {number + 2})")
    hidden = _python_test("from calculator import normalize", f"self.assertEqual(normalize(-{number}), 0)")
    files = {"calculator.py": bad, "tests/test_calculator.py": public, "pyproject.toml": _pyproject()}
    return files, {"calculator.py": fixed}, _python_verifiers(hidden, "calculator")


def _python_multifile(number: int) -> tuple[dict[str, str], dict[str, str], tuple[VerifierOracle, ...]]:
    if number % 2 == 0:
        return _python_additive_multifile(number)
    config_bad = f"MULTIPLIER = {number + 1}\n"
    config_fixed = f"MULTIPLIER = {number}\n"
    service_bad = "from config import MULTIPLIER\n\ndef scale(value):\n    return value * MULTIPLIER + 1\n"
    service_fixed = service_bad.replace(" * MULTIPLIER + 1", " * MULTIPLIER")
    public = _python_test("from service import scale", f"self.assertEqual(scale(2), {number * 2})")
    hidden = _python_test("from service import scale", f"self.assertEqual(scale(-3), {-number * 3})")
    files = {
        "config.py": config_bad,
        "service.py": service_bad,
        "tests/test_service.py": public,
        "pyproject.toml": _pyproject(),
    }
    exact = {"config.py": config_fixed, "service.py": service_fixed}
    return files, exact, _python_verifiers(hidden, "service")


def _node_bugfix(number: int) -> tuple[dict[str, str], dict[str, str], tuple[VerifierOracle, ...]]:
    if number % 2 == 0:
        return _node_boundary_bugfix(number)
    bad = f"const OFFSET = {number};\nexports.normalize = value => value - OFFSET;\n"
    fixed = bad.replace("value - OFFSET", "value + OFFSET")
    public = _node_test("../lib/calculator", "normalize", f"assert.equal(normalize(2), {number + 2});")
    hidden = _node_test("../lib/calculator", "normalize", f"assert.equal(normalize(-{number}), 0);")
    files = {"lib/calculator.js": bad, "test/public.test.js": public, "package.json": _package_json()}
    return files, {"lib/calculator.js": fixed}, _node_verifiers(hidden)


def _node_multifile(number: int) -> tuple[dict[str, str], dict[str, str], tuple[VerifierOracle, ...]]:
    if number % 2 == 0:
        return _node_additive_multifile(number)
    settings_bad = f"exports.multiplier = {number + 1};\n"
    settings_fixed = f"exports.multiplier = {number};\n"
    service_bad = "const { multiplier } = require('./settings');\nexports.scale = value => value * multiplier + 1;\n"
    service_fixed = service_bad.replace(" * multiplier + 1", " * multiplier")
    public = _node_test("../lib/service", "scale", f"assert.equal(scale(2), {number * 2});")
    hidden = _node_test("../lib/service", "scale", f"assert.equal(scale(-3), {-number * 3});")
    files = {
        "lib/settings.js": settings_bad,
        "lib/service.js": service_bad,
        "test/public.test.js": public,
        "package.json": _package_json(),
    }
    exact = {"lib/settings.js": settings_fixed, "lib/service.js": service_fixed}
    return files, exact, _node_verifiers(hidden)


def _dotnet_bugfix(number: int) -> tuple[dict[str, str], dict[str, str], tuple[VerifierOracle, ...]]:
    if number % 2 == 0:
        return _dotnet_boundary_bugfix(number)
    bad = _csharp_calculator(number, "value - Offset")
    fixed = _csharp_calculator(number, "value + Offset")
    files = _dotnet_files(bad, f"Calculator.Normalize(2) == {number + 2}")
    hidden = _dotnet_hidden(f"Calculator.Normalize(-{number}) == 0")
    return files, {"Calculator.cs": fixed}, _dotnet_verifiers(hidden)


def _dotnet_multifile(number: int) -> tuple[dict[str, str], dict[str, str], tuple[VerifierOracle, ...]]:
    if number % 2 == 0:
        return _dotnet_additive_multifile(number)
    settings_bad = f"namespace Fixture;\npublic static class Settings {{ public const int Multiplier = {number + 1}; }}\n"
    settings_fixed = settings_bad.replace(str(number + 1), str(number))
    service_bad = "namespace Fixture;\npublic static class Calculator { public static int Scale(int value) => value * Settings.Multiplier + 1; }\n"
    service_fixed = service_bad.replace(" * Settings.Multiplier + 1", " * Settings.Multiplier")
    files = _dotnet_files(service_bad, f"Calculator.Scale(2) == {number * 2}")
    files["Settings.cs"] = settings_bad
    hidden = _dotnet_hidden(f"Calculator.Scale(-3) == {-number * 3}", ("../Settings.cs",))
    exact = {"Settings.cs": settings_fixed, "Calculator.cs": service_fixed}
    return files, exact, _dotnet_verifiers(hidden)


def _python_boundary_bugfix(number: int) -> tuple[dict[str, str], dict[str, str], tuple[VerifierOracle, ...]]:
    bad = f"LIMIT = {number}\n\ndef normalize(value):\n    return max(value, LIMIT)\n"
    fixed = bad.replace("max(value, LIMIT)", "min(value, LIMIT)")
    public = _python_test("from calculator import normalize", f"self.assertEqual(normalize({number + 3}), {number})")
    hidden = _python_test("from calculator import normalize", f"self.assertEqual(normalize({number - 3}), {number - 3})")
    files = {"calculator.py": bad, "tests/test_calculator.py": public, "pyproject.toml": _pyproject()}
    return files, {"calculator.py": fixed}, _python_verifiers(hidden, "calculator")


def _node_boundary_bugfix(number: int) -> tuple[dict[str, str], dict[str, str], tuple[VerifierOracle, ...]]:
    bad = f"const LIMIT = {number};\nexports.normalize = value => Math.max(value, LIMIT);\n"
    fixed = bad.replace("Math.max(value, LIMIT)", "Math.min(value, LIMIT)")
    public = _node_test("../lib/calculator", "normalize", f"assert.equal(normalize({number + 3}), {number});")
    hidden = _node_test("../lib/calculator", "normalize", f"assert.equal(normalize({number - 3}), {number - 3});")
    files = {"lib/calculator.js": bad, "test/public.test.js": public, "package.json": _package_json()}
    return files, {"lib/calculator.js": fixed}, _node_verifiers(hidden)


def _dotnet_boundary_bugfix(number: int) -> tuple[dict[str, str], dict[str, str], tuple[VerifierOracle, ...]]:
    bad = _csharp_calculator(number, "Math.Max(value, Offset)")
    fixed = _csharp_calculator(number, "Math.Min(value, Offset)")
    files = _dotnet_files(bad, f"Calculator.Normalize({number + 3}) == {number}")
    hidden = _dotnet_hidden(f"Calculator.Normalize({number - 3}) == {number - 3}")
    return files, {"Calculator.cs": fixed}, _dotnet_verifiers(hidden)


def _python_additive_multifile(number: int) -> tuple[dict[str, str], dict[str, str], tuple[VerifierOracle, ...]]:
    config_bad, config_fixed = f"BIAS = {number + 1}\n", f"BIAS = {number}\n"
    service_bad = "from config import BIAS\n\ndef scale(value):\n    return value + BIAS + 1\n"
    service_fixed = service_bad.replace(" + BIAS + 1", " + BIAS")
    public = _python_test("from service import scale", f"self.assertEqual(scale(2), {number + 2})")
    hidden = _python_test("from service import scale", f"self.assertEqual(scale(-3), {number - 3})")
    files = {
        "config.py": config_bad,
        "service.py": service_bad,
        "tests/test_service.py": public,
        "pyproject.toml": _pyproject(),
    }
    return files, {"config.py": config_fixed, "service.py": service_fixed}, _python_verifiers(hidden, "service")


def _node_additive_multifile(number: int) -> tuple[dict[str, str], dict[str, str], tuple[VerifierOracle, ...]]:
    settings_bad, settings_fixed = f"exports.bias = {number + 1};\n", f"exports.bias = {number};\n"
    service_bad = "const { bias } = require('./settings');\nexports.scale = value => value + bias + 1;\n"
    service_fixed = service_bad.replace(" + bias + 1", " + bias")
    public = _node_test("../lib/service", "scale", f"assert.equal(scale(2), {number + 2});")
    hidden = _node_test("../lib/service", "scale", f"assert.equal(scale(-3), {number - 3});")
    files = {
        "lib/settings.js": settings_bad,
        "lib/service.js": service_bad,
        "test/public.test.js": public,
        "package.json": _package_json(),
    }
    return files, {"lib/settings.js": settings_fixed, "lib/service.js": service_fixed}, _node_verifiers(hidden)


def _dotnet_additive_multifile(number: int) -> tuple[dict[str, str], dict[str, str], tuple[VerifierOracle, ...]]:
    settings_bad = f"namespace Fixture;\npublic static class Settings {{ public const int Bias = {number + 1}; }}\n"
    settings_fixed = settings_bad.replace(str(number + 1), str(number))
    service_bad = "namespace Fixture;\npublic static class Calculator { public static int Scale(int value) => value + Settings.Bias + 1; }\n"
    service_fixed = service_bad.replace(" + Settings.Bias + 1", " + Settings.Bias")
    files = _dotnet_files(service_bad, f"Calculator.Scale(2) == {number + 2}")
    files["Settings.cs"] = settings_bad
    hidden = _dotnet_hidden(f"Calculator.Scale(-3) == {number - 3}", ("../Settings.cs",))
    exact = {"Settings.cs": settings_fixed, "Calculator.cs": service_fixed}
    return files, exact, _dotnet_verifiers(hidden)


def _python_verifiers(hidden_test: str, stem: str) -> tuple[VerifierOracle, ...]:
    return (
        VerifierOracle("python_public", ("python", "-m", "unittest", "discover", "-s", "tests")),
        VerifierOracle(
            "python_hidden",
            ("python", "-m", "unittest", "discover", "-s", "hidden_tests"),
            {f"hidden_tests/test_{stem}_hidden.py": hidden_test},
        ),
    )


def _node_verifiers(hidden_test: str) -> tuple[VerifierOracle, ...]:
    return (
        VerifierOracle("node_public", ("node", "--test", "test/public.test.js")),
        VerifierOracle(
            "node_hidden",
            ("node", "--test", "hidden_tests/hidden.test.js"),
            {"hidden_tests/hidden.test.js": hidden_test},
        ),
    )


def _dotnet_verifiers(hidden: Mapping[str, str]) -> tuple[VerifierOracle, ...]:
    return (
        VerifierOracle("dotnet_public", ("dotnet", "run", "--project", "Fixture.csproj")),
        VerifierOracle("dotnet_hidden", ("dotnet", "run", "--project", "hidden/Hidden.csproj"), hidden),
    )


def _python_test(import_line: str, assertion: str) -> str:
    return f"import unittest\n{import_line}\n\nclass BehaviorTests(unittest.TestCase):\n    def test_contract(self):\n        {assertion}\n"


def _node_test(module: str, symbol: str, assertion: str) -> str:
    return f"const test = require('node:test');\nconst assert = require('node:assert/strict');\nconst {{ {symbol} }} = require('{module}');\ntest('contract', () => {{ {assertion} }});\n"


def _pyproject() -> str:
    return "[project]\nname = \"evaluation-fixture\"\nversion = \"0.0.0\"\nrequires-python = \">=3.10\"\n"


def _package_json() -> str:
    return '{"name":"evaluation-fixture","private":true,"scripts":{"test":"node --test test/public.test.js"}}\n'


def _csharp_calculator(number: int, expression: str) -> str:
    return f"namespace Fixture;\npublic static class Calculator {{ private const int Offset = {number}; public static int Normalize(int value) => {expression}; }}\n"


def _dotnet_files(calculator: str, condition: str) -> dict[str, str]:
    project = '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings></PropertyGroup></Project>\n'
    program = f'using Fixture;\nif (!({condition})) {{ Console.Error.WriteLine("public contract failed"); Environment.Exit(1); }}\n'
    return {"Fixture.csproj": project, "Calculator.cs": calculator, "Program.cs": program}


def _dotnet_hidden(condition: str, extra_links: tuple[str, ...] = ()) -> dict[str, str]:
    links = ['<Compile Include="../Calculator.cs" Link="Calculator.cs" />']
    links.extend(
        f'<Compile Include="{path}" Link="{path.removeprefix("../")}" />'
        for path in extra_links
    )
    project = (
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
        "<TargetFramework>net8.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings>"
        "</PropertyGroup><ItemGroup>"
        + "".join(links)
        + "</ItemGroup></Project>\n"
    )
    program = f'using Fixture;\nif (!({condition})) Environment.Exit(1);\n'
    return {"hidden/Hidden.csproj": project, "hidden/Program.cs": program}
