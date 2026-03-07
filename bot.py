import discord
from discord.ext import commands, tasks
import json
import os
import aiohttp
import xml.etree.ElementTree as ET
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ─────────────────────────────────────────
#  CONFIGURAÇÕES
# ─────────────────────────────────────────
TOKEN             = os.environ.get("TOKEN")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON")
SPREADSHEET_ID    = os.environ.get("SPREADSHEET_ID")
SHEET_RANGE       = "YouTube!A2:C"  # Alias | ID no Discord | ID do Canal

CANAL_DIVULGACAO_ID   = 1468613615987851275
CANAL_CONFIG_ROLES_ID = 1479645122428932198
CARGO_STREAMANDO_NOME = "STREAMANDO AGORA"
CANAL_CRIAR_CALL_ID   = int(os.environ.get("CANAL_CRIAR_CALL_ID", 0))

MENTION_ROLES = {
    "twitch":        1478843698614898688,
    "youtube_live":  1478843587914498118,
    "youtube_video": 1478843428937666580,
}

# ─────────────────────────────────────────
#  PERSISTÊNCIA
# ─────────────────────────────────────────
LIVES_ATIVAS_FILE  = "lives_ativas.json"
VIDEOS_VISTOS_FILE = "videos_vistos.json"

def carregar_json(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}

def salvar_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

lives_ativas      = carregar_json(LIVES_ATIVAS_FILE)
videos_vistos     = carregar_json(VIDEOS_VISTOS_FILE)
canais_temporarios = {}
primeira_checagem = True

# ─────────────────────────────────────────
#  REACTION ROLES
# ─────────────────────────────────────────
def carregar_reaction_roles():
    if not os.path.exists("reaction_roles.json"):
        return {}
    with open("reaction_roles.json", "r") as f:
        data = json.load(f)
    mapping = {}
    for msg in data.get("mensagens", []):
        msg_id = msg.get("message_id")
        if not msg_id:
            continue
        mapping[int(msg_id)] = {
            r["emoji_id"]: r["role_id"] for r in msg.get("reactions", [])
        }
    return mapping

reaction_roles_map = carregar_reaction_roles()

# ─────────────────────────────────────────
#  GOOGLE SHEETS
# ─────────────────────────────────────────
def get_canais_youtube():
    try:
        creds_info = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
        )
        service = build("sheets", "v4", credentials=creds)
        result  = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=SHEET_RANGE
        ).execute()
        rows   = result.get("values", [])
        canais = []
        for row in rows:
            if len(row) >= 3:
                canais.append({
                    "nome":            row[0].strip(),
                    "discord_user_id": row[1].strip(),
                    "channel_id":      row[2].strip(),
                })
        return canais
    except Exception as e:
        print(f"❌ Erro ao ler planilha: {e}")
        return []

# ─────────────────────────────────────────
#  RSS YOUTUBE
# ─────────────────────────────────────────
async def buscar_ultimo_video(session, channel_id):
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            text = await resp.text()
            root = ET.fromstring(text)
            ns = {
                "atom":  "http://www.w3.org/2005/Atom",
                "media": "http://search.yahoo.com/mrss/",
                "yt":    "http://www.youtube.com/xml/schemas/2015",
            }
            entry = root.find("atom:entry", ns)
            if entry is None:
                return None

            video_id  = entry.find("yt:videoId", ns)
            titulo    = entry.find("atom:title", ns)
            link      = entry.find("atom:link", ns)
            thumb_url = f"https://img.youtube.com/vi/{video_id.text}/maxresdefault.jpg" if video_id is not None else None

            return {
                "id":    video_id.text if video_id is not None else None,
                "titulo": titulo.text if titulo is not None else "Vídeo sem título",
                "url":   link.attrib.get("href", "") if link is not None else "",
                "thumb": thumb_url,
            }
    except Exception as e:
        print(f"❌ Erro RSS canal {channel_id}: {e}")
        return None

# ─────────────────────────────────────────
#  SETUP DO BOT
# ─────────────────────────────────────────
intents = discord.Intents.default()
intents.members        = True
intents.presences      = True
intents.message_content = True  # necessário para comandos com prefixo

bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────────────────────────
#  DETECTAR PLATAFORMA (só lives)
# ─────────────────────────────────────────
def detectar_plataforma(activity):
    if not isinstance(activity, discord.Streaming):
        return None, None
    url = (activity.url or "").lower()
    if "twitch.tv" in url:
        return "twitch", {
            "titulo": activity.name or "Live sem título",
            "url":    activity.url,
            "jogo":   activity.game or "Nenhuma categoria",
        }
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube_live", {
            "titulo": activity.name or "Live sem título",
            "url":    activity.url,
            "jogo":   activity.game or "Nenhuma categoria",
        }
    return None, None

# ─────────────────────────────────────────
#  EMBEDS
# ─────────────────────────────────────────
def build_embed_twitch(membro, dados, mention):
    embed = discord.Embed(color=0x9146FF, timestamp=datetime.utcnow())
    embed.description = (
        f"🟣 **TEM LIVE ACONTECENDO NA TWITCH:**\n\n"
        f"**{membro.display_name}** está ao vivo na roxinha 🟪🟪🟪\n"
        f"Bora lá assistir esse conteúdo, se inscrever e apoiar o pessoal da nossa comunidade. 💜💜💜💜💜\n\n"
        f"**🎮 [{dados['titulo']}]({dados['url']})**\n"
        f"📂 {dados['jogo']}\n\n"
        f"{mention}"
    )
    embed.set_author(name=membro.display_name, icon_url=membro.display_avatar.url)
    return embed


def build_embed_youtube_live(membro, dados, mention):
    embed = discord.Embed(color=0xFF0000, timestamp=datetime.utcnow())
    embed.description = (
        f"🔴 **LIVE NO YOUTUBE AGORA. CORRE LÁ PRA VER:**\n\n"
        f"**{membro.display_name}** está ao vivo e operante. 🟥🟥🟥\n"
        f"Bora lá assistir esse conteúdo, se inscrever e apoiar o pessoal da nossa comunidade. ❤️❤️❤️❤️❤️\n\n"
        f"**🎬 [{dados['titulo']}]({dados['url']})**\n"
        f"📂 {dados['jogo']}\n\n"
        f"{mention}"
    )
    embed.set_author(name=membro.display_name, icon_url=membro.display_avatar.url)
    return embed


def build_embed_youtube_video(nome, dados, mention, avatar_url=None):
    embed = discord.Embed(color=0x3B82F6, timestamp=datetime.utcnow())
    embed.description = (
        f"🔵 **ACABOU DE SAIR VÍDEO NOVINHO EM FOLHA:**\n\n"
        f"**{nome}** postou um vídeo novo agora em seu canal. 🟦🟦🟦\n"
        f"Assista o vídeo, curta, comente, se inscreva (se não for inscrito) e apoie um criador de conteúdo da nossa comunidade. 🩵🩵🩵🩵🩵\n\n"
        f"**🎥 [{dados['titulo']}]({dados['url']})**\n\n"
        f"{mention}"
    )
    if avatar_url:
        embed.set_author(name=nome, icon_url=avatar_url)
    else:
        embed.set_author(name=nome)
    if dados.get("thumb"):
        embed.set_image(url=dados["thumb"])
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

def gerar_chave_live(membro_id, plataforma, dados):
    url = dados.get("url", "") or dados.get("titulo", "")
    return f"{membro_id}:{plataforma}:{url}"

# ─────────────────────────────────────────
#  TASK: CHECAR VÍDEOS NOVOS (a cada 5 min)
# ─────────────────────────────────────────
@tasks.loop(minutes=5)
async def checar_videos():
    global primeira_checagem
    await bot.wait_until_ready()

    guild  = bot.guilds[0] if bot.guilds else None
    if not guild:
        return

    canal   = guild.get_channel(CANAL_DIVULGACAO_ID)
    mention = get_mention(guild, "youtube_video")
    canais  = get_canais_youtube()

    async with aiohttp.ClientSession() as session:
        for entrada in canais:
            channel_id  = entrada["channel_id"]
            nome        = entrada["nome"]
            discord_uid = entrada["discord_user_id"]

            video = await buscar_ultimo_video(session, channel_id)
            if not video or not video["id"]:
                continue

            if videos_vistos.get(channel_id) == video["id"]:
                continue

            # Primeira checagem: só registra, não posta
            if primeira_checagem:
                videos_vistos[channel_id] = video["id"]
                salvar_json(VIDEOS_VISTOS_FILE, videos_vistos)
                continue

            videos_vistos[channel_id] = video["id"]
            salvar_json(VIDEOS_VISTOS_FILE, videos_vistos)

            if not canal:
                continue

            avatar_url = None
            try:
                membro = guild.get_member(int(discord_uid))
                if membro:
                    avatar_url = membro.display_avatar.url
                    nome = membro.display_name
            except Exception:
                pass

            embed = build_embed_youtube_video(nome, video, mention, avatar_url)
            await canal.send(embed=embed)
            print(f"📹 Vídeo novo postado: {nome} — {video['titulo']}")

    primeira_checagem = False

# ─────────────────────────────────────────
#  EVENTOS
# ─────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ Bot online como {bot.user} ({bot.user.id})")
    print(f"   Servidores: {[g.name for g in bot.guilds]}")
    checar_videos.start()


@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    guild = after.guild
    canal = guild.get_channel(CANAL_DIVULGACAO_ID)
    if not canal:
        return

    cargo_stream      = discord.utils.get(guild.roles, name=CARGO_STREAMANDO_NOME)
    estava_streamando = any(isinstance(a, discord.Streaming) for a in before.activities)
    esta_streamando   = any(isinstance(a, discord.Streaming) for a in after.activities)

    if cargo_stream:
        if esta_streamando and not estava_streamando:
            await after.add_roles(cargo_stream, reason="Entrou em live")
        elif estava_streamando and not esta_streamando:
            await after.remove_roles(cargo_stream, reason="Saiu da live")

    atividades_novas = [a for a in after.activities if a not in before.activities]

    for atividade in atividades_novas:
        plataforma, dados = detectar_plataforma(atividade)
        if not plataforma or not dados:
            continue

        chave = gerar_chave_live(after.id, plataforma, dados)
        if chave in lives_ativas:
            continue

        lives_ativas[chave] = True
        salvar_json(LIVES_ATIVAS_FILE, lives_ativas)

        mention = get_mention(guild, plataforma)

        if plataforma == "twitch":
            embed = build_embed_twitch(after, dados, mention)
        elif plataforma == "youtube_live":
            embed = build_embed_youtube_live(after, dados, mention)
        else:
            continue

        await canal.send(embed=embed)

    if estava_streamando and not esta_streamando:
        chaves_remover = [k for k in lives_ativas if k.startswith(f"{after.id}:")]
        for k in chaves_remover:
            del lives_ativas[k]
        salvar_json(LIVES_ATIVAS_FILE, lives_ativas)


@bot.event
async def on_voice_state_update(member, before, after):
    guild = member.guild

    # Usuário entrou no canal "➕ Criar Call"
    if after.channel and after.channel.id == CANAL_CRIAR_CALL_ID:
        categoria  = after.channel.category
        nome_canal = f"{member.display_name}'s call"
        novo_canal = await guild.create_voice_channel(
            name=nome_canal,
            category=categoria,
            reason="Call temporária criada pelo bot"
        )
        canais_temporarios[novo_canal.id] = True
        await member.move_to(novo_canal)
        print(f"✅ Canal temporário criado: {nome_canal}")

    # Usuário saiu de um canal temporário
    if before.channel and before.channel.id in canais_temporarios:
        if len(before.channel.members) == 0:
            try:
                await before.channel.delete(reason="Call temporária vazia")
                del canais_temporarios[before.channel.id]
                print(f"🗑️ Canal temporário deletado: {before.channel.name}")
            except discord.NotFound:
                pass


# ─────────────────────────────────────────
#  REACTION ROLES
# ─────────────────────────────────────────
async def handle_reaction(payload, adicionar: bool):
    global reaction_roles_map
    reaction_roles_map = carregar_reaction_roles()

    msg_map = reaction_roles_map.get(payload.message_id)
    if not msg_map:
        return

    emoji_id = str(payload.emoji.id) if payload.emoji.id else payload.emoji.name
    role_id  = msg_map.get(emoji_id)
    if not role_id:
        return

    guild  = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id)
    role   = guild.get_role(role_id)

    if not member or not role or member.bot:
        return

    if adicionar:
        await member.add_roles(role, reason="Reaction role")
        print(f"✅ Role '{role.name}' adicionada a {member.display_name}")
    else:
        await member.remove_roles(role, reason="Reaction role removida")
        print(f"❌ Role '{role.name}' removida de {member.display_name}")


@bot.event
async def on_raw_reaction_add(payload):
    if payload.channel_id != CANAL_CONFIG_ROLES_ID:
        return
    await handle_reaction(payload, adicionar=True)


@bot.event
async def on_raw_reaction_remove(payload):
    if payload.channel_id != CANAL_CONFIG_ROLES_ID:
        return
    await handle_reaction(payload, adicionar=False)


# ─────────────────────────────────────────
#  COMANDO: !setup_roles
#  Posta as mensagens de reaction roles no
#  canal de config. Só admins podem usar.
#  IMPORTANTE: após rodar, o reaction_roles.json
#  será atualizado com os message_ids — suba
#  o arquivo atualizado no GitHub em seguida.
# ─────────────────────────────────────────
@bot.command()
@commands.has_permissions(administrator=True)
async def setup_roles(ctx):
    if ctx.channel.id != CANAL_CONFIG_ROLES_ID:
        await ctx.send("❌ Use esse comando no canal de configuração de roles.", delete_after=5)
        return

    with open("reaction_roles.json", "r") as f:
        data = json.load(f)

    canal = bot.get_channel(CANAL_CONFIG_ROLES_ID)

    for i, bloco in enumerate(data["mensagens"]):
        linhas = "\n".join(
            f"{r['emoji']} → {r['descricao']}"
            for r in bloco["reactions"]
        )
        embed = discord.Embed(
            description=(
                f"## Reaja aos emotes abaixo para ser notificado sobre as novidades no servidor ##\n\n"
                f"{linhas}\n\n"
                f"-# Você receberá o cargo referente à role que reagir ao emote."
            ),
            color=0x99AAB5  # cinza Discord
        )

        msg_id = bloco.get("message_id")
        msg = None

        # Tenta editar a mensagem existente
        if msg_id:
            try:
                msg = await canal.fetch_message(int(msg_id))
                await msg.edit(content=None, embed=embed)
            except discord.NotFound:
                msg = None

        # Se não existe ainda, posta nova
        if msg is None:
            msg = await ctx.send(embed=embed)
            for r in bloco["reactions"]:
                emoji = bot.get_emoji(int(r["emoji_id"]))
                if emoji:
                    await msg.add_reaction(emoji)

        for r in bloco["reactions"]:
            emoji = bot.get_emoji(int(r["emoji_id"]))
            if emoji:
                await msg.add_reaction(emoji)

        data["mensagens"][i]["message_id"] = str(msg.id)

    # Salva localmente e imprime o JSON atualizado no log
    with open("reaction_roles.json", "w") as f:
        json.dump(data, f, indent=2)

    await ctx.message.delete()

    # Imprime o JSON atualizado nos logs do Railway
    # Copie e substitua no seu reaction_roles.json no GitHub!
    print("✅ Reaction roles configurados!")
    print("📋 Copie o JSON abaixo e atualize no GitHub:")
    print(json.dumps(data, indent=2))


# ─────────────────────────────────────────
#  RODAR
# ─────────────────────────────────────────
bot.run(TOKEN)
