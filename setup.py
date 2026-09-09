"""Build metadata for installing netconf-console2 from the repository root."""

from setuptools import find_packages, setup


setup(
    name="netconf-console2",
    version="3.6.1",
    description="Windows-native NETCONF CLI and GUI for O-RAN O-RU management-plane testing",
    packages=find_packages("ncc"),
    package_dir={"": "ncc"},
    python_requires=">=3.10",
    install_requires=[
        "ncclient>=0.7.1",
        "paramiko>=3.2.0",
        "lxml>=4.9.0",
        "prompt-toolkit>=3.0,<4",
        "pyang>=2.7,<3",
        "tomli>=2.0.0; python_version < '3.11'",
    ],
    extras_require={"interactive": []},
    entry_points={
        "console_scripts": ["netconf-console2=netconf_console.ncc:main"],
        "gui_scripts": ["netconf-console2-gui=netconf_console.gui.app:main"],
    },
    include_package_data=True,
    package_data={"netconf_console.gui": ["assets/*.ico"]},
)
