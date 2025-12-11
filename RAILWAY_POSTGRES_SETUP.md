# Railway PostgreSQL 설정 가이드

## 필요한 정보

Railway에서 PostgreSQL 데이터베이스를 사용하려면 다음 정보가 필요합니다:

### 1. DATABASE_URL (자동 제공)

Railway에서 PostgreSQL 서비스를 생성하면 **DATABASE_URL** 환경 변수가 자동으로 제공됩니다.

형식: `postgresql://user:password@host:port/database`

### 2. Railway에서 PostgreSQL 설정하기

1. **Railway 대시보드에서 프로젝트 선택**
2. **"+ New" 버튼 클릭** → **"Database" 선택** → **"Add PostgreSQL" 선택**
3. PostgreSQL 서비스가 생성되면 자동으로 **DATABASE_URL** 환경 변수가 설정됩니다.

### 3. 환경 변수 확인

Railway 대시보드에서:
- 프로젝트 → **Variables** 탭
- `DATABASE_URL` 환경 변수가 자동으로 생성되어 있는지 확인

### 4. 로컬 개발 시 (.env 파일)

로컬에서 테스트하려면 `.env` 파일에 다음을 추가하세요:

```env
DATABASE_URL=postgresql://user:password@host:port/database
```

또는 Railway CLI를 사용하여 연결:

```bash
railway link
railway variables
```

## 코드 변경 사항

코드는 이미 PostgreSQL을 사용하도록 수정되었습니다:

- ✅ `asyncpg` 라이브러리 사용 (비동기 PostgreSQL 드라이버)
- ✅ `DATABASE_URL` 환경 변수에서 연결 정보 자동 읽기
- ✅ 모든 데이터베이스 호출이 비동기(async/await)로 변경됨

## 주의사항

1. **기존 SQLite 데이터 마이그레이션**: 기존 SQLite 데이터베이스(`user_data.db`)의 데이터를 PostgreSQL로 마이그레이션해야 할 수 있습니다.

2. **환경 변수 확인**: Railway에 배포하기 전에 `DATABASE_URL` 환경 변수가 설정되어 있는지 확인하세요.

3. **연결 풀**: 코드는 연결 풀을 사용하여 효율적으로 데이터베이스 연결을 관리합니다.

## 문제 해결

### DATABASE_URL이 없을 때

에러 메시지: `DATABASE_URL environment variable is not set`

해결 방법:
1. Railway 대시보드에서 PostgreSQL 서비스를 생성했는지 확인
2. Variables 탭에서 `DATABASE_URL`이 있는지 확인
3. 없다면 PostgreSQL 서비스를 다시 생성하거나 수동으로 추가

### 연결 실패 시

1. Railway PostgreSQL 서비스가 실행 중인지 확인
2. `DATABASE_URL` 형식이 올바른지 확인 (`postgresql://` 또는 `postgres://`)
3. Railway 로그에서 오류 메시지 확인

