import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from config.settings import CHAIN_ID, CLOB_HOST, settings
from src.trade.price_refresher import refresh_market_prices
from src.trade.strategies.base import MarketSelection
from src.utils.market_parser import get_buy_price, get_order_price, get_selection_price

logger = logging.getLogger(__name__)

SIGNER_API_KEY_MISMATCH = (
    "Order signer address does not match the CLOB API key address. "
    "SIGNATURE_TYPE=3 (deposit wallet) is not supported by py-clob-client-v2 yet — "
    "the SDK binds API keys to your EOA but signs orders with DEPOSIT_WALLET_ADDRESS. "
    "If you use a Polymarket email/Magic wallet, set SIGNATURE_TYPE=1 and "
    "DEPOSIT_WALLET_ADDRESS to your Polymarket proxy wallet (profile address). "
    "If you trade from MetaMask directly, use SIGNATURE_TYPE=0 and leave "
    "DEPOSIT_WALLET_ADDRESS empty."
)


def _import_clob():
    """Import CLOB client (v2 preferred, v1 fallback)."""
    try:
        from py_clob_client_v2 import ClobClient, OrderArgs, OrderType, PartialCreateOrderOptions
        from py_clob_client_v2.order_builder.constants import BUY

        try:
            from py_clob_client_v2 import SignatureTypeV2

            sig_map = {
                0: SignatureTypeV2.EOA,
                1: SignatureTypeV2.POLY_PROXY,
                2: SignatureTypeV2.POLY_GNOSIS_SAFE,
                3: SignatureTypeV2.POLY_1271,
            }
        except ImportError:
            sig_map = None

        return "v2", ClobClient, OrderArgs, OrderType, PartialCreateOrderOptions, BUY, sig_map
    except ImportError:
        pass

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
        from py_clob_client.order_builder.constants import BUY

        return "v1", ClobClient, OrderArgs, OrderType, PartialCreateOrderOptions, BUY, None
    except ImportError as exc:
        raise ImportError(
            "Install py-clob-client-v2 (Python >=3.9.10) or py-clob-client for live trading"
        ) from exc


def compute_order_expiration(
    expiry_hours: float,
    now_ts: Optional[int] = None,
) -> tuple[int, str]:
    """Return (unix_expiration, order_type). GTC when expiry_hours <= 0, else GTD."""
    if expiry_hours <= 0:
        return 0, "GTC"
    now = now_ts if now_ts is not None else int(time.time())
    return now + int(expiry_hours * 3600), "GTD"


class TradeExecutor:
    def __init__(self, dry_run: Optional[bool] = None):
        self.dry_run = settings.dry_run if dry_run is None else dry_run
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client

        if not settings.private_key:
            raise ValueError("PRIVATE_KEY not configured")

        version, ClobClient, _, _, _, _, sig_map = _import_clob()

        if version == "v2":
            signature_type = (
                sig_map.get(settings.signature_type, sig_map[1]) if sig_map else None
            )
            kwargs = {
                "host": CLOB_HOST,
                "chain_id": CHAIN_ID,
                "key": settings.private_key,
            }
            if signature_type is not None:
                kwargs["signature_type"] = signature_type
            if settings.deposit_wallet_address:
                kwargs["funder"] = settings.deposit_wallet_address
            temp = ClobClient(**kwargs)
            creds = temp.create_or_derive_api_key()
            kwargs["creds"] = creds
            self._client = ClobClient(**kwargs)
            self._validate_signer_api_key_alignment()
        else:
            temp = ClobClient(CLOB_HOST, key=settings.private_key, chain_id=CHAIN_ID)
            creds = temp.create_or_derive_api_creds()
            self._client = ClobClient(
                CLOB_HOST,
                key=settings.private_key,
                chain_id=CHAIN_ID,
                creds=creds,
                signature_type=settings.signature_type,
                funder=settings.deposit_wallet_address or None,
            )
        return self._client

    def _validate_signer_api_key_alignment(self) -> None:
        """Fail fast when POLY_1271 would mismatch API key address (SDK issue #70)."""
        client = self._client
        if client is None or client.signer is None:
            return
        order_signer = client.builder._v2_order_signer()
        api_signer = client.signer.address()
        if order_signer.lower() != api_signer.lower():
            raise ValueError(SIGNER_API_KEY_MISMATCH)

    def _resolve_order_price(self, selection: MarketSelection) -> float:
        source = settings.order_price_source

        if selection.market:
            fresh = refresh_market_prices(selection.market)
            selection.market = fresh
            selection.yes_price = get_selection_price(fresh) or selection.yes_price
            selection.buy_price = get_buy_price(fresh) or selection.buy_price
            price = get_order_price(fresh, source)
            if price is not None:
                logger.info(
                    "Order price for market %s (%s): %.4f via %s",
                    selection.market_id,
                    selection.group_item_title,
                    price,
                    source,
                )
                return price

        fallbacks = {
            "yes_price": selection.yes_price,
            "buy_price": selection.buy_price,
        }
        if selection.market:
            fallbacks["best_bid"] = selection.market.get("bestBid")
            fallbacks["best_ask"] = selection.market.get("bestAsk")
            fallbacks["midpoint"] = selection.market.get("midpoint")
        raw = fallbacks.get(source)
        if raw is not None:
            price = float(raw)
            logger.warning(
                "Using stale %s %.4f for market %s (live refresh unavailable)",
                source,
                price,
                selection.market_id,
            )
            return price

        raise ValueError(
            f"Could not resolve order price for market {selection.market_id} (source={source})"
        )

    def buy_yes(self, selection: MarketSelection) -> dict[str, Any]:
        order_price = self._resolve_order_price(selection)
        expiration, order_type_name = compute_order_expiration(settings.order_expiry_hours)
        expires_at = (
            datetime.fromtimestamp(expiration, tz=timezone.utc).isoformat()
            if expiration
            else None
        )

        if self.dry_run:
            result = {
                "dry_run": True,
                "status": "simulated",
                "token_id": selection.yes_token_id,
                "price": order_price,
                "size": selection.share_count,
                "market_id": selection.market_id,
                "event_id": selection.event_id,
                "order_type": order_type_name,
                "expiration": expiration,
                "expires_at": expires_at,
            }
            logger.info("DRY RUN buy: %s", result)
            return result

        version, _, OrderArgs, OrderType, PartialCreateOrderOptions, BUY, _ = _import_clob()
        client = self._get_client()
        order_type = OrderType.GTD if order_type_name == "GTD" else OrderType.GTC

        order_kwargs = {
            "token_id": selection.yes_token_id,
            "price": order_price,
            "size": float(selection.share_count),
            "side": BUY,
            "expiration": expiration,
        }

        if version == "v2":
            response = client.create_and_post_order(
                OrderArgs(**order_kwargs),
                options=PartialCreateOrderOptions(
                    tick_size=selection.tick_size,
                    neg_risk=selection.neg_risk,
                ),
                order_type=order_type,
            )
        else:
            response = client.create_and_post_order(
                OrderArgs(**order_kwargs),
                options=PartialCreateOrderOptions(
                    tick_size=selection.tick_size,
                    neg_risk=selection.neg_risk,
                ),
                order_type=order_type,
            )

        logger.info("Order placed: %s", response)
        return {
            "dry_run": False,
            "order_id": response.get("orderID") or response.get("order_id"),
            "status": response.get("status"),
            "price": order_price,
            "order_type": order_type_name,
            "expiration": expiration,
            "expires_at": expires_at,
            "response": response,
        }
