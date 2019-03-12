PYTHON ?= /usr/bin/python
TARGET_PYTHON_VERSION := $$(find $(TARGET_DIR)/usr/lib -maxdepth 1 -type d -name python* -printf "%f\n" | egrep -o '[0-9].[0-9]')
IGUPD_EGG = dist/igupd-1.0-py$(TARGET_PYTHON_VERSION).egg
IGUPD_PY_SRCS = __main__.py swupd.py upsvc.py somutil.py resumetimer.py swuclient.py usbupd.py
IGUPD_PY_SETUP = setup.py

all: $(IGUPD_EGG)

$(IGUPD_EGG): $(IGUPD_PY_SRCS) $(IGUPD_PY_SETUP)
	$(PYTHON) $(IGUPD_PY_SETUP) bdist_egg --exclude-source-files

.PHONY: clean

clean:
	-rm -rf dist
	-rm -rf build
	-rm -rf *.egg-info
