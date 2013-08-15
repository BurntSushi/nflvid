all:
	@echo "Specify a target."

docs:
	pdoc --html --html-dir ./doc --overwrite ./nflvid

pypi: docs
	sudo python2 setup.py register sdist upload

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
	pep8-python2 scripts/{download-all-pbp-xml,nflvid-footage,nflvid-slice}

push:
	git push origin master
	git push github master
