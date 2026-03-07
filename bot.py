import os
import asyncio
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from email.utils import parsedate_to_datetime

import requests
import feedparser
import discord

from bs4 import BeautifulSoup
from discord import app_commands
from discord.ext import tasks
from deep_translator import GoogleTranslator
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
NEWS_CHANNEL_ID = os.getenv("DISCORD_NEWS_CHANNEL_ID")
ENABLE_AUTO_NEWS = os.getenv("ENABLE_AUTO_NEWS", "false").lower() == "true"

if not TOKEN:
    raise RuntimeError("Token não encontrado no arquivo .env")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
SENT_NEWS_FILE = DATA_DIR / "sent_news.json"

COIN_MAP = {
    "btc": "bitcoin",
    "bitcoin": "bitcoin",
    "eth": "ethereum",
    "ethereum": "ethereum",
    "sol": "solana",
    "solana": "solana",
    "link": "chainlink",
    "chainlink": "chainlink",
    "uni": "uniswap",
    "uniswap": "uniswap",
    "ldo": "lido-dao",
    "lido": "lido-dao",
    "xrp": "ripple",
    "ripple": "ripple",
    "doge": "dogecoin",
    "dogecoin": "dogecoin",
    "ada": "cardano",
    "cardano": "cardano",
    "avax": "avalanche-2",
    "avalanche": "avalanche-2",
    "arb": "arbitrum",
    "arbitrum": "arbitrum",
    "op": "optimism",
    "optimism": "optimism",
}

KEYWORDS_GERAIS = [
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "dogecoin",
    "defi", "etf", "sec", "stablecoin", "layer 2", "l2", "crypto", "cripto",
    "staking", "uniswap", "chainlink", "token", "altcoin", "airdrop", "web3",
    "arbitrum", "optimism", "base", "rollup", "fed", "treasury", "macro",
    "smart contract", "governance", "dao", "validator", "onchain", "wallet"
]

TOPIC_KEYWORDS = {
    "btc": ["bitcoin", "btc"],
    "bitcoin": ["bitcoin", "btc"],
    "eth": ["ethereum", "eth", "ether", "ethereum foundation"],
    "ethereum": ["ethereum", "eth", "ether", "ethereum foundation"],
    "sol": ["solana", "sol"],
    "solana": ["solana", "sol"],
    "defi": ["defi", "dex", "lending", "amm", "yield", "liquidity", "staking", "governance"],
    "etf": ["etf", "sec", "spot etf", "approval", "filing"],
    "macro": ["fed", "inflation", "treasury", "rates", "interest rates", "cpi", "liquidity"],
    "stablecoin": ["stablecoin", "usdt", "usdc", "dai", "rlusd", "payments"],
    "link": ["chainlink", "link", "oracle"],
    "uni": ["uniswap", "uni", "dex", "amm"],
    "xrp": ["xrp", "ripple"],
    "base": ["base", "coinbase layer 2", "base chain"],
    "l2": ["layer 2", "l2", "rollup", "arbitrum", "optimism", "base"],
    "web3": ["web3", "onchain", "wallet", "smart contract", "dao", "governance"],
}

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; XuxupiscoBot/2.0; +https://discord.com)"
}

SOURCE_FEEDS = [
    {
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "type": "media",
        "topics": ["all"],
    },
    {
        "name": "Cointelegraph",
        "url": "https://cointelegraph.com/rss",
        "type": "media",
        "topics": ["all"],
    },
    {
        "name": "Decrypt",
        "url": "https://decrypt.co/feed",
        "type": "media",
        "topics": ["all"],
    },
    {
        "name": "The Block",
        "url": "https://www.theblock.co/rss.xml",
        "type": "media",
        "topics": ["all"],
    },
    {
        "name": "Coinbase Blog",
        "url": "https://www.coinbase.com/blog.atom",
        "type": "official",
        "topics": ["all", "base", "l2", "stablecoin", "etf"],
    },
    {
        "name": "Uniswap Blog",
        "url": "https://blog.uniswap.org/rss.xml",
        "type": "official",
        "topics": ["all", "uni", "defi", "web3"],
    },
    {
        "name": "Chainlink Blog",
        "url": "https://blog.chain.link/feed/",
        "type": "official",
        "topics": ["all", "link", "web3"],
    },
    {
        "name": "Ethereum Foundation Blog",
        "url": "https://blog.ethereum.org/feed.xml",
        "type": "official",
        "topics": ["all", "eth", "ethereum", "staking", "web3"],
    },
]

TRANSLATOR = GoogleTranslator(source="auto", target="pt")

# Janela máxima para considerar notícia "recente"
MAX_NEWS_AGE_HOURS = 48


def carregar_links_enviados() -> set[str]:
    if not SENT_NEWS_FILE.exists():
        return set()

    try:
        with open(SENT_NEWS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data)
    except Exception:
        return set()


def salvar_links_enviados(links: set[str]) -> None:
    try:
        with open(SENT_NEWS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(links)), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Erro ao salvar histórico de notícias: {e}")


links_enviados = carregar_links_enviados()


def limpar_html(texto: str) -> str:
    texto = html.unescape(texto or "")
    texto = re.sub(r"<[^>]+>", "", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def normalizar_tema(tema: str) -> str:
    return (tema or "").strip().lower()


def dividir_texto_em_chunks(texto: str, limite: int = 3500) -> list[str]:
    texto = texto.strip()
    if len(texto) <= limite:
        return [texto]

    partes = []
    atual = ""

    for paragrafo in re.split(r"\n{2,}", texto):
        paragrafo = paragrafo.strip()
        if not paragrafo:
            continue

        if len(atual) + len(paragrafo) + 2 <= limite:
            atual = f"{atual}\n\n{paragrafo}".strip()
        else:
            if atual:
                partes.append(atual)
            atual = paragrafo

    if atual:
        partes.append(atual)

    return partes


def traduzir_texto(texto: str) -> str:
    if not texto:
        return texto

    try:
        return TRANSLATOR.translate(texto)
    except Exception:
        return texto


def traduzir_texto_longo(texto: str) -> str:
    if not texto:
        return texto

    partes = dividir_texto_em_chunks(texto, 3200)
    traduzidas = []

    for parte in partes:
        try:
            traduzidas.append(TRANSLATOR.translate(parte))
        except Exception:
            traduzidas.append(parte)

    return "\n\n".join(traduzidas).strip()


def noticia_relevante(titulo: str, resumo: str, tema: str = "") -> bool:
    texto = f"{titulo} {resumo}".lower()
    tema = normalizar_tema(tema)

    if tema:
        palavras = TOPIC_KEYWORDS.get(tema, [tema])
        return any(p.lower() in texto for p in palavras)

    return any(p.lower() in texto for p in KEYWORDS_GERAIS)


def obter_data_publicacao(entry) -> datetime | None:
    try:
        if getattr(entry, "published_parsed", None):
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    except Exception:
        pass

    try:
        if getattr(entry, "updated_parsed", None):
            return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
    except Exception:
        pass

    for campo in ["published", "updated", "pubDate"]:
        valor = getattr(entry, campo, None)
        if not valor:
            continue
        try:
            dt = parsedate_to_datetime(valor)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue

    return None


def calcular_idade_horas(data_publicacao: datetime | None) -> float | None:
    if not data_publicacao:
        return None

    agora = datetime.now(timezone.utc)
    diferenca = agora - data_publicacao
    return diferenca.total_seconds() / 3600


def noticia_esta_recente(data_publicacao: datetime | None, max_age_hours: int = MAX_NEWS_AGE_HOURS) -> bool:
    idade_horas = calcular_idade_horas(data_publicacao)

    if idade_horas is None:
        # Se a feed não trouxer data, mantemos como elegível,
        # mas sem bônus forte de recência.
        return True

    if idade_horas < 0:
        return True

    return idade_horas <= max_age_hours


def pontuacao_recencia(data_publicacao: datetime | None) -> int:
    idade_horas = calcular_idade_horas(data_publicacao)

    if idade_horas is None:
        return 0

    if idade_horas <= 6:
        return 12
    if idade_horas <= 12:
        return 9
    if idade_horas <= 24:
        return 6
    if idade_horas <= 48:
        return 3

    return -10


def formatar_data_publicacao(data_publicacao: datetime | None) -> str:
    if not data_publicacao:
        return "Data não informada"
    return data_publicacao.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


def pontuar_noticia(
    titulo: str,
    resumo: str,
    tema: str = "",
    source_type: str = "media",
    data_publicacao: datetime | None = None
) -> int:
    texto = f"{titulo} {resumo}".lower()
    score = 0

    for palavra in KEYWORDS_GERAIS:
        if palavra.lower() in texto:
            score += 1

    if tema:
        for palavra in TOPIC_KEYWORDS.get(normalizar_tema(tema), [tema]):
            if palavra.lower() in texto:
                score += 3

    fortes = [
        "etf", "sec", "approval", "approved", "hack", "lawsuit", "regulation",
        "institutional", "blackrock", "coinbase", "binance", "fed", "treasury",
        "governance", "proposal", "validator", "upgrade", "partnership"
    ]
    for palavra in fortes:
        if palavra in texto:
            score += 2

    if source_type == "official":
        score += 5
    elif source_type == "media":
        score += 2

    score += pontuacao_recencia(data_publicacao)

    return score


def feed_serve_para_tema(feed: dict, tema: str) -> bool:
    tema = normalizar_tema(tema)
    if not tema:
        return True

    topics = [t.lower() for t in feed.get("topics", ["all"])]
    return "all" in topics or tema in topics


def buscar_preco_sync(coin_id: str) -> dict:
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": coin_id,
        "vs_currencies": "usd,brl",
        "include_24hr_change": "true",
        "include_last_updated_at": "true",
    }

    resp = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    if coin_id not in data:
        raise ValueError("Moeda não encontrada.")

    return data[coin_id]


def buscar_top10_sync() -> list[dict]:
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 10,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "24h",
    }

    resp = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


def normalizar_url(url: str) -> str:
    if not url:
        return ""

    url = url.strip()
    url = re.sub(r"#.*$", "", url)
    return url


def extrair_texto_artigo(url: str) -> str:
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception:
        return ""

    content_type = resp.headers.get("Content-Type", "").lower()
    if "text/html" not in content_type:
        return ""

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return ""

    for tag in soup(["script", "style", "noscript", "svg", "form", "header", "footer", "nav", "aside"]):
        tag.decompose()

    candidatos = []

    article = soup.find("article")
    if article:
        candidatos.extend(article.find_all("p"))

    main = soup.find("main")
    if main:
        candidatos.extend(main.find_all("p"))

    if not candidatos:
        candidatos.extend(soup.find_all("p"))

    paragrafos = []
    vistos = set()

    for p in candidatos:
        texto = limpar_html(p.get_text(" ", strip=True))
        if len(texto) < 50:
            continue
        if texto in vistos:
            continue
        vistos.add(texto)
        paragrafos.append(texto)

    texto_final = "\n\n".join(paragrafos)
    texto_final = re.sub(r"\n{3,}", "\n\n", texto_final).strip()

    if len(texto_final) > 18000:
        texto_final = texto_final[:18000]

    return texto_final


def dividir_sentencas(texto: str) -> list[str]:
    texto = re.sub(r"\s+", " ", texto).strip()
    if not texto:
        return []
    sentencas = re.split(r"(?<=[\.\!\?])\s+", texto)
    return [s.strip() for s in sentencas if s.strip()]


def contar_palavras(texto: str) -> int:
    return len(re.findall(r"\b\w+\b", texto, flags=re.UNICODE))


def formatar_resumo_em_paragrafos(texto: str, sentencas_por_paragrafo: int = 2) -> str:
    sentencas = dividir_sentencas(texto)

    if not sentencas:
        return texto.strip()

    paragrafos = []
    bloco = []

    for sentenca in sentencas:
        bloco.append(sentenca)

        if len(bloco) >= sentencas_por_paragrafo:
            paragrafos.append(" ".join(bloco).strip())
            bloco = []

    if bloco:
        paragrafos.append(" ".join(bloco).strip())

    return "\n\n".join(paragrafos).strip()


def resumir_texto_extrativo(texto: str, tema: str = "", min_palavras: int = 300, max_palavras: int = 380) -> str:
    sentencas = dividir_sentencas(texto)

    if not sentencas:
        return texto.strip()

    texto_total = " ".join(sentencas)
    if contar_palavras(texto_total) <= max_palavras:
        return formatar_resumo_em_paragrafos(texto_total)

    tema = normalizar_tema(tema)
    palavras_tema = TOPIC_KEYWORDS.get(tema, [tema]) if tema else []
    palavras_tema = [p.lower() for p in palavras_tema if p]

    ranking = []

    for i, sentenca in enumerate(sentencas):
        s_lower = sentenca.lower()
        score = 0

        for palavra in KEYWORDS_GERAIS:
            if palavra.lower() in s_lower:
                score += 1

        for palavra in palavras_tema:
            if palavra in s_lower:
                score += 3

        if i < 5:
            score += 3
        elif i < 10:
            score += 1

        tamanho = contar_palavras(sentenca)
        if 12 <= tamanho <= 40:
            score += 2
        elif 8 <= tamanho <= 55:
            score += 1

        if any(x in s_lower for x in [
            "according", "announced", "said", "reported",
            "proposal", "update", "launch", "approval"
        ]):
            score += 1

        ranking.append((i, score, sentenca))

    ranking.sort(key=lambda x: (-x[1], x[0]))

    selecionadas = []
    indices = set()
    palavras = 0

    for i, score, sentenca in ranking:
        if i in indices:
            continue
        selecionadas.append((i, sentenca))
        indices.add(i)
        palavras += contar_palavras(sentenca)
        if palavras >= min_palavras:
            break

    if palavras < min_palavras:
        for i, sentenca in enumerate(sentencas):
            if i in indices:
                continue
            selecionadas.append((i, sentenca))
            indices.add(i)
            palavras += contar_palavras(sentenca)
            if palavras >= min_palavras:
                break

    selecionadas.sort(key=lambda x: x[0])

    resumo = " ".join(s for _, s in selecionadas).strip()

    while contar_palavras(resumo) > max_palavras:
        partes = dividir_sentencas(resumo)
        if len(partes) <= 1:
            break
        partes.pop()
        resumo = " ".join(partes).strip()

    return formatar_resumo_em_paragrafos(resumo)


def extrair_resumo_base(entry) -> str:
    candidatos = [
        getattr(entry, "summary", ""),
        getattr(entry, "description", ""),
    ]

    if hasattr(entry, "content") and entry.content:
        for item in entry.content:
            value = item.get("value", "")
            if value:
                candidatos.append(value)

    for c in candidatos:
        texto = limpar_html(c)
        if texto:
            return texto

    return ""


def obter_dominio(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def buscar_noticias_sync(limite: int = 1, tema: str = "", somente_novas: bool = False) -> list[dict]:
    noticias = []

    feeds_filtrados = [f for f in SOURCE_FEEDS if feed_serve_para_tema(f, tema)]

    for source in feeds_filtrados:
        try:
            feed = feedparser.parse(source["url"])
        except Exception:
            continue

        for entry in feed.entries[:30]:
            titulo_en = limpar_html(getattr(entry, "title", ""))
            resumo_feed_en = extrair_resumo_base(entry)
            link = normalizar_url(getattr(entry, "link", ""))
            data_publicacao = obter_data_publicacao(entry)

            if not titulo_en or not link:
                continue

            if somente_novas and link in links_enviados:
                continue

            if not noticia_relevante(titulo_en, resumo_feed_en, tema):
                continue

            if not noticia_esta_recente(data_publicacao, MAX_NEWS_AGE_HOURS):
                continue

            corpo_artigo_en = extrair_texto_artigo(link)
            base_resumo_en = corpo_artigo_en if corpo_artigo_en else resumo_feed_en

            if not base_resumo_en:
                continue

            resumo_en = resumir_texto_extrativo(
                texto=base_resumo_en,
                tema=tema,
                min_palavras=300,
                max_palavras=380
            )

            score = pontuar_noticia(
                titulo=titulo_en,
                resumo=f"{resumo_feed_en} {resumo_en}",
                tema=tema,
                source_type=source["type"],
                data_publicacao=data_publicacao
            )

            resumo_pt = traduzir_texto_longo(resumo_en)
            titulo_pt = traduzir_texto(titulo_en)

            noticias.append({
                "titulo_en": titulo_en,
                "titulo_pt": titulo_pt,
                "resumo_en": resumo_en,
                "resumo_pt": resumo_pt,
                "link": link,
                "score": score,
                "source_name": source["name"],
                "source_type": source["type"],
                "domain": obter_dominio(link),
                "word_count": contar_palavras(resumo_pt or resumo_en),
                "published_at": data_publicacao,
                "published_at_str": formatar_data_publicacao(data_publicacao),
                "age_hours": calcular_idade_horas(data_publicacao),
            })

    unicas = []
    vistos = set()

    for n in noticias:
        chave = n["link"]
        if chave in vistos:
            continue
        vistos.add(chave)
        unicas.append(n)

    unicas.sort(
        key=lambda x: (
            x["score"],
            -(x["age_hours"] if x["age_hours"] is not None else 999999),
            x["word_count"]
        ),
        reverse=True
    )
    return unicas[:limite]


def formatar_variacao(valor) -> str:
    if valor is None:
        return "N/D"
    return f"{valor:.2f}%"


def limitar_embed_texto(texto: str, limite: int = 3800) -> str:
    texto = (texto or "").strip()
    if len(texto) <= limite:
        return texto

    texto_cortado = texto[:limite - 3].rstrip()

    ultimo_paragrafo = texto_cortado.rfind("\n\n")
    if ultimo_paragrafo > 2000:
        texto_cortado = texto_cortado[:ultimo_paragrafo].rstrip()

    return texto_cortado + "..."


def embed_noticia(item: dict, tema: str = "") -> discord.Embed:
    titulo = item.get("titulo_pt") or item.get("titulo_en") or "Notícia"
    resumo = item.get("resumo_pt") or item.get("resumo_en") or "Sem resumo."
    link = item.get("link", "")
    source_name = item.get("source_name", "Fonte não identificada")
    source_type = item.get("source_type", "media")
    domain = item.get("domain", "")
    word_count = item.get("word_count", 0)
    published_at_str = item.get("published_at_str", "Data não informada")

    tipo_fonte = "Oficial" if source_type == "official" else "Mídia especializada"

    embed = discord.Embed(
        title=titulo[:256],
        description=limitar_embed_texto(resumo, 3800),
        url=link
    )

    if tema:
        embed.add_field(name="Tema", value=tema.upper(), inline=True)

    embed.add_field(name="Fonte", value=source_name[:1024], inline=True)
    embed.add_field(name="Tipo", value=tipo_fonte, inline=True)
    embed.add_field(name="Publicada em", value=published_at_str[:1024], inline=True)
    embed.add_field(name="Palavras no resumo", value=str(word_count), inline=True)

    if domain:
        embed.add_field(name="Domínio", value=domain[:1024], inline=False)

    embed.add_field(name="Link original", value=link[:1024], inline=False)
    embed.set_footer(text="Resumo extrativo traduzido automaticamente para português")
    return embed


@client.event
async def on_ready():
    try:
        await tree.sync()
        print(f"Bot online como {client.user}")
        print("Slash commands sincronizados com sucesso.")
    except Exception as e:
        print(f"Erro ao sincronizar slash commands: {e}")

    if ENABLE_AUTO_NEWS and NEWS_CHANNEL_ID:
        if not postar_noticias_automaticamente.is_running():
            postar_noticias_automaticamente.start()


@tree.command(name="preco", description="Mostra o preço de uma criptomoeda")
@app_commands.describe(moeda="Exemplo: btc, eth, sol, link, uni, ldo")
async def preco(interaction: discord.Interaction, moeda: str):
    await interaction.response.defer()

    coin_id = COIN_MAP.get(moeda.lower().strip(), moeda.lower().strip())

    try:
        info = await asyncio.to_thread(buscar_preco_sync, coin_id)
        usd = info.get("usd")
        brl = info.get("brl")
        var24 = info.get("usd_24h_change")
        last_updated = info.get("last_updated_at")

        atualizado = "N/D"
        if last_updated:
            atualizado = datetime.fromtimestamp(
                last_updated, tz=timezone.utc
            ).strftime("%d/%m/%Y %H:%M UTC")

        embed = discord.Embed(
            title=f"Cotação: {coin_id}",
            description="Dados em tempo real via CoinGecko"
        )
        embed.add_field(name="USD", value=f"${usd:,.4f}" if usd is not None else "N/D", inline=True)
        embed.add_field(name="BRL", value=f"R${brl:,.4f}" if brl is not None else "N/D", inline=True)
        embed.add_field(name="24h", value=formatar_variacao(var24), inline=True)
        embed.set_footer(text=f"Atualizado em {atualizado}")

        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"Erro ao consultar preço: {e}")


@tree.command(name="top10", description="Mostra as 10 maiores criptomoedas por market cap")
async def top10(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        moedas = await asyncio.to_thread(buscar_top10_sync)

        linhas = []
        for i, moeda in enumerate(moedas, start=1):
            nome = moeda.get("name", "N/D")
            simbolo = str(moeda.get("symbol", "")).upper()
            preco_atual = moeda.get("current_price")
            variacao_24h = moeda.get("price_change_percentage_24h")

            preco_txt = f"${preco_atual:,.2f}" if preco_atual is not None else "N/D"
            var_txt = formatar_variacao(variacao_24h)
            linhas.append(f"**{i}. {nome} ({simbolo})** — {preco_txt} | 24h: {var_txt}")

        embed = discord.Embed(
            title="Top 10 cripto por market cap",
            description="\n".join(linhas)
        )
        embed.set_footer(text="Dados em tempo real via CoinGecko")
        await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"Erro ao buscar top 10: {e}")


@tree.command(name="noticias", description="Busca notícias cripto/web3 relevantes em português")
@app_commands.describe(
    quantidade="Número de notícias",
    tema="Tema opcional: btc, eth, sol, defi, etf, macro, stablecoin, link, uni, xrp, base, l2, web3"
)
async def noticias(
    interaction: discord.Interaction,
    quantidade: app_commands.Range[int, 1, 10] = 1,
    tema: str = ""
):
    await interaction.response.defer()

    try:
        itens = await asyncio.to_thread(buscar_noticias_sync, quantidade, tema, False)

        if not itens:
            await interaction.followup.send("Não encontrei notícias relevantes e recentes agora.")
            return

        for item in itens[:quantidade]:
            await interaction.followup.send(embed=embed_noticia(item, tema))

    except Exception as e:
        await interaction.followup.send(f"Erro ao buscar notícias: {e}")


@tree.command(name="traduzir", description="Traduz um texto para português")
@app_commands.describe(texto="Texto para traduzir")
async def traduzir(interaction: discord.Interaction, texto: str):
    await interaction.response.defer()

    try:
        traducao = await asyncio.to_thread(traduzir_texto_longo, texto)
        await interaction.followup.send(traducao[:1900] if traducao else "Não foi possível traduzir.")
    except Exception as e:
        await interaction.followup.send(f"Erro ao traduzir: {e}")


@tasks.loop(minutes=60)
async def postar_noticias_automaticamente():
    if not NEWS_CHANNEL_ID:
        return

    try:
        canal_id = int(NEWS_CHANNEL_ID)
    except ValueError:
        print("DISCORD_NEWS_CHANNEL_ID inválido.")
        return

    canal = client.get_channel(canal_id)
    if canal is None:
        print("Canal de notícias não encontrado.")
        return

    try:
        itens = await asyncio.to_thread(buscar_noticias_sync, 1, "", True)

        if not itens:
            print("Nenhuma notícia nova e recente encontrada no ciclo atual.")
            return

        for item in itens:
            link = item.get("link")
            if not link or link in links_enviados:
                continue

            await canal.send(embed=embed_noticia(item))
            links_enviados.add(link)

        salvar_links_enviados(links_enviados)
        print(f"{len(itens)} notícia(s) processada(s) no autopost.")

    except Exception as e:
        print(f"Erro no autopost de notícias: {e}")


@postar_noticias_automaticamente.before_loop
async def before_postar_noticias():
    await client.wait_until_ready()


client.run(TOKEN)
