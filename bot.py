import discord
from discord.ext import commands
import json
import os
from datetime import datetime

# ─────────────────────────────────────────
#  CONFIGURAÇÕES
# ─────────────────────────────────────────
TOKEN = os.environ.get("TOKEN")

CANAL_DIVULGACAO_ID = 1468613615987851275

CARGO_STREAMANDO_NOME = "STREAMANDO AGORA"

MENTION_ROLES = {
    "twitch":        1478843698614898688,
    "youtube_live":  1478843587914498118,
    "youtube_video": 1478843428937666580,
}

# ─────────────────────────────────────────
#  PERSISTÊNCIA
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
#  DETECTAR PLATAFORMA
# ─────────────────────────────────────────
def detectar_plataforma(activity):
    if isinstance(activity, discord.Streaming):
        url = (activity.url or "").lower()
        if "twitch.tv" in url:
            return "twitch", {
                "titulo": activity.name or "Live sem título",
                "url":    activity.url,
                "jogo":   activity.game or "Nenhuma categoria",
                "thumb":  None,
            }
        if "youtube.com" in url or "youtu.be" in url:
            return "youtube_live", {
                "titulo": activity.name or "Live sem título",
                "url":    activity.url,
                "jogo":   activity.game or "Nenhuma categoria",
                "thumb":  None,
            }

    if isinstance(activity, discord.Activity):
        name = (activity.name or "").lower()
        if "youtube" in name and activity.type == discord.ActivityType.watching:
            url = activity.url if hasattr(activity, "url") and activity.url else "https://youtube.com"
            return "youtube_video", {
                "titulo": activity.details or activity.name or "Vídeo sem título",
                "canal":  activity.state or "",
                "url":    url,
                "thumb":  None,
            }

    return None, None

# ─────────────────────────────────────────
#  EMBEDS
# ─────────────────────────────────────────
def build_embed_twitch(membro, dados, mention):
    embed = discord.Embed(
        color=0x9146FF,  # roxo Twitch
        timestamp=datetime.utcnow()
    )
    embed.description = (
        f"🟣 **TEM LIVE ACONTECENDO NA TWITCH:**\n\n"
        f"**{membro.display_name}** está ao vivo na roxinha 🟪🟪🟪\n"
        f"Bora lá assistir esse conteúdo, se inscrever e apoiar o pessoal da nossa comunidade. 💜💜💜💜💜\n\n"
        f"**🎮 {dados['titulo']}**\n"
        f"📂 {dados['jogo']}\n\n"
        f"[▶️ Clique aqui para assistir a live!]({dados['url']})\n\n"
        f"{mention}"
    )
    embed.set_author(name=membro.display_name, icon_url=membro.display_avatar.url)
    embed.set_footer(text="Twitch Live")
    return embed


def build_embed_youtube_live(membro, dados, mention):
    embed = discord.Embed(
        color=0xFF0000,  # vermelho vivo YouTube
        timestamp=datetime.utcnow()
    )
    embed.description = (
        f"🔴 **LIVE NO YOUTUBE AGORA. CORRE LÁ PRA VER:**\n\n"
        f"**{membro.display_name}** está ao vivo e operante. 🟥🟥🟥\n"
        f"Bora lá assistir esse conteúdo, se inscrever e apoiar o pessoal da nossa comunidade. ❤️❤️❤️❤️❤️\n\n"
        f"**🎬 {dados['titulo']}**\n"
        f"📂 {dados['jogo']}\n\n"
        f"[▶️ Clique aqui para assistir a live!]({dados['url']})\n\n"
        f"{mention}"
    )
    embed.set_author(name=membro.display_name, icon_url=membro.display_avatar.url)
    embed.set_footer(text="YouTube Live")
    return embed


def build_embed_youtube_video(membro, dados, mention):
    embed = discord.Embed(
        color=0x3B82F6,  # azul 🩵
        timestamp=datetime.utcnow()
    )
    embed.description = (
        f"🔵 **ACABOU DE SAIR VÍDEO NOVINHO EM FOLHA:**\n\n"
        f"**{membro.display_name}** postou um vídeo novo agora em seu canal. 🟦🟦🟦\n"
        f"Assista o vídeo, curta, comente, se inscreva (se não for inscrito) e apoie um criador de conteúdo da nossa comunidade. 🩵🩵🩵🩵🩵\n\n"
        f"**🎥 {dados['titulo']}**\n\n"
        f"[▶️ Clique aqui para assistir!]({dados['url']})\n\n"
        f"{mention}"
    )
    embed.set_author(name=membro.display_name, icon_url=membro.display_avatar.url)
    embed.set_footer(text="YouTube Vídeo")
    return embed

# ─────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────
def get_mention(guild, plataforma):
    role_id = MENTION_ROLES.get(plataforma)
    if not role_id:
        return ""
    role = guild.get_role(role_id)
    return role.mention if role else ""

def gerar_chave(membro_id, plataforma, dados):
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

    # ── Cargo STREAMANDO AGORA ────────────────────────────────────────
    cargo_stream = discord.utils.get(guild.roles, name=CARGO_STREAMANDO_NOME)
    estava_streamando = any(isinstance(a, discord.Streaming) for a in before.activities)
    esta_streamando   = any(isinstance(a, discord.Streaming) for a in after.activities)

    if cargo_stream:
        if esta_streamando and not estava_streamando:
            await after.add_roles(cargo_stream, reason="Entrou em live")
        elif estava_streamando and not esta_streamando:
            await after.remove_roles(cargo_stream, reason="Saiu da live")

    # ── Detectar novas atividades ─────────────────────────────────────
    atividades_novas = [a for a in after.activities if a not in before.activities]

    for atividade in atividades_novas:
        plataforma, dados = detectar_plataforma(atividade)
        if not plataforma or not dados:
            continue

        chave = gerar_chave(after.id, plataforma, dados)
        if chave in lives_ativas:
            continue

        lives_ativas[chave] = True
        salvar_lives(lives_ativas)

        mention = get_mention(guild, plataforma)

        if plataforma == "twitch":
            embed = build_embed_twitch(after, dados, mention)
        elif plataforma == "youtube_live":
            embed = build_embed_youtube_live(after, dados, mention)
        elif plataforma == "youtube_video":
            embed = build_embed_youtube_video(after, dados, mention)
        else:
            continue

        await canal.send(embed=embed)

    # ── Limpar chaves ao encerrar live ────────────────────────────────
    if estava_streamando and not esta_streamando:
        chaves_remover = [k for k in lives_ativas if k.startswith(f"{after.id}:")]
        for k in chaves_remover:
            del lives_ativas[k]
        salvar_lives(lives_ativas)


# ─────────────────────────────────────────
#  RODAR
# ─────────────────────────────────────────
bot.run(TOKEN)
