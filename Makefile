PYTHON ?= /usr/bin/python
IGUPD_EGG = dist/igupd-1.0-py2.7.egg
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
