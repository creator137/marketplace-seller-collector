"""Adapter registry."""
from core.adapters.base import MarketplaceAdapter, SellerData


def get_adapter(code: str) -> MarketplaceAdapter:
    from core.adapters.ozon import OzonAdapter
    from core.adapters.wildberries import WildberriesAdapter
    from core.adapters.yandex_market import YandexMarketAdapter

    adapters = {
        "ozon": OzonAdapter,
        "wildberries": WildberriesAdapter,
        "yandex_market": YandexMarketAdapter,
    }
    cls = adapters.get(code)
    if not cls:
        raise ValueError(f"Unknown marketplace: {code}")
    return cls()
