import discord
from discord import app_commands
from discord.ui import Button, View, Modal, TextInput, Select
import aiohttp
import os
import re
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
MILESTONE_REWARD_IMAGE_URL = os.getenv('MILESTONE_REWARD_IMAGE_URL', 'https://i.postimg.cc/WpCsTc92/paint-(2).png')  # 마일스톤 리워드 소개 이미지 URL
REWARD_ROLE_ID = os.getenv('REWARD_ROLE_ID', '1448577103728607344')  # 모든 퀘스트 완료 시 부여할 역할 ID

intents = discord.Intents.default()
# message_content intent는 슬래시 명령어만 사용하므로 필요 없음
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


class DatabaseManager:
    """PostgreSQL 데이터베이스 관리 클래스"""
    
    def __init__(self):
        self.pool = None
        self._init_task = None
    
    async def _get_pool(self):
        """데이터베이스 연결 풀 가져오기 (초기화)"""
        if self.pool is None:
            # DATABASE_URL 환경 변수에서 연결 정보 가져오기
            # Railway에서는 DATABASE_URL (내부 네트워크) 또는 DATABASE_PUBLIC_URL (외부 접근) 사용
            database_url = os.getenv('DATABASE_URL') or os.getenv('DATABASE_PUBLIC_URL')
            
            # 디버깅: 환경 변수 확인
            print(f"[DEBUG] DATABASE_URL exists: {bool(os.getenv('DATABASE_URL'))}")
            print(f"[DEBUG] DATABASE_PUBLIC_URL exists: {bool(os.getenv('DATABASE_PUBLIC_URL'))}")
            print(f"[DEBUG] All env vars: {[k for k in os.environ.keys() if 'DATABASE' in k or 'POSTGRES' in k]}")
            
            if not database_url:
                error_msg = (
                    "DATABASE_URL or DATABASE_PUBLIC_URL environment variable is not set.\n\n"
                    "**Railway 설정 방법:**\n"
                    "1. Railway 대시보드 → 프로젝트 선택\n"
                    "2. PostgreSQL 서비스가 생성되어 있는지 확인\n"
                    "3. 봇 서비스와 PostgreSQL 서비스가 같은 프로젝트에 있는지 확인\n"
                    "4. PostgreSQL 서비스 → 'Variables' 탭에서 DATABASE_URL 확인\n"
                    "5. 봇 서비스 → 'Variables' 탭에서 DATABASE_URL이 있는지 확인\n"
                    "   - 없다면 PostgreSQL 서비스의 'Connect' 버튼 클릭\n"
                    "   - 또는 수동으로 환경 변수 추가\n"
                    "6. 서비스 재배포\n\n"
                    "**수동 추가 시:**\n"
                    "봇 서비스의 Variables 탭에서:\n"
                    "- Key: DATABASE_URL\n"
                    "  Value: postgresql://postgres:PBvfgJmxFoUoJOzRowIEbziWtSZKTywg@postgres.railway.internal:5432/railway"
                )
                raise ValueError(error_msg)
            
            # Railway PostgreSQL URL 형식: postgresql://user:password@host:port/database
            # asyncpg는 postgresql:// 대신 postgres://를 사용할 수도 있음
            if database_url.startswith('postgresql://'):
                database_url = database_url.replace('postgresql://', 'postgres://', 1)
            
            try:
                self.pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
                await self.init_database()
            except Exception as e:
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
    
    async def init_database(self):
        """데이터베이스 초기화 및 테이블 생성"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
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
            
            # 기존 테이블에 quest4_complete 컬럼 추가 (마이그레이션)
            try:
                await conn.execute('ALTER TABLE users ADD COLUMN quest4_complete INTEGER DEFAULT 0')
            except asyncpg.exceptions.DuplicateColumnError:
                # 컬럼이 이미 존재하는 경우 무시
                pass
    
    async def get_user(self, discord_id: int) -> Optional[dict]:
        """사용자 정보 조회"""
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
    
    async def create_user(self, discord_id: int):
        """새 사용자 생성"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO users (discord_id) VALUES ($1)
                ON CONFLICT (discord_id) DO NOTHING
            ''', discord_id)
    
    async def update_steam_id(self, discord_id: int, steam_id: str):
        """Steam ID 업데이트"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                UPDATE users SET steam_id = $1, quest1_complete = 1 WHERE discord_id = $2
            ''', steam_id, discord_id)
    
    async def update_quest(self, discord_id: int, quest_number: int, complete: bool = True):
        """퀘스트 완료 상태 업데이트"""
        pool = await self._get_pool()
        quest_column = f'quest{quest_number}_complete'
        async with pool.acquire() as conn:
            await conn.execute(f'''
                UPDATE users SET {quest_column} = $1 WHERE discord_id = $2
            ''', 1 if complete else 0, discord_id)
    
    def get_total_wishlist_count(self) -> int:
        """전체 위시리스트 수 조회 (캐시된 값 반환)"""
        # 실시간으로 가져오는 함수는 별도로 구현
        # 여기서는 캐시된 값을 반환 (실시간 업데이트는 async 함수에서)
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
    
    async def close(self):
        """데이터베이스 연결 풀 종료"""
        if self.pool:
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
            # 숫자만 있는 경우 (Steam ID)
            if steam_input.isdigit():
                steam_id = steam_input
        
        # 먼저 defer를 호출하여 상호작용을 처리
        await interaction.response.defer(ephemeral=True)
        
        if not steam_id:
            await interaction.followup.send(
                "❌ Invalid Steam ID or URL. Please enter Steam ID or profile URL.",
                ephemeral=True
            )
            return
        
        # Steam API로 검증
        is_valid = await verify_steam_id(steam_id)
        
        if not is_valid:
            await interaction.followup.send(
                "❌ Unable to verify Steam ID. Please check if it's a valid Steam ID.",
                ephemeral=True
            )
            return
        
        # 데이터베이스에 저장
        await self.db.create_user(interaction.user.id)
        await self.db.update_steam_id(interaction.user.id, steam_id)
        # Steam ID 연동 완료 처리
        await self.db.update_quest(interaction.user.id, 1, True)
        
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
    """Steam 커스텀 URL을 Steam ID로 변환"""
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
        print(f"위시리스트 확인 실패: steam_id가 없음")
        return False
    
    # Steam 위시리스트 데이터 가져오기
    # 참고: Steam 위시리스트 API는 로그인이 필요하거나 프로필이 공개되어 있어야 함
    url = f"https://store.steampowered.com/wishlist/profiles/{steam_id}/wishlistdata/"
    
    print(f"위시리스트 확인 시작: steam_id={steam_id}, app_id={app_id}")
    print(f"위시리스트 API URL: {url}")
    
    try:
        # 더 나은 헤더 설정 (브라우저처럼 보이도록)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': f'https://store.steampowered.com/wishlist/profiles/{steam_id}/',
            'X-Requested-With': 'XMLHttpRequest'
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                print(f"위시리스트 API 응답 상태: {response.status}")
                
                if response.status == 200:
                    # Content-Type 확인
                    content_type = response.headers.get('Content-Type', '').lower()
                    print(f"위시리스트 API Content-Type: {content_type}")
                    
                    text = await response.text()
                    # 빈 응답 체크
                    if not text or text.strip() == '':
                        print(f"위시리스트 API 빈 응답: steam_id={steam_id}")
                        return False
                    
                    # HTML 응답인지 확인 (Steam이 로그인 페이지나 오류 페이지를 반환할 수 있음)
                    if text.strip().startswith('<!DOCTYPE') or text.strip().startswith('<html'):
                        print(f"위시리스트 API가 HTML을 반환함 (로그인 필요 또는 프로필 비공개): steam_id={steam_id}")
                        print(f"응답 시작 부분: {text[:200]}")
                        return False
                    
                    try:
                        data = await response.json()
                    except Exception as json_error:
                        # JSON 파싱 실패 시 텍스트로 확인
                        print(f"위시리스트 API JSON 파싱 실패: {json_error}")
                        print(f"응답 텍스트 (처음 500자): {text[:500]}")
                        # HTML인 경우 추가 안내
                        if text.strip().startswith('<!DOCTYPE') or text.strip().startswith('<html'):
                            print(f"⚠️ Steam이 HTML 페이지를 반환했습니다. 프로필이 비공개이거나 로그인이 필요할 수 있습니다.")
                        return False
                    
                    # 위시리스트 데이터가 있고, 해당 앱 ID가 포함되어 있는지 확인
                    if data and isinstance(data, dict):
                        # 앱 ID를 여러 형식으로 확인
                        app_id_str = str(app_id)
                        app_id_int = int(app_id) if str(app_id).isdigit() else None
                        
                        print(f"위시리스트 데이터 키 개수: {len(data)}")
                        if len(data) > 0:
                            print(f"위시리스트 API 응답 키 샘플 (처음 10개): {list(data.keys())[:10]}")
                        
                        # 문자열 키로 확인
                        if app_id_str in data:
                            print(f"✅ 위시리스트 확인 성공 (문자열 키): {app_id_str}")
                            return True
                        
                        # 숫자 키로 확인 (dict의 키는 정수일 수 있음)
                        if app_id_int:
                            # 직접 숫자 키로 확인
                            if app_id_int in data:
                                print(f"✅ 위시리스트 확인 성공 (숫자 키 직접): {app_id_int}")
                                return True
                            # 문자열로 변환한 키로 확인
                            if str(app_id_int) in data:
                                print(f"✅ 위시리스트 확인 성공 (숫자 키 문자열 변환): {app_id_int}")
                                return True
                        
                        # 모든 키를 문자열로 변환하여 확인 (Steam API가 문자열 키를 사용할 수 있음)
                        data_keys_str = [str(k) for k in data.keys()]
                        if app_id_str in data_keys_str:
                            print(f"✅ 위시리스트 확인 성공 (문자열 변환 후): {app_id_str}")
                            return True
                        
                        # 모든 키를 정수로 변환하여 확인
                        data_keys_int = []
                        for k in data.keys():
                            try:
                                data_keys_int.append(int(k))
                            except (ValueError, TypeError):
                                pass
                        if app_id_int and app_id_int in data_keys_int:
                            print(f"✅ 위시리스트 확인 성공 (정수 변환 후): {app_id_int}")
                            return True
                        
                        # 찾는 앱 ID 정보 출력
                        print(f"❌ 위시리스트에 앱 ID가 없음")
                        print(f"   찾는 앱 ID: {app_id} (문자열: {app_id_str}, 숫자: {app_id_int})")
                    else:
                        print(f"위시리스트 API 응답이 dict가 아님: {type(data)}")
                        if data:
                            print(f"응답 데이터 타입: {type(data)}, 내용 (처음 200자): {str(data)[:200]}")
                elif response.status == 403:
                    print(f"위시리스트 API 접근 거부 (403): 프로필이 비공개일 수 있습니다. steam_id={steam_id}")
                    return False
                elif response.status == 404:
                    print(f"위시리스트 API 404: 프로필을 찾을 수 없습니다. steam_id={steam_id}")
                    return False
                else:
                    print(f"위시리스트 API 응답 상태 코드: {response.status}")
    except aiohttp.ClientError as e:
        print(f"위시리스트 확인 네트워크 오류: {e}")
        return False
    except Exception as e:
        print(f"위시리스트 확인 오류: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return False


async def auto_assign_reward_role(interaction: discord.Interaction, db: DatabaseManager):
    """모든 퀘스트 완료 시 자동으로 보상 역할 부여"""
    try:
        # 사용자 데이터 확인
        user_data = await db.get_user(interaction.user.id)
        if not user_data:
            print(f"[ROLE] User {interaction.user.id} not found in database")
            return False
        
        # 모든 퀘스트 완료 확인
        all_complete = await db.are_all_quests_complete(interaction.user.id)
        print(f"[ROLE] User {interaction.user.id} - All quests complete: {all_complete}")
        print(f"[ROLE] Quest status - Q1: {user_data.get('quest1_complete')}, Q2: {user_data.get('quest2_complete')}, Q3: {user_data.get('quest3_complete')}, Q4: {user_data.get('quest4_complete')}")
        
        if not all_complete:
            print(f"[ROLE] Not all quests completed for user {interaction.user.id}")
            return False
        
        # Guild 확인 (DM에서는 역할 부여 불가)
        if not interaction.guild:
            print(f"[ROLE] No guild found for user {interaction.user.id}")
            return False
        
        # 역할 ID 확인
        try:
            role_id = int(REWARD_ROLE_ID)
            print(f"[ROLE] Attempting to assign role ID: {role_id}")
        except (ValueError, TypeError):
            print(f"[ROLE] Invalid role ID: {REWARD_ROLE_ID}")
            return False
        
        # 역할 가져오기
        role = interaction.guild.get_role(role_id)
        if not role:
            print(f"[ROLE] Role {role_id} not found in guild {interaction.guild.id}")
            # 역할을 찾을 수 없을 때 사용자에게 알림
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        f"⚠️ Role with ID {role_id} not found in this server. Please contact an administrator.",
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        f"⚠️ Role with ID {role_id} not found in this server. Please contact an administrator.",
                        ephemeral=True
                    )
            except:
                pass
            return False
        
        print(f"[ROLE] Found role: {role.name} (ID: {role.id})")
        
        # 멤버 가져오기
        member = interaction.guild.get_member(interaction.user.id)
        if not member:
            print(f"[ROLE] Member not in cache, fetching...")
            member = await interaction.guild.fetch_member(interaction.user.id)
        
        if not member:
            print(f"[ROLE] Could not fetch member {interaction.user.id}")
            return False
        
        # 이미 역할이 있는지 확인
        if role in member.roles:
            print(f"[ROLE] User {interaction.user.id} already has role {role.name}")
            return True
        
        # 역할 자동 부여
        print(f"[ROLE] Assigning role {role.name} to user {interaction.user.id}")
        await member.add_roles(role, reason="Steam Code SZ Program - All quests completed")
        print(f"[ROLE] Successfully assigned role {role.name} to user {interaction.user.id}")
        
        # 성공 메시지 전송
        try:
            success_message = f"🎉 Congratulations! You've completed all quests and the role **{role.name}** has been automatically assigned!"
            if interaction.response.is_done():
                await interaction.followup.send(success_message, ephemeral=True)
            else:
                await interaction.response.send_message(success_message, ephemeral=True)
            print(f"[ROLE] Success message sent to user {interaction.user.id}")
        except Exception as e:
            print(f"[ROLE] Failed to send success message: {e}")
            # 메시지 전송 실패해도 역할은 부여되었으므로 성공으로 간주
        
        return True
        
    except discord.Forbidden as e:
        print(f"[ROLE] Permission denied: {e}")
        print(f"[ROLE] Bot may not have 'Manage Roles' permission or role hierarchy issue")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ Failed to assign role: Bot doesn't have permission to manage roles. Please contact an administrator.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ Failed to assign role: Bot doesn't have permission to manage roles. Please contact an administrator.",
                    ephemeral=True
                )
        except:
            pass
        return False
    except discord.HTTPException as e:
        print(f"[ROLE] HTTP error: {e}")
        import traceback
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"[ROLE] Unexpected error: {e}")
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
                "❌ You can only receive roles in a server!",
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
                    "✅ You already have this role!",
                    ephemeral=True
                )
                return
            
            # 역할 부여
            await member.add_roles(role, reason="Spot Zero Hunter Program - All quests completed")
            
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
                label="Enter Steam ID",
                description="Enter Steam ID directly",
                value="steam_id",
                emoji="🔢"
            ),
            discord.SelectOption(
                label="Enter Steam Profile URL",
                description="Enter Steam profile URL",
                value="profile_url",
                emoji="🔗"
            )
        ]
        super().__init__(placeholder="Link Steam Account (Optional)...", options=options, min_values=1, max_values=1)
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
        super().__init__(placeholder="Select a quest...", min_values=1, max_values=1)
        self._update_options()
    
    def _update_options(self):
        """사용자 상태에 따라 옵션 업데이트 (완료된 퀘스트는 제외)"""
        user_data = self.view_instance.user_data or {}
        options = []
        
        # Step 1: Steam ID 연동 (완료되지 않은 경우만 표시)
        if not user_data.get('quest1_complete'):
            options.append(discord.SelectOption(
                label="Step 1: Link Steam ID",
                description="Link your Steam account",
                value="quest1",
                emoji="🔗"
            ))
        
        # Step 2: Spot Zero Wishlist (완료되지 않은 경우만 표시)
        if not user_data.get('quest2_complete'):
            options.append(discord.SelectOption(
                label="Step 2: Spot Zero Wishlist",
                description="Add Spot Zero to your wishlist",
                value="quest2",
                emoji="🎁"
            ))
        
        # Step 3: Spot Zero Steam page follow (완료되지 않은 경우만 표시)
        if not user_data.get('quest3_complete'):
            options.append(discord.SelectOption(
                label="Step 3: Follow Spot Zero Steam Page",
                description="Follow the Spot Zero Steam page",
                value="quest3",
                emoji="⭐"
            ))
        
        # Step 4: 포스트 라이크 (완료되지 않은 경우만 표시)
        if not user_data.get('quest4_complete'):
            options.append(discord.SelectOption(
                label="Step 4: Like Post",
                description="Like the community post",
                value="quest4",
                emoji="👍"
            ))
        
        # 모든 퀘스트가 완료된 경우
        if not options:
            options.append(discord.SelectOption(
                label="All Quests Completed! 🎉",
                description="You've completed all quests!",
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
                "🎉 You've completed all quests!\n\n"
                "The reward role has been automatically assigned. Check it out in the server!",
                ephemeral=True
            )
            return
        
        if selected == "quest1":
            # Step 1: Steam ID 연동
            if user_data.get('quest1_complete'):
                await interaction.response.send_message(
                    "✅ Step 1 is already completed!",
                    ephemeral=True
                )
                return
            
            # 가이드 Embed 먼저 표시
            guide_embed = discord.Embed(
                title="📝 Step 1: Link Steam ID Guide",
                description="**💡 Tip**: You can find your Steam profile URL and ID by clicking on your Steam profile.\n\n"
                           "**How to find Steam ID:**\n"
                           "1. Go to your Steam profile page\n"
                           "2. In the address bar, the number after `/profiles/` is your Steam ID\n"
                           "3. Or if you have a custom URL, enter the text after `/id/`\n\n"
                           "After reviewing the guide, click the button below to enter your Steam ID.",
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
            
            # 가이드 메시지와 함께 View 표시
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
            # Step 3: Spot Zero Steam page follow
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
            
            # 가이드 메시지와 함께 View 표시 (처음에는 스토어 페이지 링크만)
            guide_embed = discord.Embed(
                title="📝 Step 3: Follow Spot Zero Steam Page Guide",
                description="**How to follow Steam page:**\n"
                           "1. Click the 'Open Store Page' button below to go to the Spot Zero store page\n"
                           "2. Click the 'Follow' button on the store page\n"
                           "3. Return to Discord and click 'Store Page Visited' button\n"
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
            # Step 4: 포스트 라이크
            if user_data.get('quest4_complete'):
                await interaction.response.send_message(
                    "✅ Step 4 is already completed!",
                    ephemeral=True
                )
                return
            
            # 가이드 메시지와 함께 View 표시
            guide_embed = discord.Embed(
                title="📝 Step 4: Like Post Guide",
                description="**How to like the post:**\n"
                           "1. Click the 'Open Post Page' button below to go to the post page\n"
                           "2. Click the like button on the post page\n"
                           "3. Return to Discord and click 'Post Page Visited' button\n"
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
    
    def __init__(self, db: DatabaseManager, quest_view_instance, steam_id: str, page_visited: bool = False):
        super().__init__(timeout=300)  # 5분 타임아웃
        self.db = db
        self.quest_view_instance = quest_view_instance
        self.steam_id = steam_id
        self.page_visited = page_visited  # 페이지 방문 여부 저장
    
    @discord.ui.button(label='✅ Manual Confirm (Added to Wishlist)', style=discord.ButtonStyle.success)
    async def manual_confirm(self, interaction: discord.Interaction, button: Button):
        user_data = await self.db.get_user(interaction.user.id)
        
        if user_data and user_data.get('quest2_complete'):
            await interaction.response.send_message(
                "✅ Step 2 is already completed!",
                ephemeral=True
            )
            return
        
        # 페이지 방문 확인 (수동 확인도 페이지 방문 후에만 가능)
        if not self.page_visited:
            await interaction.response.send_message(
                "❌ Please visit the page first to complete the quest.\n\n"
                "1. Click 'Open Store Page' button to go to the page\n"
                "2. Click 'Store Page Visited' button\n"
                "3. After adding to wishlist, click 'Wishlist Added' button\n"
                "4. If verification fails, use 'Manual Confirm' button",
                ephemeral=True
            )
            return
        
        # 수동 확인 - 완료 처리
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
        
        # 재검증 시도
        has_wishlist = await check_wishlist(self.steam_id, APP_ID)
        
        if has_wishlist:
            await self.db.create_user(interaction.user.id)
            await self.db.update_quest(interaction.user.id, 2, True)
            
            await interaction.followup.send(
                "✅ Verification successful! Step 2: Spot Zero Wishlist completed!",
                ephemeral=True
            )
            
            # 모든 퀘스트 완료 확인 및 자동 롤 부여
            await auto_assign_reward_role(interaction, self.db)
            
            # Select 메뉴가 포함된 Embed 업데이트
            await self.quest_view_instance.update_embed(interaction)
        else:
            await interaction.followup.send(
                "❌ Verification still failed.\n\n"
                "If you've added it to your wishlist, please use the 'Manual Confirm' button.",
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
        """스토어 페이지 방문 완료 버튼 - 위시리스트 확인 버튼을 활성화"""
        # 페이지 방문 플래그 설정
        self.page_visited = True
        
        # 위시리스트 확인 버튼이 있는 새로운 View 생성
        view = WishlistConfirmView(self.db, self.quest_view_instance, page_visited=True)
        
        try:
            await interaction.response.edit_message(
                content="✅ You've visited the store page!\n\n"
                       "Now add Spot Zero to your wishlist, then click the 'Wishlist Added' button below.",
                view=view
            )
        except:
            # edit_message가 실패하면 새 메시지로 전송
            await interaction.response.send_message(
                "✅ You've visited the store page!\n\n"
                "Now add Spot Zero to your wishlist, then click the 'Wishlist Added' button below.",
                view=view,
                ephemeral=True
            )


class WishlistConfirmView(View):
    """위시리스트 확인을 위한 View - page_visited=True일 때만 생성되어야 함"""
    
    def __init__(self, db: DatabaseManager, quest_view_instance, page_visited: bool = False):
        super().__init__(timeout=None)
        self.db = db
        self.quest_view_instance = quest_view_instance
        self.page_visited = page_visited
        store_url = f"https://store.steampowered.com/app/{APP_ID}/"
        self.add_item(Button(label='🔗 Open Spot Zero Store Page', style=discord.ButtonStyle.link, url=store_url))
        # page_visited가 False이면 확인 버튼을 추가하지 않음 (무조건 방문 완료 버튼을 클릭해야 함)
        # 이 View는 visited_store 버튼을 클릭했을 때만 생성되므로 page_visited=True여야 함
        if not page_visited:
            # 이 경우는 정상적인 플로우가 아님 - 경고만 출력
            print(f"경고: WishlistConfirmView가 page_visited=False로 생성됨")
    
    @discord.ui.button(label='✅ Wishlist Added', style=discord.ButtonStyle.success)
    async def confirm_wishlist(self, interaction: discord.Interaction, button: Button):
        user_data = await self.db.get_user(interaction.user.id)
        
        if user_data and user_data.get('quest2_complete'):
            await interaction.response.send_message(
                "✅ Step 2 is already completed!",
                ephemeral=True
            )
            return
        
        # 페이지 방문 확인
        if not self.page_visited:
            await interaction.response.send_message(
                "❌ Please visit the page first to complete the quest.\n\n"
                "1. Click 'Open Store Page' button to go to the page\n"
                "2. Click 'Store Page Visited' button\n"
                "3. Then click 'Wishlist Added' button",
                ephemeral=True
            )
            return
        
        # Steam ID 확인
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
            # 검증 실패 시 수동 확인 옵션 제공 (page_visited 상태 전달)
            view = WishlistManualConfirmView(self.db, self.quest_view_instance, steam_id, page_visited=self.page_visited)
            await interaction.followup.send(
                "❌ Automatic verification failed.\n\n"
                "**Please check the following:**\n"
                "1. Make sure your Steam profile is set to public\n"
                "   → [Profile Settings Link](https://steamcommunity.com/my/edit/settings)\n"
                "2. Make sure you've added Spot Zero to your wishlist\n"
                "   → [Spot Zero Store Page](https://store.steampowered.com/app/3966570/)\n\n"
                "**If you've added it to your wishlist**, please click the 'Manual Confirm' button below.\n"
                "It may take some time for Steam API to recognize your profile.",
                view=view,
                ephemeral=True
            )
            return
        
        # 검증 성공 - 완료 처리
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
        # 스토어 페이지 링크 버튼은 항상 표시
        self.add_item(Button(label='🔗 Open Spot Zero Store Page', style=discord.ButtonStyle.link, url=store_url))
    
    @discord.ui.button(label='✅ Store Page Visited', style=discord.ButtonStyle.primary)
    async def visited_store(self, interaction: discord.Interaction, button: Button):
        """스토어 페이지 방문 완료 버튼 - 확인 버튼을 활성화"""
        # 페이지 방문 플래그 설정
        self.page_visited = True
        
        # 확인 버튼이 있는 새로운 View 생성 (방문 완료 버튼을 클릭했으므로 page_visited=True)
        # 하지만 실제로는 사용자가 방문했는지 확인할 수 없으므로, 
        # View 생성 시점에 page_visited를 True로 설정하되, 
        # 실제 확인 버튼에서는 추가 검증을 수행
        view = SteamFollowConfirmView(self.db, self.quest_view_instance, page_visited=True)
        
        try:
            await interaction.response.edit_message(
                content="✅ You've visited the store page!\n\n"
                       "Now click the 'Follow' button on the store page, then click the 'Follow Confirmed' button below.",
                view=view
            )
        except:
            # edit_message가 실패하면 새 메시지로 전송
            await interaction.response.send_message(
                "✅ You've visited the store page!\n\n"
                "Now click the 'Follow' button on the store page, then click the 'Follow Confirmed' button below.",
                view=view,
                ephemeral=True
            )


class SteamFollowConfirmView(View):
    """팔로우 확인을 위한 View - page_visited=True일 때만 생성되어야 함"""
    
    def __init__(self, db: DatabaseManager, quest_view_instance, page_visited: bool = False):
        super().__init__(timeout=None)
        self.db = db
        self.quest_view_instance = quest_view_instance
        self.page_visited = page_visited
        store_url = f"https://store.steampowered.com/app/{APP_ID}/"
        self.add_item(Button(label='🔗 Open Spot Zero Store Page', style=discord.ButtonStyle.link, url=store_url))
        # page_visited가 False이면 확인 버튼을 추가하지 않음 (무조건 방문 완료 버튼을 클릭해야 함)
        # 이 View는 visited_store 버튼을 클릭했을 때만 생성되므로 page_visited=True여야 함
        if not page_visited:
            # 이 경우는 정상적인 플로우가 아님 - 경고만 출력
            print(f"경고: SteamFollowConfirmView가 page_visited=False로 생성됨")
    
    @discord.ui.button(label='✅ Follow Confirmed', style=discord.ButtonStyle.success)
    async def confirm_follow(self, interaction: discord.Interaction, button: Button):
        user_data = await self.db.get_user(interaction.user.id)
        
        if user_data and user_data.get('quest3_complete'):
            await interaction.response.send_message(
                "✅ Step 3 is already completed!",
                ephemeral=True
            )
            return
        
        # 페이지 방문 확인
        if not self.page_visited:
            await interaction.response.send_message(
                "❌ Please visit the page first to complete the quest.\n\n"
                "1. Click 'Open Store Page' button to go to the page\n"
                "2. Click 'Store Page Visited' button\n"
                "3. Then click 'Follow Confirmed' button",
                ephemeral=True
            )
            return
        
        # Steam ID 확인
        if not user_data or not user_data.get('steam_id'):
            await interaction.response.send_message(
                "❌ Please complete Step 1: Link Steam ID first!",
                ephemeral=True
            )
            return
        
        # Steam 페이지 팔로우는 API로 확인할 수 없으므로,
        # 사용자가 페이지를 방문하고 확인 버튼을 누른 것으로 간주
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
        """포스트 페이지 방문 완료 버튼 - 확인 버튼을 활성화"""
        # 페이지 방문 플래그 설정
        self.page_visited = True
        
        # 확인 버튼이 있는 새로운 View 생성 (방문 완료 버튼을 클릭했으므로 page_visited=True)
        # 하지만 실제로는 사용자가 방문했는지 확인할 수 없으므로,
        # View 생성 시점에 page_visited를 True로 설정하되,
        # 실제 확인 버튼에서는 추가 검증을 수행
        view = PostLikeConfirmView(self.db, self.quest_view_instance, page_visited=True)
        
        try:
            await interaction.response.edit_message(
                content="✅ You've visited the post page!\n\n"
                       "Now click the like button on the post page, then click the 'Post Confirmed' button below.",
                view=view
            )
        except:
            # edit_message가 실패하면 새 메시지로 전송
            await interaction.response.send_message(
                "✅ You've visited the post page!\n\n"
                "Now click the like button on the post page, then click the 'Post Confirmed' button below.",
                view=view,
                ephemeral=True
            )


class PostLikeConfirmView(View):
    """포스트 라이크 확인을 위한 View - page_visited=True일 때만 생성되어야 함"""
    
    def __init__(self, db: DatabaseManager, quest_view_instance, page_visited: bool = False):
        super().__init__(timeout=None)
        self.db = db
        self.quest_view_instance = quest_view_instance
        self.page_visited = page_visited
        self.add_item(Button(label='🔗 Open Post Page', style=discord.ButtonStyle.link, url=COMMUNITY_POST_URL))
        # page_visited가 False이면 확인 버튼을 추가하지 않음 (무조건 방문 완료 버튼을 클릭해야 함)
        # 이 View는 visited_post 버튼을 클릭했을 때만 생성되므로 page_visited=True여야 함
        if not page_visited:
            # 이 경우는 정상적인 플로우가 아님 - 경고만 출력
            print(f"경고: PostLikeConfirmView가 page_visited=False로 생성됨")
    
    @discord.ui.button(label='✅ Post Confirmed', style=discord.ButtonStyle.success)
    async def confirm_post_like(self, interaction: discord.Interaction, button: Button):
        user_data = await self.db.get_user(interaction.user.id)
        
        if user_data and user_data.get('quest4_complete'):
            await interaction.response.send_message(
                "✅ Step 4 is already completed!",
                ephemeral=True
            )
            return
        
        # 페이지 방문 확인
        if not self.page_visited:
            await interaction.response.send_message(
                "❌ Please visit the page first to complete the quest.\n\n"
                "1. Click 'Open Post Page' button to go to the page\n"
                "2. Click 'Post Page Visited' button\n"
                "3. Then click 'Post Confirmed' button",
                ephemeral=True
            )
            return
        
        # Steam ID 확인 (최소한의 검증)
        if not user_data or not user_data.get('steam_id'):
            await interaction.response.send_message(
                "❌ Please complete Step 1: Link Steam ID first!",
                ephemeral=True
            )
            return
        
        # Steam 커뮤니티 포스트 좋아요는 API로 확인할 수 없으므로,
        # 사용자가 페이지를 방문하고 확인 버튼을 누른 것으로 간주
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
        """Embed 업데이트"""
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


@tree.command(name='steam', description='Start Spot Zero Hunter Program')
async def steam_command(interaction: discord.Interaction):
    """Steam 명령어 - Welcome Embed 표시"""
    db = DatabaseManager()
    
    try:
        # 사용자 데이터 조회
        user_data = await db.get_user(interaction.user.id)
        if not user_data:
            await db.create_user(interaction.user.id)
            user_data = await db.get_user(interaction.user.id)
    except ValueError as e:
        # DATABASE_URL이 없거나 연결 실패 시 사용자에게 안내
        await interaction.response.send_message(
            f"❌ Database configuration error.\n\n"
            f"**Error:** {str(e)}\n\n"
            f"Please contact the administrator to set up the database.",
            ephemeral=True
        )
        return
    except Exception as e:
        # 기타 데이터베이스 오류
        print(f"Database error in steam_command: {e}")
        await interaction.response.send_message(
            "❌ An error occurred while accessing the database. Please try again later.",
            ephemeral=True
        )
        return
    
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
    
    view = QuestView(db, user_data)
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.event
async def on_ready():
    """Bot이 준비되었을 때 실행"""
    print(f'{bot.user}가 로그인했습니다!')
    try:
        synced = await tree.sync()
        print(f'{len(synced)}개의 명령어가 동기화되었습니다.')
    except Exception as e:
        print(f'명령어 동기화 오류: {e}')


if __name__ == '__main__':
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN 환경 변수가 설정되지 않았습니다!")
        exit(1)
    
    bot.run(DISCORD_TOKEN)

