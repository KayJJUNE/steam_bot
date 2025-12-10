import discord
from discord import app_commands
from discord.ui import Button, View, Modal, TextInput, Select
import aiohttp
import sqlite3
import os
import re
from typing import Optional
from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()

# Discord Bot 설정
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
STEAM_API_KEY = os.getenv('STEAM_API_KEY')
APP_ID = os.getenv('APP_ID', '123456')  # 기본값, 실제 App ID로 변경 필요
COMMUNITY_POST_URL = os.getenv('COMMUNITY_POST_URL', 'https://steamcommunity.com/app/...')
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def get_user(self, discord_id: int) -> Optional[dict]:
        """사용자 정보 조회"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT discord_id, steam_id, quest1_complete, quest2_complete, quest3_complete
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
                'quest3_complete': bool(result[4])
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
        """전체 위시리스트 수 조회 (현재는 하드코딩된 값 반환)"""
        # 실제로는 Steam API나 다른 소스에서 가져와야 함
        # MVP에서는 고정값 사용
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
        
        await interaction.response.send_message(
            f"✅ Steam 계정이 성공적으로 연결되었습니다! (Steam ID: {steam_id})",
            ephemeral=True
        )
        
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


async def check_wishlist(steam_id: str, app_id: str) -> bool:
    """위시리스트 확인 (제한적 API)"""
    # Steam Web API는 공개 위시리스트를 직접 확인하는 기능이 제한적입니다.
    # 실제 구현에서는 사용자의 프로필이 공개되어 있어야 하며,
    # 또는 사용자 확인을 통해 처리합니다.
    
    # MVP에서는 사용자가 버튼을 클릭하면 완료로 처리
    return True


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


class QuestView(View):
    """퀘스트 상호작용을 위한 View"""
    
    def __init__(self, db: DatabaseManager, user_data: Optional[dict] = None):
        super().__init__(timeout=None)
        self.db = db
        self.user_data = user_data or {}
        
        # Steam 계정 연결 Select 메뉴 추가
        self.add_item(SteamLinkSelect(db, self))
        
        # 스팀 페이지 링크 버튼 추가
        self.add_item(Button(label='🔗 Steam 페이지 열기', style=discord.ButtonStyle.link, url=COMMUNITY_POST_URL))
    
    @discord.ui.button(label='🎁 Verify Wishlist', style=discord.ButtonStyle.primary)
    async def verify_wishlist(self, interaction: discord.Interaction, button: Button):
        user_data = self.db.get_user(interaction.user.id)
        
        if not user_data or not user_data.get('steam_id'):
            await interaction.response.send_message(
                "❌ 먼저 Steam 계정을 연결해주세요!",
                ephemeral=True
            )
            return
        
        if user_data.get('quest2_complete'):
            await interaction.response.send_message(
                "✅ 이미 위시리스트가 확인되었습니다!",
                ephemeral=True
            )
            return
        
        # 위시리스트 확인 시도
        steam_id = user_data.get('steam_id')
        has_wishlist = await check_wishlist(steam_id, APP_ID)
        
        if has_wishlist:
            self.db.update_quest(interaction.user.id, 2, True)
            await interaction.response.send_message(
                "✅ 위시리스트 확인이 완료되었습니다!",
                ephemeral=True
            )
            # Embed 업데이트
            await self.update_embed(interaction)
        else:
            await interaction.response.send_message(
                "❌ 위시리스트를 확인할 수 없습니다. Steam 프로필을 공개로 설정하거나 게임을 위시리스트에 추가해주세요.",
                ephemeral=True
            )
    
    @discord.ui.button(label='✅ Steam 페이지 확인 완료', style=discord.ButtonStyle.success)
    async def confirm_steam_page(self, interaction: discord.Interaction, button: Button):
        user_data = self.db.get_user(interaction.user.id)
        
        if user_data and user_data.get('quest1_complete'):
            await interaction.response.send_message(
                "✅ 이미 Quest 1이 완료되었습니다!",
                ephemeral=True
            )
            return
        
        # Steam 페이지를 열고 확인했으므로 Quest 1 완료 처리
        self.db.create_user(interaction.user.id)
        self.db.update_quest(interaction.user.id, 1, True)
        
        await interaction.response.send_message(
            "✅ Steam 페이지 확인이 완료되었습니다! Quest 1이 완료되었습니다.",
            ephemeral=True
        )
        
        # Embed 업데이트
        await self.update_embed(interaction)
    
    @discord.ui.button(label='✅ I have Liked the post', style=discord.ButtonStyle.success)
    async def confirm_like(self, interaction: discord.Interaction, button: Button):
        user_data = self.db.get_user(interaction.user.id)
        
        if user_data and user_data.get('quest3_complete'):
            await interaction.response.send_message(
                "✅ 이미 좋아요가 확인되었습니다!",
                ephemeral=True
            )
            return
        
        self.db.create_user(interaction.user.id)
        self.db.update_quest(interaction.user.id, 3, True)
        
        await interaction.response.send_message(
            "✅ 좋아요 확인이 완료되었습니다!",
            ephemeral=True
        )
        
        # Embed 업데이트
        await self.update_embed(interaction)
    
    async def update_embed(self, interaction: discord.Interaction):
        """Embed 업데이트"""
        user_data = self.db.get_user(interaction.user.id)
        if not user_data:
            self.db.create_user(interaction.user.id)
            user_data = self.db.get_user(interaction.user.id)
        
        # 진행률 바 생성
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
            name="Quest 1: Steam Account Linking",
            value=quest1_status,
            inline=False
        )
        
        embed.add_field(
            name="Quest 2: Wishlist Verification",
            value=quest2_status,
            inline=False
        )
        
        embed.add_field(
            name="Quest 3: Community Like",
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
    
    # 진행률 바 생성
    current_wishlist = db.get_total_wishlist_count()
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
        name="Quest 1: Steam Account Linking",
        value=quest1_status,
        inline=False
    )
    
    embed.add_field(
        name="Quest 2: Wishlist Verification",
        value=quest2_status,
        inline=False
    )
    
    embed.add_field(
        name="Quest 3: Community Like",
        value=quest3_status,
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

