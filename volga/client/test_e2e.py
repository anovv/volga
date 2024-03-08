import unittest
import datetime

from volga.client.client import Client
from volga.data.api.dataset.aggregate import Avg, Count
from volga.data.api.dataset.dataset import dataset, field, Dataset
from volga.data.api.dataset.pipeline import pipeline
from volga.data.api.source.source import MysqlSource, source


class TestVolgaE2E(unittest.TestCase):

    def test_materialize_offline(self):
        num_users = 100
        num_orders = 1000

        user_items = [{
            'user_id': str(i),
            'registered_at': datetime.datetime.now(),
            'name': f'username_{i}'
        } for i in range(num_users)]

        order_items = [{
            'buyer_id': str(i),
            'product_id': f'prod_{i}',
            'product_type': 'ON_SALE' if i%2 == 0 else 'NORMAL',
            'purchased_at': datetime.datetime.now(),
            'product_price': 100.0
        } for i in range(num_orders)]

        @source(MysqlSource.mock_with_items(user_items))
        @dataset
        class User:
            user_id: str = field(key=True)
            registered_at: datetime.datetime = field(timestamp=True)
            name: str

        @source(MysqlSource.mock_with_items(order_items))
        @dataset
        class Order:
            buyer_id: str = field(key=True)
            product_id: str = field(key=True)
            product_type: str
            purchased_at: datetime.datetime = field(timestamp=True)
            product_price: float

        @dataset
        class OnSaleUserSpentInfo:
            user_id: str = field(key=True)
            product_id: str = field(key=True)
            timestamp: datetime.datetime = field(timestamp=True)

            avg_spent_7d: float
            avg_spent_1h: float
            num_purchases_1w: int

            @pipeline(inputs=[User, Order])
            def gen(cls, users: Dataset, orders: Dataset):
                on_sale_purchases = orders.filter(lambda df: df['product_type'] == 'ON_SALE')

                per_user = users.join(on_sale_purchases, left_on=['user_id'], right_on=['buyer_id'])

                g = per_user.group_by(keys=['user_id'])
                return g.aggregate([
                    Avg(on='product_price', window='7d', into='avg_spent_7d'),
                    Avg(on='product_price', window='1h', into='avg_spent_1h'),
                    Count(window='1w', into='num_purchases_1w'),
                ])

        client = Client()

        # run batch materialization job
        client.materialize_offline(target=OnSaleUserSpentInfo)


if __name__ == '__main__':
    t = TestVolgaE2E()
    t.test_materialize_offline()