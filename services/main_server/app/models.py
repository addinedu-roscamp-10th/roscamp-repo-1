"""
Component : app/models.py
Role      : 서비스 전체에서 공유하는 Pydantic 요청/응답 모델 정의.
            endpoints/, clients/, moosinsa_service.py 모두 여기서 import 한다.
"""

from pydantic import BaseModel, Field
from typing import Optional, List


class SearchRequest(BaseModel):
    """검색 요청 본문"""
    keyword: str
    accumulated_tags: dict[str, list[str]] = Field(default_factory=dict)


class ShoeItem(BaseModel):
    """M_LLM 이 반환하는 개별 상품 정보"""
    id: Optional[int]   = None
    shoe_id: str        = ""
    brand: str          = ""
    model: str          = ""
    colors: list[str]   = Field(default_factory=list)
    price: int          = 0
    image_url: str      = ""
    tags: str           = ""
    score: float        = 0.0


class SearchResponse(BaseModel):
    """검색 응답 본문"""
    results: list           = []
    count: int              = 0
    accumulated_tags: dict  = {}
    debug: dict             = {}


class SeatStatus(BaseModel):
    name: str
    status: int


class SeatOccupancyRequest(BaseModel):
    seats: List[SeatStatus]
