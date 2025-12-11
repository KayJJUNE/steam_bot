"""
Railway PostgreSQL 데이터베이스 통계 및 관리 스크립트

이 스크립트는 다음 기능을 제공합니다:
1. Step 1~2, Step 1~3, Step 1~4 완료한 유저 수 및 디스코드 아이디 조회
2. 롤을 획득한 유저 및 아이디 조회
3. Step 1 유저가 제출한 스팀 아이디 조회
"""

import os
import asyncpg
from typing import List, Dict, Optional
from dotenv import load_dotenv
from datetime import datetime

# 환경 변수 로드
load_dotenv()


class DatabaseStats:
    """데이터베이스 통계 및 조회 클래스"""
    
    def __init__(self):
        self.pool = None
    
    async def _get_pool(self):
        """데이터베이스 연결 풀 가져오기"""
        if self.pool is None:
            database_url = os.getenv('DATABASE_URL') or os.getenv('DATABASE_PUBLIC_URL')
            if not database_url:
                raise ValueError("DATABASE_URL or DATABASE_PUBLIC_URL environment variable is not set")
            
            # asyncpg는 postgres:// 형식 사용
            if database_url.startswith('postgresql://'):
                database_url = database_url.replace('postgresql://', 'postgres://', 1)
            
            self.pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
        return self.pool
    
    async def close(self):
        """데이터베이스 연결 풀 종료"""
        if self.pool:
            await self.pool.close()
    
    async def get_step1_to_step2_users(self) -> List[Dict]:
        """Step 1과 Step 2를 완료한 유저 조회"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT 
                    discord_id,
                    steam_id,
                    quest1_complete,
                    quest2_complete,
                    created_at
                FROM users
                WHERE quest1_complete = 1 AND quest2_complete = 1
                ORDER BY created_at DESC
            ''')
            
            return [
                {
                    'discord_id': row['discord_id'],
                    'steam_id': row['steam_id'],
                    'quest1_complete': bool(row['quest1_complete']),
                    'quest2_complete': bool(row['quest2_complete']),
                    'created_at': row['created_at']
                }
                for row in rows
            ]
    
    async def get_step1_to_step3_users(self) -> List[Dict]:
        """Step 1, Step 2, Step 3을 완료한 유저 조회"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT 
                    discord_id,
                    steam_id,
                    quest1_complete,
                    quest2_complete,
                    quest3_complete,
                    created_at
                FROM users
                WHERE quest1_complete = 1 
                  AND quest2_complete = 1 
                  AND quest3_complete = 1
                ORDER BY created_at DESC
            ''')
            
            return [
                {
                    'discord_id': row['discord_id'],
                    'steam_id': row['steam_id'],
                    'quest1_complete': bool(row['quest1_complete']),
                    'quest2_complete': bool(row['quest2_complete']),
                    'quest3_complete': bool(row['quest3_complete']),
                    'created_at': row['created_at']
                }
                for row in rows
            ]
    
    async def get_step1_to_step4_users(self) -> List[Dict]:
        """Step 1, Step 2, Step 3, Step 4를 모두 완료한 유저 조회"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT 
                    discord_id,
                    steam_id,
                    quest1_complete,
                    quest2_complete,
                    quest3_complete,
                    quest4_complete,
                    created_at
                FROM users
                WHERE quest1_complete = 1 
                  AND quest2_complete = 1 
                  AND quest3_complete = 1 
                  AND quest4_complete = 1
                ORDER BY created_at DESC
            ''')
            
            return [
                {
                    'discord_id': row['discord_id'],
                    'steam_id': row['steam_id'],
                    'quest1_complete': bool(row['quest1_complete']),
                    'quest2_complete': bool(row['quest2_complete']),
                    'quest3_complete': bool(row['quest3_complete']),
                    'quest4_complete': bool(row['quest4_complete']),
                    'created_at': row['created_at']
                }
                for row in rows
            ]
    
    async def get_role_acquired_users(self) -> List[Dict]:
        """모든 퀘스트를 완료하여 롤을 획득한 유저 조회 (Step 1~4 모두 완료)"""
        # 롤을 획득한 유저 = 모든 퀘스트 완료 유저와 동일
        return await self.get_step1_to_step4_users()
    
    async def get_step1_users_with_steam_id(self) -> List[Dict]:
        """Step 1을 완료한 유저의 스팀 아이디 조회"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT 
                    discord_id,
                    steam_id,
                    quest1_complete,
                    created_at
                FROM users
                WHERE quest1_complete = 1 AND steam_id IS NOT NULL
                ORDER BY created_at DESC
            ''')
            
            return [
                {
                    'discord_id': row['discord_id'],
                    'steam_id': row['steam_id'],
                    'quest1_complete': bool(row['quest1_complete']),
                    'created_at': row['created_at']
                }
                for row in rows
            ]
    
    async def get_statistics(self) -> Dict:
        """전체 통계 조회"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # 전체 유저 수
            total_users = await conn.fetchval('SELECT COUNT(*) FROM users')
            
            # Step 1 완료
            step1_count = await conn.fetchval('SELECT COUNT(*) FROM users WHERE quest1_complete = 1')
            
            # Step 1~2 완료
            step1_2_count = await conn.fetchval('''
                SELECT COUNT(*) FROM users 
                WHERE quest1_complete = 1 AND quest2_complete = 1
            ''')
            
            # Step 1~3 완료
            step1_3_count = await conn.fetchval('''
                SELECT COUNT(*) FROM users 
                WHERE quest1_complete = 1 AND quest2_complete = 1 AND quest3_complete = 1
            ''')
            
            # Step 1~4 완료 (롤 획득)
            step1_4_count = await conn.fetchval('''
                SELECT COUNT(*) FROM users 
                WHERE quest1_complete = 1 
                  AND quest2_complete = 1 
                  AND quest3_complete = 1 
                  AND quest4_complete = 1
            ''')
            
            # Step 1 완료 + Steam ID 등록
            step1_with_steam = await conn.fetchval('''
                SELECT COUNT(*) FROM users 
                WHERE quest1_complete = 1 AND steam_id IS NOT NULL
            ''')
            
            return {
                'total_users': total_users,
                'step1_completed': step1_count,
                'step1_2_completed': step1_2_count,
                'step1_3_completed': step1_3_count,
                'step1_4_completed': step1_4_count,
                'role_acquired': step1_4_count,  # 롤 획득 = Step 1~4 완료
                'step1_with_steam_id': step1_with_steam
            }


def print_table(title: str, headers: List[str], rows: List[List[str]]):
    """테이블 형식으로 출력"""
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}\n")
    
    if not rows:
        print("  No data found.\n")
        return
    
    # 컬럼 너비 계산
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(cell)))
    
    # 헤더 출력
    header_row = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    print(f"  {header_row}")
    print(f"  {'-'*len(header_row)}")
    
    # 데이터 출력
    for row in rows:
        data_row = " | ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row))
        print(f"  {data_row}")
    
    print(f"\n  Total: {len(rows)} users\n")


async def main():
    """메인 함수"""
    stats = DatabaseStats()
    
    try:
        print("\n" + "="*80)
        print("  Steam Code SZ Program - Database Statistics")
        print("="*80)
        
        # 전체 통계
        statistics = await stats.get_statistics()
        print("\n📊 Overall Statistics:")
        print(f"  Total Users: {statistics['total_users']}")
        print(f"  Step 1 Completed: {statistics['step1_completed']}")
        print(f"  Step 1~2 Completed: {statistics['step1_2_completed']}")
        print(f"  Step 1~3 Completed: {statistics['step1_3_completed']}")
        print(f"  Step 1~4 Completed (Role Acquired): {statistics['step1_4_completed']}")
        print(f"  Step 1 with Steam ID: {statistics['step1_with_steam_id']}")
        
        # Step 1~2 완료 유저
        step1_2_users = await stats.get_step1_to_step2_users()
        rows = [
            [
                str(user['discord_id']),
                user['steam_id'] or 'N/A',
                user['created_at'].strftime('%Y-%m-%d %H:%M:%S') if user['created_at'] else 'N/A'
            ]
            for user in step1_2_users
        ]
        print_table(
            "Step 1~2 Completed Users",
            ["Discord ID", "Steam ID", "Created At"],
            rows
        )
        
        # Step 1~3 완료 유저
        step1_3_users = await stats.get_step1_to_step3_users()
        rows = [
            [
                str(user['discord_id']),
                user['steam_id'] or 'N/A',
                user['created_at'].strftime('%Y-%m-%d %H:%M:%S') if user['created_at'] else 'N/A'
            ]
            for user in step1_3_users
        ]
        print_table(
            "Step 1~3 Completed Users",
            ["Discord ID", "Steam ID", "Created At"],
            rows
        )
        
        # Step 1~4 완료 유저 (롤 획득)
        step1_4_users = await stats.get_step1_to_step4_users()
        rows = [
            [
                str(user['discord_id']),
                user['steam_id'] or 'N/A',
                user['created_at'].strftime('%Y-%m-%d %H:%M:%S') if user['created_at'] else 'N/A'
            ]
            for user in step1_4_users
        ]
        print_table(
            "Step 1~4 Completed Users (Role Acquired)",
            ["Discord ID", "Steam ID", "Created At"],
            rows
        )
        
        # Step 1 완료 + Steam ID 등록 유저
        step1_steam_users = await stats.get_step1_users_with_steam_id()
        rows = [
            [
                str(user['discord_id']),
                user['steam_id'],
                user['created_at'].strftime('%Y-%m-%d %H:%M:%S') if user['created_at'] else 'N/A'
            ]
            for user in step1_steam_users
        ]
        print_table(
            "Step 1 Completed Users with Steam ID",
            ["Discord ID", "Steam ID", "Created At"],
            rows
        )
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await stats.close()


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())

