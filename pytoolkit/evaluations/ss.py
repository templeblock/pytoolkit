"""セマンティックセグメンテーションの評価。"""

import numpy as np

import pytoolkit as tk


def print_ss_metrics(y_true, y_pred, threshold=0.5, print_fn=None):
    """semantic segmentationの各種metricsを算出してprintする。

    Args:
        y_true (array-like): ラベル (shape=(N, H, W) or (N, H, W, C))
        y_pred (array-like): 推論結果 (shape=(N, H, W) or (N, H, W, C))

    Returns:
        dict: 各種metrics

    """
    print_fn = print_fn or tk.log.get(__name__).info
    evals = evaluate_ss(y_true, y_pred, threshold)
    print_fn(
        f"IoU:            {np.array_str(evals['iou'], precision=3, suppress_small=True)}"
    )
    print_fn(f"mIoU:           {evals['miou']:.3f}")
    print_fn(f"IoU score:      {evals['iou_score']:.3f}")
    print_fn(f"Dice coef.:     {evals['dice']:.3f}")
    print_fn(f"IoU mean:       {evals['fg_iou']:.3f}")
    print_fn(f"Acc empty:      {evals['bg_acc']:.3f}")
    print_fn(f"Pixel Accuracy: {evals['acc']:.3f}")
    return evals


def evaluate_ss(y_true, y_pred, threshold=0.5):
    """semantic segmentationの各種metricsを算出してdictで返す。

    y_true, y_predはgeneratorも可。(メモリ不足にならないように)

    Args:
        y_true (array-like): ラベル (shape=(N, H, W) or (N, H, W, C))
        y_pred (array-like): 推論結果 (shape=(N, H, W) or (N, H, W, C))
        threshold (float): 閾値 (ラベルと推論結果と両方に適用)

    Returns:
        dict: 各種metrics

        - "iou_score": IoUスコア (塩コンペのスコア)
        - "dice": ダイス係数
        - "fg_iou": 答えが空でないときのIoUの平均
        - "bg_acc": 答えが空の場合の正解率
        - "acc": Pixel Accuracy

    References:
        - <https://www.kaggle.com/c/tgs-salt-identification-challenge/overview/evaluation>
        - <https://www.kaggle.com/c/severstal-steel-defect-detection/overview/evaluation>

    """

    def process_per_image(yt, yp):
        if np.ndim(yt) == 2:
            yt = np.expand_dims(yt, axis=-1)
        if np.ndim(yp) == 2:
            yp = np.expand_dims(yp, axis=-1)
        assert np.ndim(yt) == 3  # (H, W, C)
        assert np.ndim(yp) == 3  # (H, W, C)
        assert yt.shape == yp.shape
        p_true = yt >= threshold
        p_pred = yp >= threshold
        n_true = ~p_true
        n_pred = ~p_pred
        tp = np.sum(p_true & p_pred, axis=(0, 1))  # (C,)
        fp = np.sum(n_true & p_pred, axis=(0, 1))  # (C,)
        tn = np.sum(n_true & n_pred, axis=(0, 1))  # (C,)
        fn = np.sum(p_true & n_pred, axis=(0, 1))  # (C,)
        gp = np.sum(p_true, axis=(0, 1))  # (C,)
        pp = np.sum(p_pred, axis=(0, 1))  # (C,)
        if yt.shape[-1] == 1:
            # class0=bg, class1=fg。(ひっくり返るので要注意)
            cm = np.array(
                [
                    # negative,  positive
                    [np.sum(tn), np.sum(fp)],  # gt negative
                    [np.sum(fn), np.sum(tp)],  # gt positive
                ]
            )
        else:
            assert yt.shape[-1] >= 2
            num_classes = yt.shape[-1]
            cm = np.zeros((num_classes, num_classes), dtype=np.int64)
            yt_c = yt.argmax(axis=-1)
            yp_c = yp.argmax(axis=-1)
            for i in range(num_classes):
                for j in range(num_classes):
                    cm[i, j] = np.sum((yt_c == i) & (yp_c == j))
        return tp, fp, tn, fn, gp, pp, cm

    r = [process_per_image(yt, yp) for yt, yp in zip(y_true, y_pred)]
    tp, fp, tn, fn, gp, pp, cm = zip(*r)
    tp = np.array(tp)  # (N, C), dtype=int
    fp = np.array(fp)  # (N, C), dtype=int
    tn = np.array(tn)  # (N, C), dtype=int
    fn = np.array(fn)  # (N, C), dtype=int
    gp = np.array(gp)  # (N, C), dtype=int
    pp = np.array(pp)  # (N, C), dtype=int
    cm = np.sum(cm, axis=0)  # (C, C), dtype=int
    fg_mask = gp > 0  # (N, C), dtype=bool
    bg_mask = ~fg_mask  # (N, C), dtype=bool
    pred_bg_mask = pp <= 0  # (N, C), dtype=bool

    epsilon = 1e-7
    sample_iou = tp / (tp + fp + fn + epsilon)  # (N, C)
    class_iou = np.diag(cm) / (
        np.sum(cm, axis=1) + np.sum(cm, axis=0) - np.diag(cm) + epsilon
    )  # (C,)
    dice = 2 * np.mean(tp / (gp + pp + epsilon))

    # 塩コンペのスコア
    prec_list = []
    for th in np.arange(0.5, 1.0, 0.05):
        pred_fg_mask = sample_iou > th  # (N, C)
        match = (fg_mask & pred_fg_mask) | (bg_mask & pred_bg_mask)
        assert np.ndim(match) == 2  # (N, C)
        prec_list.append(np.mean(match))
    iou_score = np.mean(prec_list)

    acc = np.sum(np.diag(cm)) / np.sum(cm)

    return {
        "iou": class_iou,
        "miou": np.mean(class_iou),
        "iou_score": iou_score,
        "dice": dice,
        "fg_iou": np.mean(sample_iou[fg_mask]),
        "bg_acc": np.mean(pred_bg_mask[bg_mask]),
        "acc": acc,
    }
