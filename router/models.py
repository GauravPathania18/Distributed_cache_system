from pydantic import BaseModel
from typing import Any, Optional


class CacheEntry(BaseModel):

    value: Any

    ttl: Optional[int] = None
