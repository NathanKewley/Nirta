# The version comes from setup.py, installing from source avoids hardcoding the wheel file name
.PHONY: rebuild
rebuild:
	pip3 uninstall nitra -y && python3 -m build && pip3 install .

.PHONY: build
build:
	python3 -m build && pip3 install --force-reinstall --no-deps .

.PHONY: test
test:
	pytest

# Not packaging for PyPi for now at least
# .PHONY: uploadToPyPi
# uploadToPyPi:
# 	python3 -m twine upload --repository nitra --repository-url https://upload.pypi.org/legacy/ dist/*
