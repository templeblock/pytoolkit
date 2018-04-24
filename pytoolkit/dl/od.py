"""お手製Object detection。

https://github.com/ak110/object_detector
"""
import pathlib
import typing

import numpy as np

from . import callbacks, hvd, losses, models, od_gen, od_net, od_pb
from .. import jsonex, log, ml, utils

# バージョン
_JSON_VERSION = '0.0.1'


class ObjectDetector(object):
    """モデル。

    候補として最初に準備するboxの集合を持つ。
    """

    def __init__(self, base_network, input_size, map_sizes, num_classes):
        self.base_network = base_network
        self.input_size = tuple(input_size)
        self.pb = od_pb.PriorBoxes(map_sizes)
        self.num_classes = num_classes
        self.model: models.Model = None

    def save(self, path: typing.Union[str, pathlib.Path]):
        """保存。"""
        jsonex.dump({
            'version': _JSON_VERSION,
            'base_network': self.base_network,
            'input_size': self.input_size,
            'pb': self.pb.to_dict(),
            'num_classes': self.num_classes,
        }, path)

    @staticmethod
    def load(path: typing.Union[str, pathlib.Path]):
        """読み込み。"""
        data = jsonex.load(path)
        od = ObjectDetector(
            base_network=data['base_network'],
            input_size=data['input_size'],
            map_sizes=data['pb']['map_sizes'],
            num_classes=data['num_classes'])
        od.pb.from_dict(data['pb'])
        return od

    def fit(self, y_train: [ml.ObjectsAnnotation], y_val: [ml.ObjectsAnnotation], batch_size, initial_weights=None):
        """学習。"""
        assert self.model is None
        logger = log.get(__name__)
        logger.info(f'base network:         {self.base_network}')
        logger.info(f'input size:           {self.input_size}')
        logger.info(f'number of classes:    {self.num_classes}')
        # 訓練データに合わせたprior boxの作成
        if hvd.is_master():
            self.pb.fit(self.input_size, y_train)
            pb_dict = self.pb.to_dict()
        else:
            pb_dict = None
        pb_dict = hvd.bcast(pb_dict)
        self.pb.from_dict(pb_dict)
        # prior boxのチェック
        if hvd.is_master():
            self.pb.summary()
            self.pb.check_prior_boxes(y_val, self.num_classes)
        hvd.barrier()
        # モデルの作成
        self._create_model(mode='train', weights=initial_weights, batch_size=batch_size)

    def save_weights(self, path: typing.Union[str, pathlib.Path]):
        """重みの保存。(学習後用)"""
        assert self.model is not None
        self.model.save(path)

    def load_weights(self, path: typing.Union[str, pathlib.Path], batch_size):
        """重みの読み込み。(予測用)"""
        assert self.model is None
        self._create_model(mode='predict', weights=path, batch_size=batch_size)

    def predict(self, X, conf_threshold=0.1, verbose=1):
        """予測。"""
        assert self.model is not None
        pred_classes_list, pred_confs_list, pred_locs_list = [], [], []
        steps = self.model.gen.steps_per_epoch(len(X), self.model.batch_size)
        with utils.tqdm(total=len(X), unit='f', desc='predict', disable=verbose == 0) as pbar:
            for i, X_batch in enumerate(self.model.gen.flow(X, batch_size=self.model.batch_size)):
                # 予測
                pred_list = self.model.model.predict_on_batch(X_batch)
                # 整形：キャストしたりマスクしたり
                for pred in pred_list:
                    pred_classes = pred[:, 0].astype(np.int32)
                    pred_confs = pred[:, 1]
                    pred_locs = pred[:, 2:]
                    mask = pred_confs >= conf_threshold
                    pred_classes_list.append(pred_classes[mask])
                    pred_confs_list.append(pred_confs[mask])
                    pred_locs_list.append(pred_locs[mask, :])
                # 次へ
                pbar.update(len(X_batch))
                if i + 1 >= steps:
                    assert i + 1 == steps
                    break
        return pred_classes_list, pred_confs_list, pred_locs_list

    @log.trace()
    def _create_model(self, mode, weights, batch_size, multi_gpu_predict=True):
        """学習とか予測とか用に`tk.dl.models.Model`を作成して返す。

        # 引数
        - mode: 'pretrain', 'train', 'predict'のいずれか。(出力などが違う)
        - weights: 読み込む重み。Noneなら読み込まない。'imagenet'ならバックボーンだけ。'voc'ならVOC07+12で学習したものを読み込む。pathlib.Pathならそのまま読み込む。

        """
        logger = log.get(__name__)

        network, lr_multipliers = od_net.create_network(self.base_network, self.input_size, self.pb, self.num_classes, mode)
        pi = od_net.get_preprocess_input(self.base_network)
        if mode == 'pretrain':
            gen = od_gen.create_pretrain_generator(self.input_size, pi)
        else:
            gen = od_gen.create_generator(self.input_size, pi,
                                          lambda y_gt: self.pb.encode_truth(y_gt, self.num_classes))
        self.model = models.Model(network, gen, batch_size)
        if mode in ('pretrain', 'train'):
            self.model.summary()

        if weights == 'voc':
            pass  # TODO: githubに学習済みモデル置いてkeras.applicationsみたいなダウンロード機能作る。
        elif isinstance(weights, pathlib.Path):
            self.model.load_weights(weights)
            logger.info(f'warm start: {weights.name}')
        else:
            logger.info(f'cold start.')

        if mode == 'pretrain':
            # 事前学習：通常の分類としてコンパイル
            self.model.compile(sgd_lr=0.5 / 256, loss='categorical_crossentropy', metrics=['acc'])
        elif mode == 'train':
            # Object detectionとしてコンパイル
            sgd_lr = 0.5 / 256 / 3  # lossが複雑なので微調整
            self.model.compile(sgd_lr=sgd_lr, lr_multipliers=lr_multipliers, loss=self.loss, metrics=self.metrics)
        else:
            assert mode == 'predict'
            # 予測：コンパイル不要。マルチGPU化。
            if multi_gpu_predict:
                self.model.set_multi_gpu_model()

    def loss(self, y_true, y_pred):
        """損失関数。"""
        import keras.backend as K
        loss_obj = self.loss_obj(y_true, y_pred)
        loss_clf = self.loss_clf(y_true, y_pred)
        loss_loc = self.loss_loc(y_true, y_pred)
        loss = loss_obj + loss_clf + loss_loc
        assert len(K.int_shape(loss)) == 1  # (None,)
        return loss

    @property
    def metrics(self):
        """各種metricをまとめて返す。"""
        import keras.backend as K

        def acc_bg(y_true, y_pred):
            """背景の再現率。"""
            gt_mask = y_true[:, :, 0]
            gt_obj, pred_obj = y_true[:, :, 1], y_pred[:, :, 1]
            gt_bg = (1 - gt_obj) * gt_mask   # 背景
            acc = K.cast(K.equal(K.greater(gt_obj, 0.5), K.greater(pred_obj, 0.5)), K.floatx())
            return K.sum(acc * gt_bg, axis=-1) / K.sum(gt_bg, axis=-1)

        def acc_obj(y_true, y_pred):
            """物体の再現率。"""
            gt_obj, pred_obj = y_true[:, :, 1], y_pred[:, :, 1]
            acc = K.cast(K.equal(K.greater(gt_obj, 0.5), K.greater(pred_obj, 0.5)), K.floatx())
            return K.sum(acc * gt_obj, axis=-1) / K.sum(gt_obj, axis=-1)

        return [self.loss_obj, self.loss_clf, self.loss_loc, acc_bg, acc_obj]

    def loss_obj(self, y_true, y_pred):
        """ObjectnessのFocal loss。"""
        import keras.backend as K
        import tensorflow as tf
        gt_mask = y_true[:, :, 0]
        gt_obj, pred_obj = y_true[:, :, 1], y_pred[:, :, 1]
        gt_obj_count = K.sum(gt_obj, axis=-1)  # 各batch毎のobj数。
        with tf.control_dependencies([tf.assert_positive(gt_obj_count)]):  # obj_countが1以上であることの確認
            gt_obj_count = tf.identity(gt_obj_count)
        pb_mask = np.expand_dims(self.pb.pb_mask, axis=0)
        loss = losses.binary_focal_loss(gt_obj, pred_obj)
        loss = K.sum(loss * gt_mask * pb_mask, axis=-1) / gt_obj_count  # normalized by the number of anchors assigned to a ground-truth box
        return loss

    @staticmethod
    def loss_clf(y_true, y_pred):
        """クラス分類のloss。多クラスだけどbinary_crossentropy。(cf. YOLOv3)"""
        import keras.backend as K
        gt_obj = y_true[:, :, 1]
        gt_classes, pred_classes = y_true[:, :, 2:-4], y_pred[:, :, 2:-4]
        loss = K.binary_crossentropy(gt_classes, pred_classes)
        loss = K.sum(loss, axis=-1)  # sum(classes)
        loss = K.sum(gt_obj * loss, axis=-1) / K.sum(gt_obj, axis=-1)  # mean (box)
        return loss

    @staticmethod
    def loss_loc(y_true, y_pred):
        """位置のloss。"""
        import keras.backend as K
        gt_obj = y_true[:, :, 1]
        gt_locs, pred_locs = y_true[:, :, -4:], y_pred[:, :, -4:]
        loss = losses.l1_smooth_loss(gt_locs, pred_locs)
        loss = K.sum(loss, axis=-1)  # loss(x1) + loss(y1) + loss(x2) + loss(y2)
        loss = K.sum(gt_obj * loss, axis=-1) / K.sum(gt_obj, axis=-1)  # mean (box)
        return loss