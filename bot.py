import discord
from discord.ext import commands
import json
import os
from datetime import datetime

# ─────────────────────────────────────────
#  CONFIGURAÇÕES — edite aqui
# ─────────────────────────────────────────
TOKEN = "SEU_TOKEN_AQUI"

# ID do canal onde as divulgações serão postadas
CANAL_DIVULGACAO_ID = 000000000000000000  # substitua pelo ID real

# Cargo que será atribuído ao streamer
CARGO_STREAMANDO_NOME = "STREAMANDO AGORA"

# Mapeamento: ID da role do usuário → mention que será usada no embed
# Ex: se o usuário tem a role "Twitch Streamer", menciona @TwitchStreamer
ROLES_PLATAFORMA = {
    "Twitch Streamer":   "twitch",
    "YouTuber":          "youtube",
    # adicione mais se precisar
}

# IDs das roles para menção no canal (opcional — deixe vazio para não mencionar)
# Formato: "nome_da_role": ID_da_role
MENTION_ROLES = {
    "twitch":  000000000000000000,  # ID da role que será mencionada nas lives Twitch
    "youtube": 000000000000000000,  # ID da role que será mencionada nos vídeos/lives YouTube
}

# ─────────────────────────────────────────
#  PERSISTÊNCIA (evita re-postar mesma live)
# ─────────────────────────────────────────
LIVES_ATIVAS_FILE = "lives_ativas.json"

def carregar_lives():
    if os.path.exists(LIVES_ATIVAS_FILE):
        with open(LIVES_ATIVAS_FILE, "r") as f:
            return json.load(f)
    return {}

def salvar_lives(data):
    with open(LIVES_ATIVAS_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ─────────────────────────────────────────
#  SETUP DO BOT
# ─────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents)
lives_ativas = carregar_lives()

# ─────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────
def detectar_plataforma(activity: discord.Activity):
    """Retorna ('twitch', dados) ou ('youtube', dados) ou (None, None)"""
    if isinstance(activity, discord.Streaming):
        url = (activity.url or "").lower()
        if "twitch.tv" in url:
            return "twitch", {
                "titulo": activity.name or "Live sem título",
                "url":    activity.url,
                "jogo":   activity.game or "Nenhum",
                "thumb":  activity.assets.get("large_image") if activity.assets else None,
            }
        if "youtube.com" in url or "youtu.be" in url:
            return "youtube_live", {
                "titulo": activity.name or "Live sem título",
                "url":    activity.url,
                "thumb":  None,
            }

    if isinstance(activity, discord.Activity):
        name = (activity.name or "").lower()
        # YouTube Music / vídeo via Rich Presence
        if "youtube" in name and activity.type == discord.ActivityType.watching:
            state = activity.state or ""
            url_asset = activity.url if hasattr(activity, "url") else ""
            return "youtube_video", {
                "titulo": activity.details or activity.name or "Vídeo sem título",
                "canal":  state,
                "url":    url_asset or "https://youtube.com",
                "thumb":  None,
            }

    return None, None


def build_embed_twitch(membro, dados, mention_str):
    embed = discord.Embed(
        title=f"🔴 {membro.display_name} está ao vivo na Twitch!",
        description=f"**{dados['titulo']}**",
        url=dados["url"],
        color=0x9146FF,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="🎮 Jogo", value=dados["jogo"], inline=True)
    embed.add_field(name="📺 Assistir", value=f"[Clique aqui]({dados['url']})", inline=True)
    embed.set_author(name=membro.display_name, icon_url=membro.display_avatar.url)
    embed.set_footer(text="Twitch Live")
    if mention_str:
        embed.description += f"\n\n{mention_str}"
    return embed


def build_embed_youtube_live(membro, dados, mention_str):
    embed = discord.Embed(
        title=f"🔴 {membro.display_name} está ao vivo no YouTube!",
        description=f"**{dados['titulo']}**",
        url=dados["url"],
        color=0xFF0000,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="📺 Assistir", value=f"[Clique aqui]({dados['url']})", inline=True)
    embed.set_author(name=membro.display_name, icon_url=membro.display_avatar.url)
    embed.set_footer(text="YouTube Live")
    if mention_str:
        embed.description += f"\n\n{mention_str}"
    return embed


def build_embed_youtube_video(membro, dados, mention_str):
    embed = discord.Embed(
        title=f"🎬 {membro.display_name} postou um vídeo no YouTube!",
        description=f"**{dados['titulo']}**",
        url=dados["url"],
        color=0xFF0000,
        timestamp=datetime.utcnow()
    )
    if dados.get("canal"):
        embed.add_field(name="📢 Canal", value=dados["canal"], inline=True)
    embed.add_field(name="▶️ Assistir", value=f"[Clique aqui]({dados['url']})", inline=True)
    embed.set_author(name=membro.display_name, icon_url=membro.display_avatar.url)
    embed.set_footer(text="YouTube Vídeo")
    if mention_str:
        embed.description += f"\n\n{mention_str}"
    return embed


def get_mention_str(guild, membro, plataforma):
    """Descobre qual role de plataforma o usuário tem e retorna a mention."""
    plataforma_map = {
        "twitch":        "twitch",
        "youtube_live":  "youtube",
        "youtube_video": "youtube",
    }
    chave = plataforma_map.get(plataforma)
    if not chave:
        return ""

    role_id = MENTION_ROLES.get(chave)
    if not role_id:
        return ""

    role = guild.get_role(role_id)
    return role.mention if role else ""


def gerar_chave(membro_id, plataforma, dados):
    """Chave única por live/vídeo para evitar spam."""
    url = dados.get("url", "") or dados.get("titulo", "")
    return f"{membro_id}:{plataforma}:{url}"

# ─────────────────────────────────────────
#  EVENTOS
# ─────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Bot online como {bot.user} ({bot.user.id})")
    print(f"   Servidores: {[g.name for g in bot.guilds]}")


@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    guild = after.guild
    canal = guild.get_channel(CANAL_DIVULGACAO_ID)
    if not canal:
        return

    # ── Cargo "STREAMANDO AGORA" ──────────────────────────────────────
    cargo_stream = discord.utils.get(guild.roles, name=CARGO_STREAMANDO_NOME)

    atividades_antes = {type(a) for a in before.activities}
    atividades_depois = {type(a) for a in after.activities}

    estava_streamando = any(isinstance(a, discord.Streaming) for a in before.activities)
    esta_streamando   = any(isinstance(a, discord.Streaming) for a in after.activities)

    if cargo_stream:
        if esta_streamando and not estava_streamando:
            await after.add_roles(cargo_stream, reason="Entrou em live")
        elif estava_streamando and not esta_streamando:
            await after.remove_roles(cargo_stream, reason="Saiu da live")

    # ── Detectar nova atividade relevante ────────────────────────────
    atividades_novas = [a for a in after.activities if a not in before.activities]

    for atividade in atividades_novas:
        plataforma, dados = detectar_plataforma(atividade)
        if not plataforma or not dados:
            continue

        chave = gerar_chave(after.id, plataforma, dados)

        # Já postou essa live/vídeo? Pula.
        if chave in lives_ativas:
            continue

        lives_ativas[chave] = True
        salvar_lives(lives_ativas)

        mention_str = get_mention_str(guild, after, plataforma)

        if plataforma == "twitch":
            embed = build_embed_twitch(after, dados, mention_str)
        elif plataforma == "youtube_live":
            embed = build_embed_youtube_live(after, dados, mention_str)
        elif plataforma == "youtube_video":
            embed = build_embed_youtube_video(after, dados, mention_str)
        else:
            continue

        await canal.send(embed=embed)


# ── Limpar chave ao sair da live (permite re-post se ID mudar) ────────
    if estava_streamando and not esta_streamando:
        chaves_remover = [k for k in lives_ativas if k.startswith(f"{after.id}:")]
        for k in chaves_remover:
            del lives_ativas[k]
        salvar_lives(lives_ativas)


# ─────────────────────────────────────────
#  RODAR
# ─────────────────────────────────────────
bot.run(TOKEN)
