"""動画処理関連。"""
import pathlib

import numpy as np

from . import generator, image, ndimage


class VideoDataGenerator(generator.Generator):
    """動画データのgenerator。

    Xは画像のファイルパスの配列またはndarray。
    ndarrayの場合はRGB形式で、samples×times×rows×cols×channels。

    # 引数
    - grayscale: グレースケールで読み込むならTrue、RGBならFalse

    # 使用例
    ```
    gen = tk.video.VideoDataGenerator()
    gen.add(tk.video.Resize((299, 299)))
    gen.add(tk.video.RandomFlipLR(probability=0.5))
    gen.add(tk.video.GaussianNoise(probability=0.5))
    gen.add(tk.video.RandomSaturation(probability=0.5))
    gen.add(tk.video.RandomBrightness(probability=0.5))
    gen.add(tk.video.RandomContrast(probability=0.5))
    gen.add(tk.video.RandomHue(probability=0.5))
    gen.add(tk.generator.ProcessInput(tk.video.preprocess_input_abs1))
    gen.add(tk.generator.ProcessOutput(tk.ml.to_categorical(num_classes), batch_axis=True))
    ```

    """

    def __init__(self, profile=False):
        super().__init__(profile=profile)
        self.add(LoadVideo())


class LoadVideo(generator.Operator):
    """動画の読み込み。"""

    def execute(self, x, y, w, rand, ctx: generator.GeneratorContext):
        """処理。"""
        assert rand is not None  # noqa
        if isinstance(x, np.ndarray):
            # ndarrayならそのまま画像扱い
            x = np.copy(x).astype(np.float32)
        else:
            # ファイルパスなら読み込み
            assert isinstance(x, (str, pathlib.Path))
            import cv2
            cap = cv2.VideoCapture(str(x))
            frames = []
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                frames.append(frame)
            x = np.asarray(frames)[..., ::-1]  # BGR -> RGB
            x = x.astype(np.float32)
        assert len(x.shape) == 4
        assert x.shape[-1] == 3
        return x, y, w


# 画像と同じのをそのまま使えるものたち

preprocess_input_abs1 = image.preprocess_input_abs1
preprocess_input_mean = image.preprocess_input_mean
unpreprocess_input_abs1 = image.unpreprocess_input_abs1
GaussianNoise = image.GaussianNoise
RandomBrightness = image.RandomBrightness
RandomContrast = image.RandomContrast
RandomSaturation = image.RandomSaturation
RandomHue = image.RandomHue