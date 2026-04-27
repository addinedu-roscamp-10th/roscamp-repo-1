"""
Component : app/clients/llm.py
Role      : M_LLM 서버와의 TCP 통신 담당 클라이언트.
            MoosinsaService 가 검색 파이프라인 STEP 3 에서 호출한다.

프로토콜
  송신: [4bytes big-endian 길이] + [JSON {"user_text": "...", "accumulated_tags": {...}}]
  수신: [4bytes big-endian 길이] + [JSON {"results": [...], "count": N, "debug": {...}}]

설정값 (.env)
  MLLM_HOST     - M_LLM 서버 IP
  MLLM_PORT     - M_LLM 서버 포트        (기본 9000)
  MLLM_TIMEOUT  - TCP 타임아웃 (초)      (기본 30.0)
"""

import asyncio
import json
import logging
import os
import struct

from typing import Optional
from dotenv import load_dotenv

from app.models import SearchResponse, ShoeItem

load_dotenv()

logger = logging.getLogger("clients.llm")

TAG_SCHEMA_KEYS = [
    "activity", "style", "feature", "color",
    "brand", "season_weather", "price", "target",
]


class MLLMClient:
    """
    M_LLM 서버 TCP 클라이언트.
    매 요청마다 새 TCP 연결을 맺고 응답 수신 후 닫는다 (stateless).
    """

    def __init__(
        self,
        host: str   = os.getenv("MLLM_HOST"),
        port: int   = int(os.getenv("MLLM_PORT", 9000)),
        timeout: float = float(os.getenv("MLLM_TIMEOUT", 30.0)),
    ):
        self.host    = host
        self.port    = port
        self.timeout = timeout

    # ── 공개 메서드 ──────────────────────────────────────────

    async def health_check(self) -> bool:
        """서버 연결 가능 여부 확인. 연결만 열고 바로 닫는다."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=3.0,
            )
            writer.close()
            await writer.wait_closed()
            logger.info(f"M_LLM health_check 성공: {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.warning(f"M_LLM health_check 실패: {e}")
            return False

    async def request_filtering(
        self,
        user_text: str,
        accumulated_tags: dict,
    ) -> Optional[SearchResponse]:
        """
        키워드 + 누적 태그 기반 상품 필터링 요청.

        input : user_text        - 사용자 검색 키워드
                accumulated_tags - 이전 대화에서 누적된 태그
        output: SearchResponse   - 필터링된 상품 목록
                None             - 오류 또는 결과 없음
        """
        full_tags = {k: accumulated_tags.get(k, []) for k in TAG_SCHEMA_KEYS}
        try:
            resp = await self._send_tcp(user_text, full_tags)
        except asyncio.TimeoutError:
            logger.error(f"M_LLM TCP 타임아웃 (>{self.timeout}s)")
            return None
        except ConnectionRefusedError:
            logger.error(f"M_LLM 연결 거부: {self.host}:{self.port}")
            return None
        except Exception as e:
            logger.error(f"M_LLM request_filtering 예외: {e}")
            return None

        if resp.get("error"):
            logger.error(f"M_LLM 서버 오류: {resp['error']}")
            return None

        results = resp.get("results", [])
        if not results:
            logger.warning("M_LLM 매칭 상품 없음")
            return None

        logger.info(f"M_LLM 응답 - {len(results)}개 수신")
        return SearchResponse(
            results          = [ShoeItem(**item) for item in results],
            count            = resp.get("count", len(results)),
            accumulated_tags = resp.get("debug", {}).get("accumulated_tags", full_tags),
            debug            = resp.get("debug", {}),
        )

    # ── 내부 메서드 ──────────────────────────────────────────

    async def _send_tcp(self, user_text: str, accumulated_tags: dict) -> dict:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.timeout,
        )
        try:
            payload = json.dumps(
                {"user_text": user_text, "accumulated_tags": accumulated_tags},
                ensure_ascii=False,
            ).encode("utf-8")
            writer.write(struct.pack("!I", len(payload)) + payload)
            await writer.drain()

            resp_len = struct.unpack("!I", await asyncio.wait_for(
                reader.readexactly(4), timeout=self.timeout,
            ))[0]
            resp_raw = await asyncio.wait_for(
                reader.readexactly(resp_len), timeout=self.timeout,
            )
            return json.loads(resp_raw.decode("utf-8"))
        finally:
            writer.close()
            await writer.wait_closed()
