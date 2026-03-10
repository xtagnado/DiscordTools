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
        print(f"❌ Erro ao ler planilha: {e}")
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

        # Lê as chaves existentes para ver se já existe
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="IDMensagens!A2:A"
        ).execute()
        rows  = result.get("values", [])
        chaves = [r[0] for r in rows if r]

        if chave in chaves:
            linha = chaves.index(chave) + 2  # +2 por causa do header
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

# ─────────────────────────────────────────
#  RSS YOUTUBE
# ─────────────────────────────────────────
async def buscar_ultimo_conteudo(session, channel_id):
    """Retorna o conteúdo mais recente do canal via RSS.
    Detecta se é live ativa, live encerrada ou vídeo normal."""
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

            video_id = entry.find("yt:videoId", ns)
            titulo   = entry.find("atom:title", ns)
            link     = entry.find("atom:link", ns)

            if video_id is None:
                return None

            vid_id    = video_id.text
            thumb_url = f"https://img.youtube.com/vi/{vid_id}/maxresdefault.jpg"
            vid_url   = link.attrib.get("href", "") if link is not None else f"https://www.youtube.com/watch?v={vid_id}"

            # Verifica se é live ativa consultando a thumbnail especial
            # YouTube usa /vi/{id}/maxresdefault.jpg para lives também,
            # mas podemos checar via oEmbed se é live
            is_live = await checar_se_live(session, vid_id)

            return {
                "id":     vid_id,
                "titulo": titulo.text if titulo is not None else "Sem título",
                "url":    vid_url,
                "thumb":  thumb_url,
                "is_live": is_live,
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
#  TASK: CHECAR VÍDEOS NOVOS (a cada 5 min)
# ─────────────────────────────────────────
LIVES_YT_ATIVAS_FILE = "lives_yt_ativas.json"
lives_yt_ativas = carregar_json(LIVES_YT_ATIVAS_FILE)

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

            # Registra estado da live
            novo_estado = "live" if is_live else "video"
            lives_yt_ativas[channel_id] = novo_estado
            salvar_json(LIVES_YT_ATIVAS_FILE, lives_yt_ativas)

            # ── Verifica se já postou esse conteúdo ──────────────────
            if videos_vistos.get(channel_id) == vid_id:
                continue

            # Primeira checagem: só registra, não posta
            if primeira_checagem:
                videos_vistos[channel_id] = vid_id
                salvar_json(VIDEOS_VISTOS_FILE, videos_vistos)
                continue

            videos_vistos[channel_id] = vid_id
            salvar_json(VIDEOS_VISTOS_FILE, videos_vistos)

            if not canal:
                continue

            avatar_url = None
            if membro:
                avatar_url = membro.display_avatar.url
                nome = membro.display_name

            if is_live:
                embed = build_embed_youtube_live_rss(nome, conteudo, mention_live, avatar_url)
                print(f"🔴 YouTube Live detectada via RSS: {nome} — {conteudo['titulo']}")
            else:
                embed = build_embed_youtube_video(nome, conteudo, mention_video, avatar_url)
                print(f"📹 Vídeo novo postado: {nome} — {conteudo['titulo']}")

            await canal.send(embed=embed)

    primeira_checagem = False

# ─────────────────────────────────────────
#  EVENTOS
# ─────────────────────────────────────────
@bot.event
async def on_ready():
    global reaction_roles_map
    print(f"✅ Bot online como {bot.user} ({bot.user.id})")
    print(f"   Servidores: {[g.name for g in bot.guilds]}")
    reaction_roles_map = carregar_reaction_roles()
    checar_videos.start()
    limpar_cargos_presos.start()


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

    for membro in cargo_stream.members:
        if membro.bot:
            await membro.remove_roles(cargo_stream, reason="Bot não deve ter cargo de streaming")
            print(f"🧹 Cargo removido do bot: {membro.display_name}")
            continue

        esta_streamando = any(isinstance(a, discord.Streaming) for a in membro.activities)
        em_live_yt = any(
            lives_yt_ativas.get(ch) == "live"
            for ch in lives_yt_ativas
        )

        if not esta_streamando and not em_live_yt:
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
