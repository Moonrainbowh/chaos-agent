"""Build development evaluation modules without moving their source paths."""
from pathlib import Path
import shutil
import tempfile

from setuptools import setup
from setuptools.command.build import build
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist


HERE = Path(__file__).resolve().parent
SOURCE = HERE / "src" if (HERE / "src").is_dir() else HERE.parent
EVALUATION = (SOURCE / "code_agent/evaluation" if SOURCE == HERE / "src"
              else SOURCE / "src/code_agent/evaluation")
HOST = SOURCE / "chaos_agent"


def source_files():
    for path in sorted(EVALUATION.glob("*.py")):
        yield path, Path("src/code_agent/evaluation") / path.name
    for path in sorted(HOST.glob("continuity_*.py")):
        yield path, Path("src/chaos_agent") / path.name
    yield HOST / "context_experiment_host.py", Path("src/chaos_agent/context_experiment_host.py")


class IsolatedBuild(build):
    def finalize_options(self):
        self._owned_build = tempfile.TemporaryDirectory(prefix="chaos-benchmark-build-")
        self.build_base = self._owned_build.name
        super().finalize_options()


class BenchmarkBuildPy(build_py):
    def find_package_modules(self, package, package_dir):
        # Shared namespace: never replace the runtime package's __init__ or Units.
        if package == "chaos_agent":
            return [(package, path.stem, str(path)) for path, relative in source_files()
                    if relative.parent == Path("src/chaos_agent")]
        return super().find_package_modules(package, package_dir)


class SelfContainedSdist(sdist):
    def make_release_tree(self, base_dir, files):
        super().make_release_tree(base_dir, files)
        for source, relative in source_files():
            target = Path(base_dir) / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


setup(
    name="chaos-agent-benchmarks", version="1.0.3",
    description="Offline and developer evaluation add-on for Chaos Agent",
    long_description=(HERE / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown", python_requires=">=3.10",
    install_requires=["chaos-agent==1.0.3"],
    packages=["code_agent.evaluation", "chaos_agent", "chaos_benchmarks"],
    package_dir={"code_agent.evaluation": str(EVALUATION), "chaos_agent": str(HOST),
                 "chaos_benchmarks": "chaos_benchmarks"},
    include_package_data=False,
    entry_points={"console_scripts": ["chaos-benchmarks=chaos_benchmarks.cli:main"]},
    cmdclass={"build": IsolatedBuild, "build_py": BenchmarkBuildPy,
              "sdist": SelfContainedSdist},
)
