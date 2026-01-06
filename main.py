import discord
from discord import app_commands
from discord.ui import Button, View, Modal, TextInput, Select
import aiohttp
import sqlite3
import os
import re
import ssl
import asyncio
from typing import Optional
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import asyncpg
from urllib.parse import urlparse

# 환경 변수 로드
load_dotenv()

# Discord Bot 설정
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
STEAM_API_KEY = os.getenv('STEAM_API_KEY')
APP_ID = os.getenv('APP_ID', '123456')  # 기본값, 실제 App ID로 변경 필요
COMMUNITY_POST_URL = os.getenv('COMMUNITY_POST_URL', 'https://store.steampowered.com/news/app/3966570/view/515228475882209343?l=english')
MILESTONES = [10000, 30000, 50000]  # 마일스톤: 1만, 3만, 5만
TARGET_WISHLIST_COUNT = 50000  # 최종 목표 위시리스트 수
WISHLIST_API_URL = os.getenv('WISHLIST_API_URL')  # 위시리스트 수를 가져올 API URL (선택사항)
MILESTONE_REWARD_IMAGE_URL = os.getenv('MILESTONE_REWARD_IMAGE_URL', 'https://i.postimg.cc/mk2pHYd5/Hailuo-Image-kkwagchan-imijilo-455099822323220490.jpg')  # 마일스톤 리워드 소개 이미지 URL
REWARD_ROLE_ID = os.getenv('REWARD_ROLE_ID', '1448242630667534449')  # 모든 퀘스트 완료 시 부여할 역할 ID

intents = discord.Intents.default()
# message_content intent는 슬래시 명령어만 사용하므로 필요 없음
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


class DatabaseManager:
    """PostgreSQL 또는 SQLite 데이터베이스 관리 클래스 (자동 감지)"""
    
    def __init__(self, db_name: str = 'user_data.db'):
        # DATABASE_URL이 있으면 PostgreSQL 사용, 없으면 SQLite 사용
        self.database_url = os.getenv('DATABASE_URL') or os.getenv('DATABASE_PUBLIC_URL')
        self.use_postgres = bool(self.database_url)
        
        if self.use_postgres:
            # PostgreSQL 사용
            self.pool = None
            self._init_lock = asyncio.Lock()
            self._initialized = False
        else:
            # SQLite 사용 (로컬 개발용)
            self.db_name = db_name
            self.init_database()
    
    async def _get_pool(self):
        """PostgreSQL 연결 풀 가져오기 (Thread-safe)"""
        if self.pool is not None:
            return self.pool
        
        async with self._init_lock:
            if self.pool is not None:
                return self.pool
            
            if not self.database_url:
                raise ValueError("DATABASE_URL or DATABASE_PUBLIC_URL environment variable is not set")
            
            is_railway = 'railway' in self.database_url.lower() or 'rlwy.net' in self.database_url.lower()
            parsed = urlparse(self.database_url)
            
            host = parsed.hostname
            port = parsed.port or 5432
            user = parsed.username
            password = parsed.password
            database = parsed.path.lstrip('/')
            
            print(f"[DB] Parsed connection: host={host}, port={port}, user={user}, database={database}")
            
            # SSL 설정 - Railway PostgreSQL의 자체 서명 인증서 검증 비활성화
            ssl_config = None
            if is_railway:
                # Railway PostgreSQL: SSL 컨텍스트를 명시적으로 설정하여 인증서 검증 비활성화
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                ssl_config = ssl_context
                print(f"[DB] Railway PostgreSQL detected - SSL with certificate verification disabled")
            else:
                ssl_config = True
            
            try:
                print(f"[DB] Creating connection pool...")
                self.pool = await asyncpg.create_pool(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database,
                    ssl=ssl_config,
                    min_size=1,
                    max_size=10,
                    command_timeout=60,
                    server_settings={
                        'application_name': 'steam_bot'
                    }
                )
                
                # 연결 테스트
                print(f"[DB] Testing connection...")
                async with self.pool.acquire() as test_conn:
                    version = await test_conn.fetchval('SELECT version()')
                    print(f"[DB] ✅ Successfully connected to PostgreSQL")
                    print(f"[DB] PostgreSQL version: {version[:50]}...")
                
                # 데이터베이스 초기화
                if not self._initialized:
                    print(f"[DB] Initializing database...")
                    await self._init_database_internal()
                    self._initialized = True
                    print(f"[DB] ✅ Database initialized successfully")
            except Exception as e:
                self.pool = None
                error_msg = (
                    f"Failed to connect to PostgreSQL database.\n\n"
                    f"Error: {str(e)}\n\n"
                    f"Please check:\n"
                    f"1. DATABASE_URL is correct\n"
                    f"2. PostgreSQL service is running in Railway\n"
                    f"3. Network connectivity is available"
                )
                raise ValueError(error_msg) from e
        
        return self.pool
    
    async def _init_database_internal(self):
        """PostgreSQL 데이터베이스 초기화 (내부 메서드)"""
        if self.pool is None:
            raise RuntimeError("Database pool is not initialized")
        
        async with self.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    discord_id BIGINT PRIMARY KEY,
                    steam_id TEXT,
                    quest1_complete INTEGER DEFAULT 0,
                    quest2_complete INTEGER DEFAULT 0,
                    quest3_complete INTEGER DEFAULT 0,
                    quest4_complete INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # quest4_complete 컬럼 마이그레이션
            try:
                column_exists = await conn.fetchval('''
                    SELECT EXISTS (
                        SELECT 1 
                        FROM information_schema.columns 
                        WHERE table_name = 'users' 
                        AND column_name = 'quest4_complete'
                    )
                ''')
                
                if not column_exists:
                    await conn.execute('ALTER TABLE users ADD COLUMN quest4_complete INTEGER DEFAULT 0')
            except Exception as e:
                error_str = str(e).lower()
                if 'already exists' not in error_str and 'duplicate' not in error_str:
                    debug_mode = os.getenv('DEBUG', 'False').lower() == 'true'
                    if debug_mode:
                        print(f"[DB] Could not add quest4_complete column: {e}")
    
    def init_database(self):
        """SQLite 데이터베이스 초기화"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                discord_id INTEGER PRIMARY KEY,
                steam_id TEXT,
                quest1_complete INTEGER DEFAULT 0,
                quest2_complete INTEGER DEFAULT 0,
                quest3_complete INTEGER DEFAULT 0,
                quest4_complete INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        try:
            cursor.execute('ALTER TABLE users ADD COLUMN quest4_complete INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        
        conn.commit()
        conn.close()
    
    async def get_user(self, discord_id: int) -> Optional[dict]:
        """사용자 정보 조회"""
        if self.use_postgres:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                result = await conn.fetchrow('''
                    SELECT discord_id, steam_id, quest1_complete, quest2_complete, quest3_complete, quest4_complete
                    FROM users WHERE discord_id = $1
                ''', discord_id)
                
                if result:
                    return {
                        'discord_id': result['discord_id'],
                        'steam_id': result['steam_id'],
                        'quest1_complete': bool(result['quest1_complete']),
                        'quest2_complete': bool(result['quest2_complete']),
                        'quest3_complete': bool(result['quest3_complete']),
                        'quest4_complete': bool(result['quest4_complete']) if result['quest4_complete'] is not None else False
                    }
                return None
        else:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT discord_id, steam_id, quest1_complete, quest2_complete, quest3_complete, quest4_complete
                FROM users WHERE discord_id = ?
            ''', (discord_id,))
            result = cursor.fetchone()
            conn.close()
            
            if result:
                return {
                    'discord_id': result[0],
                    'steam_id': result[1],
                    'quest1_complete': bool(result[2]),
                    'quest2_complete': bool(result[3]),
                    'quest3_complete': bool(result[4]),
                    'quest4_complete': bool(result[5]) if len(result) > 5 else False
                }
            return None
    
    async def create_user(self, discord_id: int):
        """새 사용자 생성"""
        if self.use_postgres:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO users (discord_id) VALUES ($1)
                    ON CONFLICT (discord_id) DO NOTHING
                ''', discord_id)
        else:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO users (discord_id) VALUES (?)
            ''', (discord_id,))
            conn.commit()
            conn.close()
    
    async def update_steam_id(self, discord_id: int, steam_id: str):
        """Steam ID 업데이트"""
        if self.use_postgres:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute('''
                    UPDATE users SET steam_id = $1, quest1_complete = 1 WHERE discord_id = $2
                ''', steam_id, discord_id)
        else:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users SET steam_id = ?, quest1_complete = 1 WHERE discord_id = ?
            ''', (steam_id, discord_id))
            conn.commit()
            conn.close()
    
    async def update_quest(self, discord_id: int, quest_number: int, complete: bool = True):
        """퀘스트 완료 상태 업데이트"""
        quest_column = f'quest{quest_number}_complete'
        value = 1 if complete else 0
        
        if self.use_postgres:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(f'''
                    UPDATE users SET {quest_column} = $1 WHERE discord_id = $2
                ''', value, discord_id)
        else:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute(f'''
                UPDATE users SET {quest_column} = ? WHERE discord_id = ?
            ''', (value, discord_id))
            conn.commit()
            conn.close()
    
    def get_total_wishlist_count(self) -> int:
        """전체 위시리스트 수 조회 (캐시된 값 반환)"""
        return 32500
    
    async def are_all_quests_complete(self, discord_id: int) -> bool:
        """모든 퀘스트가 완료되었는지 확인"""
        user_data = await self.get_user(discord_id)
        if not user_data:
            return False
        
        return (
            user_data.get('quest1_complete', False) and
            user_data.get('quest2_complete', False) and
            user_data.get('quest3_complete', False) and
            user_data.get('quest4_complete', False)
        )
    
    async def get_user_by_steam_id(self, steam_id: str) -> Optional[dict]:
        """Steam ID로 사용자 조회 (중복 확인용)"""
        if self.use_postgres:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                result = await conn.fetchrow('''
                    SELECT discord_id, steam_id FROM users WHERE steam_id = $1
                ''', steam_id)
                if result:
                    return {
                        'discord_id': result['discord_id'],
                        'steam_id': result['steam_id']
                    }
                return None
        else:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute('SELECT discord_id, steam_id FROM users WHERE steam_id = ?', (steam_id,))
            result = cursor.fetchone()
            conn.close()
            if result:
                return {'discord_id': result[0], 'steam_id': result[1]}
            return None
    
    async def close(self):
        """데이터베이스 연결 풀 종료"""
        if self.use_postgres and self.pool:
            await self.pool.close()


def create_progress_bar(current: int, milestones: list, length: int = 20) -> tuple:
    """진행률 바 생성 및 마일스톤 정보 반환"""
    if not milestones:
        return "", []
    
    # 현재 달성한 마일스톤 찾기
    achieved_milestones = []
    next_milestone = None
    
    for milestone in milestones:
        if current >= milestone:
            achieved_milestones.append(milestone)
        elif next_milestone is None:
            next_milestone = milestone
            break
    
    if next_milestone is None:
        next_milestone = milestones[-1]
        percentage = 100.0
    else:
        # 다음 마일스톤까지의 진행률 계산
        prev_milestone = achieved_milestones[-1] if achieved_milestones else 0
        if next_milestone > prev_milestone:
            progress = (current - prev_milestone) / (next_milestone - prev_milestone)
            percentage = min(100.0, (prev_milestone / milestones[-1] * 100) + (progress * (next_milestone - prev_milestone) / milestones[-1] * 100))
        else:
            percentage = (current / milestones[-1]) * 100
    
    # 전체 진행률 (최종 목표 기준)
    total_percentage = (current / milestones[-1]) * 100
    
    # 진행률 바 생성
    filled = int((total_percentage / 100) * length)
    bar = "🟩" * filled + "⬜" * (length - filled)
    
    # 마일스톤 텍스트는 제거 (이미지로 대체)
    progress_text = f"{bar}\n**{current:,}** / {milestones[-1]:,} ({total_percentage:.1f}% 달성)"
    
    return progress_text, achieved_milestones


class SteamLinkModal(Modal, title='Link Steam Account'):
    """Modal for linking Steam account"""
    
    steam_input = TextInput(
        label='Steam ID or Profile URL',
        placeholder='Enter Steam ID or profile URL',
        required=True,
        max_length=200
    )
    
    def __init__(self, db: DatabaseManager, view_instance):
        super().__init__()
        self.db = db
        self.view_instance = view_instance
    
    async def on_submit(self, interaction: discord.Interaction):
        steam_input = self.steam_input.value.strip()
        
        # Steam ID 추출
        steam_id = None
        
        # URL에서 Steam ID 추출
        if 'steamcommunity.com' in steam_input:
            # URL 패턴 매칭
            match = re.search(r'/profiles/(\d+)', steam_input)
            if match:
                steam_id = match.group(1)
            else:
                match = re.search(r'/id/([^/]+)', steam_input)
                if match:
                    # 커스텀 URL인 경우, API로 변환 필요
                    custom_url = match.group(1)
                    steam_id = await resolve_vanity_url(custom_url)
        else:
            # 숫자만 있는 경우 (Steam ID 64)
            if steam_input.isdigit():
                steam_id = steam_input
        
        if not steam_id:
            await interaction.response.send_message(
                "❌ Invalid Steam ID or URL. Please enter a valid Steam ID or profile URL.",
                ephemeral=True
            )
            return
        
        # Steam API로 검증
        is_valid = await verify_steam_id(steam_id)
        
        if not is_valid:
            await interaction.response.send_message(
                "❌ Could not verify Steam ID. Please check if the Steam ID is correct.",
                ephemeral=True
            )
            return
        
        # 데이터베이스에 저장
        await self.db.create_user(interaction.user.id)
        await self.db.update_steam_id(interaction.user.id, steam_id)
        # Steam ID 연동 완료 처리
        await self.db.update_quest(interaction.user.id, 1, True)
        
        await interaction.response.defer(ephemeral=True)
        
        await interaction.followup.send(
            f"✅ Step 1: Steam ID linking completed! (Steam ID: {steam_id})",
            ephemeral=True
        )
        
        # 모든 퀘스트 완료 확인 및 자동 롤 부여
        await auto_assign_reward_role(interaction, self.db)
        
        # Select 메뉴가 포함된 Embed 업데이트
        try:
            if hasattr(self, 'view_instance') and self.view_instance:
                await self.view_instance.update_embed(interaction)
        except Exception as e:
            print(f"update_embed 오류 (Step 1): {e}")
            # 오류 발생 시 새로운 Embed 전송
            try:
                user_data = await self.db.get_user(interaction.user.id)
                embed = discord.Embed(
                    title="🎮 Steam Code SZ Program",
                    description="Complete these quests to receive a special Discord role.\nAdventurers who receive the special role will get additional rewards. (Rewards to be announced)",
                    color=discord.Color.blue()
                )
                if MILESTONE_REWARD_IMAGE_URL:
                    embed.set_image(url=MILESTONE_REWARD_IMAGE_URL)
                view = QuestView(self.db, user_data)
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            except:
                pass


async def resolve_vanity_url(vanity_url: str) -> Optional[str]:
    """Steam 커스텀 URL을 Steam ID 64로 변환"""
    if not STEAM_API_KEY:
        return None
    
    url = f"http://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001/"
    params = {
        'key': STEAM_API_KEY,
        'vanityurl': vanity_url
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                data = await response.json()
                if data.get('response', {}).get('success') == 1:
                    return data['response'].get('steamid')
    except Exception as e:
        print(f"Vanity URL 해석 오류: {e}")
    
    return None


async def verify_steam_id(steam_id: str) -> bool:
    """Steam ID 유효성 검증"""
    if not STEAM_API_KEY:
        # API 키가 없으면 기본 검증만 수행 (숫자 체크)
        return steam_id.isdigit() and len(steam_id) == 17
    
    url = f"http://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/"
    params = {
        'key': STEAM_API_KEY,
        'steamids': steam_id
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                data = await response.json()
                players = data.get('response', {}).get('players', [])
                return len(players) > 0 and players[0].get('steamid') == steam_id
    except Exception as e:
        print(f"Steam ID 검증 오류: {e}")
        # 오류 발생 시 기본 검증만 수행
        return steam_id.isdigit() and len(steam_id) == 17


async def get_wishlist_count_from_store(app_id: str) -> Optional[int]:
    """위시리스트 수 가져오기 - API 우선, 실패 시 Steam Store 스크래핑"""
    # 1. 사용자 정의 API URL이 있으면 우선 사용
    if WISHLIST_API_URL:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(WISHLIST_API_URL, headers={'User-Agent': 'Mozilla/5.0'}) as response:
                    if response.status == 200:
                        text = await response.text().strip()
                        # 숫자만 반환하는 경우 직접 변환 시도
                        try:
                            # 쉼표 제거 후 숫자로 변환
                            count = int(text.replace(',', '').replace(' ', ''))
                            print(f"위시리스트 API에서 수치 가져옴: {count}")
                            return count
                        except ValueError:
                            # 숫자가 아닌 경우 JSON 파싱 시도
                            try:
                                data = await response.json()
                                # JSON 응답에서 위시리스트 수 추출 (다양한 형식 지원)
                                if isinstance(data, dict):
                                    # 가능한 키 이름들
                                    for key in ['wishlist_count', 'wishlistCount', 'count', 'wishlist', 'total']:
                                        if key in data:
                                            count = data[key]
                                            if isinstance(count, (int, str)):
                                                return int(str(count).replace(',', ''))
                                elif isinstance(data, (int, str)):
                                    return int(str(data).replace(',', ''))
                            except:
                                # JSON도 아닌 경우 텍스트에서 숫자 추출
                                numbers = re.findall(r'\d+', text.replace(',', ''))
                                if numbers:
                                    return int(numbers[0])
        except Exception as e:
            print(f"위시리스트 API 호출 오류: {e}")
    
    # 2. Steam Store 페이지 스크래핑 시도
    url = f"https://store.steampowered.com/app/{app_id}/"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={'User-Agent': 'Mozilla/5.0'}) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # 위시리스트 수를 찾는 여러 방법 시도
                    # 방법 1: wishlist_count 클래스 찾기
                    wishlist_elem = soup.find(class_='wishlist_count')
                    if wishlist_elem:
                        text = wishlist_elem.get_text()
                        # 숫자만 추출
                        numbers = re.findall(r'\d+', text.replace(',', ''))
                        if numbers:
                            return int(numbers[0])
                    
                    # 방법 2: data-wishlist-count 속성 찾기
                    wishlist_attr = soup.find(attrs={'data-wishlist-count': True})
                    if wishlist_attr:
                        count = wishlist_attr.get('data-wishlist-count')
                        if count:
                            return int(count)
                    
                    # 방법 3: JavaScript 변수에서 찾기
                    scripts = soup.find_all('script')
                    for script in scripts:
                        if script.string:
                            # 더 정확한 패턴 시도
                            patterns = [
                                r'wishlist_count["\']?\s*[:=]\s*(\d+)',
                                r'"wishlist_count"\s*:\s*(\d+)',
                                r'wishlistCount["\']?\s*[:=]\s*(\d+)',
                                r'g_rgWishlistData\s*=\s*\{[^}]*"(\d+)"',
                            ]
                            for pattern in patterns:
                                match = re.search(pattern, script.string)
                                if match:
                                    return int(match.group(1))
    except Exception as e:
        print(f"위시리스트 수 가져오기 오류: {e}")
    
    return None


async def check_wishlist(steam_id: str, app_id: str) -> bool:
    """위시리스트 확인 - Steam 위시리스트 API 사용"""
    if not steam_id:
        return False
    
    # Steam 위시리스트 데이터 가져오기
    url = f"https://store.steampowered.com/wishlist/profiles/{steam_id}/wishlistdata/"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={'User-Agent': 'Mozilla/5.0'}) as response:
                if response.status == 200:
                    text = await response.text()
                    # 빈 응답 체크
                    if not text or text.strip() == '':
                        print(f"위시리스트 API 빈 응답: steam_id={steam_id}")
                        return False
                    
                    try:
                        data = await response.json()
                    except:
                        # JSON 파싱 실패 시 텍스트로 확인
                        print(f"위시리스트 API JSON 파싱 실패: {text[:200]}")
                        return False
                    
                    # 위시리스트 데이터가 있고, 해당 앱 ID가 포함되어 있는지 확인
                    if data and isinstance(data, dict):
                        # 앱 ID를 여러 형식으로 확인
                        app_id_str = str(app_id)
                        app_id_int = int(app_id) if app_id.isdigit() else None
                        
                        # 문자열 키로 확인
                        if app_id_str in data:
                            print(f"위시리스트 확인 성공 (문자열 키): {app_id_str}")
                            return True
                        
                        # 숫자 키로 확인
                        if app_id_int and app_id_int in data:
                            print(f"위시리스트 확인 성공 (숫자 키): {app_id_int}")
                            return True
                        
                        # 모든 키 확인 (디버깅용)
                        if len(data) > 0:
                            print(f"위시리스트 API 응답 키 샘플: {list(data.keys())[:5]}")
                            print(f"찾는 앱 ID: {app_id} (문자열: {app_id_str}, 숫자: {app_id_int})")
                    else:
                        print(f"위시리스트 API 응답이 dict가 아님: {type(data)}")
                else:
                    print(f"위시리스트 API 응답 상태 코드: {response.status}")
    except Exception as e:
        print(f"위시리스트 확인 오류: {e}")
        import traceback
        traceback.print_exc()
        # 오류 발생 시 사용자 확인에 의존
        return False
    
    return False


async def auto_assign_reward_role(interaction: discord.Interaction, db: DatabaseManager):
    """모든 퀘스트 완료 시 자동으로 보상 역할 부여"""
    # 모든 퀘스트 완료 확인
    if not await db.are_all_quests_complete(interaction.user.id):
        return False
    
    # Guild 확인 (DM에서는 역할 부여 불가)
    if not interaction.guild:
        return False
    
    try:
        role_id = int(REWARD_ROLE_ID)
    except (ValueError, TypeError):
        print(f"잘못된 역할 ID: {REWARD_ROLE_ID}")
        return False
    
    # 역할 가져오기
    role = interaction.guild.get_role(role_id)
    if not role:
        print(f"역할을 찾을 수 없습니다: {role_id}")
        return False
    
    try:
        # 멤버 가져오기
        member = interaction.guild.get_member(interaction.user.id)
        if not member:
            member = await interaction.guild.fetch_member(interaction.user.id)
        
        # 이미 역할이 있는지 확인
        if role in member.roles:
            return True
        
        # 역할 자동 부여
        await member.add_roles(role, reason="Spot Zero Hunter Program 모든 퀘스트 완료")
        
        # 성공 메시지 전송 (defer가 이미 호출되었는지 확인)
        try:
            # followup이 가능한지 확인
            if interaction.response.is_done():
                await interaction.followup.send(
                    f"🎉 Congratulations! You've completed all quests and the role **{role.name}** has been automatically assigned!",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"🎉 Congratulations! You've completed all quests and the role **{role.name}** has been automatically assigned!",
                    ephemeral=True
                )
        except Exception as e:
            print(f"롤 부여 성공 메시지 전송 실패: {e}")
        
        return True
        
    except discord.Forbidden:
        print(f"역할 부여 권한이 없습니다: {role_id}")
        return False
    except discord.HTTPException as e:
        print(f"역할 부여 중 HTTP 오류: {e}")
        return False
    except Exception as e:
        print(f"역할 부여 중 예외 발생: {e}")
        import traceback
        traceback.print_exc()
        return False


async def send_reward_role_embed(interaction: discord.Interaction, db: DatabaseManager):
    """모든 퀘스트 완료 시 보상 역할 받기 Embed 전송 (레거시 - 자동 부여로 대체됨)"""
    # 자동 부여 시도
    return await auto_assign_reward_role(interaction, db)


class ClaimRoleView(View):
    """보상 역할 받기를 위한 View"""
    
    def __init__(self, db: DatabaseManager, role_id: int):
        super().__init__(timeout=None)
        self.db = db
        self.role_id = role_id
    
    @discord.ui.button(label='🎁 Claim Role', style=discord.ButtonStyle.success)
    async def claim_role(self, interaction: discord.Interaction, button: Button):
        # 모든 퀘스트 완료 확인
        if not await self.db.are_all_quests_complete(interaction.user.id):
            await interaction.response.send_message(
                "❌ You must complete all quests to receive the role!",
                ephemeral=True
            )
            return
        
        # Guild 확인
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ You can only receive the role in a server!",
                ephemeral=True
            )
            return
        
        try:
            # 역할 가져오기
            role = interaction.guild.get_role(self.role_id)
            if not role:
                await interaction.response.send_message(
                    "❌ Role not found. Please contact an administrator.",
                    ephemeral=True
                )
                return
            
            # 멤버 가져오기
            member = interaction.guild.get_member(interaction.user.id)
            if not member:
                member = await interaction.guild.fetch_member(interaction.user.id)
            
            # 이미 역할이 있는지 확인
            if role in member.roles:
                await interaction.response.send_message(
                    "✅ You have already acquired the role!",
                    ephemeral=True
                )
                return
            
            # 역할 부여
            await member.add_roles(role, reason="Spot Zero Hunter Program 모든 퀘스트 완료")
            
            await interaction.response.send_message(
                "🎉 Congratulations! The role has been assigned!",
                ephemeral=True
            )
            
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ No permission to assign roles. Please contact an administrator.",
                ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"❌ An error occurred while assigning the role: {e}",
                ephemeral=True
            )
        except Exception as e:
            print(f"역할 부여 중 예외 발생: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while assigning the role. Please contact an administrator.",
                ephemeral=True
            )


class SteamLinkGuideView(View):
    """Steam ID 연동 가이드 후 Modal을 여는 View"""
    
    def __init__(self, db: DatabaseManager, view_instance):
        super().__init__(timeout=300)  # 5분 타임아웃
        self.db = db
        self.view_instance = view_instance
    
    @discord.ui.button(label='📝 Enter Steam ID', style=discord.ButtonStyle.primary)
    async def open_modal(self, interaction: discord.Interaction, button: Button):
        modal = SteamLinkModal(self.db, self.view_instance)
        await interaction.response.send_modal(modal)


class SteamLinkSelect(Select):
    """Steam 계정 연결을 위한 Select 메뉴 (선택사항)"""
    
    def __init__(self, db: DatabaseManager, view_instance):
        options = [
            discord.SelectOption(
                label="Steam ID 64 입력",
                description="Steam ID 64를 직접 입력합니다",
                value="steam_id",
                emoji="🔢"
            ),
            discord.SelectOption(
                label="Steam 프로필 URL 입력",
                description="Steam 프로필 URL을 입력합니다",
                value="profile_url",
                emoji="🔗"
            )
        ]
        super().__init__(placeholder="Steam 계정 연결 (선택사항)...", options=options, min_values=1, max_values=1)
        self.db = db
        self.view_instance = view_instance
    
    async def callback(self, interaction: discord.Interaction):
        # Steam 계정 연결은 선택사항이므로 Quest 완료와 무관
        modal = SteamLinkModal(self.db, self.view_instance)
        await interaction.response.send_modal(modal)


class QuestSelect(Select):
    """퀘스트 선택을 위한 Select 메뉴"""
    
    def __init__(self, db: DatabaseManager, view_instance):
        self.db = db
        self.view_instance = view_instance
        super().__init__(placeholder="퀘스트를 선택하세요...", min_values=1, max_values=1)
        self._update_options()
    
    def _update_options(self):
        """사용자 상태에 따라 옵션 업데이트 (완료된 퀘스트는 제외)"""
        user_data = self.view_instance.user_data or {}
        options = []
        
        # Step 1: Link Steam ID (only show if not completed)
        if not user_data.get('quest1_complete'):
            options.append(discord.SelectOption(
                label="Step 1: Link Steam ID",
                description="Link your Steam account",
                value="quest1",
                emoji="🔗"
            ))
        
        # Step 2: Spot Zero Wishlist (only show if not completed)
        if not user_data.get('quest2_complete'):
            options.append(discord.SelectOption(
                label="Step 2: Spot Zero Wishlist",
                description="Add Spot Zero to your wishlist",
                value="quest2",
                emoji="🎁"
            ))
        
        # Step 3: Follow Spot Zero Steam Page (only show if not completed)
        if not user_data.get('quest3_complete'):
            options.append(discord.SelectOption(
                label="Step 3: Follow Spot Zero Steam Page",
                description="Follow the Spot Zero Steam page",
                value="quest3",
                emoji="⭐"
            ))
        
        # Step 4: Like Post (only show if not completed)
        if not user_data.get('quest4_complete'):
            options.append(discord.SelectOption(
                label="Step 4: Like Post",
                description="Like the post",
                value="quest4",
                emoji="👍"
            ))
        
        # All quests completed
        if not options:
            options.append(discord.SelectOption(
                label="All Quests Completed! 🎉",
                description="You have completed all quests!",
                value="all_complete",
                emoji="🎉"
            ))
        
        self.options = options
    
    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        user_data = await self.db.get_user(interaction.user.id)
        if not user_data:
            await self.db.create_user(interaction.user.id)
            user_data = await self.db.get_user(interaction.user.id)
        
        if selected == "all_complete":
            await interaction.response.send_message(
                "🎉 You have completed all quests!",
                ephemeral=True
            )
            return
        
        if selected == "quest1":
            # Step 1: Link Steam ID
            if user_data.get('quest1_complete'):
                await interaction.response.send_message(
                    "✅ Step 1 is already completed!",
                    ephemeral=True
                )
                return
            
            # Show guide embed first
            guide_embed = discord.Embed(
                title="📝 Step 1: Link Steam ID Guide",
                description="**💡 Tip**: You can find your Steam profile URL and ID by clicking on your Steam profile.\n\n"
                           "**How to find Steam ID:**\n"
                           "1. Go to your Steam profile page\n"
                           "2. The number after `/profiles/` in the address bar is your Steam ID\n"
                           "3. Or if you have a custom URL, enter the text after `/id/`\n\n"
                           "After reading the guide, click the button below to enter your Steam ID.",
                color=discord.Color.blue()
            )
            
            # 가이드와 함께 Modal 열기 버튼이 있는 View 표시
            view = SteamLinkGuideView(self.db, self.view_instance)
            await interaction.response.send_message(embed=guide_embed, view=view, ephemeral=True)
        
        elif selected == "quest2":
            # Step 2: Spot Zero Wishlist
            if user_data.get('quest2_complete'):
                await interaction.response.send_message(
                    "✅ Step 2 is already completed! (Completion status is maintained even if you remove it from wishlist)",
                    ephemeral=True
                )
                return
            
            if not user_data.get('steam_id'):
                await interaction.response.send_message(
                    "❌ Please complete Step 1: Link Steam ID first!",
                    ephemeral=True
                )
                return
            
            # Show guide message with View
            guide_embed = discord.Embed(
                title="📝 Step 2: Spot Zero Wishlist Guide",
                description="**💡 Tip**: Your Steam profile must be set to public for this to work.\n\n"
                           f"**Profile Privacy Settings**: [Click here to check](https://steamcommunity.com/my/edit/settings)\n\n"
                           "**How to add to wishlist:**\n"
                           "1. Click the button below to go to the Spot Zero store page\n"
                           "2. Click 'Add to Wishlist' button\n"
                           "3. Come back and click 'Wishlist Added' button",
                color=discord.Color.blue()
            )
            
            view = WishlistView(self.db, self.view_instance, page_visited=False)
            store_url = f"https://store.steampowered.com/app/{APP_ID}/"
            
            await interaction.response.send_message(
                embed=guide_embed,
                view=view,
                ephemeral=True
            )
        
        elif selected == "quest3":
            # Step 3: Follow Spot Zero Steam Page
            if user_data.get('quest3_complete'):
                await interaction.response.send_message(
                    "✅ Step 3 is already completed!",
                    ephemeral=True
                )
                return
            
            if not user_data.get('steam_id'):
                await interaction.response.send_message(
                    "❌ Please complete Step 1: Link Steam ID first!",
                    ephemeral=True
                )
                return
            
            # Show guide message with View
            guide_embed = discord.Embed(
                title="📝 Step 3: Follow Spot Zero Steam Page Guide",
                description="**How to follow Steam page:**\n"
                           "1. Click 'Open Store Page' button below to go to the Spot Zero store page\n"
                           "2. Click 'Follow' button on the store page\n"
                           "3. Come back to Discord and click 'Store Page Visited' button\n"
                           "4. Then click 'Follow Confirmed' button",
                color=discord.Color.blue()
            )
            
            # 처음에는 스토어 페이지 링크와 방문 완료 버튼만 표시
            view = SteamFollowView(self.db, self.view_instance, page_visited=False)
            await interaction.response.send_message(
                embed=guide_embed,
                view=view,
                ephemeral=True
            )
        
        elif selected == "quest4":
            # Step 4: Like Post
            if user_data.get('quest4_complete'):
                await interaction.response.send_message(
                    "✅ Step 4 is already completed!",
                    ephemeral=True
                )
                return
            
            # Show guide message with View
            guide_embed = discord.Embed(
                title="📝 Step 4: Like Post Guide",
                description="**How to like the post:**\n"
                           "1. Click 'Open Post Page' button below to go to the post page\n"
                           "2. Click the like button on the post page\n"
                           "3. Come back to Discord and click 'Post Page Visited' button\n"
                           "4. Then click 'Post Confirmed' button",
                color=discord.Color.blue()
            )
            
            view = PostLikeView(self.db, self.view_instance, page_visited=False)
            await interaction.response.send_message(
                embed=guide_embed,
                view=view,
                ephemeral=True
            )


class WishlistManualConfirmView(View):
    """위시리스트 수동 확인을 위한 View"""
    
    def __init__(self, db: DatabaseManager, quest_view_instance, steam_id: str):
        super().__init__(timeout=300)  # 5분 타임아웃
        self.db = db
        self.quest_view_instance = quest_view_instance
        self.steam_id = steam_id
    
    @discord.ui.button(label='✅ Manual Confirm (Added to Wishlist)', style=discord.ButtonStyle.success)
    async def manual_confirm(self, interaction: discord.Interaction, button: Button):
        user_data = await self.db.get_user(interaction.user.id)
        
        if user_data and user_data.get('quest2_complete'):
            await interaction.response.send_message(
                "✅ Step 2 is already completed!",
                ephemeral=True
            )
            return
        
        # Manual confirmation - mark as complete
        await self.db.create_user(interaction.user.id)
        await self.db.update_quest(interaction.user.id, 2, True)
        
        await interaction.response.defer(ephemeral=True)
        
        await interaction.followup.send(
            "✅ Step 2: Spot Zero Wishlist completed!\n\n"
            "Processed via manual confirmation.",
            ephemeral=True
        )
        
        # 모든 퀘스트 완료 확인 및 자동 롤 부여
        await auto_assign_reward_role(interaction, self.db)
        
        # Select 메뉴가 포함된 Embed 업데이트
        try:
            await self.quest_view_instance.update_embed(interaction)
        except Exception as e:
            print(f"update_embed 오류 (Step 2 수동 확인): {e}")
    
    @discord.ui.button(label='🔄 Retry Verification', style=discord.ButtonStyle.primary)
    async def retry_verification(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        
        # Retry verification
        has_wishlist = await check_wishlist(self.steam_id, APP_ID)
        
        if has_wishlist:
            await self.db.create_user(interaction.user.id)
            await self.db.update_quest(interaction.user.id, 2, True)
            
            await interaction.followup.send(
                "✅ Verification successful! Step 2: Spot Zero Wishlist completed!",
                ephemeral=True
            )
            
            # Check all quests completion and auto assign role
            await auto_assign_reward_role(interaction, self.db)
            
            # Update embed with Select menu
            await self.quest_view_instance.update_embed(interaction)
        else:
            await interaction.followup.send(
                "❌ Verification still failed.\n\n"
                "If you have added it to your wishlist, please use the 'Manual Confirm' button.",
                ephemeral=True
            )


class WishlistView(View):
    """위시리스트 추가를 위한 View"""
    
    def __init__(self, db: DatabaseManager, quest_view_instance, page_visited: bool = False):
        super().__init__(timeout=None)
        self.db = db
        self.quest_view_instance = quest_view_instance
        self.page_visited = page_visited
        store_url = f"https://store.steampowered.com/app/{APP_ID}/"
        self.add_item(Button(label='🔗 Open Spot Zero Store Page', style=discord.ButtonStyle.link, url=store_url))
    
    @discord.ui.button(label='✅ Store Page Visited', style=discord.ButtonStyle.primary)
    async def visited_store(self, interaction: discord.Interaction, button: Button):
        """Store page visited button - activates wishlist confirmation button"""
        # Set page visited flag
        self.page_visited = True
        
        # Create new View with wishlist confirmation button
        view = WishlistConfirmView(self.db, self.quest_view_instance, page_visited=True)
        
        try:
            await interaction.response.edit_message(
                content="✅ You have visited the store page!\n\n"
                       "Now add Spot Zero to your wishlist, then click the 'Wishlist Added' button below.",
                view=view
            )
        except:
            # If edit_message fails, send new message
            await interaction.response.send_message(
                "✅ You have visited the store page!\n\n"
                "Now add Spot Zero to your wishlist, then click the 'Wishlist Added' button below.",
                view=view,
                ephemeral=True
            )


class WishlistConfirmView(View):
    """위시리스트 확인을 위한 View"""
    
    def __init__(self, db: DatabaseManager, quest_view_instance, page_visited: bool = False):
        super().__init__(timeout=None)
        self.db = db
        self.quest_view_instance = quest_view_instance
        self.page_visited = page_visited
        store_url = f"https://store.steampowered.com/app/{APP_ID}/"
        self.add_item(Button(label='🔗 Open Spot Zero Store Page', style=discord.ButtonStyle.link, url=store_url))
    
    @discord.ui.button(label='✅ Wishlist Added', style=discord.ButtonStyle.success)
    async def confirm_wishlist(self, interaction: discord.Interaction, button: Button):
        user_data = await self.db.get_user(interaction.user.id)
        
        if user_data and user_data.get('quest2_complete'):
            await interaction.response.send_message(
                "✅ Step 2 is already completed!",
                ephemeral=True
            )
            return
        
        # Check if page was visited
        if not self.page_visited:
            await interaction.response.send_message(
                "❌ Please visit the page first to complete the quest.\n\n"
                "1. Click 'Open Store Page' button to go to the page\n"
                "2. Click 'Store Page Visited' button\n"
                "3. Then click 'Wishlist Added' button",
                ephemeral=True
            )
            return
        
        # Check Steam ID
        if not user_data or not user_data.get('steam_id'):
            await interaction.response.send_message(
                "❌ Please complete Step 1: Link Steam ID first!",
                ephemeral=True
            )
            return
        
        # 위시리스트 검증 시도
        steam_id = user_data.get('steam_id')
        
        # 검증 중 메시지 표시
        await interaction.response.defer(ephemeral=True)
        
        has_wishlist = await check_wishlist(steam_id, APP_ID)
        
        if not has_wishlist:
            # 검증 실패 시 수동 확인 옵션 제공
            view = WishlistManualConfirmView(self.db, self.quest_view_instance, steam_id)
            await interaction.followup.send(
                "❌ Automatic verification failed.\n\n"
                "**Please check the following:**\n"
                "1. Make sure your Steam profile is set to public\n"
                "   → [Profile Settings Link](https://steamcommunity.com/my/edit/settings)\n"
                "2. Make sure you have added Spot Zero to your wishlist\n"
                "   → [Spot Zero Store Page](https://store.steampowered.com/app/3966570/)\n\n"
                "**If you have added it to your wishlist**, please click the 'Manual Confirm' button below.\n"
                "It may take some time for Steam API to recognize your profile.",
                view=view,
                ephemeral=True
            )
            return
        
        # Verification successful - mark as complete
        await self.db.create_user(interaction.user.id)
        await self.db.update_quest(interaction.user.id, 2, True)
        
        await interaction.followup.send(
            "✅ Step 2: Spot Zero Wishlist completed!",
            ephemeral=True
        )
        
        # 모든 퀘스트 완료 확인 및 자동 롤 부여
        await auto_assign_reward_role(interaction, self.db)
        
        # Select 메뉴가 포함된 Embed 업데이트
        await self.quest_view_instance.update_embed(interaction)


class SteamFollowView(View):
    """Steam 페이지 팔로우를 위한 View"""
    
    def __init__(self, db: DatabaseManager, quest_view_instance, page_visited: bool = False):
        super().__init__(timeout=None)
        self.db = db
        self.quest_view_instance = quest_view_instance
        self.page_visited = page_visited
        store_url = f"https://store.steampowered.com/app/{APP_ID}/"
        # Store page link button is always shown
        self.add_item(Button(label='🔗 Open Spot Zero Store Page', style=discord.ButtonStyle.link, url=store_url))
    
    @discord.ui.button(label='✅ Store Page Visited', style=discord.ButtonStyle.primary)
    async def visited_store(self, interaction: discord.Interaction, button: Button):
        """Store page visited button - activates confirmation button"""
        # Set page visited flag
        self.page_visited = True
        
        # Create new View with confirmation button
        view = SteamFollowConfirmView(self.db, self.quest_view_instance, page_visited=True)
        
        try:
            await interaction.response.edit_message(
                content="✅ You have visited the store page!\n\n"
                       "Now click the 'Follow' button on the store page, then click 'Follow Confirmed' below.",
                view=view
            )
        except:
            # If edit_message fails, send new message
            await interaction.response.send_message(
                "✅ You have visited the store page!\n\n"
                "Now click the 'Follow' button on the store page, then click 'Follow Confirmed' below.",
                view=view,
                ephemeral=True
            )


class SteamFollowConfirmView(View):
    """팔로우 확인을 위한 View"""
    
    def __init__(self, db: DatabaseManager, quest_view_instance, page_visited: bool = False):
        super().__init__(timeout=None)
        self.db = db
        self.quest_view_instance = quest_view_instance
        self.page_visited = page_visited
        store_url = f"https://store.steampowered.com/app/{APP_ID}/"
        self.add_item(Button(label='🔗 Open Spot Zero Store Page', style=discord.ButtonStyle.link, url=store_url))
    
    @discord.ui.button(label='✅ Follow Confirmed', style=discord.ButtonStyle.success)
    async def confirm_follow(self, interaction: discord.Interaction, button: Button):
        user_data = await self.db.get_user(interaction.user.id)
        
        if user_data and user_data.get('quest3_complete'):
            await interaction.response.send_message(
                "✅ Step 3 is already completed!",
                ephemeral=True
            )
            return
        
        # Check if page was visited
        if not self.page_visited:
            await interaction.response.send_message(
                "❌ Please visit the page first to complete the quest.\n\n"
                "1. Click 'Open Store Page' button to go to the page\n"
                "2. Click 'Store Page Visited' button\n"
                "3. Then click 'Follow Confirmed' button",
                ephemeral=True
            )
            return
        
        # Check Steam ID
        if not user_data or not user_data.get('steam_id'):
            await interaction.response.send_message(
                "❌ Please complete Step 1: Link Steam ID first!",
                ephemeral=True
            )
            return
        
        # Steam page follow cannot be verified via API,
        # so we assume the user visited the page and clicked confirm
        await self.db.create_user(interaction.user.id)
        await self.db.update_quest(interaction.user.id, 3, True)
        
        await interaction.response.defer(ephemeral=True)
        
        await interaction.followup.send(
            "✅ Step 3: Follow Spot Zero Steam Page completed!",
            ephemeral=True
        )
        
        # 모든 퀘스트 완료 확인 및 자동 롤 부여
        await auto_assign_reward_role(interaction, self.db)
        
        # Select 메뉴가 포함된 Embed 업데이트
        await self.quest_view_instance.update_embed(interaction)


class PostLikeView(View):
    """포스트 라이크를 위한 View"""
    
    def __init__(self, db: DatabaseManager, quest_view_instance, page_visited: bool = False):
        super().__init__(timeout=None)
        self.db = db
        self.quest_view_instance = quest_view_instance
        self.page_visited = page_visited
        self.add_item(Button(label='🔗 Open Post Page', style=discord.ButtonStyle.link, url=COMMUNITY_POST_URL))
    
    @discord.ui.button(label='✅ Post Page Visited', style=discord.ButtonStyle.primary)
    async def visited_post(self, interaction: discord.Interaction, button: Button):
        """Post page visited button - activates confirmation button"""
        # Set page visited flag
        self.page_visited = True
        
        # Create new View with confirmation button
        view = PostLikeConfirmView(self.db, self.quest_view_instance, page_visited=True)
        
        try:
            await interaction.response.edit_message(
                content="✅ You have visited the post page!\n\n"
                       "Now click the like button on the post page, then click 'Post Confirmed' below.",
                view=view
            )
        except:
            # If edit_message fails, send new message
            await interaction.response.send_message(
                "✅ You have visited the post page!\n\n"
                "Now click the like button on the post page, then click 'Post Confirmed' below.",
                view=view,
                ephemeral=True
            )


class PostLikeConfirmView(View):
    """포스트 라이크 확인을 위한 View"""
    
    def __init__(self, db: DatabaseManager, quest_view_instance, page_visited: bool = False):
        super().__init__(timeout=None)
        self.db = db
        self.quest_view_instance = quest_view_instance
        self.page_visited = page_visited
        self.add_item(Button(label='🔗 Open Post Page', style=discord.ButtonStyle.link, url=COMMUNITY_POST_URL))
    
    @discord.ui.button(label='✅ Post Confirmed', style=discord.ButtonStyle.success)
    async def confirm_post_like(self, interaction: discord.Interaction, button: Button):
        user_data = await self.db.get_user(interaction.user.id)
        
        if user_data and user_data.get('quest4_complete'):
            await interaction.response.send_message(
                "✅ Step 4 is already completed!",
                ephemeral=True
            )
            return
        
        # Check if page was visited
        if not self.page_visited:
            await interaction.response.send_message(
                "❌ Please visit the page first to complete the quest.\n\n"
                "1. Click 'Open Post Page' button to go to the page\n"
                "2. Click 'Post Page Visited' button\n"
                "3. Then click 'Post Confirmed' button",
                ephemeral=True
            )
            return
        
        # Check Steam ID (minimal verification)
        if not user_data or not user_data.get('steam_id'):
            await interaction.response.send_message(
                "❌ Please complete Step 1: Link Steam ID first!",
                ephemeral=True
            )
            return
        
        # Steam community post likes cannot be verified via API,
        # so we assume the user visited the page and clicked confirm
        await self.db.create_user(interaction.user.id)
        await self.db.update_quest(interaction.user.id, 4, True)
        
        await interaction.response.defer(ephemeral=True)
        
        await interaction.followup.send(
            "✅ Step 4: Like Post completed!",
            ephemeral=True
        )
        
        # 모든 퀘스트 완료 확인 및 자동 롤 부여
        await auto_assign_reward_role(interaction, self.db)
        
        # Select 메뉴가 포함된 Embed 업데이트
        await self.quest_view_instance.update_embed(interaction)


class QuestView(View):
    """퀘스트 상호작용을 위한 View"""
    
    def __init__(self, db: DatabaseManager, user_data: Optional[dict] = None):
        super().__init__(timeout=None)
        self.db = db
        self.user_data = user_data or {}
        
        # 퀘스트 Select 메뉴 추가
        quest_select = QuestSelect(db, self)
        self.add_item(quest_select)
    
    async def update_embed(self, interaction: discord.Interaction):
        """Update embed"""
        user_data = await self.db.get_user(interaction.user.id)
        if not user_data:
            await self.db.create_user(interaction.user.id)
            user_data = await self.db.get_user(interaction.user.id)
        
        # 퀘스트 상태
        quest1_status = "✅ Complete" if user_data.get('quest1_complete') else "❌ Incomplete"
        quest2_status = "✅ Complete" if user_data.get('quest2_complete') else "❌ Incomplete"
        quest3_status = "✅ Complete" if user_data.get('quest3_complete') else "❌ Incomplete"
        quest4_status = "✅ Complete" if user_data.get('quest4_complete') else "❌ Incomplete"
        
        embed = discord.Embed(
            title="🎮 Steam Code SZ Program",
            description="Complete these quests to receive a special Discord role.\nAdventurers who receive the special role will get additional rewards. (Rewards to be announced)",
            color=discord.Color.blue()
        )
        
        # 마일스톤 리워드 이미지 추가
        if MILESTONE_REWARD_IMAGE_URL:
            embed.set_image(url=MILESTONE_REWARD_IMAGE_URL)
        
        embed.add_field(
            name="Step 1: Link Steam ID",
            value=quest1_status,
            inline=False
        )
        
        embed.add_field(
            name="Step 2: Spot Zero Wishlist",
            value=quest2_status,
            inline=False
        )
        
        embed.add_field(
            name="Step 3: Follow Spot Zero Steam Page",
            value=quest3_status,
            inline=False
        )
        
        embed.add_field(
            name="Step 4: Like Post",
            value=quest4_status,
            inline=False
        )
        
        # View 재생성 (상태 반영)
        view = QuestView(self.db, user_data)
        
        # interaction 상태 확인 및 메시지 전송
        try:
            # response가 이미 완료되었는지 확인
            if interaction.response.is_done():
                # followup.send 사용 (이미 defer 또는 response가 완료된 경우)
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            else:
                # response.send_message 사용 (아직 response가 완료되지 않은 경우)
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except discord.errors.InteractionResponded:
            # 이미 응답이 전송된 경우 followup 사용
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            print(f"update_embed 메시지 전송 오류: {e}")
            import traceback
            traceback.print_exc()
            # edit 시도
            try:
                await interaction.edit_original_response(embed=embed, view=view)
            except Exception as e2:
                print(f"update_embed edit 오류: {e2}")
                # 최후의 수단: followup 재시도
                try:
                    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                except:
                    pass


@tree.command(name='steam', description='Start Steam Code SZ Program')
async def steam_command(interaction: discord.Interaction):
    """Steam command - Show Welcome Embed"""
    # Defer response immediately to avoid rate limit issues
    # This gives us more time to process and reduces rate limit errors
    try:
        await interaction.response.defer(ephemeral=True)
    except discord.errors.InteractionResponded:
        # Already responded, continue with followup
        pass
    except discord.errors.HTTPException as e:
        if e.status == 429:
            # Rate limited - try to send error message via followup
            try:
                await interaction.followup.send(
                    "⚠️ Discord API rate limit exceeded. Please try again in a few seconds.",
                    ephemeral=True
                )
            except:
                pass
            return
        raise
    
    db = DatabaseManager()
    
    try:
        # Get user data
        user_data = await db.get_user(interaction.user.id)
        if not user_data:
            await db.create_user(interaction.user.id)
            user_data = await db.get_user(interaction.user.id)
    except ValueError as e:
        # DATABASE_URL not set or connection failed
        try:
            await interaction.followup.send(
                f"❌ Database configuration error.\n\n"
                f"**Error:** {str(e)}\n\n"
                f"Please contact the administrator to set up the database.",
                ephemeral=True
            )
        except discord.errors.HTTPException as http_err:
            if http_err.status == 429:
                print(f"Rate limited while sending database error message: {http_err}")
            else:
                raise
        return
    except Exception as e:
        # Other database errors
        print(f"Database error in steam_command: {e}")
        try:
            await interaction.followup.send(
                "❌ An error occurred while accessing the database. Please try again later.",
                ephemeral=True
            )
        except discord.errors.HTTPException as http_err:
            if http_err.status == 429:
                print(f"Rate limited while sending database error message: {http_err}")
            else:
                raise
        return
    
    # Quest status
    quest1_status = "✅ Complete" if user_data.get('quest1_complete') else "❌ Incomplete"
    quest2_status = "✅ Complete" if user_data.get('quest2_complete') else "❌ Incomplete"
    quest3_status = "✅ Complete" if user_data.get('quest3_complete') else "❌ Incomplete"
    quest4_status = "✅ Complete" if user_data.get('quest4_complete') else "❌ Incomplete"
    
    embed = discord.Embed(
        title="🎮 Steam Code SZ Program",
        description="Complete these quests to receive a special Discord role.\nAdventurers who receive the special role will get additional rewards. (Rewards to be announced)",
        color=discord.Color.blue()
    )
    
    # Add milestone reward image
    if MILESTONE_REWARD_IMAGE_URL:
        embed.set_image(url=MILESTONE_REWARD_IMAGE_URL)
    
    embed.add_field(
        name="Step 1: Link Steam ID",
        value=quest1_status,
        inline=False
    )
    
    embed.add_field(
        name="Step 2: Spot Zero Wishlist",
        value=quest2_status,
        inline=False
    )
    
    embed.add_field(
        name="Step 3: Follow Spot Zero Steam Page",
        value=quest3_status,
        inline=False
    )
    
    embed.add_field(
        name="Step 4: Like Post",
        value=quest4_status,
        inline=False
    )
    
    view = QuestView(db, user_data)
    
    # Send message via followup (since we already deferred)
    try:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except discord.errors.HTTPException as e:
        if e.status == 429:
            # Rate limited - try again with exponential backoff
            print(f"Rate limited in steam_command followup, retrying...")
            await asyncio.sleep(2)  # Wait 2 seconds
            try:
                await interaction.followup.send(
                    "⚠️ Discord API is currently rate limited. Please try the command again in a few seconds.",
                    ephemeral=True
                )
            except:
                pass
        else:
            raise


@bot.event
async def on_ready():
    """Bot이 준비되었을 때 실행"""
    print(f'{bot.user}가 로그인했습니다!')
    try:
        synced = await tree.sync()
        print(f'{len(synced)}개의 명령어가 동기화되었습니다.')
    except Exception as e:
        print(f'명령어 동기화 오류: {e}')

@bot.event
async def on_resume():
    """Gateway 연결이 재개되었을 때 실행"""
    print(f'[INFO] Gateway 연결이 재개되었습니다. (Session: {bot.session_id})')

@bot.event
async def on_disconnect():
    """Gateway 연결이 끊어졌을 때 실행"""
    print(f'[WARNING] Gateway 연결이 끊어졌습니다. 자동 재연결을 시도합니다...')

@bot.event
async def on_connect():
    """Gateway에 연결되었을 때 실행"""
    print(f'[INFO] Gateway에 연결되었습니다.')


if __name__ == '__main__':
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN 환경 변수가 설정되지 않았습니다!")
        exit(1)
    
    bot.run(DISCORD_TOKEN)

