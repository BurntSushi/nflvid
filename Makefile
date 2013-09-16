all:
	@echo "Specify a target."

pypi: docs longdesc.rst
	sudo python2 setup.py register sdist upload

docs:
	pdoc --html --html-dir ./doc --overwrite ./nflvid

longdesc.rst: nflvid/__init__.py docstring
	pandoc -f markdown -t rst -o longdesc.rst docstring
	rm -f docstring

docstring: nflvid/__init__.py
	./extract-docstring > docstring

dev-install: docs longdesc.rst
	[[ -n "$$VIRTUAL_ENV" ]] || exit
	rm -rf ./dist
	python setup.py sdist
	pip install -U dist/*.tar.gz

pep8:
	pep8-python2 nflvid/*.py
	pep8-python2 scripts/download-all-pbp-xml
	pep8-python2 scripts/{nflvid-watch,nflvid-footage,nflvid-slice,nflvid-incomplete}

push:
	git push origin master
	git push github master
