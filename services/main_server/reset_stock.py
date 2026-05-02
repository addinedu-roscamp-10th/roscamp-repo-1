#!/home/addinedu/venv/ms_service_test/bin/python3
"""
reset_stock.py
==============
shoes_inventory 테이블의 모든 유효 상품 재고를 1로 원복한다.

사용법:
    python reset_stock.py          # 실행 (확인 프롬프트 있음)
    python reset_stock.py --force  # 확인 프롬프트 없이 즉시 실행
"""

import sys
from pathlib import Path

# main_server/.env 로드
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import os
import mysql.connector


def get_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        charset="utf8mb4",
    )


def reset_stock():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # 현재 상태 확인
        cursor.execute(
            "SELECT COUNT(*) FROM shoes_inventory "
            "WHERE shoe_id IS NOT NULL AND shoe_id != ''"
        )
        total = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM shoes_inventory "
            "WHERE shoe_id IS NOT NULL AND shoe_id != '' AND stock != 1"
        )
        changed = cursor.fetchone()[0]

        print(f"대상 상품 행 수 : {total}개")
        print(f"stock != 1 행 수: {changed}개")

        if changed == 0:
            print("이미 모든 재고가 1입니다. 변경 없음.")
            return

        # 원복 실행
        cursor.execute(
            "UPDATE shoes_inventory SET stock = 1 "
            "WHERE shoe_id IS NOT NULL AND shoe_id != ''"
        )
        conn.commit()
        print(f"완료: {cursor.rowcount}개 행 stock → 1 원복")

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    force = "--force" in sys.argv

    print("=" * 40)
    print("  shoes_inventory stock 원복 (→ 1)")
    print(f"  DB: {os.getenv('DB_HOST')} / {os.getenv('DB_NAME')}")
    print("=" * 40)

    if not force:
        ans = input("실행하시겠습니까? [y/N] ").strip().lower()
        if ans != "y":
            print("취소됨.")
            sys.exit(0)

    reset_stock()
