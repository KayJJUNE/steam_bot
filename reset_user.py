"""
사용자 퀘스트 데이터 초기화 스크립트

이 스크립트는 특정 사용자의 퀘스트 완료 상태를 초기화합니다.
롤 획득 테스트를 위해 사용할 수 있습니다.
"""

import os
import asyncpg
from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()


async def reset_user_quests(discord_id: int):
    """사용자의 모든 퀘스트 완료 상태를 초기화"""
    database_url = os.getenv('DATABASE_URL') or os.getenv('DATABASE_PUBLIC_URL')
    if not database_url:
        raise ValueError("DATABASE_URL or DATABASE_PUBLIC_URL environment variable is not set")
    
    # asyncpg는 postgres:// 형식 사용
    if database_url.startswith('postgresql://'):
        database_url = database_url.replace('postgresql://', 'postgres://', 1)
    
    async with asyncpg.create_pool(database_url, min_size=1, max_size=5) as pool:
        async with pool.acquire() as conn:
            # 사용자 데이터 조회
            user = await conn.fetchrow('''
                SELECT discord_id, steam_id, quest1_complete, quest2_complete, quest3_complete, quest4_complete
                FROM users WHERE discord_id = $1
            ''', discord_id)
            
            if not user:
                print(f"❌ User {discord_id} not found in database.")
                return False
            
            print(f"\n📋 Current Status for User {discord_id}:")
            print(f"  Steam ID: {user['steam_id'] or 'Not set'}")
            print(f"  Quest 1 Complete: {bool(user['quest1_complete'])}")
            print(f"  Quest 2 Complete: {bool(user['quest2_complete'])}")
            print(f"  Quest 3 Complete: {bool(user['quest3_complete'])}")
            print(f"  Quest 4 Complete: {bool(user['quest4_complete'])}")
            
            # 모든 퀘스트 완료 상태 초기화 (Steam ID는 유지)
            await conn.execute('''
                UPDATE users 
                SET quest1_complete = 0,
                    quest2_complete = 0,
                    quest3_complete = 0,
                    quest4_complete = 0
                WHERE discord_id = $1
            ''', discord_id)
            
            print(f"\n✅ Successfully reset all quest completion status for user {discord_id}")
            print(f"   Steam ID is preserved: {user['steam_id'] or 'Not set'}")
            print(f"\n   You can now test the quest completion flow again!")
            
            return True


async def reset_all_users():
    """모든 사용자의 퀘스트 완료 상태를 초기화 (주의!)"""
    database_url = os.getenv('DATABASE_URL') or os.getenv('DATABASE_PUBLIC_URL')
    if not database_url:
        raise ValueError("DATABASE_URL or DATABASE_PUBLIC_URL environment variable is not set")
    
    if database_url.startswith('postgresql://'):
        database_url = database_url.replace('postgresql://', 'postgres://', 1)
    
    async with asyncpg.create_pool(database_url, min_size=1, max_size=5) as pool:
        async with pool.acquire() as conn:
            # 전체 사용자 수 확인
            total_users = await conn.fetchval('SELECT COUNT(*) FROM users')
            completed_users = await conn.fetchval('''
                SELECT COUNT(*) FROM users 
                WHERE quest1_complete = 1 OR quest2_complete = 1 OR quest3_complete = 1 OR quest4_complete = 1
            ''')
            
            print(f"\n⚠️  WARNING: This will reset quest completion for ALL users!")
            print(f"   Total users: {total_users}")
            print(f"   Users with completed quests: {completed_users}")
            
            confirm = input("\n   Type 'RESET ALL' to confirm: ")
            if confirm != 'RESET ALL':
                print("❌ Reset cancelled.")
                return False
            
            # 모든 사용자의 퀘스트 완료 상태 초기화
            result = await conn.execute('''
                UPDATE users 
                SET quest1_complete = 0,
                    quest2_complete = 0,
                    quest3_complete = 0,
                    quest4_complete = 0
            ''')
            
            print(f"\n✅ Successfully reset all quest completion status for all users")
            return True


async def main():
    """메인 함수"""
    print("\n" + "="*80)
    print("  Steam Code SZ Program - User Quest Reset")
    print("="*80)
    print("\nOptions:")
    print("  1. Reset quests for a specific user (by Discord ID)")
    print("  2. Reset quests for ALL users (WARNING: This affects everyone!)")
    print("  3. Exit")
    
    choice = input("\nSelect an option (1-3): ").strip()
    
    if choice == '1':
        try:
            discord_id = int(input("\nEnter Discord User ID: ").strip())
            await reset_user_quests(discord_id)
        except ValueError:
            print("❌ Invalid Discord ID. Please enter a numeric ID.")
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
    
    elif choice == '2':
        try:
            await reset_all_users()
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
    
    elif choice == '3':
        print("\nExiting...")
        return
    
    else:
        print("❌ Invalid option.")


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())

