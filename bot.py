import os
import asyncio
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests
import feedparser
import discord
from discord import app_commands
from discord.ext import tasks
from deep_translator import GoogleTranslator
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Opcional: coloque o ID do canal no .env para autopost
# Exemplo: DISCORD_NEWS_CHANNEL_ID=123456789012345678
NEWS_CHANNEL_ID = os.getenv("DISCORD_NEWS_CHANNEL_ID")

# Opcional: ativa/desativa autopost no .env
# Exemplo: ENABLE_AUTO_NEWS=true
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
    "arbitrum", "optimism", "base", "rollup", "fed", "treasury", "macro"
]

TOPIC_KEYWORDS = {
    "btc": ["bitcoin", "btc"],
    "bitcoin": ["bitcoin", "btc"],
    "eth": ["ethereum", "eth", "ether"],
    "ethereum": ["ethereum", "eth", "ether"],
    "sol": ["solana", "sol"],
    "solana": ["solana", "sol"],
    "defi": ["defi", "dex", "lending", "amm", "yield", "liquidity"],
    "etf": ["etf", "sec", "spot etf"],
    "macro": ["fed", "inflation", "treasury", "rates", "interest rates", "cpi"],
    "stablecoin": ["stablecoin", "usdt", "usdc", "dai", "rlusd"],
    "link": ["chainlink", "link"],
    "uni": ["uniswap", "uni"],
    "xrp": ["xrp", "ripple"],
    "base": ["base", "coinbase layer 2"],
    "l2": ["layer 2", "l2", "rollup", "arbitrum", "optimism", "base"],
}

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; XuxupiscoBot/1.0)"
}


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


def traduzir_texto(texto: str) -> str:
    if not texto:
        return texto

    try:
        return GoogleTranslator(source="auto", target="pt").translate(texto)
    except Exception:
        return texto


def limpar_html(texto: str) -> str:
    texto = html.unescape(texto or "")
    texto = re.sub(r"<[^>]+>", "", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def normalizar_tema(tema: str) -> str:
    return (tema or "").strip().lower()


def gerar_feeds_por_tema(tema: str = "") -> list[str]:
    tema = normalizar_tema(tema)

    if tema in TOPIC_KEYWORDS:
        consulta = " OR ".join(TOPIC_KEYWORDS[tema])
    elif tema:
        consulta = tema
    else:
        consulta = "crypto OR cryptocurrency OR bitcoin OR ethereum OR solana OR defi OR etf OR stablecoin"

    consulta_encoded = quote_plus(f"({consulta}) when:1d")

    return [
        f"https://news.google.com/rss/search?q={consulta_encoded}&hl=en-US&gl=US&ceid=US:en",
        f"https://news.google.com/rss/search?q={consulta_encoded}&hl=en-GB&gl=GB&ceid=GB:en",
    ]


def noticia_relevante(titulo: str, resumo: str, tema: str = "") -> bool:
    texto = f"{titulo} {resumo}".lower()
    tema = normalizar_tema(tema)

    if tema:
        palavras = TOPIC_KEYWORDS.get(tema, [tema])
        return any(p.lower() in texto for p in palavras)

    return any(p.lower() in texto for p in KEYWORDS_GERAIS)


def pontuar_noticia(titulo: str, resumo: str, tema: str = "") -> int:
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
        "institutional", "blackrock", "coinbase", "binance", "fed", "treasury"
    ]
    for palavra in fortes:
        if palavra in texto:
            score += 2

    return score


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


def buscar_noticias_sync(limite: int = 5, tema: str = "", somente_novas: bool = False) -> list[dict]:
    noticias = []
    feeds = gerar_feeds_por_tema(tema)

    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
        except Exception:
            continue

        for entry in feed.entries[:20]:
            titulo_en = limpar_html(getattr(entry, "title", ""))
            resumo_en = limpar_html(getattr(entry, "summary", ""))
            link = getattr(entry, "link", "").strip()

            if not titulo_en or not link:
                continue

            if somente_novas and link in links_enviados:
                continue

            if not noticia_relevante(titulo_en, resumo_en, tema):
                continue

            score = pontuar_noticia(titulo_en, resumo_en, tema)

            noticias.append({
                "titulo_en": titulo_en,
                "resumo_en": resumo_en[:400],
                "titulo_pt": traduzir_texto(titulo_en),
                "resumo_pt": traduzir_texto(resumo_en[:400]) if resumo_en else "",
                "link": link,
                "score": score,
            })

    unicas = []
    vistos = set()

    for n in noticias:
        if n["link"] in vistos:
            continue
        vistos.add(n["link"])
        unicas.append(n)

    unicas.sort(key=lambda x: x["score"], reverse=True)
    return unicas[:limite]


def formatar_variacao(valor) -> str:
    if valor is None:
        return "N/D"
    return f"{valor:.2f}%"


def embed_noticia(item: dict, tema: str = "") -> discord.Embed:
    titulo = item.get("titulo_pt") or item.get("titulo_en") or "Notícia"
    resumo = item.get("resumo_pt") or item.get("resumo_en") or "Sem resumo."
    link = item.get("link", "")

    embed = discord.Embed(
        title=titulo[:256],
        description=resumo[:1000] if resumo else "Sem resumo.",
        url=link
    )

    if tema:
        embed.add_field(name="Tema", value=tema.upper(), inline=True)

    embed.set_footer(text="Notícia cripto traduzida automaticamente para português")
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


@tree.command(name="noticias", description="Busca notícias cripto relevantes em português")
@app_commands.describe(
    quantidade="Número de notícias",
    tema="Tema opcional: btc, eth, sol, defi, etf, macro, stablecoin, link, uni, xrp, base, l2"
)
async def noticias(
    interaction: discord.Interaction,
    quantidade: app_commands.Range[int, 1, 10] = 5,
    tema: str = ""
):
    await interaction.response.defer()

    try:
        itens = await asyncio.to_thread(buscar_noticias_sync, quantidade, tema, False)

        if not itens:
            await interaction.followup.send("Não encontrei notícias relevantes agora.")
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
        traducao = await asyncio.to_thread(traduzir_texto, texto)
        await interaction.followup.send(traducao[:1900] if traducao else "Não foi possível traduzir.")
    except Exception as e:
        await interaction.followup.send(f"Erro ao traduzir: {e}")


@tasks.loop(minutes=30)
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
        itens = await asyncio.to_thread(buscar_noticias_sync, 5, "", True)

        if not itens:
            print("Nenhuma notícia nova encontrada no ciclo atual.")
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
