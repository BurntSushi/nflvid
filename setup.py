from distutils.core import setup

longdesc = \
'''A simple library to download, slice and search NFL game footage on a play-by-play basis.

This library comes with preloaded play-by-play meta data, which describes the start time of each play in the game footage. However, the actual footage does not come with this library and is not released by me. This package therefore provides utilities to batch download NFL Game Footage from the original source.

Once game footage is downloaded, you can use this library to search plays and construct a playlist to play in any video player.'''

setup(
    name='nflvid',
    author='Andrew Gallant',
    author_email='nflvid@burntsushi.net',
    version='0.0.11',
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
    data_files=[('share/doc/nflvid', ['README.md', 'COPYING', 'INSTALL']),
                ('share/doc/nflvid/doc', ['doc/nflvid.m.html'])],
    install_requires=['httplib2', 'eventlet', 'beautifulsoup4', 'nflgame'],
    scripts=['scripts/nflvid-footage', 'scripts/nflvid-slice']
)
