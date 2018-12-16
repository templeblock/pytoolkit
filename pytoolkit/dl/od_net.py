"""お手製Object detectionのネットワーク部分。"""

import numpy as np

from . import initializers, layers, networks
from .. import applications, log


def get_preprocess_input():
    """`preprocess_input`を返す。"""
    return applications.darknet53.preprocess_input


@log.trace()
def create_network(pb, mode, strict_nms: bool, load_base_weights: bool):
    """学習とか予測とか用のネットワークを作って返す。"""
    assert mode in ('train', 'predict')
    import tensorflow as tf
    builder = networks.Builder()

    tf.keras.backend.reset_uids()
    x = inputs = tf.keras.layers.Input(pb.input_size + (3,))
    basenet = applications.darknet53.darknet53(input_tensor=x, weights='imagenet' if load_base_weights else None)
    ref_list = [
        basenet.get_layer(name='add_1').output,  # 320→160
        basenet.get_layer(name='add_3').output,  # 320→80
        basenet.get_layer(name='add_11').output,  # 320→40
        basenet.get_layer(name='add_19').output,  # 320→20
        basenet.get_layer(name='add_23').output,  # 320→10
    ]

    # 転移学習元部分の学習率は控えめにする
    lr_multipliers = {}
    if basenet is not None:
        lr_multipliers.update(zip(basenet.layers, [0.01] * len(basenet.layers)))

    # チャンネル数が多ければここで減らす
    x = ref_list[-1]
    if builder.shape(x)[-1] > 256:
        x = builder.conv2d(256, 1, name='tail_sq')(x)
    assert builder.shape(x)[-1] == 256

    # 追加のdownsampling
    down_count = 0
    while True:
        down_count += 1
        map_size = builder.shape(x)[1] // 2
        x = builder.conv2d(256, 2, strides=2, name=f'down{down_count}_ds')(x)
        x = builder.conv2d(256, name=f'down{down_count}_conv1')(x)
        x = builder.conv2d(256, name=f'down{down_count}_conv2')(x)
        assert builder.shape(x)[1] == map_size
        ref_list.append(x)
        if map_size <= pb.map_sizes[-1] and (map_size <= 8 or map_size % 2 != 0):  # 充分小さくなるか奇数になったら終了
            break
    map_size = builder.shape(x)[1]

    # center
    x = builder.conv2d(256, use_act=False, name='center_conv')(x)
    x = builder.res_block(256, name='center_block1')(x)
    x = builder.res_block(256, name='center_block2')(x)
    x = builder.res_block(256, name='center_block3')(x)
    x = builder.bn_act(name='center')(x)
    x = tf.keras.layers.Dropout(0.5)(x)
    x = tf.keras.layers.AveragePooling2D(map_size, name='center_pool')(x)
    x = builder.conv2d(32 * (map_size ** 2), 1, name='center_ex')(x)

    # shared layers
    old_bn, builder.bn_class = builder.bn_class, layers.group_normalization()
    shared_layers = {}
    shared_layers['up_layer1'] = builder.res_block(256, name='up_block1')
    shared_layers['up_layer2'] = builder.res_block(256, name='up_block2')
    shared_layers['up_act'] = builder.bn_act(name='up')
    shared_layers['pm_layer1'] = builder.res_block(256, name='pm_block1')
    shared_layers['pm_layer2'] = builder.res_block(256, name='pm_block2')
    shared_layers['pm_act'] = builder.bn_act(name='pm')
    for pat_ix in range(len(pb.pb_size_patterns)):
        shared_layers[f'pm-{pat_ix}_obj'] = builder.conv2d(
            1, 1,
            kernel_initializer='zeros',
            kernel_regularizer=tf.keras.regularizers.l2(1e-4),
            bias_initializer=initializers.focal_loss_bias_initializer()(nb_classes=1),
            bias_regularizer=None,
            activation='sigmoid',
            use_bias=True,
            use_bn=False,
            name=f'pm-{pat_ix}_obj')
        shared_layers[f'pm-{pat_ix}_clf'] = builder.conv2d(
            pb.num_classes, 1,
            kernel_initializer='zeros',
            kernel_regularizer=tf.keras.regularizers.l2(1e-4),
            bias_regularizer=tf.keras.regularizers.l2(1e-4),
            activation='softmax',
            use_bias=True,
            use_bn=False,
            name=f'pm-{pat_ix}_clf')
        shared_layers[f'pm-{pat_ix}_loc'] = networks.Sequence([
            builder.conv2d(256, 1, name=f'pm-{pat_ix}_loc_conv'),
            builder.conv2d(
                4, 1,
                kernel_initializer='zeros',
                kernel_regularizer=tf.keras.regularizers.l2(1e-4),
                bias_regularizer=tf.keras.regularizers.l2(1e-4),
                use_bias=True,
                use_bn=False,
                use_act=False,
                name=f'pm-{pat_ix}_loc'),
        ])
    builder.bn_class = old_bn

    # upsampling
    up_count = 0
    ref = {f'down{builder.shape(x)[1]}': x for x in ref_list}
    while True:
        up_count += 1
        up_size = map_size // builder.shape(x)[1] if up_count == 1 else 2
        x = layers.subpixel_conv2d()(scale=up_size, name=f'up{up_count}_us')(x)
        x = builder.dwconv2d(5, name=f'up{up_count}_dw')(x)
        x = builder.conv2d(256, 1, use_act=False, name=f'up{up_count}_pre')(x)
        t = builder.conv2d(256, 1, use_act=False, name=f'up{up_count}_lt')(ref[f'down{map_size}'])
        x = tf.keras.layers.add([x, t], name=f'up{up_count}_add')
        x = shared_layers['up_layer1'](x)
        x = shared_layers['up_layer2'](x)
        ref[f'out{map_size}'] = x
        if pb.map_sizes[0] <= map_size:
            break
        x = shared_layers['up_act'](x)
        map_size *= 2

    # prediction module
    objs, locs, clfs = [], [], []
    for map_size in pb.map_sizes:
        assert f'out{map_size}' in ref, f'map_size={map_size} is not found in ref:{ref}'
        x = ref[f'out{map_size}']
        x = shared_layers['pm_layer1'](x)
        x = shared_layers['pm_layer2'](x)
        x = shared_layers['pm_act'](x)
        for pat_ix in range(len(pb.pb_size_patterns)):
            obj = shared_layers[f'pm-{pat_ix}_obj'](x)
            loc = shared_layers[f'pm-{pat_ix}_loc'](x)
            clf = shared_layers[f'pm-{pat_ix}_clf'](x)
            obj = tf.keras.layers.Reshape((-1, 1), name=f'pm{map_size}-{pat_ix}_r1')(obj)
            loc = tf.keras.layers.Reshape((-1, 4), name=f'pm{map_size}-{pat_ix}_r3')(loc)
            clf = tf.keras.layers.Reshape((-1, pb.num_classes), name=f'pm{map_size}-{pat_ix}_r2')(clf)
            objs.append(obj)
            locs.append(loc)
            clfs.append(clf)
    objs = tf.keras.layers.concatenate(objs, axis=-2, name='output_objs')
    locs = tf.keras.layers.concatenate(locs, axis=-2, name='output_locs')
    clfs = tf.keras.layers.concatenate(clfs, axis=-2, name='output_clfs')

    if mode == 'train':
        assert strict_nms is None
        # ラベル側とshapeを合わせるためのダミー
        dummy1 = tf.keras.layers.Lambda(tf.keras.backend.zeros_like, name='dummy1')(objs)
        dummy2 = tf.keras.layers.Lambda(tf.keras.backend.zeros_like, name='dummy2')(objs)
        # いったんくっつける (損失関数の中で分割して使う)
        outputs = tf.keras.layers.concatenate([dummy1, dummy2, objs, clfs, locs], axis=-1, name='outputs')
        model = tf.keras.models.Model(inputs=inputs, outputs=outputs)
    else:
        model = _create_prediction_part(pb, inputs, clfs, objs, locs, strict_nms)
    return model, lr_multipliers


def create_prediction_network(train_model, pb, strict_nms):
    """学習用のネットワークから予測用のネットワークを作成する。"""
    clfs = train_model.get_layer(name='output_clfs').output
    objs = train_model.get_layer(name='output_objs').output
    locs = train_model.get_layer(name='output_locs').output
    return _create_prediction_part(pb, train_model.inputs, clfs, objs, locs, strict_nms)


def _create_prediction_part(pb, inputs, clfs, objs, locs, strict_nms):
    """予測部分。"""
    import tensorflow as tf
    nms_all_threshold = 0.5 if strict_nms else None

    def _objconf(x):
        """物体のconfidenceを算出するレイヤーの処理。"""
        objs = x[0][:, :, 0]
        confs = x[1]
        # objectnessとconfidenceの幾何平均をconfidenceということにしてみる
        # → √すると1.0寄りになりすぎるので√無しに。(objectnessをgateにしたようなもの？)
        conf = objs * confs
        return conf * np.expand_dims(pb.pb_mask, axis=0)

    classes = layers.channel_argmax()(name='classes')(clfs)
    confs = layers.channel_max()(name='confs')(clfs)
    objconfs = tf.keras.layers.Lambda(_objconf, tf.keras.backend.int_shape(clfs)[1:-1], name='objconfs')([objs, confs])
    locs = tf.keras.layers.Lambda(pb.decode_locs, name='locs')(locs)
    nms = layers.nms()(pb.num_classes, len(pb.pb_locs), nms_all_threshold=nms_all_threshold, name='nms')([classes, objconfs, locs])
    model = tf.keras.models.Model(inputs=inputs, outputs=nms)
    return model
