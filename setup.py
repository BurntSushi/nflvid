import codecs
from distutils.core import setup
import os.path as path

install_requires = ['httplib2', 'beautifulsoup4', 'nflgame']
try:
    import argparse
except ImportError:
    install_requires.append('argparse')

cwd = path.dirname(__file__)
longdesc = codecs.open(path.join(cwd, 'longdesc.rst'), 'r', 'utf-8').read()

version = '0.0.0'
with codecs.open(path.join(cwd, 'nflvid/version.py'), 'r', 'ascii') as f:
    exec(f.read())
    version = __version__
assert version != '0.0.0'

setup(
    name='nflvid',
    author='Andrew Gallant',
    author_email='nflvid@burntsushi.net',
    version=version,
    license='UNLICENSE',
    description='A simple library to download, slice and search NFL game '
                'footage on a play-by-play basis.',
    long_description=longdesc,
    url='https://github.com/BurntSushi/nflvid',
    classifiers=[
        'License :: Public Domain',
        'Development Status :: 3 - Alpha',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'Intended Audience :: End Users/Desktop',
        'Intended Audience :: Other Audience',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Topic :: Database',
    ],
    platforms='ANY',
    packages=['nflvid'],
    package_data={'nflvid': ['schedule-status', 'pbp-xml/*.xml.gz']},
    data_files=[('share/doc/nflvid', ['README.md', 'longdesc.rst',
                                      'UNLICENSE']),
                ('share/doc/nflvid/doc', ['doc/nflvid/index.html'])],
    install_requires=install_requires,
    scripts=['scripts/nflvid-footage', 'scripts/nflvid-slice',
             'scripts/nflvid-watch', 'scripts/nflvid-incomplete']
)
