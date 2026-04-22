"""
M_LLM 서버 (TCP)
────────────────────────────────────────────
backend → m_llm : 키워드 기반 필터링 요청
m_llm   → backend : 필터링된 결과 중 상품 하나 응답

실행: python mllm_server.py
"""

import asyncio
import json
import struct
import pymysql

# ── MySQL 접속 설정 ──
DB_CONFIG = {
    "host": "192.168.1.120",
    "user": "admin",
    "password": "team1!",
    "database": "MSS_DB",
    "charset": "utf8mb4",
}

TABLE_NAME = "llm_shoes"
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 9000


# ─────────────────────────────────────────
# DB 필터링 로직
# ─────────────────────────────────────────
def filter_by_keywords(keyword_input: str) -> dict | None:
    """키워드 수신 → 분리 → DB 필터링 → 결과 조합"""

    # ━━━ STEP 1: 키워드 수신 ━━━
    print(f"\n{'━' * 50}")
    print("  STEP 1 │ 키워드 수신")
    print(f"  원본 입력: \"{keyword_input}\"")

    # ━━━ STEP 2: 키워드 분리 ━━━
    keywords = [
        k.strip()
        for k in keyword_input.replace(",", " ").replace("/", " ").split()
        if k.strip()
    ]
    print("\n  STEP 2 │ 키워드 분리")
    print(f"  분리 결과: {keywords} ({len(keywords)}개)")

    if not keywords:
        print("  :warning: 유효한 키워드 없음")
        return None

    # ━━━ STEP 3: DB 필터링 ━━━
    print("\n  STEP 3 │ DB 필터링")

    conn = pymysql.connect(**DB_CONFIG)
    row = None

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:

            # 3-1) AND 검색
            and_cond = " AND ".join(["tags LIKE %s"] * len(keywords))
            and_params = [f"%{kw}%" for kw in keywords]
            sql = f"SELECT * FROM {TABLE_NAME} WHERE {and_cond} LIMIT 1"

            print(f"  [AND] {sql}")
            print(f"        params = {and_params}")

            cursor.execute(sql, and_params)
            row = cursor.fetchone()

            if row:
                print("  → AND 매칭 성공 :white_check_mark:")
            elif len(keywords) > 1:
                # 3-2) OR 검색
                or_cond = " OR ".join(["tags LIKE %s"] * len(keywords))
                or_params = [f"%{kw}%" for kw in keywords]
                sql = f"SELECT * FROM {TABLE_NAME} WHERE {or_cond} LIMIT 1"

                print("  → AND 매칭 실패, OR 검색 시도...")
                print(f"  [OR]  {sql}")
                print(f"        params = {or_params}")

                cursor.execute(sql, or_params)
                row = cursor.fetchone()

                if row:
                    print("  → OR 매칭 성공 :white_check_mark:")
                else:
                    print("  → OR 매칭도 실패 :x:")
            else:
                print("  → 매칭 실패 :x:")
    finally:
        conn.close()

    if row is None:
        return None

    # ━━━ STEP 4: 결과 조합 ━━━
    tags_str = row.get("tags") or ""
    matched = sum(1 for kw in keywords if kw in tags_str)
    confidence = round(matched / len(keywords), 2)

    result = {
        "product_id": row["SSID"],
        "name": row["model"],
        "brand": row["brand"],
        "color": row["color"],
        "price": row["price"],
        "image_url": row["image_url"],
        "tags": tags_str,
        "confidence": confidence,
    }

    print("\n  STEP 4 │ 결과 조합")
    print(f"  매칭 키워드: {matched}/{len(keywords)} → confidence={confidence}")
    print("  ┌─────────────────────────────────────────")
    print(f"  │ 상품ID : {result['product_id']}")
    print(f"  │ 브랜드 : {result['brand']}")
    print(f"  │ 모델명 : {result['name']}")
    print(f"  │ 색상   : {result['color']}")
    print(f"  │ 가격   : {result['price']:,}원")
    print(f"  │ 신뢰도 : {confidence:.0%}")
    print("  └─────────────────────────────────────────")
    print(f"{'━' * 50}")

    return result


# ─────────────────────────────────────────
# TCP 서버 (backend 요청 수신/응답)
# ─────────────────────────────────────────
async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    addr = writer.get_extra_info("peername")
    print(f"\n╔══ backend 연결: {addr} ══╗")

    try:
        # 수신: [4byte 길이] + [JSON payload]
        header = await reader.readexactly(4)
        length = struct.unpack("!I", header)[0]
        raw = await reader.readexactly(length)
        data = json.loads(raw.decode("utf-8"))

        keyword = data["keyword"]
        print(f"  :inbox_tray: backend → m_llm 수신 키워드: \"{keyword}\"")

        # 필터링 수행
        result = filter_by_keywords(keyword)

        # 응답 조립
        if result:
            response = {"result": result}
        else:
            response = {"result": None, "message": "매칭 상품 없음"}

        resp_bytes = json.dumps(response, ensure_ascii=False).encode("utf-8")

        # 송신: [4byte 길이] + [JSON payload]
        writer.write(struct.pack("!I", len(resp_bytes)))
        writer.write(resp_bytes)
        await writer.drain()

        print(f"  :outbox_tray: m_llm → backend 응답 전송 완료 ({len(resp_bytes)} bytes)")

    except asyncio.IncompleteReadError:
        print(f"  :warning: 연결 끊김: {addr}")
    except Exception as e:
        print(f"  :x: 오류: {e}")
        err = json.dumps({"error": str(e)}).encode("utf-8")
        writer.write(struct.pack("!I", len(err)))
        writer.write(err)
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()
        print(f"╚══ backend 연결 종료: {addr} ══╝\n")


async def main():
    # DB 연결 확인
    print("═══ M_LLM 서버 시작 준비 ═══")

    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
            count = cur.fetchone()[0]
            print(f"  :white_check_mark: DB 연결 성공 ({DB_CONFIG['host']}/{DB_CONFIG['database']})")
            print(f"  :white_check_mark: {TABLE_NAME} 테이블 행 수: {count}")
        conn.close()
    except Exception as e:
        print(f"  :x: DB 연결 실패: {e}")
        return

    # TCP 서버 시작
    server = await asyncio.start_server(handle_client, SERVER_HOST, SERVER_PORT)
    addr = server.sockets[0].getsockname()

    print(f"\n  :rocket: TCP 서버 대기 중: {addr[0]}:{addr[1]}")
    print("  test_client.py로 키워드를 전송하세요\n")

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
