import numpy as np
import pytest
import tensorflow as tf

import pytoolkit as tk

K = tf.keras.backend


def test_binary_iou():
    y_true = K.constant(np.array([[0.0, 0.0, 1.0, 1.0]]))
    y_pred = K.constant(np.array([[0.0, 0.9, 0.8, 0.0]]))
    metric = tk.metrics.binary_iou(y_true, y_pred, threshold=0.8)
    assert metric.numpy() == pytest.approx(1 / 3)
