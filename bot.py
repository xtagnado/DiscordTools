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
SHEET_RANGE       = "YouTube!A2:C"
TWITCH_CLIENT_ID     = os.environ.get("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET")
twitch_access_token  = None  # será obtido automaticamente

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

    # Busca os message_ids salvos na planilha
    ids_planilha = get_message_ids()

    mapping = {}
    for i, msg in enumerate(data.get("mensagens", [])):
        chave  = f"message_id_{i}"
        msg_id = ids_planilha.get(chave) or msg.get("message_id")
        if not msg_id:
            continue
        mapping[int(msg_id)] = {
            r["emoji_id"]: r["role_id"] for r in msg.get("reactions", [])
        }
    return mapping

# reaction_roles_map será carregado no on_ready, após as funções do Sheets estarem definidas
reaction_roles_map = {}

# ─────────────────────────────────────────
#  GOOGLE SHEETS
# ─────────────────────────────────────────
def get_sheets_service(readonly=True):
    creds_info = json.loads(GOOGLE_CREDS_JSON)
    scope = ["https://www.googleapis.com/auth/spreadsheets.readonly"] if readonly else \
            ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_info, scopes=scope)
    return build("sheets", "v4", credentials=creds)


def get_canais_youtube():
    try:
        service = get_sheets_service(readonly=True)
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
        print(f"❌ Erro ao ler planilha YouTube: {e}")
        return []


def get_canais_twitch():
    try:
        service = get_sheets_service(readonly=True)
        result  = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Twitch!A2:C"
        ).execute()
        rows   = result.get("values", [])
        canais = []
        for row in rows:
            if len(row) >= 3:
                canais.append({
                    "nome":            row[0].strip(),
                    "discord_user_id": row[1].strip(),
                    "twitch_id":       row[2].strip(),
                })
        return canais
    except Exception as e:
        print(f"❌ Erro ao ler planilha Twitch: {e}")
        return []


def get_message_ids():
    """Lê os message_ids da aba IDMensagens. Retorna dict {chave: message_id}"""
    try:
        service = get_sheets_service(readonly=True)
        result  = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="IDMensagens!A2:B"
        ).execute()
        rows = result.get("values", [])
        return {row[0]: row[1] for row in rows if len(row) >= 2}
    except Exception as e:
        print(f"❌ Erro ao ler IDMensagens: {e}")
        return {}


def save_message_id(chave, message_id):
    """Salva ou atualiza um message_id na aba IDMensagens."""
    try:
        service = get_sheets_service(readonly=False)
        result  = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="IDMensagens!A2:A"
        ).execute()
        rows   = result.get("values", [])
        chaves = [r[0] for r in rows if r]

        if chave in chaves:
            linha = chaves.index(chave) + 2
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"IDMensagens!B{linha}",
                valueInputOption="RAW",
                body={"values": [[str(message_id)]]}
            ).execute()
        else:
            service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range="IDMensagens!A:B",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [[chave, str(message_id)]]}
            ).execute()

        print(f"✅ message_id salvo na planilha: {chave} = {message_id}")
    except Exception as e:
        print(f"❌ Erro ao salvar IDMensagens: {e}")


def get_estados_lives():
    """Lê os estados das lives da aba IDMensagens. Retorna dict {chave: estado}"""
    try:
        service = get_sheets_service(readonly=True)
        result  = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="IDMensagens!A2:B"
        ).execute()
        rows = result.get("values", [])
        return {row[0]: row[1] for row in rows if len(row) >= 2}
    except Exception as e:
        print(f"❌ Erro ao ler estados de lives: {e}")
        return {}


def save_estado_live(chave, estado):
    """Salva ou atualiza o estado de uma live na aba IDMensagens."""
    try:
        service = get_sheets_service(readonly=False)
        result  = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="IDMensagens!A2:A"
        ).execute()
        rows   = result.get("values", [])
        chaves = [r[0] for r in rows if r]

        if chave in chaves:
            linha = chaves.index(chave) + 2
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"IDMensagens!B{linha}",
                valueInputOption="RAW",
                body={"values": [[estado]]}
            ).execute()
        else:
            service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range="IDMensagens!A:B",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [[chave, estado]]}
            ).execute()
    except Exception as e:
        print(f"❌ Erro ao salvar estado de live: {e}")

# ─────────────────────────────────────────
#  RSS YOUTUBE
# ─────────────────────────────────────────
async def buscar_ultimo_conteudo(session, channel_id):
    """Retorna o conteúdo mais recente do canal via RSS.
    Varre os primeiros 5 itens para detectar live ativa ou vídeo novo."""
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
            entries = root.findall("atom:entry", ns)[:5]
            if not entries:
                return None

            # Primeiro tenta achar uma live ativa entre os 5 primeiros
            for entry in entries:
                video_id = entry.find("yt:videoId", ns)
                titulo   = entry.find("atom:title", ns)
                link     = entry.find("atom:link", ns)

                if video_id is None:
                    continue

                vid_id    = video_id.text
                thumb_url = f"https://img.youtube.com/vi/{vid_id}/maxresdefault.jpg"
                vid_url   = link.attrib.get("href", "") if link is not None else f"https://www.youtube.com/watch?v={vid_id}"

                is_live = await checar_se_live(session, vid_id)
                if is_live:
                    return {
                        "id":      vid_id,
                        "titulo":  titulo.text if titulo is not None else "Live sem título",
                        "url":     vid_url,
                        "thumb":   thumb_url,
                        "is_live": True,
                    }

            # Nenhuma live ativa — retorna o vídeo mais recente (primeiro item)
            entry    = entries[0]
            video_id = entry.find("yt:videoId", ns)
            titulo   = entry.find("atom:title", ns)
            link     = entry.find("atom:link", ns)

            if video_id is None:
                return None

            vid_id    = video_id.text
            thumb_url = f"https://img.youtube.com/vi/{vid_id}/maxresdefault.jpg"
            vid_url   = link.attrib.get("href", "") if link is not None else f"https://www.youtube.com/watch?v={vid_id}"

            return {
                "id":      vid_id,
                "titulo":  titulo.text if titulo is not None else "Vídeo sem título",
                "url":     vid_url,
                "thumb":   thumb_url,
                "is_live": False,
            }
    except Exception as e:
        print(f"❌ Erro RSS canal {channel_id}: {e}")
        return None


async def checar_se_live(session, video_id):
    """Checa se o vídeo é uma live ativa pela thumbnail especial do YouTube.
    Lives ativas têm a thumbnail _live.jpg disponível."""
    url = f"https://img.youtube.com/vi/{video_id}/maxres2.jpg"
    try:
        # YouTube retorna uma thumbnail especial para lives ativas
        # Checamos via a página do vídeo se contém indicador de live
        page_url = f"https://www.youtube.com/watch?v={video_id}"
        async with session.get(page_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status != 200:
                return False
            text = await resp.text()
            # Se a página contém "isLiveBroadcast" e "startDate" sem "endDate" = live ativa
            is_broadcast = '"isLiveBroadcast"' in text
            has_ended    = '"endDate"' in text
            return is_broadcast and not has_ended
    except Exception:
        return False

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
def build_embed_twitch_api(nome, dados, mention, avatar_url=None):
    embed = discord.Embed(color=0x9146FF, timestamp=datetime.utcnow())
    embed.description = (
        f"🟣 **TEM LIVE ACONTECENDO NA TWITCH:**\n\n"
        f"**{nome}** está ao vivo na roxinha 🟪🟪🟪\n"
        f"Bora lá assistir esse conteúdo, se inscrever e apoiar o pessoal da nossa comunidade. 💜💜💜💜💜\n\n"
        f"**🎮 [{dados['titulo']}]({dados['url']})**\n"
        f"📂 {dados['jogo']}\n\n"
        f"{mention}"
    )
    if avatar_url:
        embed.set_author(name=nome, icon_url=avatar_url)
    else:
        embed.set_author(name=nome)
    return embed


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


def build_embed_youtube_live_rss(nome, dados, mention, avatar_url=None):
    embed = discord.Embed(color=0xFF0000, timestamp=datetime.utcnow())
    embed.description = (
        f"🔴 **LIVE NO YOUTUBE AGORA. CORRE LÁ PRA VER:**\n\n"
        f"**{nome}** está ao vivo e operante. 🟥🟥🟥\n"
        f"Bora lá assistir esse conteúdo, se inscrever e apoiar o pessoal da nossa comunidade. ❤️❤️❤️❤️❤️\n\n"
        f"**🎬 [{dados['titulo']}]({dados['url']})**\n\n"
        f"{mention}"
    )
    if avatar_url:
        embed.set_author(name=nome, icon_url=avatar_url)
    else:
        embed.set_author(name=nome)
    if dados.get("thumb"):
        embed.set_image(url=dados["thumb"])
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
#  TWITCH API
# ─────────────────────────────────────────
twitch_access_token  = None
lives_twitch_ativas  = carregar_json("lives_twitch_ativas.json")

def get_canais_twitch():
    try:
        service = get_sheets_service(readonly=True)
        result  = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Twitch!A2:C"
        ).execute()
        rows   = result.get("values", [])
        canais = []
        for row in rows:
            if len(row) >= 3:
                canais.append({
                    "nome":            row[0].strip(),
                    "discord_user_id": row[1].strip(),
                    "twitch_id":       row[2].strip(),
                })
        return canais
    except Exception as e:
        print(f"❌ Erro ao ler aba Twitch: {e}")
        return []


async def get_twitch_token(session):
    global twitch_access_token
    url  = "https://id.twitch.tv/oauth2/token"
    data = {
        "client_id":     TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "grant_type":    "client_credentials",
    }
    async with session.post(url, data=data) as resp:
        result = await resp.json()
        twitch_access_token = result.get("access_token")
        print(f"✅ Twitch token obtido")


async def checar_lives_twitch_api(session, twitch_ids):
    """Checa quais canais estão ao vivo via Twitch API. Retorna dict {user_id: dados_live}"""
    global twitch_access_token

    if not twitch_access_token:
        await get_twitch_token(session)

    ids_query = "&".join(f"user_id={tid}" for tid in twitch_ids)
    url     = f"https://api.twitch.tv/helix/streams?{ids_query}"
    headers = {
        "Client-ID":     TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {twitch_access_token}",
    }

    async with session.get(url, headers=headers) as resp:
        # Token expirado — renova e tenta de novo
        if resp.status == 401:
            await get_twitch_token(session)
            headers["Authorization"] = f"Bearer {twitch_access_token}"
            async with session.get(url, headers=headers) as resp2:
                data = await resp2.json()
        else:
            data = await resp.json()

    lives = {}
    for stream in data.get("data", []):
        lives[stream["user_id"]] = {
            "titulo": stream["title"] or "Live sem título",
            "jogo":   stream["game_name"] or "Nenhuma categoria",
            "url":    f"https://www.twitch.tv/{stream['user_login']}",
            "user_login": stream["user_login"],
        }
    return lives


# ─────────────────────────────────────────
#  TASK: CHECAR LIVES TWITCH (a cada 5 min)
# ─────────────────────────────────────────
@tasks.loop(minutes=5)
async def checar_twitch():
    global lives_twitch_ativas
    await bot.wait_until_ready()

    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        return

    canal        = guild.get_channel(CANAL_DIVULGACAO_ID)
    cargo_stream = discord.utils.get(guild.roles, name=CARGO_STREAMANDO_NOME)
    canais       = get_canais_twitch()

    if not canais:
        return

    twitch_ids = [c["twitch_id"] for c in canais]

    async with aiohttp.ClientSession() as session:
        lives_agora = await checar_lives_twitch_api(session, twitch_ids)

    for entrada in canais:
        twitch_id   = entrada["twitch_id"]
        nome        = entrada["nome"]
        discord_uid = entrada["discord_user_id"]

        esta_live       = twitch_id in lives_agora
        estava_live     = lives_twitch_ativas.get(twitch_id) == "live"

        try:
            membro = guild.get_member(int(discord_uid))
        except Exception:
            membro = None

        # Gerencia cargo
        if cargo_stream and membro and not membro.bot:
            if esta_live and not estava_live:
                await membro.add_roles(cargo_stream, reason="Twitch Live iniciada")
                print(f"🟣 Cargo STREAMANDO AGORA adicionado (Twitch): {nome}")
            elif not esta_live and estava_live:
                await membro.remove_roles(cargo_stream, reason="Twitch Live encerrada")
                print(f"🟣 Cargo STREAMANDO AGORA removido (Twitch): {nome}")

        # Registra estado no Sheets
        lives_twitch_ativas[twitch_id] = "live" if esta_live else "offline"
        salvar_json("lives_twitch_ativas.json", lives_twitch_ativas)
        save_estado_live(f"twitch_{twitch_id}", "live" if esta_live else "offline")

        # Posta embed só quando a live começa
        if esta_live and not estava_live and canal:
            dados   = lives_agora[twitch_id]
            mention = get_mention(guild, "twitch")

            nome_exibir = membro.display_name if membro else nome
            avatar_url  = membro.display_avatar.url if membro else None

            embed = build_embed_twitch_api(nome_exibir, dados, mention, avatar_url)
            await canal.send(embed=embed)
            print(f"🟣 Twitch Live postada: {nome_exibir} — {dados['titulo']}")


# ─────────────────────────────────────────
#  TASK: CHECAR VÍDEOS/LIVES YOUTUBE
# ─────────────────────────────────────────
LIVES_YT_ATIVAS_FILE = "lives_yt_ativas.json"
lives_yt_ativas      = carregar_json(LIVES_YT_ATIVAS_FILE)

@tasks.loop(minutes=5)
async def checar_videos():
    global primeira_checagem, lives_yt_ativas
    await bot.wait_until_ready()

    guild  = bot.guilds[0] if bot.guilds else None
    if not guild:
        return

    canal         = guild.get_channel(CANAL_DIVULGACAO_ID)
    mention_video = get_mention(guild, "youtube_video")
    mention_live  = get_mention(guild, "youtube_live")
    cargo_stream  = discord.utils.get(guild.roles, name=CARGO_STREAMANDO_NOME)
    canais        = get_canais_youtube()

    async with aiohttp.ClientSession() as session:
        for entrada in canais:
            channel_id  = entrada["channel_id"]
            nome        = entrada["nome"]
            discord_uid = entrada["discord_user_id"]

            conteudo = await buscar_ultimo_conteudo(session, channel_id)
            if not conteudo or not conteudo["id"]:
                continue

            vid_id  = conteudo["id"]
            is_live = conteudo["is_live"]

            # ── Gerencia cargo STREAMANDO AGORA para YouTube ──────────
            try:
                membro = guild.get_member(int(discord_uid))
            except Exception:
                membro = None

            if membro and cargo_stream:
                estava_em_live_yt = lives_yt_ativas.get(channel_id) == "live"
                if is_live and not estava_em_live_yt:
                    await membro.add_roles(cargo_stream, reason="YouTube Live iniciada")
                elif not is_live and estava_em_live_yt:
                    await membro.remove_roles(cargo_stream, reason="YouTube Live encerrada")

            # Registra estado da live local e no Sheets
            novo_estado = "live" if is_live else "offline"
            lives_yt_ativas[channel_id] = novo_estado
            salvar_json(LIVES_YT_ATIVAS_FILE, lives_yt_ativas)
            save_estado_live(f"yt_{channel_id}", novo_estado)

            # ── Gerencia postagem ─────────────────────────────────────
            estava_em_live_yt = lives_yt_ativas.get(channel_id) == "live"

            if is_live:
                # Posta live só se acabou de começar (não estava em live antes)
                if not estava_em_live_yt and not primeira_checagem:
                    if canal and membro:
                        mention_live = get_mention(guild, "youtube_live")
                        avatar_url = membro.display_avatar.url if membro else None
                        nome_exibir = membro.display_name if membro else nome
                        embed = build_embed_youtube_live_rss(nome_exibir, conteudo, mention_live, avatar_url)
                        await canal.send(embed=embed)
                        print(f"🔴 YouTube Live postada: {nome_exibir} — {conteudo['titulo']}")
            else:
                # Vídeo normal — só posta se ID é novo
                if videos_vistos.get(channel_id) == vid_id:
                    continue

                if primeira_checagem:
                    videos_vistos[channel_id] = vid_id
                    salvar_json(VIDEOS_VISTOS_FILE, videos_vistos)
                    continue

                videos_vistos[channel_id] = vid_id
                salvar_json(VIDEOS_VISTOS_FILE, videos_vistos)

                if canal:
                    mention_video = get_mention(guild, "youtube_video")
                    avatar_url = membro.display_avatar.url if membro else None
                    nome_exibir = membro.display_name if membro else nome
                    embed = build_embed_youtube_video(nome_exibir, conteudo, mention_video, avatar_url)
                    await canal.send(embed=embed)
                    print(f"📹 Vídeo novo postado: {nome_exibir} — {conteudo['titulo']}")

    primeira_checagem = False

# ─────────────────────────────────────────
#  EVENTOS
# ─────────────────────────────────────────
@bot.event
async def on_ready():
    global reaction_roles_map, lives_yt_ativas, lives_twitch_ativas
    print(f"✅ Bot online como {bot.user} ({bot.user.id})")
    print(f"   Servidores: {[g.name for g in bot.guilds]}")
    reaction_roles_map = carregar_reaction_roles()

    # Carrega estados das lives do Sheets para não perder entre reinicializações
    estados = get_estados_lives()
    for chave, estado in estados.items():
        if chave.startswith("yt_"):
            channel_id = chave[3:]
            lives_yt_ativas[channel_id] = estado
        elif chave.startswith("twitch_"):
            twitch_id = chave[7:]
            lives_twitch_ativas[twitch_id] = estado
    print(f"📋 Estados de lives carregados do Sheets: {len(estados)} entradas")

    # Carrega canais temporários do Sheets
    calls_salvas = get_calls_temp()
    canais_temporarios.update(calls_salvas)
    print(f"📋 Calls temporárias carregadas do Sheets: {len(calls_salvas)} entradas")

    checar_videos.start()
    checar_twitch.start()
    limpar_cargos_presos.start()
    limpar_calls_vazias.start()


@tasks.loop(minutes=10)
async def limpar_cargos_presos():
    """Remove o cargo STREAMANDO AGORA de quem não está mais streamando."""
    await bot.wait_until_ready()
    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        return

    cargo_stream = discord.utils.get(guild.roles, name=CARGO_STREAMANDO_NOME)
    if not cargo_stream:
        return

    # Monta dict de discord_user_id → channel_id para checar live YT por membro
    canais_por_membro = {}
    try:
        canais = get_canais_youtube()
        for entrada in canais:
            try:
                uid = int(entrada["discord_user_id"])
                canais_por_membro[uid] = entrada["channel_id"]
            except Exception:
                pass
    except Exception:
        pass

    for membro in cargo_stream.members:
        # Bots nunca devem ter o cargo
        if membro.bot:
            await membro.remove_roles(cargo_stream, reason="Bot não deve ter cargo de streaming")
            print(f"🧹 Cargo removido do bot: {membro.display_name}")
            continue

        # Checa se está em live na Twitch via presença
        esta_streamando_twitch = any(isinstance(a, discord.Streaming) for a in membro.activities)

        # Checa se está em live no YouTube via RSS
        channel_id    = canais_por_membro.get(membro.id)
        em_live_yt    = lives_yt_ativas.get(channel_id) == "live" if channel_id else False

        if not esta_streamando_twitch and not em_live_yt:
            await membro.remove_roles(cargo_stream, reason="Não está mais streamando")
            print(f"🧹 Cargo STREAMANDO AGORA removido (preso): {membro.display_name}")


@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    # Ignora bots
    if after.bot:
        return

    guild = after.guild
    canal = guild.get_channel(CANAL_DIVULGACAO_ID)
    if not canal:
        return

    cargo_stream      = discord.utils.get(guild.roles, name=CARGO_STREAMANDO_NOME)
    estava_streamando = any(isinstance(a, discord.Streaming) for a in before.activities)
    esta_streamando   = any(isinstance(a, discord.Streaming) for a in after.activities)

    if cargo_stream:
        try:
            if esta_streamando and not estava_streamando:
                await after.add_roles(cargo_stream, reason="Entrou em live")
                print(f"🎮 Cargo STREAMANDO AGORA adicionado: {after.display_name}")
            elif estava_streamando and not esta_streamando:
                await after.remove_roles(cargo_stream, reason="Saiu da live")
                print(f"🎮 Cargo STREAMANDO AGORA removido: {after.display_name}")
        except Exception as e:
            print(f"❌ Erro ao gerenciar cargo de {after.display_name}: {e}")

    atividades_novas = [a for a in after.activities if a not in before.activities]

    for atividade in atividades_novas:
        plataforma, dados = detectar_plataforma(atividade)
        if not plataforma or not dados:
            continue

        # Twitch agora é gerenciada via API — ignora presença para evitar duplicatas
        if plataforma == "twitch":
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


def get_calls_temp():
    """Lê os canais temporários salvos na aba CallsTemp."""
    try:
        service = get_sheets_service(readonly=True)
        result  = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="CallsTemp!A2:B"
        ).execute()
        rows = result.get("values", [])
        return {int(row[0]): row[1] for row in rows if len(row) >= 2}
    except Exception as e:
        print(f"❌ Erro ao ler CallsTemp: {e}")
        return {}


def save_call_temp(canal_id, nome):
    """Salva um canal temporário na aba CallsTemp."""
    try:
        service = get_sheets_service(readonly=False)
        service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="CallsTemp!A:B",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [[str(canal_id), nome]]}
        ).execute()
    except Exception as e:
        print(f"❌ Erro ao salvar CallsTemp: {e}")


def delete_call_temp(canal_id):
    """Remove um canal temporário da aba CallsTemp."""
    try:
        service = get_sheets_service(readonly=False)
        result  = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="CallsTemp!A2:A"
        ).execute()
        rows = result.get("values", [])
        ids  = [r[0] for r in rows if r]

        if str(canal_id) not in ids:
            return

        linha = ids.index(str(canal_id)) + 2
        service.spreadsheets().values().clear(
            spreadsheetId=SPREADSHEET_ID,
            range=f"CallsTemp!A{linha}:B{linha}"
        ).execute()
    except Exception as e:
        print(f"❌ Erro ao remover CallsTemp: {e}")


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
        canais_temporarios[novo_canal.id] = novo_canal.name
        save_call_temp(novo_canal.id, novo_canal.name)
        await member.move_to(novo_canal)
        print(f"✅ Canal temporário criado: {nome_canal}")

    # Usuário saiu de um canal — checa se era temporário (pelo Sheets, dict ou nome)
    if before.channel and before.channel.id != CANAL_CRIAR_CALL_ID:
        canal_era_temp = (
            before.channel.id in canais_temporarios or
            before.channel.name.endswith("'s call")
        )
        if canal_era_temp and len(before.channel.members) == 0:
            try:
                await before.channel.delete(reason="Call temporária vazia")
                canais_temporarios.pop(before.channel.id, None)
                delete_call_temp(before.channel.id)
                print(f"🗑️ Canal temporário deletado: {before.channel.name}")
            except discord.NotFound:
                pass


@tasks.loop(minutes=5)
async def limpar_calls_vazias():
    """Varre Sheets + canais de voz e deleta calls temporárias vazias."""
    await bot.wait_until_ready()
    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        return

    # IDs salvos no Sheets
    calls_sheets = get_calls_temp()

    for canal_id, nome in list(calls_sheets.items()):
        canal = guild.get_channel(canal_id)
        if canal is None or len(canal.members) == 0:
            if canal:
                try:
                    await canal.delete(reason="Call temporária vazia — limpeza automática")
                    print(f"🗑️ Call vazia deletada (Sheets): {nome}")
                except discord.NotFound:
                    pass
            canais_temporarios.pop(canal_id, None)
            delete_call_temp(canal_id)

    # Fallback: varre todos os canais de voz pelo nome
    for canal in guild.voice_channels:
        if canal.id == CANAL_CRIAR_CALL_ID:
            continue
        if canal.name.endswith("'s call") and len(canal.members) == 0:
            try:
                await canal.delete(reason="Call temporária vazia — limpeza automática")
                canais_temporarios.pop(canal.id, None)
                delete_call_temp(canal.id)
                print(f"🗑️ Call vazia deletada (fallback nome): {canal.name}")
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
#  COMMAND: !setup_roles
# ─────────────────────────────────────────
# Títulos por índice de bloco
TITULOS_BLOCOS = {
    0: "## NOTIFICAÇÕES ##",
    1: "## Criadores de conteúdo ##\n### Para ver os canais de comunicação específicos desses canais ###",
}

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
        titulo = TITULOS_BLOCOS.get(i, f"## {bloco['descricao']} ##")
        linhas = "\n".join(
            f"{r['emoji']} → {r['descricao']}"
            for r in bloco["reactions"]
        )
        embed = discord.Embed(
            description=(
                f"{titulo}\n\n"
                f"{linhas}\n\n"
                f"-# Você receberá o cargo referente à role que reagir ao emote."
            ),
            color=0x99AAB5
        )

        msg_id = bloco.get("message_id")
        msg    = None

        # Tenta editar mensagem existente
        if msg_id:
            try:
                msg = await canal.fetch_message(int(msg_id))
                await msg.edit(content=None, embed=embed)
            except discord.NotFound:
                msg = None

        # Posta nova se não existir
        if msg is None:
            msg = await ctx.send(embed=embed)
            for r in bloco["reactions"]:
                emoji = bot.get_emoji(int(r["emoji_id"]))
                if emoji:
                    await msg.add_reaction(emoji)

        data["mensagens"][i]["message_id"] = str(msg.id)
        save_message_id(f"message_id_{i}", msg.id)

    with open("reaction_roles.json", "w") as f:
        json.dump(data, f, indent=2)

    await ctx.message.delete()
    print("✅ Reaction roles configurados e message_ids salvos na planilha!")


# ─────────────────────────────────────────
#  RODAR
# ─────────────────────────────────────────
bot.run(TOKEN)
