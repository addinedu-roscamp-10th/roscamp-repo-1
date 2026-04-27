from fastapi import HTTPException

_service = None

def set_service(service):
    global _service
    _service = service

def get_service():
    if _service is None:
        raise HTTPException(status_code=503, detail="서비스 초기화 중입니다.")
    return _service