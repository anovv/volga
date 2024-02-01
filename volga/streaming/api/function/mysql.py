from typing import Optional

from volga.streaming.api.function.function import SourceFunction, SourceContext


class MysqlSourceFunction(SourceFunction):
    def __init__(
        self,
        host: str,
        port: str,
        user: str,
        password: str,
        database: str,
        table: str,
    ):
        pass

    def init(self, parallel, index):
        pass

    def fetch(self, ctx: SourceContext):
        pass