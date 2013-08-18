import codecs
from distutils.core import setup
import os.path as path

cwd = path.dirname(__file__)
longdesc = codecs.open(path.join(cwd, 'longdesc.rst'), 'r', 'utf-8').read()

setup(
    name='nflvid',
    author='Andrew Gallant',
    author_email='nflvid@burntsushi.net',
    version='0.0.13',
    license='WTFPL',
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
    package_dir={'nflvid': 'nflvid'},
    package_data={'nflvid': ['schedule-status', 'pbp-xml/*.xml.gz']},
    data_files=[('share/doc/nflvid', ['README.md', 'longdesc.rst',
                                      'COPYING', 'INSTALL']),
                ('share/doc/nflvid/doc', ['doc/nflvid.m.html'])],
    install_requires=['httplib2', 'eventlet', 'beautifulsoup4', 'nflgame'],
    scripts=['scripts/nflvid-footage', 'scripts/nflvid-slice']
)
