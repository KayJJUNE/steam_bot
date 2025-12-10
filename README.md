# Spot Zero Discord Bot

Discord Bot for Spot Zero game community. Handles user onboarding, Steam account linking, and quest tracking.

## Features

- `/steam` command to start the Hunter Program
- Quest 1: Steam Account Linking via Modal
- Quest 2: Wishlist Verification
- Quest 3: Community Like & Comment tracking
- Progress bar for Wishlist Milestone
- SQLite database for user data storage

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create a `.env` file based on `.env.example`:
```bash
cp .env.example .env
```

3. Fill in your credentials in `.env`:
   - `DISCORD_TOKEN`: Your Discord Bot Token
   - `STEAM_API_KEY`: Your Steam Web API Key
   - `APP_ID`: Your game's Steam App ID
   - `COMMUNITY_POST_URL`: URL to your Steam Community post

4. Run the bot:
```bash
python main.py
```

## Database

The bot uses SQLite database (`user_data.db`) to store:
- Discord User ID
- Steam ID
- Quest completion status

## Commands

- `/steam` - Start the Spot Zero Hunter Program and view your progress

