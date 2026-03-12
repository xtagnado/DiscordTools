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
TOKEN                = os.environ.get("TOKEN")
GOOGLE_CREDS_JSON    = os.environ.get("GOOGLE_CREDS_JSON")
SPREADSHEET_ID       = os.environ.get("SPREADSHEET_ID")
TWITCH_CLIENT_ID     = os.environ.get("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET")
CANAL_CRIAR_CALL_ID  = int(os.environ.get("CANAL_CRIAR_CALL_ID", 0))

CANAL_DIVULGACAO_ID   = 1468613615987851275
CANAL_CONFIG_ROLES_ID = 1479645122428932198
CARGO_STREAMANDO_NOME = "STREAMANDO AGORA"

MENTION_ROLES = {
    "twitch":        1478843698614898688,
    "youtube_live":  1478843587914498118,
    "youtube_video": 1478843428937666580,
}

TITULOS_BLOCOS = {
    0: "## NOTIFICAÇÕES ##",
    1: "## Criadores de conteúdo ##\n### Para ver os canais de comunicação específicos desses canais ###",
}

# ─────────────────────────────────────────
#  PERSISTÊNCIA LOCAL
# ─────────────────────────────────────────
def carregar_json(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}

def salvar_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

videos_vistos      = carregar_json("videos_vistos.json")
lives_yt_ativas    = carregar_json("lives_yt_ativas.json")
lives_twitch_ativas = carregar_json("lives_twitch_ativas.json")
canais_temporarios  = {}  # {canal_id: True} — resetado a cada reinício (ok, é RAM)
primeira_checagem   = True
twitch_access_token = None

# ─────────────────────────────────────────
#  GOOGLE SHEETS
# ─────────────────────────────────────────
def get_sheets_service(readonly=True):
    creds_info = json.loads(GOOGLE_CREDS_JSON)
    scope = (
        ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        if readonly else
        ["https://www.googleapis.com/auth/spreadsheets"]
    )
    creds = Credentials.from_service_account_info(creds_info, scopes=scope)
    return build("sheets", "v4", credentials=creds)


def get_canais_youtube():
    try:
        service = get_sheets_service()
        result  = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range="YouTube!A2:C"
        ).execute()
        return [
            {"nome": r[0].strip(), "discord_user_id": r[1].strip(), "channel_id": r[2].strip()}
            for r in result.get("values", []) if len(r) >= 3
        ]
    except Exception as e:
        print(f"❌ Erro ao ler planilha YouTube: {e}")
        return []


def get_canais_twitch():
    try:
        service = get_sheets_service()
        result  = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range="Twitch!A2:C"
        ).execute()
        return [
            {"nome": r[0].strip(), "discord_user_id": r[1].strip(), "twitch_id": r[2].strip()}
            for r in result.get("values", []) if len(r) >= 3 and r[2].strip()
        ]
    except Exception as e:
        print(f"❌ Erro ao ler planilha Twitch: {e}")
        return []


def get_message_ids():
    try:
        service = get_sheets_service()
        result  = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range="IDMensagens!A2:B"
        ).execute()
        return {r[0]: r[1] for r in result.get("values", []) if len(r) >= 2}
    except Exception as e:
        print(f"❌ Erro ao ler IDMensagens: {e}")
        return {}


def save_message_id(chave, message_id):
    try:
        service = get_sheets_service(readonly=False)
        result  = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range="IDMensagens!A2:A"
        ).execute()
        chaves = [r[0] for r in result.get("values", []) if r]

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
        print(f"✅ message_id salvo: {chave} = {message_id}")
    except Exception as e:
        print(f"❌ Erro ao salvar IDMensagens: {e}")

# ─────────────────────────────────────────
#  REACTION ROLES
# ─────────────────────────────────────────
def carregar_reaction_roles():
    if not os.path.exists("reaction_roles.json"):
        return {}
    with open("reaction_roles.json", "r") as f:
        data = json.load(f)
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

reaction_roles_map = {}

# ─────────────────────────────────────────
#  TWITCH API
# ─────────────────────────────────────────
async def get_twitch_token(session):
    global twitch_access_token
    async with session.post(
        "https://id.twitch.tv/oauth2/token",
        data={
            "client_id":     TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type":    "client_credentials",
        }
    ) as resp:
        result = await resp.json()
        twitch_access_token = result.get("access_token")
        print("✅ Twitch token obtido")


async def checar_lives_twitch_api(session, twitch_ids):
    global twitch_access_token
    if not twitch_access_token:
        await get_twitch_token(session)

    ids_query = "&".join(f"user_id={tid}" for tid in twitch_ids)
    headers   = {
        "Client-ID":     TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {twitch_access_token}",
    }
    async with session.get(
        f"https://api.twitch.tv/helix/streams?{ids_query}", headers=headers
    ) as resp:
        if resp.status == 401:
            await get_twitch_token(session)
            headers["Authorization"] = f"Bearer {twitch_access_token}"
            async with session.get(
                f"https://api.twitch.tv/helix/streams?{ids_query}", headers=headers
            ) as resp2:
                data = await resp2.json()
        else:
            data = await resp.json()

    return {
        stream["user_id"]: {
            "titulo":     stream["title"] or "Live sem título",
            "jogo":       stream["game_name"] or "Nenhuma categoria",
            "url":        f"https://www.twitch.tv/{stream['user_login']}",
        }
        for stream in data.get("data", [])
    }

# ─────────────────────────────────────────
#  RSS YOUTUBE
# ─────────────────────────────────────────
async def buscar_ultimo_conteudo(session, channel_id):
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            root = ET.fromstring(await resp.text())
            ns   = {
                "atom":  "http://www.w3.org/2005/Atom",
                "media": "http://search.yahoo.com/mrss/",
                "yt":    "http://www.youtube.com/xml/schemas/2015",
            }
            entries = root.findall("atom:entry", ns)[:5]
            if not entries:
                return None

            # Procura live ativa nos 5 primeiros itens
            for entry in entries:
                video_id = entry.find("yt:videoId", ns)
                if video_id is None:
                    continue
                vid_id  = video_id.text
                is_live = await checar_se_live(session, vid_id)
                if is_live:
                    titulo = entry.find("atom:title", ns)
                    link   = entry.find("atom:link", ns)
                    return {
                        "id":      vid_id,
                        "titulo":  titulo.text if titulo is not None else "Live sem título",
                        "url":     link.attrib.get("href", f"https://www.youtube.com/watch?v={vid_id}") if link is not None else "",
                        "thumb":   f"https://img.youtube.com/vi/{vid_id}/maxresdefault.jpg",
                        "is_live": True,
                    }

            # Nenhuma live — retorna o vídeo mais recente
            entry    = entries[0]
            video_id = entry.find("yt:videoId", ns)
            if video_id is None:
                return None
            vid_id = video_id.text
            titulo = entry.find("atom:title", ns)
            link   = entry.find("atom:link", ns)
            return {
                "id":      vid_id,
                "titulo":  titulo.text if titulo is not None else "Vídeo sem título",
                "url":     link.attrib.get("href", f"https://www.youtube.com/watch?v={vid_id}") if link is not None else "",
                "thumb":   f"https://img.youtube.com/vi/{vid_id}/maxresdefault.jpg",
                "is_live": False,
            }
    except Exception as e:
        print(f"❌ Erro RSS canal {channel_id}: {e}")
        return None


async def checar_se_live(session, video_id):
    try:
        async with session.get(
            f"https://www.youtube.com/watch?v={video_id}",
            timeout=aiohttp.ClientTimeout(total=8)
        ) as resp:
            if resp.status != 200:
                return False
            text = await resp.text()
            return '"isLiveBroadcast"' in text and '"endDate"' not in text
    except Exception:
        return False

# ─────────────────────────────────────────
#  SETUP DO BOT
# ─────────────────────────────────────────
intents = discord.Intents.default()
intents.members         = True
intents.presences       = True
intents.message_content = True
intents.voice_states    = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────
def get_mention(guild, plataforma):
    role = guild.get_role(MENTION_ROLES.get(plataforma, 0))
    return role.mention if role else ""

# ─────────────────────────────────────────
#  EMBEDS
# ─────────────────────────────────────────
def build_embed_twitch(nome, dados, mention, avatar_url=None):
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


def build_embed_youtube_live(nome, dados, mention, avatar_url=None):
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
#  EVENTOS
# ─────────────────────────────────────────
@bot.event
async def on_ready():
    global reaction_roles_map
    print(f"✅ Bot online como {bot.user} ({bot.user.id})")
    print(f"   Servidores: {[g.name for g in bot.guilds]}")
    reaction_roles_map = carregar_reaction_roles()
    checar_youtube.start()
    checar_twitch.start()
    limpar_cargos_presos.start()


@bot.event
async def on_voice_state_update(member, before, after):
    guild = member.guild
    print(f"🎤 Voice update: {member.display_name} | antes: {before.channel} | depois: {after.channel} | CANAL_CRIAR_CALL_ID: {CANAL_CRIAR_CALL_ID}")

    # Usuário entrou no canal "➕ Criar Call"
    if after.channel and after.channel.id == CANAL_CRIAR_CALL_ID:
        try:
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
        except Exception as e:
            print(f"❌ Erro ao criar call: {e}")

    # Usuário saiu de um canal temporário
    if before.channel and before.channel.id in canais_temporarios:
        if len(before.channel.members) == 0:
            try:
                await before.channel.delete(reason="Call temporária vazia")
                del canais_temporarios[before.channel.id]
                print(f"🗑️ Canal temporário deletado: {before.channel.name}")
            except discord.NotFound:
                pass


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
#  REACTION ROLES — HANDLER
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

# ─────────────────────────────────────────
#  TASK: CHECAR YOUTUBE (a cada 5 min)
# ─────────────────────────────────────────
@tasks.loop(minutes=5)
async def checar_youtube():
    global primeira_checagem, lives_yt_ativas
    await bot.wait_until_ready()

    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        return

    canal        = guild.get_channel(CANAL_DIVULGACAO_ID)
    cargo_stream = discord.utils.get(guild.roles, name=CARGO_STREAMANDO_NOME)
    canais       = get_canais_youtube()

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

            try:
                membro = guild.get_member(int(discord_uid))
            except Exception:
                membro = None

            estava_em_live = lives_yt_ativas.get(channel_id) == "live"

            # Gerencia cargo
            if cargo_stream and membro and not membro.bot:
                if is_live and not estava_em_live:
                    await membro.add_roles(cargo_stream, reason="YouTube Live iniciada")
                    print(f"🔴 Cargo adicionado (YT Live): {membro.display_name}")
                elif not is_live and estava_em_live:
                    await membro.remove_roles(cargo_stream, reason="YouTube Live encerrada")
                    print(f"🔴 Cargo removido (YT Live encerrada): {membro.display_name}")

            # Na primeira checagem: registra estado mas não posta
            if primeira_checagem:
                lives_yt_ativas[channel_id] = "live" if is_live else "video"
                salvar_json("lives_yt_ativas.json", lives_yt_ativas)
                if not is_live:
                    videos_vistos[channel_id] = vid_id
                    salvar_json("videos_vistos.json", videos_vistos)
                continue

            # Atualiza estado
            lives_yt_ativas[channel_id] = "live" if is_live else "video"
            salvar_json("lives_yt_ativas.json", lives_yt_ativas)

            nome_exibir = membro.display_name if membro else nome
            avatar_url  = membro.display_avatar.url if membro else None

            if is_live:
                # Posta só quando a live começa
                if not estava_em_live and not primeira_checagem and canal:
                    mention = get_mention(guild, "youtube_live")
                    embed   = build_embed_youtube_live(nome_exibir, conteudo, mention, avatar_url)
                    await canal.send(embed=embed)
                    print(f"🔴 YouTube Live postada: {nome_exibir} — {conteudo['titulo']}")
            else:
                # Vídeo novo
                if videos_vistos.get(channel_id) == vid_id:
                    continue
                videos_vistos[channel_id] = vid_id
                salvar_json("videos_vistos.json", videos_vistos)
                if canal:
                    mention = get_mention(guild, "youtube_video")
                    embed   = build_embed_youtube_video(nome_exibir, conteudo, mention, avatar_url)
                    await canal.send(embed=embed)
                    print(f"📹 Vídeo novo postado: {nome_exibir} — {conteudo['titulo']}")

    primeira_checagem = False

# ─────────────────────────────────────────
#  TASK: CHECAR TWITCH (a cada 5 min)
# ─────────────────────────────────────────
@tasks.loop(minutes=5)
async def checar_twitch():
    global lives_twitch_ativas
    await bot.wait_until_ready()

    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        return

    try:
        canal        = guild.get_channel(CANAL_DIVULGACAO_ID)
        cargo_stream = discord.utils.get(guild.roles, name=CARGO_STREAMANDO_NOME)
        canais       = get_canais_twitch()

        if not canais:
            return

        async with aiohttp.ClientSession() as session:
            twitch_ids = [c["twitch_id"] for c in canais if c["twitch_id"]]
            if not twitch_ids:
                return
            lives_agora = await checar_lives_twitch_api(session, twitch_ids)

        for entrada in canais:
            if not entrada["twitch_id"]:
                continue
            twitch_id   = entrada["twitch_id"]
            nome        = entrada["nome"]
            discord_uid = entrada["discord_user_id"]

            esta_live   = twitch_id in lives_agora
            estava_live = lives_twitch_ativas.get(twitch_id) == "live"

            try:
                membro = guild.get_member(int(discord_uid))
            except Exception:
                membro = None

            # Gerencia cargo
            if cargo_stream and membro and not membro.bot:
                if esta_live and not estava_live:
                    await membro.add_roles(cargo_stream, reason="Twitch Live iniciada")
                    print(f"🟣 Cargo adicionado (Twitch): {nome}")
                elif not esta_live and estava_live:
                    await membro.remove_roles(cargo_stream, reason="Twitch Live encerrada")
                    print(f"🟣 Cargo removido (Twitch encerrada): {nome}")

            # Atualiza estado
            lives_twitch_ativas[twitch_id] = "live" if esta_live else "offline"
            salvar_json("lives_twitch_ativas.json", lives_twitch_ativas)

            # Posta embed quando live começa
            if esta_live and not estava_live and canal:
                dados       = lives_agora[twitch_id]
                mention     = get_mention(guild, "twitch")
                nome_exibir = membro.display_name if membro else nome
                avatar_url  = membro.display_avatar.url if membro else None
                embed       = build_embed_twitch(nome_exibir, dados, mention, avatar_url)
                await canal.send(embed=embed)
                print(f"🟣 Twitch Live postada: {nome_exibir} — {dados['titulo']}")

    except Exception as e:
        print(f"❌ Erro em checar_twitch: {e}")

# ─────────────────────────────────────────
#  TASK: LIMPAR CARGOS PRESOS (a cada 10 min)
# ─────────────────────────────────────────
@tasks.loop(minutes=10)
async def limpar_cargos_presos():
    await bot.wait_until_ready()
    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        return

    cargo_stream = discord.utils.get(guild.roles, name=CARGO_STREAMANDO_NOME)
    if not cargo_stream:
        return

    canais_yt     = {int(c["discord_user_id"]): c["channel_id"] for c in get_canais_youtube()}
    canais_twitch = {int(c["discord_user_id"]): c["twitch_id"] for c in get_canais_twitch()}

    for membro in cargo_stream.members:
        if membro.bot:
            await membro.remove_roles(cargo_stream, reason="Bot não deve ter cargo de streaming")
            print(f"🧹 Cargo removido do bot: {membro.display_name}")
            continue

        channel_id = canais_yt.get(membro.id)
        twitch_id  = canais_twitch.get(membro.id)

        em_live_yt     = lives_yt_ativas.get(channel_id) == "live" if channel_id else False
        em_live_twitch = lives_twitch_ativas.get(twitch_id) == "live" if twitch_id else False

        if not em_live_yt and not em_live_twitch:
            await membro.remove_roles(cargo_stream, reason="Não está mais streamando")
            print(f"🧹 Cargo removido (preso): {membro.display_name}")

# ─────────────────────────────────────────
#  COMANDO: !setup_roles
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
        titulo = TITULOS_BLOCOS.get(i, f"## {bloco['descricao']} ##")
        linhas = "\n".join(f"{r['emoji']} → {r['descricao']}" for r in bloco["reactions"])
        embed  = discord.Embed(
            description=f"{titulo}\n\n{linhas}\n\n-# Você receberá o cargo referente à role que reagir ao emote.",
            color=0x99AAB5
        )

        msg_id = bloco.get("message_id")
        msg    = None

        if msg_id:
            try:
                msg = await canal.fetch_message(int(msg_id))
                await msg.edit(content=None, embed=embed)
            except discord.NotFound:
                msg = None

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
    print("✅ Reaction roles configurados!")

# ─────────────────────────────────────────
#  RODAR
# ─────────────────────────────────────────
bot.run(TOKEN)
