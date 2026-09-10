"""Discord bot インスタンスの定義。

bot.py（エントリポイント）とツール群の両方から参照される共有インスタンス。

`python src/lilla_core/bot.py` のようにスクリプトとして実行された場合、bot.py は
`sys.modules["__main__"]` として登録され `"lilla_core.bot"` という名前では登録されない。
その状態で他モジュールが `from lilla_core.bot import bot` を行うと bot.py が
「別モジュール」として二重にロードされ、Discord へ接続していない
幽霊インスタンスを掴んでしまう。Client 生成のみをこのモジュールへ
切り出すことで、どの経路から import しても同一インスタンスを共有する。
"""
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
