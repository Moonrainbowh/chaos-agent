import tempfile
from pathlib import Path

from setuptools import find_namespace_packages, setup
from setuptools.command.build import build
from setuptools.command.build_py import build_py


class IsolatedBuild(build):
    """Never reuse a source checkout's potentially stale build tree."""

    def finalize_options(self):
        self._owned_build = tempfile.TemporaryDirectory(prefix="chaos-runtime-build-")
        self.build_base = self._owned_build.name
        super().finalize_options()


class RuntimeBuildPy(build_py):
    def find_data_files(self, package, src_dir):
        # A parent namespace may otherwise reintroduce excluded packages as data.
        return [name for name in super().find_data_files(package, src_dir)
                if Path(name).suffix != ".py"
                and "tests" not in Path(name).parts
                and "evaluation" not in Path(name).parts]

    def find_package_modules(self, package, package_dir):
        modules = super().find_package_modules(package, package_dir)
        if package == "chaos_agent":
            modules = [item for item in modules
                       if not item[1].startswith("continuity_")
                       and item[1] != "context_experiment_host"]
        return modules


setup(
    packages=find_namespace_packages(
        where="src",
        include=["code_agent*"],
        exclude=["code_agent.*.tests", "code_agent.*.tests.*",
                 "code_agent.evaluation", "code_agent.evaluation.*"],
    )
    + ["chaos_agent", "chaos_agent.remote"],
    package_dir={"": "src", "chaos_agent": "chaos_agent"},
    include_package_data=True,
    cmdclass={"build": IsolatedBuild, "build_py": RuntimeBuildPy},
)
