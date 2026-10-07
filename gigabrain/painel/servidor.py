"""Servidor do painel visual (só biblioteca padrão, sem dependências).

Serve a página index.html e uma API JSON somente leitura sobre o banco:

    GET /api/resumo                 contagens e lista de conversas
    GET /api/eventos?conversa=<id>  mensagens de uma conversa, em ordem
    GET /api/requisitos             requisitos + ligações
    GET /api/requisito/<id>         versões, ligações e trilha de um requisito
    GET /api/especialistas          registro de especialistas
    GET /api/especialista/<id>      arquivo atual + todas as versões
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


def _rotas(banco: Banco, caminho: str, query: dict) -> object:
    partes = [p for p in caminho.split("/") if p][1:]  # tira o "api"
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


def criar_handler(caminho_banco: str):
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

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                with open(os.path.join(PASTA, "index.html"), "rb") as f:
                    return self._responder(200, f.read(), "text/html; charset=utf-8")
            if url.path.startswith("/api/"):
                with trava:
                    banco = Banco(caminho_banco)
                    try:
                        dados = _rotas(banco, url.path, parse_qs(url.query))
                    finally:
                        banco.fechar()
                if dados is None:
                    return self._responder(404, b'{"erro": "nao encontrado"}', "application/json")
                corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
                return self._responder(200, corpo, "application/json; charset=utf-8")
            self._responder(404, b"nao encontrado", "text/plain")

    return Handler


def servir(pasta_dados: str, porta: int = 8765, abrir_navegador: bool = True) -> None:
    caminho_banco = os.path.join(pasta_dados, "gigabrain.db")
    if not os.path.exists(caminho_banco):
        print(f"Aviso: {caminho_banco} ainda não existe; o painel vai abrir vazio.")
        os.makedirs(pasta_dados, exist_ok=True)
    servidor = ThreadingHTTPServer(("127.0.0.1", porta), criar_handler(caminho_banco))
    url = f"http://127.0.0.1:{porta}/"
    print(f"Painel do GigaBrain em {url}  (dados: {os.path.abspath(pasta_dados)})  Ctrl+C para parar.")
    if abrir_navegador:
        webbrowser.open(url)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
