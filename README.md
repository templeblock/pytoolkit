# pytoolkit

[![Build Status](https://travis-ci.org/ak110/pytoolkit.svg?branch=master)](https://travis-ci.org/ak110/pytoolkit)

コンペなどで使いまわすコードを集めたもの。

いわゆるオレオレライブラリ。

`git submodule add https://github.com/ak110/pytoolkit.git` で配置して `import pytoolkit as tk` とかで使う。

## importするために最低限必要なライブラリ

- numpy
- scikit-learn
- scipy

## 動的な依存ライブラリ

- Pillow
- h5py
- horovod
- keras
- matplotlib
- mpi4py
- opencv-python
- pandas
- sqlalchemy
- tensorflow-gpu

## matplotlibの「Invalid DISPLAY variable」対策

必要に応じて環境変数 `MPLBACKEND` を `Agg` とかにしておく前提とする。ソースコード上では対策しない。
