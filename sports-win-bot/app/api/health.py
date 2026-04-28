from fastapi import APIRouter
from app.workers.ws_feed import is_connected

router = APIRouter()


@router.get("/health")
async def health():
    from app.workers.jobs import _active_markets
    return {
        "status": "ok",
        "active_markets": len(_active_markets),
        "ws_connected": is_connected(),
    }


@router.get("/markets")
async def list_markets():
    from app.workers.jobs import _active_markets
    return [
        {
            "player1": m.player1,
            "player2": m.player2,
            "price_p1": m.price_p1,
            "price_p2": m.price_p2,
            "url": m.url,
            "condition_id": m.condition_id,
        }
        for m in _active_markets
    ]
