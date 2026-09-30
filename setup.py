import setuptools

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="nitra",
    version="0.0.4",
    author="Nath Kewley",
    author_email="nathan.kewley@kubiieo.com",
    description="Azure Bicep Deployment Orchestration",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/NathanKewley/nitra",
    project_urls={
        "Bug Tracker": "https://github.com/NathanKewley/nitra/issues",
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],
    package_dir={"": "src"},
    packages=setuptools.find_packages(where="src"),
    python_requires=">=3.9",
    install_requires=[
        "pyyaml",
    ],
    entry_points={
        'console_scripts': [
            'nitra = nitra:nitra',
        ],
    },    
)
