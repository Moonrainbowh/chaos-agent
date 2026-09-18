from setuptools import find_namespace_packages, setup


setup(
    packages=find_namespace_packages(
        where="src",
        include=["code_agent*"],
        exclude=["code_agent.*.tests", "code_agent.*.tests.*"],
    )
    + ["code_agent_win"],
    package_dir={"": "src", "code_agent_win": "code_agent_win"},
    include_package_data=False,
)
