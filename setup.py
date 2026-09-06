"""Build metadata for installing netconf-console2 from the repository root."""

from setuptools import find_packages, setup


setup(
    name="netconf-console2",
    version="3.2.1",
    description="Windows-native NETCONF CLI for O-RAN O-RU management-plane testing",
    packages=find_packages("ncc"),
    package_dir={"": "ncc"},
    python_requires=">=3.10",
    install_requires=[
        "ncclient>=0.7.1",
        "paramiko>=3.2.0",
        "lxml>=4.9.0",
        "prompt-toolkit>=3.0,<4",
        "tomli>=2.0.0; python_version < '3.11'",
    ],
    extras_require={"interactive": []},
    entry_points={"console_scripts": ["netconf-console2=netconf_console.ncc:main"]},
    include_package_data=True,
)
