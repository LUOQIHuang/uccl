"""Keep the static torchrun TCP store alive until its peers leave the barrier."""

import logging
import os
import time
from datetime import timedelta

from torch.distributed import run
from torch.distributed.elastic.agent.server import api
from torch.distributed.elastic.utils import store as store_util


LOGGER = logging.getLogger("uccl.torchrun_exit_guard")
NODE_RANK = int(os.environ["UCCL_TORCHRUN_NODE_RANK"])
GUARD_ENABLED = os.environ.get("UCCL_EXIT_GUARD", "1") == "1"
DELAY_SECONDS = float(os.environ.get("UCCL_DIAG_EXIT_DELAY", "0"))
ORIGINAL_BARRIER = store_util.barrier
ORIGINAL_NONBLOCKING = store_util._barrier_nonblocking


def delayed_nonblocking(store, world_size, key_prefix):
    """Inject the exit race only in the dedicated CPU diagnostic."""
    key = ORIGINAL_NONBLOCKING(store, world_size, key_prefix)
    if NODE_RANK == 1 and key_prefix == api._TERMINAL_STATE_SYNC_ID:
        time.sleep(DELAY_SECONDS)
    return key


def guarded_barrier(store, world_size, key_prefix, *args, **kwargs):
    """Add a peer acknowledgement after the unmodified terminal barrier."""
    ORIGINAL_BARRIER(store, world_size, key_prefix, *args, **kwargs)
    if key_prefix != api._TERMINAL_STATE_SYNC_ID:
        return
    if world_size != 2 or NODE_RANK not in (0, 1):
        raise RuntimeError("Exit guard requires two static nodes with ranks 0 and 1")
    ack_key = key_prefix + "/uccl_peer_exit_ack/1"
    if NODE_RANK == 1:
        store.set(ack_key, "done")
    else:
        store.wait([ack_key], timedelta(seconds=60))
    LOGGER.warning("UCCL_EXIT_ACK_OK node_rank=%s", NODE_RANK)


if __name__ == "__main__":
    if DELAY_SECONDS:
        store_util._barrier_nonblocking = delayed_nonblocking
    if GUARD_ENABLED:
        store_util.barrier = guarded_barrier
    run.main()
