from typing import Dict, List
from pydantic import BaseModel, Field


class IngestPayload(BaseModel):
    symbol: str
    server_time: int = 0
    gmt_offset: int = 0
    digits: int = 2
    point: float = 0.01
    tick_size: float = 0.01
    tick_value: float = 1.0
    vol_min: float = 0.01
    vol_step: float = 0.01
    vol_max: float = 100.0
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    free_margin: float = 0.0
    margin_level: float = 0.0
    currency: str = "USD"
    full: bool = False
    # each row: [time, open, high, low, close]  (CLOSED candles only, server time)
    tfs: Dict[str, List[List[float]]] = Field(default_factory=dict)
    positions: List[dict] = Field(default_factory=list)
    deals: List[dict] = Field(default_factory=list)
    sync_time: int = 0
