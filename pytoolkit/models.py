"""Kerasのモデル関連。"""
import pathlib

import numpy as np
import tensorflow as tf

from . import callbacks as cb, data, hvd, keras, log, utils


def load(path, custom_objects=None, compile=False) -> keras.models.Model:  # pylint: disable=redefined-outer-name
    """モデルの読み込み。"""
    path = pathlib.Path(path)
    with log.trace_scope(f'load({path})'):
        from . import get_custom_objects
        custom_objects = custom_objects.copy() if custom_objects else dict()
        custom_objects.update(get_custom_objects())
        return keras.models.load_model(str(path), custom_objects=custom_objects, compile=compile)


def load_weights(model: keras.models.Model, path, by_name=False, skip_not_exist=False):
    """モデルの重みの読み込み。"""
    path = pathlib.Path(path)
    if path.exists():
        with log.trace_scope(f'load_weights({path})'):
            model.load_weights(str(path), by_name=by_name)
    elif skip_not_exist:
        log.get(__name__).info(f'{path} is not found.')
    else:
        raise RuntimeError(f'{path} is not found.')


def save(model: keras.models.Model, path, include_optimizer=False):
    """モデルの保存。"""
    path = pathlib.Path(path)
    if hvd.is_master():
        with log.trace_scope(f'save({path})'):
            model.save(str(path), include_optimizer=include_optimizer)
    hvd.barrier()


def summary(model: keras.models.Model):
    """summaryを実行するだけ。"""
    model.summary(print_fn=log.get(__name__).info if hvd.is_master() else lambda x: None)


def plot_model(model: keras.models.Model, to_file='model.svg', show_shapes=True, show_layer_names=True, rankdir='TB'):
    """モデルのグラフのplot。"""
    path = pathlib.Path(to_file)
    if hvd.is_master():
        with log.trace_scope(f'plot_model({path})'):
            keras.utils.plot_model(model, str(path), show_shapes=show_shapes, show_layer_names=show_layer_names, rankdir=rankdir)
    hvd.barrier()


def compile(model: keras.models.Model, optimizer, loss, metrics=None, loss_weights=None):  # pylint: disable=redefined-builtin
    """compileするだけ。"""
    if hvd.initialized():
        optimizer = hvd.get().DistributedOptimizer(optimizer, compression=hvd.get().Compression.fp16)
    model.compile(optimizer, loss, metrics, loss_weights=loss_weights)


@log.trace()
def fit(model: keras.models.Model,
        training_data: data.Dataset,
        validation_data: data.Dataset = None,
        validation_freq: int = 1,
        batch_size=32, epochs=1800,
        callbacks=None, verbose=1,
        mixup=False,
        initial_epoch=0,
        use_multiprocessing=False, workers=1, max_queue_size=10):
    """独自のtraining loopになる予定の関数。

    Args:
        model: モデル。
        training_data (tk.data.Dataset): 訓練データ。
        validation_data (tk.data.Dataset): 検証データ。Noneなら省略。
        validation_freq (int or list): 検証を行うエポック数の間隔、またはエポック数のリスト。
        batch_size (int): バッチサイズ。
        epochs (int): エポック数。
        callbacks (list): コールバック。EpochLoggerとErrorOnNaNは自動追加。
        verbose (int): 1ならプログレスバー表示、2ならepoch毎の結果だけ表示。
        mixup (bool): mixupするのか否か。
        initial_epoch (int): 学習を開始するエポック数 - 1。
        use_multiprocessing (bool): Trueならマルチプロセス。
        workers (int): ワーカー数。
        max_queue_size (int): キューの最大サイズ。

    """
    callbacks = (callbacks or []) + [
        cb.EpochLogger(),
        cb.ErrorOnNaN(),
    ]
    train_data_loader = data.DataLoader(training_data, batch_size, shuffle=True, mixup=mixup, use_horovod=True)
    val_data_loader = data.DataLoader(validation_data, batch_size * 2, shuffle=True, use_horovod=True) if validation_data is not None else None

    # TODO: TensorFlowに合わせて対応予定
    _ = validation_freq

    # TensorFlowのバグ対策
    if tf.__version__ == '1.13.1':
        from tensorflow.python.keras.engine import training_generator
        original = training_generator.model_iteration
        training_generator.model_iteration = lambda *args, verbose=0, **kwargs: original(*args, verbose=verbose, **kwargs)  # pylint: disable=unnecessary-lambda
    try:
        model.fit_generator(
            train_data_loader, validation_data=val_data_loader,
            epochs=epochs, callbacks=callbacks,
            verbose=verbose if hvd.is_master() else 0,
            initial_epoch=initial_epoch,
            use_multiprocessing=use_multiprocessing,
            workers=workers, max_queue_size=max_queue_size)
    finally:
        if tf.__version__ == '1.13.1':
            training_generator.model_iteration = original


@log.trace()
def predict(model: keras.models.Model, predict_data: data.Dataset, batch_size, verbose=1, desc='predict', on_batch_fn=None):
    """予測。

    Args:
        model: モデル。
        predict_data: 予測したい入力データ。
        batch_size (int): バッチサイズ
        verbose (int): プログレスバー(tqdm)を表示するか否か。
        desc (str): プログレスバーの説明。
        on_batch_fn (callable): モデルとミニバッチ分の入力データを受け取り、予測結果を返す処理。(TTA用)

    Returns:
        np.ndarray: 予測結果。

    """
    return np.array(list(predict_flow(model, predict_data, batch_size, verbose, desc, on_batch_fn)))


def predict_flow(model: keras.models.Model, predict_data: data.Dataset, batch_size, verbose=1, desc='predict', on_batch_fn=None):
    """予測。(yieldバージョン)

    Args:
        model: モデル。
        predict_data: 予測したい入力データ。
        batch_size (int): バッチサイズ
        verbose (int): プログレスバー(tqdm)を表示するか否か。
        desc (str): プログレスバーの説明。
        on_batch_fn (callable): モデルとミニバッチ分の入力データを受け取り、予測結果を返す処理。(TTA用)

    Yields:
        np.ndarray: 予測結果。(サンプル毎)

    """
    if on_batch_fn is None:
        on_batch_fn = _predict_on_batch
    data_loader = data.DataLoader(predict_data, batch_size)
    for X, _ in utils.tqdm(data_loader, desc=desc, total=len(data_loader), disable=verbose < 1):
        pred_batch = on_batch_fn(model, X)
        yield from pred_batch


def _predict_on_batch(model: keras.models.Model, X):
    return model.predict_on_batch(X)


@log.trace()
def evaluate(model: keras.models.Model, evaluate_data: data.Dataset, batch_size=32, verbose=1):
    """評価。

    Args:
        model: モデル。
        evaluate_data (tk.data.Dataset): データ。
        verbose (int): 1ならプログレスバー表示。

    Returns:
        dict: metricsの文字列と値のdict

    """
    if hvd.initialized():
        evaluate_data = data.split(evaluate_data, hvd.get().size())[hvd.get().rank()]

    data_loader = data.DataLoader(evaluate_data, batch_size)
    values = model.evaluate_generator(data_loader, verbose=verbose if hvd.is_master() else 0)

    if hvd.initialized():
        values = hvd.get().allreduce(values)

    return dict(zip(model.metrics_names, values))