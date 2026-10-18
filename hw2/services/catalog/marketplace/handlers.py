from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from marketplace import auth, products
from marketplace.context import current_actor
from marketplace.database import connect
from marketplace_api.apis.auth_api_base import BaseAuthApi
from marketplace_api.apis.health_api_base import BaseHealthApi
from marketplace_api.apis.orders_api_base import BaseOrdersApi
from marketplace_api.apis.products_api_base import BaseProductsApi
from marketplace_api.apis.promo_codes_api_base import BasePromoCodesApi
from marketplace_api.models.health_response import HealthResponse
from marketplace_api.models.order_response import OrderResponse
from marketplace_api.models.product_page import ProductPage
from marketplace_api.models.product_response import ProductResponse
from marketplace_api.models.promo_code_response import PromoCodeResponse
from marketplace_api.models.token_pair import TokenPair
from marketplace_api.models.user_response import UserResponse


def response(model, data, status=200):
    return JSONResponse(status_code=status, content=model.model_validate(data).model_dump(mode='json', by_alias=True))


class HealthHandlers(BaseHealthApi):
    async def health(self):
        def check():
            with connect() as conn:
                conn.execute('SELECT 1')
        await run_in_threadpool(check)
        return response(HealthResponse, {'status': 'ok'})


class AuthHandlers(BaseAuthApi):
    async def register(self, register_request):
        data = await run_in_threadpool(auth.register, register_request.model_dump())
        return response(UserResponse, data, 201)

    async def login(self, login_request):
        return response(TokenPair, await run_in_threadpool(auth.login, login_request.model_dump()))

    async def refresh(self, refresh_request):
        return response(TokenPair, await run_in_threadpool(auth.refresh, refresh_request.model_dump()))


class ProductHandlers(BaseProductsApi):
    async def create_product(self, product_create):
        data = await run_in_threadpool(products.create_product, product_create.model_dump(), current_actor.get())
        return response(ProductResponse, data, 201)

    async def get_product(self, id):
        return response(ProductResponse, await run_in_threadpool(products.get_product, id, current_actor.get()))

    async def list_products(self, page=0, size=20, status=None, category=None):
        data = await run_in_threadpool(products.list_products, page, size, status, category, current_actor.get())
        return response(ProductPage, data)

    async def update_product(self, id, product_update):
        data = await run_in_threadpool(products.update_product, id, product_update.model_dump(), current_actor.get())
        return response(ProductResponse, data)

    async def archive_product(self, id):
        await run_in_threadpool(products.archive_product, id, current_actor.get())
        return Response(status_code=204)


class OrderHandlers(BaseOrdersApi):
    async def create_order(self, order_create):
        from marketplace.orders import create_order
        return response(OrderResponse, await run_in_threadpool(create_order, order_create.model_dump(), current_actor.get()), 201)

    async def get_order(self, id):
        from marketplace.orders import get_order
        return response(OrderResponse, await run_in_threadpool(get_order, id, current_actor.get()))

    async def update_order(self, id, order_update):
        from marketplace.orders import update_order
        return response(OrderResponse, await run_in_threadpool(update_order, id, order_update.model_dump(), current_actor.get()))

    async def cancel_order(self, id):
        from marketplace.orders import cancel_order
        return response(OrderResponse, await run_in_threadpool(cancel_order, id, current_actor.get()))

    async def change_order_status(self, id, order_status_update):
        from marketplace.orders import change_order_status
        return response(OrderResponse, await run_in_threadpool(change_order_status, id, order_status_update.model_dump(), current_actor.get()))


class PromoHandlers(BasePromoCodesApi):
    async def create_promo_code(self, promo_code_create):
        from marketplace.promos import create_promo_code
        data = await run_in_threadpool(create_promo_code, promo_code_create.model_dump(), current_actor.get())
        return response(PromoCodeResponse, data, 201)
