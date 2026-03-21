import discord
from discord.ext import commands, tasks
import json
import os
import aiohttp
import xml.etree.ElementTree as ET
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ═══════════════════════════════════════════════════════════════
#  CONFIGURAÇÕES
# ═══════════════════════════════════════════════════════════════
TOKEN                = os.environ.get("TOKEN")
GOOGLE_CREDS_JSON    = os.environ.get("GOOGLE_CREDS_JSON")
SPREADSHEET_ID       = os.environ.get("SPREADSHEET_ID")
TWITCH_CLIENT_ID     = os.environ.get("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET")
CANAL_CRIAR_CALL_ID  = int(os.environ.get("CANAL_CRIAR_CALL_ID", 0))
ANTHROPIC_API_KEY    = os.environ.get("ANTHROPIC_API_KEY")
GROQ_API_KEY         = os.environ.get("GROQ_API_KEY")

CANAL_DIVULGACAO_ID    = 1468613615987851275
CANAL_CONFIG_ROLES_ID  = 1479645122428932198
CANAL_BOAS_VINDAS_ID   = 1465061820451520754
CANAL_APRESENTACAO_ID  = 1465074754246676591
CARGO_STREAMANDO_NOME  = "STREAMANDO AGORA"
CARGO_LADO_FORA_ID     = 1465890444746559663

MENTION_ROLES = {
    "twitch":        1478843698614898688,
    "youtube_live":  1478843587914498118,
    "youtube_video": 1478843428937666580,
}

TITULOS_BLOCOS = {
    0: "## NOTIFICAÇÕES ##",
    1: "## Criadores de conteúdo ##\n### Para ver os canais de comunicação específicos desses canais ###",
}

# ═══════════════════════════════════════════════════════════════
#  PERSISTÊNCIA LOCAL (JSON)
# ═══════════════════════════════════════════════════════════════
def carregar_json(path):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"❌ Erro ao carregar {path}: {e}")
    return {}

def salvar_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"❌ Erro ao salvar {path}: {e}")

videos_vistos       = carregar_json("videos_vistos.json")
lives_yt_ativas     = carregar_json("lives_yt_ativas.json")
lives_twitch_ativas = carregar_json("lives_twitch_ativas.json")
canais_temporarios  = {}
primeira_checagem   = True
twitch_access_token = None

# ═══════════════════════════════════════════════════════════════
#  GOOGLE SHEETS
# ═══════════════════════════════════════════════════════════════
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
        svc    = get_sheets_service()
        result = svc.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range="YouTube!A2:C"
        ).execute()
        return [
            {"nome": r[0].strip(), "discord_user_id": r[1].strip(), "channel_id": r[2].strip()}
            for r in result.get("values", [])
            if len(r) >= 3 and r[0].strip() and r[1].strip() and r[2].strip()
        ]
    except Exception as e:
        print(f"❌ Sheets YouTube: {e}")
        return []


def get_canais_twitch():
    try:
        svc    = get_sheets_service()
        result = svc.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range="Twitch!A2:C"
        ).execute()
        return [
            {"nome": r[0].strip(), "discord_user_id": r[1].strip(), "twitch_id": r[2].strip()}
            for r in result.get("values", [])
            if len(r) >= 3 and r[0].strip() and r[1].strip() and r[2].strip()
        ]
    except Exception as e:
        print(f"❌ Sheets Twitch: {e}")
        return []


def get_message_ids():
    try:
        svc    = get_sheets_service()
        result = svc.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range="IDMensagens!A2:B"
        ).execute()
        return {r[0]: r[1] for r in result.get("values", []) if len(r) >= 2}
    except Exception as e:
        print(f"❌ Sheets IDMensagens (leitura): {e}")
        return {}


def save_message_id(chave, message_id):
    try:
        svc    = get_sheets_service(readonly=False)
        result = svc.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range="IDMensagens!A2:A"
        ).execute()
        chaves = [r[0] for r in result.get("values", []) if r]

        if chave in chaves:
            linha = chaves.index(chave) + 2
            svc.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"IDMensagens!B{linha}",
                valueInputOption="RAW",
                body={"values": [[str(message_id)]]}
            ).execute()
        else:
            svc.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range="IDMensagens!A:B",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [[chave, str(message_id)]]}
            ).execute()
        print(f"✅ message_id salvo: {chave} = {message_id}")
    except Exception as e:
        print(f"❌ Sheets IDMensagens (escrita): {e}")

# ═══════════════════════════════════════════════════════════════
#  REACTION ROLES
# ═══════════════════════════════════════════════════════════════
reaction_roles_map = {}

def carregar_reaction_roles():
    try:
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
    except Exception as e:
        print(f"❌ Erro ao carregar reaction roles: {e}")
        return {}

# ═══════════════════════════════════════════════════════════════
#  TWITCH API
# ═══════════════════════════════════════════════════════════════
async def get_twitch_token(session):
    global twitch_access_token
    try:
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
    except Exception as e:
        print(f"❌ Erro ao obter token Twitch: {e}")


async def checar_lives_twitch_api(session, twitch_ids):
    global twitch_access_token
    try:
        ids_validos = [str(tid).strip() for tid in twitch_ids if tid and str(tid).strip()]
        if not ids_validos:
            return {}
        if not twitch_access_token:
            await get_twitch_token(session)
        if not twitch_access_token:
            return {}

        ids_query = "&".join(f"user_id={tid}" for tid in ids_validos)
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
                "titulo": stream["title"] or "Live sem título",
                "jogo":   stream["game_name"] or "Nenhuma categoria",
                "url":    f"https://www.twitch.tv/{stream['user_login']}",
            }
            for stream in data.get("data", [])
        }
    except Exception as e:
        print(f"❌ Erro Twitch API: {e}")
        return {}

# ═══════════════════════════════════════════════════════════════
#  RSS YOUTUBE
# ═══════════════════════════════════════════════════════════════
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


async def buscar_ultimo_conteudo(session, channel_id):
    try:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            root = ET.fromstring(await resp.text())
            ns   = {
                "atom": "http://www.w3.org/2005/Atom",
                "yt":   "http://www.youtube.com/xml/schemas/2015",
            }
            entries = root.findall("atom:entry", ns)[:5]
            if not entries:
                return None

            for entry in entries:
                vid_el = entry.find("yt:videoId", ns)
                if vid_el is None:
                    continue
                vid_id  = vid_el.text
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

            entry  = entries[0]
            vid_el = entry.find("yt:videoId", ns)
            if vid_el is None:
                return None
            vid_id = vid_el.text
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
        print(f"❌ Erro RSS {channel_id}: {e}")
        return None

# ═══════════════════════════════════════════════════════════════
#  EMBEDS
# ═══════════════════════════════════════════════════════════════
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
    embed.set_author(name=nome, icon_url=avatar_url) if avatar_url else embed.set_author(name=nome)
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
    embed.set_author(name=nome, icon_url=avatar_url) if avatar_url else embed.set_author(name=nome)
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
    embed.set_author(name=nome, icon_url=avatar_url) if avatar_url else embed.set_author(name=nome)
    if dados.get("thumb"):
        embed.set_image(url=dados["thumb"])
    return embed

# ═══════════════════════════════════════════════════════════════
#  BOT
# ═══════════════════════════════════════════════════════════════
intents = discord.Intents.default()
intents.members         = True
intents.presences       = True
intents.message_content = True
intents.voice_states    = True

bot = commands.Bot(command_prefix="!", intents=intents)

def get_mention(guild, plataforma):
    role = guild.get_role(MENTION_ROLES.get(plataforma, 0))
    return role.mention if role else ""

# ═══════════════════════════════════════════════════════════════
#  ON READY
# ═══════════════════════════════════════════════════════════════
@bot.event
async def on_ready():
    global reaction_roles_map
    print(f"✅ Bot online: {bot.user} ({bot.user.id})")
    print(f"   Servidor: {[g.name for g in bot.guilds]}")
    print(f"   CANAL_CRIAR_CALL_ID: {CANAL_CRIAR_CALL_ID}")
    reaction_roles_map = carregar_reaction_roles()
    checar_youtube.start()
    checar_twitch.start()
    limpar_cargos_presos.start()
    limpar_calls_vazias.start()


# ═══════════════════════════════════════════════════════════════
#  BOAS-VINDAS — AUTOROLE + MENSAGEM IA
# ═══════════════════════════════════════════════════════════════
async def gerar_boas_vindas(nome: str) -> str:
    """Gera mensagem de boas-vindas personalizada via Groq (grátis)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":      "llama3-8b-8192",
                    "max_tokens": 300,
                    "messages": [
                        {
                            "role":    "system",
                            "content": (
                                "Você é o bot de boas-vindas do servidor Discord 'Rebuildando Achievements', "
                                "uma comunidade de jogadores de RetroAchievements e entusiastas de lives de Twitch e YouTube. "
                                "Gere mensagens curtas, criativas e animadas em português brasileiro. "
                                "Máximo 3 linhas. Apenas a mensagem, sem explicações."
                            )
                        },
                        {
                            "role":    "user",
                            "content": f"Crie uma mensagem de boas-vindas para '{nome}' que acabou de entrar no servidor."
                        }
                    ]
                },
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"❌ Erro ao gerar boas-vindas com IA: {e}")
        return f"Bem-vindo ao Rebuildando Achievements, **{nome}**! 🎮"


@bot.event
async def on_member_join(member):
    guild = member.guild

    # 1. Autorole — atribui [LADO DE FORA]
    try:
        cargo = guild.get_role(CARGO_LADO_FORA_ID)
        if cargo:
            await member.add_roles(cargo, reason="Autorole — entrou no servidor")
            print(f"✅ Autorole atribuído: {member.display_name}")
    except Exception as e:
        print(f"❌ Erro ao atribuir autorole: {e}")

    # 2. Mensagem de boas-vindas com IA
    try:
        canal = guild.get_channel(CANAL_BOAS_VINDAS_ID)
        if not canal:
            return

        mensagem_ia = await gerar_boas_vindas(member.display_name)

        embed = discord.Embed(color=0x57F287, timestamp=datetime.utcnow())
        embed.set_author(
            name=member.display_name,
            icon_url=member.display_avatar.url
        )
        embed.description = (
            f"{mensagem_ia}\n\n"
            f"📋 Para ter acesso ao servidor, vá até <#{CANAL_APRESENTACAO_ID}> "
            f"e se apresente para a galera!"
        )
        embed.set_footer(text=f"Membro #{guild.member_count}")

        await canal.send(content=member.mention, embed=embed)
        print(f"✅ Boas-vindas enviadas: {member.display_name}")
    except Exception as e:
        print(f"❌ Erro ao enviar boas-vindas: {e}")

# ═══════════════════════════════════════════════════════════════
#  CALLS TEMPORÁRIAS — PLANILHA
# ═══════════════════════════════════════════════════════════════
def get_calls_salvas():
    """Retorna {canal_id: nome} das calls salvas na planilha."""
    try:
        service = get_sheets_service()
        result  = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range="CallsTemp!A2:B"
        ).execute()
        return {int(r[0]): r[1] for r in result.get("values", []) if len(r) >= 2}
    except Exception as e:
        print(f"❌ Erro ao ler CallsTemp: {e}")
        return {}

def salvar_call(canal_id, nome):
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
        print(f"❌ Erro ao salvar call: {e}")

def remover_call(canal_id):
    try:
        service = get_sheets_service(readonly=False)
        result  = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range="CallsTemp!A2:A"
        ).execute()
        ids = [r[0] for r in result.get("values", []) if r]
        if str(canal_id) not in ids:
            return
        linha = ids.index(str(canal_id)) + 2
        service.spreadsheets().values().clear(
            spreadsheetId=SPREADSHEET_ID,
            range=f"CallsTemp!A{linha}:B{linha}"
        ).execute()
    except Exception as e:
        print(f"❌ Erro ao remover call: {e}")

# ═══════════════════════════════════════════════════════════════
#  CRIAR CALL — completamente isolado
# ═══════════════════════════════════════════════════════════════
@bot.event
async def on_voice_state_update(member, before, after):
    # ── Entrou no canal Criar Call ──
    if after.channel and after.channel.id == CANAL_CRIAR_CALL_ID:
        try:
            nome_canal = f"{member.display_name}'s call"
            novo_canal = await member.guild.create_voice_channel(
                name=nome_canal,
                category=after.channel.category,
                reason="Call temporária"
            )
            canais_temporarios[novo_canal.id] = True
            salvar_call(novo_canal.id, nome_canal)
            await member.move_to(novo_canal)
            print(f"✅ Call criada: {nome_canal} ({novo_canal.id})")
        except Exception as e:
            print(f"❌ Erro ao criar call: {e}")
        return

    # ── Saiu de uma call temporária ──
    if before.channel and before.channel.id in canais_temporarios:
        if len(before.channel.members) == 0:
            try:
                await before.channel.delete(reason="Call temporária vazia")
                canais_temporarios.pop(before.channel.id, None)
                remover_call(before.channel.id)
                print(f"🗑️ Call deletada: {before.channel.name}")
            except discord.NotFound:
                canais_temporarios.pop(before.channel.id, None)
                remover_call(before.channel.id)
            except Exception as e:
                print(f"❌ Erro ao deletar call: {e}")


@tasks.loop(minutes=5)
async def limpar_calls_vazias():
    """A cada 5 min: verifica planilha + canais de voz e deleta calls vazias."""
    await bot.wait_until_ready()
    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        return
    try:
        calls = get_calls_salvas()
        for canal_id, nome in list(calls.items()):
            canal = guild.get_channel(canal_id)
            if canal is None:
                # Canal já não existe — limpa da planilha
                canais_temporarios.pop(canal_id, None)
                remover_call(canal_id)
                print(f"🧹 Call removida da planilha (não existe mais): {nome}")
            elif len(canal.members) == 0:
                try:
                    await canal.delete(reason="Call vazia — limpeza automática")
                    print(f"🗑️ Call vazia deletada (limpeza): {nome}")
                except discord.NotFound:
                    pass
                canais_temporarios.pop(canal_id, None)
                remover_call(canal_id)
    except Exception as e:
        print(f"❌ Erro em limpar_calls_vazias: {e}")

# ═══════════════════════════════════════════════════════════════
#  REACTION ROLES — completamente isolado
# ═══════════════════════════════════════════════════════════════
@bot.event
async def on_raw_reaction_add(payload):
    if payload.channel_id != CANAL_CONFIG_ROLES_ID:
        return
    try:
        await handle_reaction(payload, adicionar=True)
    except Exception as e:
        print(f"❌ Erro reaction add: {e}")


@bot.event
async def on_raw_reaction_remove(payload):
    if payload.channel_id != CANAL_CONFIG_ROLES_ID:
        return
    try:
        await handle_reaction(payload, adicionar=False)
    except Exception as e:
        print(f"❌ Erro reaction remove: {e}")


async def handle_reaction(payload, adicionar: bool):
    mapa    = carregar_reaction_roles()
    msg_map = mapa.get(payload.message_id)
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
        print(f"✅ Role '{role.name}' → {member.display_name}")
    else:
        await member.remove_roles(role, reason="Reaction role removida")
        print(f"➖ Role '{role.name}' ← {member.display_name}")

# ═══════════════════════════════════════════════════════════════
#  TASK: YOUTUBE — completamente isolada
# ═══════════════════════════════════════════════════════════════
@tasks.loop(minutes=5)
async def checar_youtube():
    global primeira_checagem, lives_yt_ativas, videos_vistos
    await bot.wait_until_ready()
    try:
        guild = bot.guilds[0] if bot.guilds else None
        if not guild:
            return

        canal        = guild.get_channel(CANAL_DIVULGACAO_ID)
        cargo_stream = discord.utils.get(guild.roles, name=CARGO_STREAMANDO_NOME)
        canais       = get_canais_youtube()

        async with aiohttp.ClientSession() as session:
            for entrada in canais:
                try:
                    channel_id  = entrada["channel_id"]
                    nome        = entrada["nome"]
                    discord_uid = entrada["discord_user_id"]

                    conteudo = await buscar_ultimo_conteudo(session, channel_id)
                    if not conteudo:
                        continue

                    vid_id  = conteudo["id"]
                    is_live = conteudo["is_live"]
                    membro  = guild.get_member(int(discord_uid)) if discord_uid else None

                    estava_em_live = lives_yt_ativas.get(channel_id) == "live"

                    # Cargo
                    if cargo_stream and membro and not membro.bot:
                        if is_live and not estava_em_live:
                            await membro.add_roles(cargo_stream, reason="YT Live iniciada")
                            print(f"🔴 Cargo adicionado (YT): {membro.display_name}")
                        elif not is_live and estava_em_live:
                            await membro.remove_roles(cargo_stream, reason="YT Live encerrada")
                            print(f"🔴 Cargo removido (YT): {membro.display_name}")

                    # Primeira checagem: só registra, não posta
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
                        if not estava_em_live and canal:
                            embed = build_embed_youtube_live(nome_exibir, conteudo, get_mention(guild, "youtube_live"), avatar_url)
                            await canal.send(embed=embed)
                            print(f"🔴 YT Live postada: {nome_exibir}")
                    else:
                        if videos_vistos.get(channel_id) == vid_id:
                            continue
                        videos_vistos[channel_id] = vid_id
                        salvar_json("videos_vistos.json", videos_vistos)
                        if canal:
                            embed = build_embed_youtube_video(nome_exibir, conteudo, get_mention(guild, "youtube_video"), avatar_url)
                            await canal.send(embed=embed)
                            print(f"📹 YT Vídeo postado: {nome_exibir}")

                except Exception as e:
                    print(f"❌ Erro YT canal {entrada.get('nome', '?')}: {e}")

        primeira_checagem = False

    except Exception as e:
        print(f"❌ Erro geral checar_youtube: {e}")

# ═══════════════════════════════════════════════════════════════
#  TASK: TWITCH — completamente isolada
# ═══════════════════════════════════════════════════════════════
@tasks.loop(minutes=5)
async def checar_twitch():
    global lives_twitch_ativas
    await bot.wait_until_ready()
    try:
        guild = bot.guilds[0] if bot.guilds else None
        if not guild:
            return

        canais = get_canais_twitch()
        if not canais:
            return

        canal        = guild.get_channel(CANAL_DIVULGACAO_ID)
        cargo_stream = discord.utils.get(guild.roles, name=CARGO_STREAMANDO_NOME)

        async with aiohttp.ClientSession() as session:
            lives_agora = await checar_lives_twitch_api(session, [c["twitch_id"] for c in canais])

        for entrada in canais:
            try:
                twitch_id   = entrada["twitch_id"]
                nome        = entrada["nome"]
                discord_uid = entrada["discord_user_id"]

                esta_live   = twitch_id in lives_agora
                estava_live = lives_twitch_ativas.get(twitch_id) == "live"
                membro      = guild.get_member(int(discord_uid)) if discord_uid else None

                # Cargo
                if cargo_stream and membro and not membro.bot:
                    if esta_live and not estava_live:
                        await membro.add_roles(cargo_stream, reason="Twitch Live iniciada")
                        print(f"🟣 Cargo adicionado (Twitch): {nome}")
                    elif not esta_live and estava_live:
                        await membro.remove_roles(cargo_stream, reason="Twitch Live encerrada")
                        print(f"🟣 Cargo removido (Twitch): {nome}")

                # Atualiza estado
                lives_twitch_ativas[twitch_id] = "live" if esta_live else "offline"
                salvar_json("lives_twitch_ativas.json", lives_twitch_ativas)

                # Posta embed
                if esta_live and not estava_live and canal:
                    dados       = lives_agora[twitch_id]
                    nome_exibir = membro.display_name if membro else nome
                    avatar_url  = membro.display_avatar.url if membro else None
                    embed       = build_embed_twitch(nome_exibir, dados, get_mention(guild, "twitch"), avatar_url)
                    await canal.send(embed=embed)
                    print(f"🟣 Twitch Live postada: {nome_exibir}")

            except Exception as e:
                print(f"❌ Erro Twitch canal {entrada.get('nome', '?')}: {e}")

    except Exception as e:
        print(f"❌ Erro geral checar_twitch: {e}")

# ═══════════════════════════════════════════════════════════════
#  TASK: LIMPAR CARGOS PRESOS — completamente isolada
# ═══════════════════════════════════════════════════════════════
@tasks.loop(minutes=10)
async def limpar_cargos_presos():
    await bot.wait_until_ready()
    try:
        guild = bot.guilds[0] if bot.guilds else None
        if not guild:
            return

        cargo_stream = discord.utils.get(guild.roles, name=CARGO_STREAMANDO_NOME)
        if not cargo_stream:
            return

        canais_yt     = {int(c["discord_user_id"]): c["channel_id"] for c in get_canais_youtube()}
        canais_twitch = {int(c["discord_user_id"]): c["twitch_id"]  for c in get_canais_twitch()}

        for membro in list(cargo_stream.members):
            try:
                if membro.bot:
                    await membro.remove_roles(cargo_stream, reason="Bot não deve ter cargo de stream")
                    print(f"🧹 Cargo removido do bot: {membro.display_name}")
                    continue

                channel_id = canais_yt.get(membro.id)
                twitch_id  = canais_twitch.get(membro.id)

                em_live_yt     = lives_yt_ativas.get(channel_id)    == "live" if channel_id else False
                em_live_twitch = lives_twitch_ativas.get(twitch_id) == "live" if twitch_id  else False

                if not em_live_yt and not em_live_twitch:
                    await membro.remove_roles(cargo_stream, reason="Não está mais streamando")
                    print(f"🧹 Cargo removido (preso): {membro.display_name}")

            except Exception as e:
                print(f"❌ Erro ao limpar cargo de {membro.display_name}: {e}")

    except Exception as e:
        print(f"❌ Erro geral limpar_cargos_presos: {e}")

# ═══════════════════════════════════════════════════════════════
#  COMANDO: !setup_roles — completamente isolado
# ═══════════════════════════════════════════════════════════════
@bot.command()
@commands.has_permissions(administrator=True)
async def setup_roles(ctx):
    if ctx.channel.id != CANAL_CONFIG_ROLES_ID:
        await ctx.send("❌ Use esse comando no canal de configuração de roles.", delete_after=5)
        return
    try:
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

    except Exception as e:
        print(f"❌ Erro setup_roles: {e}")
        await ctx.send(f"❌ Erro: {e}", delete_after=10)

# ═══════════════════════════════════════════════════════════════
#  RODAR
# ═══════════════════════════════════════════════════════════════
bot.run(TOKEN)
