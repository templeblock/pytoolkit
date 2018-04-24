"""Horovodの薄いwrapper。"""

_initialized = False


def get():
    """`horovod.keras`モジュールを返す。"""
    import horovod.keras as hvd
    return hvd


def init():
    """初期化。"""
    global _initialized
    _initialized = True
    get().init()


def initialized():
    """初期化済みなのか否か(Horovodを使うのか否か)"""
    return _initialized


def is_master():
    """Horovod未使用 or hvd.rank() == 0ならTrue。"""
    if not initialized():
        return True
    return get().rank() == 0


def barrier():
    """全員が揃うまで待つ。"""
    if not initialized():
        return True
    get().allreduce([], name='Barrier')


def bcast(buf, root=0):
    """MPI_Bcastを呼び出す。"""
    if not initialized():
        return buf
    import mpi4py
    mpi4py.rc.initialize = False
    from mpi4py import MPI
    comm = MPI.COMM_WORLD
    return comm.bcast(buf, root)