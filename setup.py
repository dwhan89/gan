#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""The setup script."""

from numpy.distutils.core import setup, Extension, build_ext, build_src

build_ext = build_ext.build_ext
build_src = build_src.build_src

setup(
    author="Dongwon 'DW' Han",
    author_email='dwhan89@gmail.com',
    classifiers=[
        'Development Status :: Pre-Alpha',
        'Intended Audience :: Mostly Me',
        'License :: Apache LICENSE-2.0',
        'Natural Language :: English',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
    ],
    description="cosmikyu",
    install_requires=["torch",
                      "numpy >= 1.10",
                      "pixell",
                      "scipy",
                      "mpi4py",
                      "lmdb",
                      "mlflow"
                      ],
    license="Apache LICENSE-2.0",
    keywords='cosmikyu',
    name='cosmikyu',
    packages=['cosmikyu'],
    include_package_data=True,
    package_data={'cosmikyu': ['data/*']},
    url='https://github.com/dwhan89/cosmimikyu',
)

print('\n[setup.py request was successful.]')
