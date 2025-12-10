import discord
from discord import app_commands
from discord.ui import Button, View, Modal, TextInput, Select
import aiohttp
import sqlite3
import os
import re
from typing import Optional
from dotenv import load_dotenv
from bs4 import BeautifulSoup

# 환경 변수 로드
load_dotenv()

# Discord Bot 설정
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
STEAM_API_KEY = os.getenv('STEAM_API_KEY')
APP_ID = os.getenv('APP_ID', '123456')  # 기본값, 실제 App ID로 변경 필요
COMMUNITY_POST_URL = os.getenv('COMMUNITY_POST_URL', f'https://store.steampowered.com/app/{APP_ID}/Spot_Zero/')
MILESTONES = [10000, 30000, 50000]  # 마일스톤: 1만, 3만, 5만
TARGET_WISHLIST_COUNT = 50000  # 최종 목표 위시리스트 수

intents = discord.Intents.default()
# message_content intent는 슬래시 명령어만 사용하므로 필요 없음
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


class DatabaseManager:
    """SQLite 데이터베이스 관리 클래스"""
    
    def __init__(self, db_name: str = 'user_data.db'):
        self.db_name = db_name
        self.init_database()
    
    def init_database(self):
        """데이터베이스 초기화 및 테이블 생성"""
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
        
        # 기존 테이블에 quest4_complete 컬럼 추가 (마이그레이션)
        try:
            cursor.execute('ALTER TABLE users ADD COLUMN quest4_complete INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            # 컬럼이 이미 존재하는 경우 무시
            pass
        
        conn.commit()
        conn.close()
    
    def get_user(self, discord_id: int) -> Optional[dict]:
        """사용자 정보 조회"""
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
    
    def create_user(self, discord_id: int):
        """새 사용자 생성"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR IGNORE INTO users (discord_id) VALUES (?)
        ''', (discord_id,))
        
        conn.commit()
        conn.close()
    
    def update_steam_id(self, discord_id: int, steam_id: str):
        """Steam ID 업데이트"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE users SET steam_id = ?, quest1_complete = 1 WHERE discord_id = ?
        ''', (steam_id, discord_id))
        
        conn.commit()
        conn.close()
    
    def update_quest(self, discord_id: int, quest_number: int, complete: bool = True):
        """퀘스트 완료 상태 업데이트"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        quest_column = f'quest{quest_number}_complete'
        cursor.execute(f'''
            UPDATE users SET {quest_column} = ? WHERE discord_id = ?
        ''', (1 if complete else 0, discord_id))
        
        conn.commit()
        conn.close()
    
    def get_total_wishlist_count(self) -> int:
        """전체 위시리스트 수 조회 (캐시된 값 반환)"""
        # 실시간으로 가져오는 함수는 별도로 구현
        # 여기서는 캐시된 값을 반환 (실시간 업데이트는 async 함수에서)
        return 32500


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
    
    # 마일스톤 텍스트 생성
    milestone_text = ""
    for milestone in milestones:
        if milestone in achieved_milestones:
            milestone_text += f"✅ **{milestone//10000}만** "
        else:
            milestone_text += f"⚪ {milestone//10000}만 "
    
    progress_text = f"{bar}\n**{current:,}** / {milestones[-1]:,} ({total_percentage:.1f}% 달성)\n\n{milestone_text.strip()}"
    
    return progress_text, achieved_milestones


class SteamLinkModal(Modal, title='Steam 계정 연결'):
    """Steam 계정 연결을 위한 Modal"""
    
    steam_input = TextInput(
        label='Steam ID 또는 Profile URL',
        placeholder='Steam ID 64 또는 프로필 URL을 입력하세요',
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
                "❌ 유효하지 않은 Steam ID 또는 URL입니다. Steam ID 64 또는 프로필 URL을 입력해주세요.",
                ephemeral=True
            )
            return
        
        # Steam API로 검증
        is_valid = await verify_steam_id(steam_id)
        
        if not is_valid:
            await interaction.response.send_message(
                "❌ Steam ID를 확인할 수 없습니다. 올바른 Steam ID인지 확인해주세요.",
                ephemeral=True
            )
            return
        
        # 데이터베이스에 저장
        self.db.create_user(interaction.user.id)
        self.db.update_steam_id(interaction.user.id, steam_id)
        # Steam ID 연동 완료 처리
        self.db.update_quest(interaction.user.id, 1, True)
        
        await interaction.response.send_message(
            f"✅ Step 1: Steam ID 연동이 완료되었습니다! (Steam ID: {steam_id})",
            ephemeral=True
        )
        
        # Embed 업데이트
        await self.view_instance.update_embed(interaction)
        
        # Embed 업데이트
        await self.view_instance.update_embed(interaction)


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
    """Steam Store 페이지에서 위시리스트 수 가져오기"""
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
                            match = re.search(r'wishlist_count["\']?\s*[:=]\s*(\d+)', script.string)
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
                    data = await response.json()
                    # 위시리스트 데이터가 있고, 해당 앱 ID가 포함되어 있는지 확인
                    if data and isinstance(data, dict):
                        # 앱 ID가 문자열 키로 존재하는지 확인
                        if app_id in data:
                            return True
                        # 또는 숫자 키로 존재하는지 확인
                        if str(app_id) in data:
                            return True
    except Exception as e:
        print(f"위시리스트 확인 오류: {e}")
        # 오류 발생 시 사용자 확인에 의존
        return False
    
    return False


class SteamLinkGuideView(View):
    """Steam ID 연동 가이드 후 Modal을 여는 View"""
    
    def __init__(self, db: DatabaseManager, view_instance):
        super().__init__(timeout=300)  # 5분 타임아웃
        self.db = db
        self.view_instance = view_instance
    
    @discord.ui.button(label='📝 Steam ID 입력하기', style=discord.ButtonStyle.primary)
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
        
        # Step 1: Steam ID 연동 (완료되지 않은 경우만 표시)
        if not user_data.get('quest1_complete'):
            options.append(discord.SelectOption(
                label="Step 1: Steam ID 연동",
                description="Steam 계정을 연결하세요",
                value="quest1",
                emoji="🔗"
            ))
        
        # Step 2: Spot Zero Wishlist (완료되지 않은 경우만 표시)
        if not user_data.get('quest2_complete'):
            options.append(discord.SelectOption(
                label="Step 2: Spot Zero Wishlist",
                description="Spot Zero를 위시리스트에 추가하세요",
                value="quest2",
                emoji="🎁"
            ))
        
        # Step 3: Spot Zero Steam page follow (완료되지 않은 경우만 표시)
        if not user_data.get('quest3_complete'):
            options.append(discord.SelectOption(
                label="Step 3: Spot Zero Steam page follow",
                description="Spot Zero Steam 페이지를 팔로우하세요",
                value="quest3",
                emoji="⭐"
            ))
        
        # Step 4: 포스트 라이크 (완료되지 않은 경우만 표시)
        if not user_data.get('quest4_complete'):
            options.append(discord.SelectOption(
                label="Step 4: 포스트 라이크",
                description="포스트에 좋아요를 눌러주세요",
                value="quest4",
                emoji="👍"
            ))
        
        # 모든 퀘스트가 완료된 경우
        if not options:
            options.append(discord.SelectOption(
                label="모든 퀘스트 완료! 🎉",
                description="모든 퀘스트를 완료하셨습니다!",
                value="all_complete",
                emoji="🎉"
            ))
        
        self.options = options
    
    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        user_data = self.db.get_user(interaction.user.id)
        if not user_data:
            self.db.create_user(interaction.user.id)
            user_data = self.db.get_user(interaction.user.id)
        
        if selected == "all_complete":
            await interaction.response.send_message(
                "🎉 모든 퀘스트를 완료하셨습니다!",
                ephemeral=True
            )
            return
        
        if selected == "quest1":
            # Step 1: Steam ID 연동
            if user_data.get('quest1_complete'):
                await interaction.response.send_message(
                    "✅ 이미 Step 1이 완료되었습니다!",
                    ephemeral=True
                )
                return
            
            # 가이드 Embed 먼저 표시
            guide_embed = discord.Embed(
                title="📝 Step 1: Steam ID 연동 가이드",
                description="**💡 Tip**: Steam 프로필 URL과 ID는, Steam 프로필을 클릭하면 확인할 수 있습니다.\n\n"
                           "**Steam ID 64 찾는 방법:**\n"
                           "1. Steam 프로필 페이지로 이동\n"
                           "2. 주소창에서 `/profiles/` 뒤의 숫자가 Steam ID 64입니다\n"
                           "3. 또는 커스텀 URL인 경우 `/id/` 뒤의 텍스트를 입력하세요\n\n"
                           "가이드를 확인한 후, 아래 버튼을 클릭하여 Steam ID를 입력하세요.",
                color=discord.Color.blue()
            )
            
            # 가이드와 함께 Modal 열기 버튼이 있는 View 표시
            view = SteamLinkGuideView(self.db, self.view_instance)
            await interaction.response.send_message(embed=guide_embed, view=view, ephemeral=True)
        
        elif selected == "quest2":
            # Step 2: Spot Zero Wishlist
            if user_data.get('quest2_complete'):
                await interaction.response.send_message(
                    "✅ 이미 Step 2가 완료되었습니다! (위시리스트를 취소해도 완료 상태는 유지됩니다)",
                    ephemeral=True
                )
                return
            
            if not user_data.get('steam_id'):
                await interaction.response.send_message(
                    "❌ 먼저 Step 1: Steam ID 연동을 완료해주세요!",
                    ephemeral=True
                )
                return
            
            # 가이드 메시지와 함께 View 표시
            guide_embed = discord.Embed(
                title="📝 Step 2: Spot Zero Wishlist 가이드",
                description="**💡 Tip**: 사용자의 Steam 프로필이 공개로 설정되어 있어야 작동합니다.\n\n"
                           f"**프로필 공개 설정**: [여기를 클릭하여 확인하세요](https://steamcommunity.com/my/edit/settings)\n\n"
                           "**위시리스트 추가 방법:**\n"
                           "1. 아래 버튼을 클릭하여 Spot Zero 스토어 페이지로 이동\n"
                           "2. '위시리스트에 추가' 버튼 클릭\n"
                           "3. 돌아와서 '위시리스트 추가 완료' 버튼 클릭",
                color=discord.Color.blue()
            )
            
            view = WishlistView(self.db, self.view_instance)
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
                    "✅ 이미 Step 3이 완료되었습니다!",
                    ephemeral=True
                )
                return
            
            if not user_data.get('steam_id'):
                await interaction.response.send_message(
                    "❌ 먼저 Step 1: Steam ID 연동을 완료해주세요!",
                    ephemeral=True
                )
                return
            
            # 가이드 메시지와 함께 View 표시
            guide_embed = discord.Embed(
                title="📝 Step 3: Spot Zero Steam page follow 가이드",
                description="**Steam 페이지 팔로우 방법:**\n"
                           "1. 아래 버튼을 클릭하여 Spot Zero 스토어 페이지로 이동\n"
                           "2. 페이지에서 '팔로우' 버튼 클릭\n"
                           "3. 돌아와서 '팔로우 확인 완료' 버튼 클릭",
                color=discord.Color.blue()
            )
            
            view = SteamFollowView(self.db, self.view_instance)
            await interaction.response.send_message(
                embed=guide_embed,
                view=view,
                ephemeral=True
            )
        
        elif selected == "quest4":
            # Step 4: 포스트 라이크
            if user_data.get('quest4_complete'):
                await interaction.response.send_message(
                    "✅ 이미 Step 4가 완료되었습니다!",
                    ephemeral=True
                )
                return
            
            # 가이드 메시지와 함께 View 표시
            guide_embed = discord.Embed(
                title="📝 Step 4: 포스트 라이크 가이드",
                description="**포스트 라이크 방법:**\n"
                           "1. 아래 버튼을 클릭하여 Spot Zero 스토어 페이지로 이동\n"
                           "2. 페이지에서 좋아요 버튼을 클릭\n"
                           "3. 돌아와서 '포스트 확인 완료' 버튼 클릭",
                color=discord.Color.blue()
            )
            
            view = PostLikeView(self.db, self.view_instance)
            await interaction.response.send_message(
                embed=guide_embed,
                view=view,
                ephemeral=True
            )


class WishlistView(View):
    """위시리스트 추가를 위한 View"""
    
    def __init__(self, db: DatabaseManager, quest_view_instance):
        super().__init__(timeout=None)
        self.db = db
        self.quest_view_instance = quest_view_instance
        store_url = f"https://store.steampowered.com/app/{APP_ID}/"
        self.add_item(Button(label='🔗 Spot Zero 스토어 페이지 열기', style=discord.ButtonStyle.link, url=store_url))
    
    @discord.ui.button(label='✅ 위시리스트 추가 완료', style=discord.ButtonStyle.success)
    async def confirm_wishlist(self, interaction: discord.Interaction, button: Button):
        user_data = self.db.get_user(interaction.user.id)
        
        if user_data and user_data.get('quest2_complete'):
            await interaction.response.send_message(
                "✅ 이미 Step 2가 완료되었습니다!",
                ephemeral=True
            )
            return
        
        # Steam ID 확인
        if not user_data or not user_data.get('steam_id'):
            await interaction.response.send_message(
                "❌ 먼저 Step 1: Steam ID 연동을 완료해주세요!",
                ephemeral=True
            )
            return
        
        # 위시리스트 검증 시도
        steam_id = user_data.get('steam_id')
        has_wishlist = await check_wishlist(steam_id, APP_ID)
        
        if not has_wishlist:
            await interaction.response.send_message(
                "❌ 위시리스트에 Spot Zero가 추가되지 않았습니다.\n\n"
                "다음을 확인해주세요:\n"
                "1. Steam 프로필이 공개로 설정되어 있는지 확인\n"
                "2. 위시리스트에 Spot Zero를 추가했는지 확인\n"
                "3. 잠시 후 다시 시도해주세요",
                ephemeral=True
            )
            return
        
        # 검증 성공 - 완료 처리
        self.db.create_user(interaction.user.id)
        self.db.update_quest(interaction.user.id, 2, True)
        
        await interaction.response.send_message(
            "✅ Step 2: Spot Zero Wishlist가 완료되었습니다!",
            ephemeral=True
        )
        
        # Embed 업데이트
        await self.quest_view_instance.update_embed(interaction)


class SteamFollowView(View):
    """Steam 페이지 팔로우를 위한 View"""
    
    def __init__(self, db: DatabaseManager, quest_view_instance):
        super().__init__(timeout=None)
        self.db = db
        self.quest_view_instance = quest_view_instance
        store_url = f"https://store.steampowered.com/app/{APP_ID}/"
        self.add_item(Button(label='🔗 Spot Zero 스토어 페이지 열기', style=discord.ButtonStyle.link, url=store_url))
    
    @discord.ui.button(label='✅ 팔로우 확인 완료', style=discord.ButtonStyle.success)
    async def confirm_follow(self, interaction: discord.Interaction, button: Button):
        user_data = self.db.get_user(interaction.user.id)
        
        if user_data and user_data.get('quest3_complete'):
            await interaction.response.send_message(
                "✅ 이미 Step 3이 완료되었습니다!",
                ephemeral=True
            )
            return
        
        # Steam ID 확인
        if not user_data or not user_data.get('steam_id'):
            await interaction.response.send_message(
                "❌ 먼저 Step 1: Steam ID 연동을 완료해주세요!",
                ephemeral=True
            )
            return
        
        # Steam 페이지 팔로우는 API로 확인할 수 없으므로,
        # 사용자가 페이지를 방문하고 확인 버튼을 누른 것으로 간주
        self.db.create_user(interaction.user.id)
        self.db.update_quest(interaction.user.id, 3, True)
        
        await interaction.response.send_message(
            "✅ Step 3: Spot Zero Steam page follow가 완료되었습니다!",
            ephemeral=True
        )
        
        # Embed 업데이트
        await self.quest_view_instance.update_embed(interaction)


class PostLikeView(View):
    """포스트 라이크를 위한 View"""
    
    def __init__(self, db: DatabaseManager, quest_view_instance):
        super().__init__(timeout=None)
        self.db = db
        self.quest_view_instance = quest_view_instance
        store_url = f"https://store.steampowered.com/app/{APP_ID}/Spot_Zero/"
        self.add_item(Button(label='🔗 포스트 페이지 열기', style=discord.ButtonStyle.link, url=store_url))
    
    @discord.ui.button(label='✅ 포스트 확인 완료', style=discord.ButtonStyle.success)
    async def confirm_post_like(self, interaction: discord.Interaction, button: Button):
        user_data = self.db.get_user(interaction.user.id)
        
        if user_data and user_data.get('quest4_complete'):
            await interaction.response.send_message(
                "✅ 이미 Step 4가 완료되었습니다!",
                ephemeral=True
            )
            return
        
        # Steam ID 확인 (최소한의 검증)
        if not user_data or not user_data.get('steam_id'):
            await interaction.response.send_message(
                "❌ 먼저 Step 1: Steam ID 연동을 완료해주세요!",
                ephemeral=True
            )
            return
        
        # Steam 커뮤니티 포스트 좋아요는 API로 확인할 수 없으므로,
        # 사용자가 페이지를 방문하고 확인 버튼을 누른 것으로 간주
        self.db.create_user(interaction.user.id)
        self.db.update_quest(interaction.user.id, 4, True)
        
        await interaction.response.send_message(
            "✅ Step 4: 포스트 라이크가 완료되었습니다!",
            ephemeral=True
        )
        
        # Embed 업데이트
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
        user_data = self.db.get_user(interaction.user.id)
        if not user_data:
            self.db.create_user(interaction.user.id)
            user_data = self.db.get_user(interaction.user.id)
        
        # 실시간 위시리스트 수 가져오기
        current_wishlist = await get_wishlist_count_from_store(APP_ID)
        if current_wishlist is None:
            # 실시간 가져오기 실패 시 기본값 사용
            current_wishlist = self.db.get_total_wishlist_count()
        
        progress_text, achieved = create_progress_bar(current_wishlist, MILESTONES)
        
        # 퀘스트 상태
        quest1_status = "✅ Complete" if user_data.get('quest1_complete') else "❌ Incomplete"
        quest2_status = "✅ Complete" if user_data.get('quest2_complete') else "❌ Incomplete"
        quest3_status = "✅ Complete" if user_data.get('quest3_complete') else "❌ Incomplete"
        
        embed = discord.Embed(
            title="🎮 Welcome to Spot Zero Hunter Program",
            description=f"**📊 Wishlist Milestone**\n\n{progress_text}",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="Step 1: Steam ID 연동",
            value=quest1_status,
            inline=False
        )
        
        embed.add_field(
            name="Step 2: Spot Zero Wishlist",
            value=quest2_status,
            inline=False
        )
        
        embed.add_field(
            name="Step 3: 포스트 라이크",
            value=quest3_status,
            inline=False
        )
        
        # View 재생성 (상태 반영)
        view = QuestView(self.db, user_data)
        
        try:
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except:
            # followup이 실패하면 edit 시도
            try:
                await interaction.edit_original_response(embed=embed, view=view)
            except:
                pass


@tree.command(name='steam', description='Spot Zero Hunter Program 시작하기')
async def steam_command(interaction: discord.Interaction):
    """Steam 명령어 - Welcome Embed 표시"""
    db = DatabaseManager()
    
    # 사용자 데이터 조회
    user_data = db.get_user(interaction.user.id)
    if not user_data:
        db.create_user(interaction.user.id)
        user_data = db.get_user(interaction.user.id)
    
    # 실시간 위시리스트 수 가져오기
    current_wishlist = await get_wishlist_count_from_store(APP_ID)
    if current_wishlist is None:
        # 실시간 가져오기 실패 시 기본값 사용
        current_wishlist = db.get_total_wishlist_count()
    
    progress_text, achieved = create_progress_bar(current_wishlist, MILESTONES)
    
    # 퀘스트 상태
    quest1_status = "✅ Complete" if user_data.get('quest1_complete') else "❌ Incomplete"
    quest2_status = "✅ Complete" if user_data.get('quest2_complete') else "❌ Incomplete"
    quest3_status = "✅ Complete" if user_data.get('quest3_complete') else "❌ Incomplete"
    quest4_status = "✅ Complete" if user_data.get('quest4_complete') else "❌ Incomplete"
    
    embed = discord.Embed(
        title="🎮 Welcome to Spot Zero Hunter Program",
        description=f"**📊 Wishlist Milestone**\n\n{progress_text}",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="Step 1: Steam ID 연동",
        value=quest1_status,
        inline=False
    )
    
    embed.add_field(
        name="Step 2: Spot Zero Wishlist",
        value=quest2_status,
        inline=False
    )
    
    embed.add_field(
        name="Step 3: Spot Zero Steam page follow",
        value=quest3_status,
        inline=False
    )
    
    embed.add_field(
        name="Step 4: 포스트 라이크",
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

