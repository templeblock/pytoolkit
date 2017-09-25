import pathlib

import numpy as np
import pytest

import pytoolkit as tk


def test_compute_map():
    gt_classes_list = [np.array([1, 2])]
    gt_bboxes_list = [np.array([
        [0, 0, 200, 200],
        [900, 900, 1000, 1000],
    ])]
    gt_difficults_list = [np.array([False, False])]
    pred_class_list1 = [np.array([9, 9, 3, 3, 3, 3])]
    pred_class_list2 = [np.array([9, 1, 3, 3, 3, 3])]
    pred_class_list3 = [np.array([2, 9, 3, 3, 3, 3])]
    pred_class_list4 = [np.array([2, 1, 3, 3, 3, 3])]
    pred_confs_list = [np.array([1, 1, 1, 1, 1, 1])]
    pred_bboxes_list = [np.array([
        [901, 901, 1000, 1000],
        [1, 1, 199, 199],
        [333, 333, 334, 334],  # error
        [333, 333, 334, 334],  # error
        [333, 333, 334, 334],  # error
        [333, 333, 334, 334],  # error
    ])]
    assert tk.ml.compute_map(gt_classes_list, gt_bboxes_list, gt_difficults_list, pred_class_list1, pred_confs_list, pred_bboxes_list) == 0
    assert tk.ml.compute_map(gt_classes_list, gt_bboxes_list, gt_difficults_list, pred_class_list2, pred_confs_list, pred_bboxes_list) == 0.5
    assert tk.ml.compute_map(gt_classes_list, gt_bboxes_list, gt_difficults_list, pred_class_list3, pred_confs_list, pred_bboxes_list) == 0.5
    assert tk.ml.compute_map(gt_classes_list, gt_bboxes_list, gt_difficults_list, pred_class_list4, pred_confs_list, pred_bboxes_list) == 1


def test_iou():
    bboxes_a = np.array([
        [0, 0, 200, 200],
        [1000, 1000, 1001, 1001],
    ])
    bboxes_b = np.array([
        [100, 100, 300, 300]
    ])
    iou = tk.ml.compute_iou(bboxes_a, bboxes_b)
    assert iou.shape == (2, 1)
    assert iou[0][0] == pytest.approx(100 * 100 / (200 * 200 * 2 - 100 * 100))
    assert iou[1][0] == 0


def test_non_maximum_suppression():
    boxes = np.array([
        [200, 0, 200, 200],  # empty
        [0, 201, 200, 200],  # empty
        [0, 0, 200, 200],  # col
        [0, 0, 201, 201],  # col
        [0, 0, 202, 202],  # col
        [1000, 1000, 1001, 1001],
    ])
    scores = np.array([
        0.9,
        0.9,
        0.7,
        0.8,
        0.6,
        0.85,
    ])
    idx = tk.ml.non_maximum_suppression(boxes, scores, 200, 0.45)
    assert (idx == np.array([5, 3])).all()


def test_plot_cm(tmpdir):
    filepath = str(tmpdir.join('confusion_matrix.png'))
    cm = [
        [5, 0, 0],
        [0, 3, 2],
        [0, 2, 3],
    ]
    tk.ml.plot_cm(cm, filepath)
    assert pathlib.Path(filepath).is_file()  # とりあえずファイルの存在チェックだけ。。
