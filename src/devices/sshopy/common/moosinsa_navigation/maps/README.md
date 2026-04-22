# Maps 폴더

이 폴더에는 세트장 SLAM 맵 파일과 keepout mask가 들어갑니다.

## 핑키에서 맵 파일 복사하기

```bash
# 핑키에서 로컬로 맵 파일 복사
scp pinky@192.168.1.112:~/pinky_pro/src/pinky_pro/pinky_navigation/maps/*.pgm ./
scp pinky@192.168.1.112:~/pinky_pro/src/pinky_pro/pinky_navigation/maps/*.yaml ./
```

## 필수 파일
- `keepout_mask.pgm` + `keepout_mask.yaml` — 진입금지 구역
- `mapgood.pgm` + `mapgood.yaml` — 메인 세트장 맵 (또는 사용할 맵)
