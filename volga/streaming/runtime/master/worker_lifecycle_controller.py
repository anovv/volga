import logging
import time
from random import randint
from typing import Dict, List, Any

import ray
from ray.actor import ActorHandle

from volga.streaming.runtime.core.execution_graph.execution_graph import ExecutionGraph, ExecutionVertex
from volga.streaming.runtime.transfer.channel import Channel, LocalChannel, RemoteChannel
from volga.streaming.runtime.worker.job_worker import JobWorker

VALID_PORT_RANGE = (30000, 65000)


# logger = logging.getLogger(__name__)
logger = logging.getLogger("ray")


class WorkerNetworkInfo:

    def __init__(self, node_ip: str, node_id: str):
        self.node_ip = node_ip
        self.node_id = node_id


class WorkerLifecycleController:

    def __init__(self, job_master: ActorHandle):
        self.job_master = job_master
        self._node_ports = {}
        self._ipc_addr_suffix = {}

    def create_workers(self, execution_graph: ExecutionGraph):
        workers = {}
        vertex_ids = []
        logger.info(f'Creating {len(execution_graph.execution_vertices_by_id)} workers...')
        for vertex_id in execution_graph.execution_vertices_by_id:
            vertex = execution_graph.execution_vertices_by_id[vertex_id]
            resources = vertex.resources
            options_kwargs = {
                'max_restarts': -1,
                'max_concurrency': 10
            }
            if resources.num_cpus is not None:
                options_kwargs['num_cpus'] = resources.num_cpus
            if resources.num_gpus is not None:
                options_kwargs['num_gpus'] = resources.num_gpus
            if resources.memory is not None:
                options_kwargs['memory'] = resources.memory
            worker = JobWorker.options(**options_kwargs).remote(job_master=self.job_master)
            vertex_ids.append(vertex_id)
            workers[vertex_id] = worker
            vertex.set_worker(worker)

        worker_hosts_info = ray.get([workers[vertex_id].get_host_info.remote() for vertex_id in vertex_ids])
        worker_infos = []
        for i in range(len(vertex_ids)):
            vertex_id = vertex_ids[i]
            node_id, node_ip = worker_hosts_info[i]
            vertex = execution_graph.execution_vertices_by_id[vertex_id]
            ni = WorkerNetworkInfo(
                node_ip=node_ip,
                node_id=node_id,
            )
            vertex.set_worker_network_info(ni)
            worker_infos.append((vertex_id, ni.node_id, ni.node_ip))

        logger.info(f'Created {len(workers)} workers')
        logger.info(f'Workers writer network info: {worker_infos}')

    # construct channels based on Ray assigned actor IPs and update execution_graph
    def connect_and_init_workers(self, execution_graph: ExecutionGraph):
        logger.info(f'Initing {len(execution_graph.execution_vertices_by_id)} workers...')

        # create channels
        for edge in execution_graph.execution_edges:
            source_worker_network_info: WorkerNetworkInfo = edge.source_execution_vertex.worker_network_info
            target_worker_network_info: WorkerNetworkInfo = edge.target_execution_vertex.worker_network_info
            if source_worker_network_info is None or target_worker_network_info is None:
                raise RuntimeError(f'No worker network info')

            if source_worker_network_info.node_id == target_worker_network_info.node_id:
                channel = LocalChannel(
                    channel_id=edge.id,
                    ipc_addr=self._gen_ipc_addr(node_id=source_worker_network_info.node_id)
                )
            else:
                # unique port per node-node connection
                port = self._gen_port(key=f'{source_worker_network_info.node_id}-{target_worker_network_info.node_id}')
                channel = RemoteChannel(
                    channel_id=edge.id,
                    source_local_ipc_addr=self._gen_ipc_addr(node_id=source_worker_network_info.node_id),
                    source_node_ip=source_worker_network_info.node_ip,
                    source_node_id=source_worker_network_info.node_id,
                    target_local_ipc_addr=self._gen_ipc_addr(node_id=source_worker_network_info.node_id),
                    target_node_ip=target_worker_network_info.node_ip,
                    target_node_id=target_worker_network_info.node_id,
                    port=port,
                )

            edge.set_channel(channel)

        # init workers
        f = []
        for execution_vertex in execution_graph.execution_vertices_by_id.values():
            worker = execution_vertex.worker
            f.append(worker.init.remote(execution_vertex))

        t = time.time()
        ray.wait(f)
        logger.info(f'Inited workers in {time.time() - t}s')

    def start_workers(self, execution_graph: ExecutionGraph):
        logger.info(f'Starting workers...')
        # start source workers first
        f = []
        for w in execution_graph.get_source_workers():
            f.append(w.start_or_rollback.remote())

        t = time.time()
        ray.wait(f)
        logger.info(f'Started source workers in {time.time() - t}s')

        # start rest
        f = []
        for w in execution_graph.get_non_source_workers():
            f.append(w.start_or_rollback.remote())

        t = time.time()
        ray.wait(f)
        logger.info(f'Started non-source workers in {time.time() - t}s')

    def delete_workers(self, vertices: List[ExecutionVertex]):
        # close workers first
        workers = [v.worker for v in vertices]

        # wait for actors to properly close
        timeout=5
        closed_finished_refs, closed_pending_refs = ray.wait(
            [w.close.remote() for w in workers],
            timeout=timeout,
            num_returns=len(workers)
        )
        if len(closed_finished_refs) == len(workers):
            logger.info('All workers closed gracefully')
        else:
            logger.info(f'Timeout ({timeout}s) waiting for actors to close gracefully, {len(closed_pending_refs)} not ready')

        for w in workers:
            w.exit.remote()

    def _gen_port(self, key: str) -> int:
        if key not in self._node_ports:
            port = randint(VALID_PORT_RANGE[0], VALID_PORT_RANGE[1])
            self._node_ports[key] = [port]
        else:
            return self._node_ports[key]

    def _gen_ipc_addr(self, node_id) -> str:
        session_id = f'run_{int(time.time())}'
        PREFIX = f'ipc:///tmp/volga_ipc/{session_id}'
        if node_id in self._ipc_addr_suffix:
            suff = self._ipc_addr_suffix[node_id]
            self._ipc_addr_suffix[node_id] += 1
            return f'{PREFIX}/ipc_{suff}'
        else:
            self._ipc_addr_suffix[node_id] = 0
            return f'{PREFIX}/ipc_0'

