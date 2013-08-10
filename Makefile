all:
	@echo "Specify a target."

docs:
	pdoc --html --html-dir ./doc --overwrite ./nflvid

pypi: docs
	sudo python2 setup.py register sdist upload

pypi-meta:
	python2 setup.py register

dev-install:
	[[ -n "$$VIRTUAL_ENV" ]] || exit
	rm -rf ./dist
	python2 setup.py sdist
	pip install -U dist/*.tar.gz

pep8:
	pep8-python2 nflvid/*.py
	pep8-python2 scripts/{download-all-pbp-xml,nflvid-footage,nflvid-slice}

push:
	git push origin master
	git push github master
