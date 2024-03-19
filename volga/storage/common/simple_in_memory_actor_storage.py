import bisect
import heapq
from datetime import datetime
from threading import Thread
from typing import Dict, Any, Optional, List, Tuple

import ray
from datashape import Decimal
from ray.actor import ActorHandle
import time

from volga.storage.cold.cold import ColdStorage
from volga.storage.common.key_index import compose_main_key, KeyIndex
from volga.storage.hot.hot import HotStorage
from volga.streaming.api.context.runtime_context import RuntimeContext
from volga.streaming.api.function.function import SinkFunction
from volga.streaming.api.stream.stream_sink import StreamSink


class SimpleInMemoryActorStorage(ColdStorage, HotStorage):

    def get_stream_sink(self) -> StreamSink:
        pass

    def get_data(self, dataset_name: str, keys: Dict[str, Any], start_date: Optional[datetime], end_date: Optional[datetime]) -> Any:
        pass

    def get_latest_data(self, dataset_name: str, keys: Dict[str, Any]) -> Any:
        pass


@ray.remote(num_cpus=0.01) # TODO set memory request
class SimpleInMemoryCacheActor:
    def __init__(self):
        self.per_dataset_per_key: Dict[str, Dict[str, List[Tuple[Decimal, Any]]]] = {}
        self.key_index = KeyIndex()

    def put_values(self, dataset_name: str, keys_dict: Dict[str, Any], timestamped_values: List[Tuple[Decimal, Any]]):
        key = compose_main_key(keys_dict)
        self.key_index.put(keys_dict)
        if dataset_name in self.per_dataset_per_key:
            per_key = self.per_dataset_per_key[dataset_name]
        else:
            per_key = {}
            self.per_dataset_per_key[dataset_name] = per_key

        if key in per_key:
            vals = per_key[key]
        else:
            vals = []
            per_key[key] = vals

        for ts_val in timestamped_values:
            bisect.insort_right(vals, ts_val)

    def get_values(self, dataset_name: str, keys_dict: Dict[str, Any], start: Optional[Decimal], end: Optional[Decimal]) -> List:
        if dataset_name not in self.per_dataset_per_key:
            # raise RuntimeError(f'No dataset {dataset_name}')
            return []

        main_key = compose_main_key(keys_dict)
        possible_keys = self.key_index.get(keys_dict)
        possible_keys.append(main_key)
        res = []

        for key in possible_keys:
            timestamped_values = self.per_dataset_per_key[dataset_name][key]
            # remove timestamp keys
            vals = list(map(lambda v: v[1], timestamped_values))

            # range query
            first = bisect.bisect_left(vals, start) if start is not None else 0
            last = bisect.bisect_right(vals, end) if end is not None else vals[-1]

            v = vals[first:last]
            res = list(heapq.merge(res, v))

        return res

    def get_latest(self, dataset_name: str, keys_dict: Dict[str, Any]) -> Optional[Any]:
        vals = self.get_values(dataset_name=dataset_name, keys_dict=keys_dict, start=None, end=None)
        if len(vals) == 0:
            return None
        return vals[-1]


class BulkSinkToCacheActorFunction(SinkFunction):

    DUMPER_PERIOD_S = 1

    def __init__(self, cache_actor: ActorHandle):
        self.cache_actor = cache_actor
        self.buffer = []
        self.dumper_thread = None
        self.running = False

    def sink(self, value):
        self.buffer.append(value)

    def _dump_buffer_if_needed(self):
        if len(self.buffer) == 0:
            return
        self.cache_actor.extend_values.remote(self.buffer)
        self.buffer = []

    def _dump_buffer_loop(self):
        while self.running:
            self._dump_buffer_if_needed()
            time.sleep(self.DUMPER_PERIOD_S)

    def open(self, runtime_context: RuntimeContext):
        self.running = True
        self.dumper_thread = Thread(target=self._dump_buffer_loop)
        self.dumper_thread.start()

    def close(self):
        self.running = False
        self._dump_buffer_if_needed()
        if self.dumper_thread is not None:
            self.dumper_thread.join(timeout=5)