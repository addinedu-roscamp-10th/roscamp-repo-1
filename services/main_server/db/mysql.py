import mysql.connector
import json
from mysql.connector import Error
from fastapi import HTTPException
from dotenv import load_dotenv
import os

load_dotenv()

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        charset="utf8mb4"
    )

# 로봇 조회
def get_robot_by_domain_id(domain_id: int):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        sql = """
        SELECT id, name, status, domain_id
        FROM robot
        WHERE domain_id = %s
        LIMIT 1
        """
        cursor.execute(sql, (domain_id,))
        return cursor.fetchone()

    except Error as e:
        raise RuntimeError(f"로봇 조회 실패: {e}")

    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


# 상태 업데이트
def update_robot_status(robot_id: int, status: int):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        sql = """
        UPDATE robot
        SET status = %s
        WHERE id = %s
        """
        cursor.execute(sql, (status, robot_id))
        conn.commit()

    except Error as e:
        raise RuntimeError(f"상태 업데이트 실패: {e}")

    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

#신발 조회

# def get_shoe_all_information 등록된 전체 신발 조회 
def get_shoe_all_information():
    conn = None
    cursor = None

    print('get_shoe_all_information ------ ');
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        sql = """
            SELECT *
            FROM shoes
        """
        cursor.execute(sql)
        rows = cursor.fetchall()
        print(rows)
        return rows

    except mysql.connector.Error as e:
        print("MySQL 오류:", e)
        raise HTTPException(status_code=500, detail=f"MySQL 오류: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        print("서버 오류:", e)
        raise HTTPException(status_code=500, detail=f"서버 오류: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# def get_shoe_information_by_shoe_id 등록한 신발 중 shoe_id 로 검색
def get_shoe_information_by_shoe_id(shoe_id: str):   
    print("get_product_by_shoe_id: ", shoe_id)
    conn = None
    cursor = None

    try:      
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        sql = """
            SELECT *
            FROM shoes
            WHERE shoe_id = %s
        """
        cursor.execute(sql, (shoe_id,))
        rows = cursor.fetchone()

        if not rows:
            raise HTTPException(status_code=404, detail="상품을 찾을 수 없습니다.")

        # image_url = build_image_url(row, image_base_url)

        return rows

    except mysql.connector.Error as e:
        print("MySQL 오류:", e)
        raise HTTPException(status_code=500, detail=f"MySQL 오류: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        print("서버 오류:", e)
        raise HTTPException(status_code=500, detail=f"서버 오류: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

#창고 신발 조회
def get_shoe_information_by_shoe_id_from_inventory(shoe_id: str):   
    print("get_shoe_information_by_shoe_id_from_inventory: ", shoe_id)
    conn = None
    cursor = None

    try:      
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        sql = """
            SELECT *
            FROM shoes_inventory
            WHERE shoe_id = %s
        """
        cursor.execute(sql, (shoe_id,))
        rows = cursor.fetchall()   # 결과 전체 읽기

        if not rows:
            raise HTTPException(status_code=404, detail="상품을 찾을 수 없습니다.")

        # image_url = build_image_url(row, image_base_url)

        return rows

    except mysql.connector.Error as e:
        print("MySQL 오류:", e)
        raise HTTPException(status_code=500, detail=f"MySQL 오류: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        print("서버 오류:", e)
        raise HTTPException(status_code=500, detail=f"서버 오류: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()