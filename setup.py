from setuptools import find_namespace_packages, setup


setup(
    packages=find_namespace_packages(
        where="src",
        include=["code_agent*"],
        exclude=["code_agent.*.tests", "code_agent.*.tests.*"],
    )
    + ["chaos_agent", "chaos_agent.remote"],
    package_dir={"": "src", "chaos_agent": "chaos_agent"},
    include_package_data=True,
)
