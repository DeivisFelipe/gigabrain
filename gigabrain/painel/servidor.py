"""Servidor do painel visual (só biblioteca padrão, sem dependências).

Um painel só para todas as pastas de dados: o servidor procura, a partir de
uma pasta raiz, toda subpasta que tenha um gigabrain.db (dados/, dados-demo/,
dados-avaliacao/<provedor>/<projeto>/<modo>/...). A página escolhe qual ver
pelo parâmetro ?fonte=<caminho relativo>.

    GET /api/fontes                         pastas de dados encontradas
    GET /api/benchmark                      todos os avaliacao.json, para comparar
    GET /api/resumo?fonte=...               contagens e lista de conversas
    GET /api/eventos?fonte=...&conversa=    mensagens de uma conversa, em ordem
    GET /api/requisitos?fonte=...           requisitos + ligações
    GET /api/requisito/<id>?fonte=...       versões, ligações e trilha de um requisito
    GET /api/especialistas?fonte=...        registro de especialistas
    GET /api/especialista/<id>?fonte=...    arquivo atual + todas as versões
    GET /api/avaliacao?fonte=...            resultado contra o gabarito, se houver
"""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .. import conhecimento
from ..banco import Banco

PASTA = os.path.dirname(os.path.abspath(__file__))
IGNORAR = {".git", "venv", ".venv", "__pycache__", "node_modules", "conhecimento", "logs"}
PROFUNDIDADE_MAX = 5


def descobrir_fontes(raiz: str) -> list[dict]:
    """Toda pasta (até 5 níveis abaixo da raiz) que contém um gigabrain.db."""
    fontes = []
    for pasta, subpastas, arquivos in os.walk(raiz):
        rel = os.path.relpath(pasta, raiz).replace("\\", "/")
        nivel = 0 if rel == "." else rel.count("/") + 1
        subpastas[:] = sorted(s for s in subpastas if s not in IGNORAR) if nivel < PROFUNDIDADE_MAX else []
        if "gigabrain.db" not in arquivos:
            continue
        partes = [] if rel == "." else rel.split("/")
        fontes.append({
            "id": rel,
            "grupo": partes[0] if partes else os.path.basename(raiz),
            "rotulo": " / ".join(partes[1:]) or (partes[0] if partes else "."),
            "avaliacao": "avaliacao.json" in arquivos,
        })
    return fontes


def _ler_json(caminho: str) -> dict:
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def benchmark(raiz: str) -> dict:
    """Junta as métricas de todas as avaliações, para a aba Benchmark."""
    linhas, em_andamento = [], []
    for fonte in descobrir_fontes(raiz):
        if not fonte["id"].startswith("dados-avaliacao/"):
            continue
        arquivo = os.path.join(raiz, fonte["id"], "avaliacao.json")
        if not fonte["avaliacao"]:
            em_andamento.append(fonte["id"])
            continue
        try:
            av = _ler_json(arquivo)
        except (OSError, json.JSONDecodeError):
            em_andamento.append(fonte["id"])
            continue
        linhas.append({
            "fonte": fonte["id"],
            "provedor": av.get("provedor"),
            "projeto": av.get("projeto"),
            "titulo": av.get("titulo"),
            "modo": av.get("modo"),
            **av["metricas"],
        })
    return {"linhas": linhas, "em_andamento": em_andamento}


def _rotas(banco: Banco, pasta: str, partes: list[str], query: dict) -> object:
    if partes == ["avaliacao"]:
        arquivo = os.path.join(pasta, "avaliacao.json")
        return _ler_json(arquivo) if os.path.exists(arquivo) else {}
    if partes == ["resumo"]:
        requisitos = banco.listar_requisitos()
        return {
            "conversas": banco.listar_conversas(),
            "requisitos": len(requisitos),
            "por_status": {s: sum(r["status"] == s for r in requisitos) for s in {r["status"] for r in requisitos}},
            "especialistas": len(banco.listar_especialistas()),
            "ligacoes": len(banco.listar_ligacoes()),
        }
    if partes == ["eventos"]:
        conversa = (query.get("conversa") or [None])[0]
        eventos = banco.listar_eventos(conversa, limite=2000)
        return eventos if conversa else list(reversed(eventos))
    if partes == ["requisitos"]:
        return {"requisitos": banco.listar_requisitos(), "ligacoes": banco.listar_ligacoes()}
    if len(partes) == 2 and partes[0] == "requisito":
        req = banco.obter_requisito(partes[1])
        if not req:
            return None
        return {
            **req,
            "versoes": banco.versoes_requisito(req["id"]),
            "ligacoes": banco.listar_ligacoes(req["id"]),
            "trilha": banco.trilha(req["id"]),
        }
    if partes == ["especialistas"]:
        return banco.listar_especialistas()
    if len(partes) == 2 and partes[0] == "especialista":
        esp = banco.obter_especialista(partes[1])
        if not esp:
            return None
        return {**esp, "conteudo": conhecimento.ler(esp["arquivo"]), "versoes": banco.versoes_conhecimento(esp["id"])}
    return None


def criar_handler(raiz: str):
    trava = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # silencia o log padrão a cada requisição
            pass

        def _responder(self, status: int, corpo: bytes, tipo: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)

        def _json(self, dados: object) -> None:
            if dados is None:
                return self._responder(404, b'{"erro": "nao encontrado"}', "application/json")
            self._responder(200, json.dumps(dados, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                with open(os.path.join(PASTA, "index.html"), "rb") as f:
                    return self._responder(200, f.read(), "text/html; charset=utf-8")
            if not url.path.startswith("/api/"):
                return self._responder(404, b"nao encontrado", "text/plain")

            partes = [p for p in url.path.split("/") if p][1:]  # tira o "api"
            query = parse_qs(url.query)
            if partes == ["fontes"]:
                return self._json(descobrir_fontes(raiz))
            if partes == ["benchmark"]:
                return self._json(benchmark(raiz))

            # Só aceita fontes que existem de fato (evita sair da pasta raiz).
            fonte = (query.get("fonte") or [""])[0]
            validas = {f["id"] for f in descobrir_fontes(raiz)}
            if fonte not in validas:
                return self._json(None)
            pasta = os.path.join(raiz, fonte)
            with trava:
                banco = Banco(os.path.join(pasta, "gigabrain.db"))
                try:
                    dados = _rotas(banco, pasta, partes, query)
                finally:
                    banco.fechar()
            self._json(dados)

    return Handler


def servir(raiz: str, porta: int = 8765, abrir_navegador: bool = True) -> None:
    raiz = os.path.abspath(raiz)
    fontes = descobrir_fontes(raiz)
    servidor = ThreadingHTTPServer(("127.0.0.1", porta), criar_handler(raiz))
    url = f"http://127.0.0.1:{porta}/"
    print(f"Painel do GigaBrain em {url}  ({len(fontes)} pasta(s) de dados em {raiz})  Ctrl+C para parar.")
    if abrir_navegador:
        webbrowser.open(url)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
