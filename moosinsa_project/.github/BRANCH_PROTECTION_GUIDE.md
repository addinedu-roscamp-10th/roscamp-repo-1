# Branch Protection 설정 가이드

GitHub 웹 → Settings → Branches → Add rule

## main 브랜치
Branch name pattern: main
- [x] Require a pull request before merging
  - [x] Require approvals: 1
- [x] Do not allow bypassing the above settings

## develop 브랜치
Branch name pattern: develop
- direct push 허용 (GitHub Actions 자동 merge를 위해)
- force push 비활성화
