import asyncio
import json
import struct
import re
import difflib
import pymysql
import random

# ── MySQL 접속 설정 ──
DB_CONFIG = {
    "host": "192.168.1.120",
    "user": "admin",
    "password": "team1!",
    "database": "MSS_DB",
    "charset": "utf8mb4",
}

SHOES_TABLE_NAME = "shoes"
INVENTORY_TABLE_NAME = "shoes_inventory"
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 9000
TOP_K = 3

TAG_SCHEMA = {
    "activity": ["러닝", "웨이트", "등산", "축구", "농구", "데이트", "출근", "일상", "격식", "캠핑", "물놀이"],
    "style": ["힙한", "무난한", "깔끔한", "화려한", "빈티지", "클래식", "귀여운", "레트로", "테크웨어", "고프코어", "스포티", "발레코어"],
    "feature": ["쿠션감", "발볼 넓음", "방수", "키높이", "가벼움", "통기성", "미끄럼 방지", "편안함", "내구성", "보온성"],
    "color": ["white", "black", "gray", "grey", "red", "orange", "yellow", "green", "blue", "purple", "brown", "beige", "silver", "navy", "pink"],
    "brand": ["나이키", "아디다스", "뉴발란스", "반스", "컨버스", "아식스", "살로몬", "오니츠카타이거", "푸마", "미즈노", "킨", "호카", "닥터마틴", "어그", "리복"],
    "season_weather": ["봄/가을용", "여름용", "겨울용", "사계절용", "우천용"],
    "price": ["가성비", "일반", "프리미엄"],
    "target": ["남성용", "여성용", "공용"]
}

WEIGHTS = {
    "activity": 5, "style": 3, "feature": 4, "color": 3,
    "brand": 5, "season_weather": 3, "price": 4, "target": 3
}

MODEL_SYNONYMS = {
    "에어푸스": "에어 포스 1 07",
    "에어포스": "에어 포스 1 07",
    "에어포스1": "에어 포스 1 07",
    "에어포스107": "에어 포스 1 07",
    "삼바오쥐": "삼바 OG",
    "삼바오지": "삼바 OG",
    "젤카야노": "젤 카야노 14",
    "보메로": "줌 보메로 5",
    "카야노": "젤 카야노 14",
    "님버스": "젤 님버스 26",
    "샨티": "샨티 슬라이드",
    "멕시코66": "멕시코 66",
    "스피드캣": "스피드캣 OG",
}


def normalize_text(text):
    return re.sub(r"[^가-힣a-zA-Z0-9]", "", str(text)).lower()


def normalize_model_text(text):
    text = str(text).lower()
    text = re.sub(r"[^a-z0-9가-힣\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_codes(text):
    text = str(text).lower().replace("-", "").replace(".", "")
    return re.findall(r"[a-z]*\d+[a-z]*", text)


def decompose_hangul(text):
    CHOSEONG = [
        'ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ','ㅅ',
        'ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ'
    ]
    JUNGSEONG = [
        'ㅏ','ㅐ','ㅑ','ㅒ','ㅓ','ㅔ','ㅕ','ㅖ','ㅗ','ㅘ',
        'ㅙ','ㅚ','ㅛ','ㅜ','ㅝ','ㅞ','ㅟ','ㅠ','ㅡ','ㅢ','ㅣ'
    ]
    JONGSEONG = [
        '', 'ㄱ','ㄲ','ㄳ','ㄴ','ㄵ','ㄶ','ㄷ','ㄹ','ㄺ',
        'ㄻ','ㄼ','ㄽ','ㄾ','ㄿ','ㅀ','ㅁ','ㅂ','ㅄ','ㅅ',
        'ㅆ','ㅇ','ㅈ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ'
    ]

    result = []
    for char in str(text):
        code = ord(char)
        if 0xAC00 <= code <= 0xD7A3:
            base = code - 0xAC00
            cho = base // 588
            jung = (base % 588) // 28
            jong = base % 28
            result.append(CHOSEONG[cho])
            result.append(JUNGSEONG[jung])
            if JONGSEONG[jong]:
                result.append(JONGSEONG[jong])
        else:
            if char.strip():
                result.append(char.lower())
    return "".join(result)


def parse_color_field(color_raw):
    """
    DB의 colors/color 컬럼을 list[str]로 통일
    예:
    - '["white","black"]' -> ["white", "black"]
    - 'white' -> ["white"]
    - None -> []
    """
    if color_raw is None:
        return []

    if isinstance(color_raw, list):
        return [str(c).strip().lower() for c in color_raw if str(c).strip()]

    if isinstance(color_raw, str):
        color_raw = color_raw.strip()
        if not color_raw:
            return []

        try:
            parsed = json.loads(color_raw)
            if isinstance(parsed, list):
                return [str(c).strip().lower() for c in parsed if str(c).strip()]
        except Exception:
            pass

        return [color_raw.lower()]

    return [str(color_raw).strip().lower()]


def color_list_to_text(color_value):
    if isinstance(color_value, list):
        return " ".join(str(c) for c in color_value if str(c).strip())
    return str(color_value)


def get_table_columns(cursor, table_name):
    """테이블의 컬럼명을 list[str]로 반환"""
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
    return [row["Field"] for row in cursor.fetchall()]


def pick_join_condition(shoes_columns, inventory_columns):
    """
    shoes 테이블과 shoes_inventory 테이블을 연결할 조건을 자동 선택한다.

    우선순위:
    1) shoes_inventory.shoe_id     = shoes.id
    2) shoes_inventory.shoes_id    = shoes.id
    3) shoes_inventory.product_id  = shoes.id
    4) shoes_inventory.item_id     = shoes.id
    5) shoes_inventory.id          = shoes.id
    6) brand/model 기반 매칭
    7) model 기반 매칭

    실제 DB 컬럼명이 다르면 아래 후보에 컬럼명을 추가하면 된다.
    """
    shoes_set = set(shoes_columns)
    inv_set = set(inventory_columns)

    if "id" not in shoes_set:
        raise ValueError("shoes 테이블에 id 컬럼이 필요합니다.")

    fk_candidates = ["shoe_id", "shoes_id", "product_id", "item_id", "id"]
    for fk in fk_candidates:
        if fk in inv_set:
            return f"i.`{fk}` = s.`id`"

    if "brand" in shoes_set and "model" in shoes_set and "brand" in inv_set and "model" in inv_set:
        return "i.`brand` = s.`brand` AND i.`model` = s.`model`"

    if "model" in shoes_set and "model" in inv_set:
        return "i.`model` = s.`model`"

    raise ValueError(
        "shoes와 shoes_inventory를 연결할 컬럼을 찾지 못했습니다. "
        "shoes_inventory에 shoe_id/shoes_id/product_id/item_id/id 중 하나가 있거나, "
        "brand/model 또는 model 컬럼이 있어야 합니다."
    )


def to_int_stock(value):
    """DB에서 넘어온 stock 값을 안전하게 int로 변환"""
    try:
        if value is None:
            return 0
        return int(value)
    except Exception:
        return 0


def load_inventory_from_db():
    """
    shoes_inventory.stock 기준으로 재고가 있는 상품만 shoes에서 가져온다.

    핵심 로직:
    - shoes_inventory 테이블에서 stock 컬럼 확인
    - shoes와 shoes_inventory 연결 컬럼 자동 감지
    - 같은 상품의 재고 row가 여러 개면 SUM(stock)으로 합산
    - total_stock > 0인 상품만 추천 후보 inventory에 추가
    """
    inventory = []
    conn = pymysql.connect(**DB_CONFIG)

    total_count = 0

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:

            # ✅ 전체 상품 개수 (필터 전)
            cursor.execute("SELECT COUNT(*) AS cnt FROM shoes")
            total_count = cursor.fetchone()["cnt"]

            # ✅ 재고 있는 상품만
            cursor.execute("""
                SELECT 
                    s.id,
                    s.shoe_id,
                    s.brand,
                    s.model,
                    s.colors,
                    s.image_url,
                    s.price,
                    s.tags,
                    SUM(si.stock) AS stock
                FROM shoes s
                JOIN shoes_inventory si
                    ON s.shoe_id = si.shoe_id
                WHERE si.stock > 0
                GROUP BY 
                    s.id, s.shoe_id, s.brand, s.model,
                    s.colors, s.image_url, s.price, s.tags
            """)

            rows = cursor.fetchall()

            for row in rows:
                color_raw = row.get("colors")
                color_parsed = parse_color_field(color_raw)

                inventory.append({
                    "id": row.get("id"),
                    "shoe_id": row.get("shoe_id"),
                    "brand": row.get("brand"),
                    "model": row.get("model"),
                    "color": color_parsed,
                    "image_url": row.get("image_url"),
                    "price": int(row.get("price") or 0),
                    "tags": row.get("tags") or "",
                    "stock": int(row.get("stock") or 0),
                })
    finally:
        conn.close()

    return inventory, total_count


def find_best_model(user_text, inventory, threshold=0.62):
    clean_input = normalize_model_text(user_text)
    compact_input = normalize_text(user_text)

    for wrong, official in MODEL_SYNONYMS.items():
        if normalize_text(wrong) in compact_input:
            return official

    all_models = list(set([shoe["model"] for shoe in inventory if shoe.get("model")]))

    input_tokens = clean_input.split()
    input_codes = extract_codes(clean_input)
    input_jamo = decompose_hangul(compact_input)

    best_model = None
    best_score = 0.0

    weak_tokens = {
        "nike", "adidas", "new", "balance", "air", "zoom",
        "og", "low", "mid", "black", "white", "gel", "wave"
    }

    for model in all_models:
        model_norm = normalize_model_text(model)
        model_compact = normalize_text(model)
        model_tokens = model_norm.split()
        model_codes = extract_codes(model_norm)
        model_jamo = decompose_hangul(model_compact)

        score = 0.0

        for code in input_codes:
            if code in model_codes:
                score += 1.0

        for ut in input_tokens:
            for mt in model_tokens:
                if ut == mt:
                    score += 0.4 if ut not in weak_tokens else 0.1

        token_sim = 0.0
        for ut in input_tokens:
            best_token_score = 0.0
            for mt in model_tokens:
                sim = difflib.SequenceMatcher(None, ut, mt).ratio()
                best_token_score = max(best_token_score, sim)
            token_sim += best_token_score

        if input_tokens:
            token_sim /= len(input_tokens)

        score_full = difflib.SequenceMatcher(None, compact_input, model_compact).ratio()
        score_jamo = difflib.SequenceMatcher(None, input_jamo, model_jamo).ratio()

        score += max(token_sim, score_full, score_jamo)

        if compact_input and (compact_input in model_compact or model_compact in compact_input):
            score += 0.2

        if score > best_score:
            best_score = score
            best_model = model

    if best_score >= threshold:
        return best_model

    return None


def extract_brands(user_text):
    found = []

    for brand in TAG_SCHEMA["brand"]:
        if brand in user_text:
            found.append(brand)

    dedup = []
    seen = set()
    for x in found:
        if x not in seen:
            seen.add(x)
            dedup.append(x)

    return dedup


def score_shoe(shoe, accumulated_tags, target_model, mentioned_brands, user_text):
    score = 0
    color_str = color_list_to_text(shoe["color"])
    db_str = f"{shoe['brand']} {shoe['model']} {color_str} {shoe['tags']}".lower().replace(" ", "")

    for field, vals in accumulated_tags.items():
        if not vals:
            continue

        weight = WEIGHTS.get(field, 3)
        for v in vals:
            if v.lower().replace(" ", "") in db_str:
                score += weight

    if target_model and normalize_text(target_model) == normalize_text(shoe["model"]):
        score += 100

    for brand in mentioned_brands:
        if normalize_text(brand) in normalize_text(shoe["brand"]):
            score += 30

    user_compact = normalize_text(user_text)
    if user_compact and user_compact in normalize_text(shoe["model"]):
        score += 20
    if user_compact and user_compact in normalize_text(shoe["brand"]):
        score += 15

    return score


def match_all_filters(shoe, accumulated_tags):
    color_str = color_list_to_text(shoe["color"])
    db_str = f"{shoe['brand']} {shoe['model']} {color_str} {shoe['tags']}".lower().replace(" ", "")

    for field, vals in accumulated_tags.items():
        if not vals:
            continue

        if not any(v.lower().replace(" ", "") in db_str for v in vals):
            return False

    return True


def get_recommendations(user_text, accumulated_tags, inventory):
    mentioned_brands = extract_brands(user_text)
    target_model = find_best_model(user_text, inventory, threshold=0.62)

    if target_model:
        candidates = [
            s for s in inventory
            if normalize_text(s["model"]) == normalize_text(target_model)
        ]
    else:
        candidates = [s for s in inventory if match_all_filters(s, accumulated_tags)]

    ranked = []
    for shoe in candidates:
        score = 0
        color_str = color_list_to_text(shoe["color"])
        db_str = f"{shoe['brand']} {shoe['model']} {color_str} {shoe['tags']}".lower().replace(" ", "")

        for field, vals in accumulated_tags.items():
            if not vals:
                continue

            weight = WEIGHTS.get(field, 3)
            for v in vals:
                if v.lower().replace(" ", "") in db_str:
                    score += weight

        if target_model and normalize_text(target_model) == normalize_text(shoe["model"]):
            score += 100

        for brand in mentioned_brands:
            if normalize_text(brand) in normalize_text(shoe["brand"]):
                score += 30

        user_compact = normalize_text(user_text)
        if user_compact and user_compact in normalize_text(shoe["model"]):
            score += 20
        if user_compact and user_compact in normalize_text(shoe["brand"]):
            score += 15

        target_colors = accumulated_tags.get("color", [])
        for color in target_colors:
            if any(normalize_text(color) in normalize_text(col) for col in shoe["color"]):
                score += 20

        if score > 0 or target_model or (not any(accumulated_tags.values()) and not target_model):
            ranked.append({
                "id": shoe.get("id"),
                "shoe_id": shoe.get("shoe_id"),
                "brand": shoe.get("brand"),
                "model": shoe.get("model"),
                "colors": shoe.get("color"),
                "price": int(shoe.get("price") or 0),
                "stock": int(shoe.get("stock") or 0),
                "image_url": shoe.get("image_url"),
                "tags": shoe.get("tags"),
                "score": score
            })

    ranked = sorted(
        ranked,
        key=lambda x: (-x["score"], x["price"] if isinstance(x["price"], int) else 999999999)
    )

    top_pool = ranked[:10]
    random_results = random.sample(top_pool, min(3, len(top_pool)))

    return random_results, target_model, mentioned_brands


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    addr = writer.get_extra_info("peername")
    print(f"\n╔══ backend 연결: {addr} ══╗")

    try:
        header = await reader.readexactly(4)
        length = struct.unpack("!I", header)[0]
        raw = await reader.readexactly(length)
        data = json.loads(raw.decode("utf-8"))

        user_text = data.get("user_text", "")
        accumulated_tags = data.get("accumulated_tags", {k: [] for k in TAG_SCHEMA.keys()})

        print(f'backend → m_llm  원문 입력: "{user_text}"')

        print(f"\n{'━' * 50}")
        print("  STEP 1 │ 입력 수신")
        print(f'  user_text       : "{user_text}"')
        print(f"  accumulated_tags: {accumulated_tags}")

        inventory, total_count = load_inventory_from_db()
        print(f"\n  STEP 2 │ DB 로드")
        print(f"  재고 있는 inventory 개수: {len(inventory)}")

        print("\n[DEBUG] inventory brand/model 샘플")
        for s in inventory[:10]:
            print(f"brand={repr(s['brand'])}, model={repr(s['model'])}, color={repr(s['color'])}, stock={repr(s.get('stock'))}")

        ranked, target_model, mentioned_brands = get_recommendations(user_text, accumulated_tags, inventory)

        print(f"\n  STEP 3 │ 분석 결과")
        print(f"  누적 태그: {accumulated_tags}")
        print(f"  모델 직접 매칭: {target_model}")
        print(f"  브랜드 감지: {mentioned_brands}")

        print(f"\n  STEP 4 │ 추천 결과")
        if ranked:
            print(f"  최종 추천 개수: {len(ranked)}개")
            print("  ┌─────────────────────────────────────────")
            for idx, item in enumerate(ranked, start=1):
                print(f"  │ [{idx}] 브랜드 : {item['brand']}")
                print(f"  │     모델명 : {item['model']}")
                print(f"  │     색상   : {', '.join(item['colors']) if isinstance(item['colors'], list) else item['colors']}")
                if isinstance(item["price"], int):
                    print(f"  │     가격   : {item['price']:,}원")
                else:
                    print(f"  │     가격   : {item['price']}")
                print(f"  │     재고   : {item.get('stock', 0)}")
                print(f"  │     점수   : {item['score']}")
            print("  └─────────────────────────────────────────")
        else:
            print("  추천 결과 없음")

        response = {
            "results": ranked,
            "count": len(ranked),
            "debug": {
                "accumulated_tags": accumulated_tags,
                "target_model": target_model,
                "mentioned_brands": mentioned_brands,
                "db_dump" : {
                    "total_count": total_count,
                    "filtered_count": len(inventory)
                }
            }
        }

        if not ranked:
            response["message"] = "매칭 상품 없음"

        resp_bytes = json.dumps(response, ensure_ascii=False).encode("utf-8")

        writer.write(struct.pack("!I", len(resp_bytes)))
        writer.write(resp_bytes)
        await writer.drain()

        print(f"m_llm → backend  응답 전송 완료 ({len(resp_bytes)} bytes)")

    except asyncio.IncompleteReadError:
        print(f"연결 끊김: {addr}")
    except Exception as e:
        print(f"오류: {e}")
        err = json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8")
        writer.write(struct.pack("!I", len(err)))
        writer.write(err)
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()
        print(f"╚══ backend 연결 종료: {addr} ══╝\n")


async def main():
    print("═══ M_LLM 서버 시작 준비 ═══")
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM `{SHOES_TABLE_NAME}`")
            shoes_count = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM `{INVENTORY_TABLE_NAME}`")
            inventory_count = cur.fetchone()[0]
            cur.execute(f"SELECT COUNT(*) FROM `{INVENTORY_TABLE_NAME}` WHERE `stock` > 0")
            stock_count = cur.fetchone()[0]
            print(f" ✅ DB 연결 성공 ({DB_CONFIG['host']}/{DB_CONFIG['database']})")
            print(f" ✅ {SHOES_TABLE_NAME} 테이블 행 수: {shoes_count}")
            print(f" ✅ {INVENTORY_TABLE_NAME} 테이블 행 수: {inventory_count}")
            print(f" ✅ {INVENTORY_TABLE_NAME}.stock > 0 행 수: {stock_count}")
        conn.close()
    except Exception as e:
        print(f"DB 연결 실패: {e}")
        return

    server = await asyncio.start_server(handle_client, SERVER_HOST, SERVER_PORT)
    addr = server.sockets[0].getsockname()
    print(f"\nTCP 서버 대기 중: {addr[0]}:{addr[1]}")
    print("  llm_service.py로 요청을 전송하세요\n")

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())