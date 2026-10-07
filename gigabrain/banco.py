"""Bancos locais (SQLite) do GigaBrain.

São três bases separadas, cada uma num arquivo da pasta de dados:

1. Repositório de Requisitos  (requisitos.db)
   - requisito:          o requisito "vivo" (id R1, R2..., status, versão atual)
   - requisito_versao:   cada versão aprovada, sem apagar as anteriores
   - requisito_ligacao:  ligações entre requisitos ("R2 refina R1")
2. Registro de Especialistas  (especialistas.db)
   - especialista:               quem existe e qual tema cobre
   - especialista_conhecimento:  cada versão do arquivo de conhecimento
3. Log  (log.db)
   - conversa:  cada sessão com o PO
   - evento:    cada mensagem JSON trocada entre os agentes

Ligações sempre se leem "origem <tipo> destino". Ex.: "R3 substitui R1".

Os três arquivos são abertos numa conexão só (ATTACH), então as consultas
continuam simples. Pastas antigas, com tudo num gigabrain.db, são migradas
automaticamente na primeira abertura.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata

from .mensagens import Mensagem, agora

TIPOS_LIGACAO = {
    "refina": "detalha o destino, que continua valendo",
    "divide": "é uma das partes em que o destino foi dividido",
    "junta": "resultou da junção do destino com outros requisitos",
    "substitui": "toma o lugar do destino, que fica obsoleto",
    "depende_de": "precisa do destino para funcionar",
    "conflita_com": "contradiz o destino; o PO precisa resolver",
}

# Ligações que mudam o status do requisito de destino.
STATUS_APOS_LIGACAO = {
    "substitui": "substituido",
    "divide": "dividido",
    "junta": "unido",
}

# Ligações que contam a "evolução" (de onde um requisito veio).
LIGACOES_DE_EVOLUCAO = ("refina", "divide", "junta", "substitui")

SCHEMA = """
CREATE TABLE IF NOT EXISTS log.conversa (
    id            TEXT PRIMARY KEY,
    iniciada_em   TEXT NOT NULL,
    encerrada_em  TEXT,
    modo          TEXT NOT NULL,           -- reuniao | pos_reuniao
    status        TEXT NOT NULL            -- aberta | aprovada | encerrada
);

CREATE TABLE IF NOT EXISTS main.requisito (
    id            TEXT PRIMARY KEY,        -- R1, R2, ...
    titulo        TEXT NOT NULL,
    tipo          TEXT NOT NULL,           -- negocio | enabler
    temas         TEXT NOT NULL,           -- JSON: ["pagamentos", ...]
    status        TEXT NOT NULL,           -- ativo | em_revisao | substituido | dividido | unido
    versao_atual  INTEGER NOT NULL,
    criado_em     TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS main.requisito_versao (
    requisito_id  TEXT NOT NULL REFERENCES requisito(id),
    versao        INTEGER NOT NULL,
    conteudo      TEXT NOT NULL,           -- JSON: titulo, historia, criterios_aceite...
    fontes        TEXT NOT NULL,           -- JSON: de onde veio cada informação
    motivo        TEXT,                    -- por que essa versão existe
    conversa_id   TEXT,                    -- conversa no log.db
    aprovado_em   TEXT NOT NULL,
    PRIMARY KEY (requisito_id, versao)
);

CREATE TABLE IF NOT EXISTS main.requisito_ligacao (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    origem_id     TEXT NOT NULL REFERENCES requisito(id),
    tipo          TEXT NOT NULL,
    destino_id    TEXT NOT NULL REFERENCES requisito(id),
    motivo        TEXT,
    conversa_id   TEXT,                    -- conversa no log.db
    criado_em     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS esp.especialista (
    id                  TEXT PRIMARY KEY,  -- slug do tema: pagamentos
    tema                TEXT NOT NULL,
    descricao           TEXT NOT NULL,
    arquivo             TEXT NOT NULL,     -- caminho do arquivo de conhecimento
    versao_conhecimento INTEGER NOT NULL DEFAULT 0,
    consultas           INTEGER NOT NULL DEFAULT 0,
    criado_em           TEXT NOT NULL,
    atualizado_em       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS esp.especialista_conhecimento (
    especialista_id TEXT NOT NULL REFERENCES especialista(id),
    versao          INTEGER NOT NULL,
    conteudo        TEXT NOT NULL,         -- o Markdown inteiro daquela versão
    hash            TEXT NOT NULL,
    motivo          TEXT NOT NULL,         -- criacao | requisito R3 aprovado | documento ...
    origem          TEXT NOT NULL,         -- JSON com detalhes do gatilho
    criado_em       TEXT NOT NULL,
    PRIMARY KEY (especialista_id, versao)
);

CREATE TABLE IF NOT EXISTS log.evento (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    mensagem_id  TEXT NOT NULL,
    conversa_id  TEXT,
    momento      TEXT NOT NULL,
    de           TEXT NOT NULL,
    para         TEXT NOT NULL,
    tipo         TEXT NOT NULL,
    conteudo     TEXT NOT NULL            -- JSON da mensagem
);

CREATE INDEX IF NOT EXISTS log.idx_evento_conversa ON evento(conversa_id);
CREATE INDEX IF NOT EXISTS main.idx_ligacao_origem ON requisito_ligacao(origem_id);
CREATE INDEX IF NOT EXISTS main.idx_ligacao_destino ON requisito_ligacao(destino_id);
"""

# apelido na conexão -> (arquivo, tabelas)
BASES = {
    "main": ("requisitos.db", ["requisito", "requisito_versao", "requisito_ligacao"]),
    "esp": ("especialistas.db", ["especialista", "especialista_conhecimento"]),
    "log": ("log.db", ["conversa", "evento"]),
}
ARQUIVO_ANTIGO = "gigabrain.db"


def slug(texto: str) -> str:
    """"Notificações de Pagamento" -> "notificacoes-de-pagamento"."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-") or "geral"


def _palavras(texto: str) -> set[str]:
    return {p for p in slug(texto).split("-") if len(p) > 3}


def existe_banco(pasta: str) -> bool:
    return any(os.path.exists(os.path.join(pasta, a)) for a in (BASES["main"][0], ARQUIVO_ANTIGO))


class Banco:
    def __init__(self, pasta: str):
        os.makedirs(pasta, exist_ok=True)
        self.pasta = pasta
        antigo = os.path.join(pasta, ARQUIVO_ANTIGO)
        migrar = os.path.exists(antigo) and not os.path.exists(os.path.join(pasta, BASES["main"][0]))

        self.con = sqlite3.connect(os.path.join(pasta, BASES["main"][0]), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        for apelido, (arquivo, _) in BASES.items():
            if apelido != "main":
                self.con.execute(f"ATTACH DATABASE ? AS {apelido}", (os.path.join(pasta, arquivo),))
        self.con.execute("PRAGMA foreign_keys = ON")
        # WAL + synchronous NORMAL: cada evento do log vira uma transação, e sem
        # isso o Windows espera o disco a cada mensagem (lento com centenas delas).
        for apelido in BASES:
            self.con.execute(f"PRAGMA {apelido}.journal_mode = WAL")
            self.con.execute(f"PRAGMA {apelido}.synchronous = NORMAL")
        self.con.executescript(SCHEMA)
        if migrar:
            self._migrar(antigo)

    def _migrar(self, antigo: str) -> None:
        """Copia uma pasta do formato antigo (tudo em gigabrain.db) para os três arquivos."""
        # Conexão separada e fechada antes de renomear: no Windows, um arquivo
        # aberto (mesmo só para leitura) não pode ser renomeado.
        velho = sqlite3.connect(antigo)
        try:
            with self.con:
                for apelido, (_, tabelas) in BASES.items():
                    for tabela in tabelas:
                        linhas = velho.execute(f"SELECT * FROM {tabela}").fetchall()
                        if linhas:
                            marcas = ", ".join("?" * len(linhas[0]))
                            self.con.executemany(f"INSERT INTO {apelido}.{tabela} VALUES ({marcas})", linhas)
        finally:
            velho.close()
        os.replace(antigo, antigo + ".migrado")
        for sufixo in ("-wal", "-shm"):
            if os.path.exists(antigo + sufixo):
                os.remove(antigo + sufixo)

    def fechar(self) -> None:
        self.con.close()

    def _todos(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(linha) for linha in self.con.execute(sql, params)]

    def _um(self, sql: str, params: tuple = ()) -> dict | None:
        linha = self.con.execute(sql, params).fetchone()
        return dict(linha) if linha else None

    # ------------------------------------------------------------------ conversas

    def iniciar_conversa(self, modo: str) -> str:
        id_ = "c-" + agora().replace(":", "").replace("-", "").replace("T", "-")
        # Duas conversas no mesmo segundo (ex.: testes) ganham sufixo.
        n = 1
        base = id_
        while self._um("SELECT id FROM conversa WHERE id = ?", (id_,)):
            n += 1
            id_ = f"{base}-{n}"
        with self.con:
            self.con.execute(
                "INSERT INTO conversa (id, iniciada_em, modo, status) VALUES (?, ?, ?, 'aberta')",
                (id_, agora(), modo),
            )
        return id_

    def encerrar_conversa(self, id_: str, status: str) -> None:
        with self.con:
            self.con.execute(
                "UPDATE conversa SET encerrada_em = ?, status = ? WHERE id = ?",
                (agora(), status, id_),
            )

    def listar_conversas(self) -> list[dict]:
        return self._todos(
            """SELECT c.*, (SELECT COUNT(*) FROM evento e WHERE e.conversa_id = c.id) AS eventos
               FROM conversa c ORDER BY c.iniciada_em DESC"""
        )

    # ------------------------------------------------------------------ eventos (log)

    def registrar_evento(self, msg: Mensagem) -> None:
        with self.con:
            self.con.execute(
                """INSERT INTO evento (mensagem_id, conversa_id, momento, de, para, tipo, conteudo)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (msg.id, msg.conversa_id, msg.momento, msg.de, msg.para, msg.tipo,
                 json.dumps(msg.conteudo, ensure_ascii=False)),
            )

    def listar_eventos(self, conversa_id: str | None = None, limite: int = 500) -> list[dict]:
        if conversa_id:
            linhas = self._todos(
                "SELECT * FROM evento WHERE conversa_id = ? ORDER BY id LIMIT ?", (conversa_id, limite)
            )
        else:
            linhas = self._todos("SELECT * FROM evento ORDER BY id DESC LIMIT ?", (limite,))
        for linha in linhas:
            linha["conteudo"] = json.loads(linha["conteudo"])
        return linhas

    # ------------------------------------------------------------------ requisitos

    def _proximo_id_requisito(self) -> str:
        linha = self._um("SELECT MAX(CAST(SUBSTR(id, 2) AS INTEGER)) AS n FROM requisito")
        return f"R{(linha['n'] or 0) + 1}"

    def salvar_requisito(
        self,
        conteudo: dict,
        conversa_id: str | None,
        base_id: str | None = None,
        motivo: str | None = None,
        ligacoes: list[dict] | None = None,
        fontes: list[dict] | None = None,
    ) -> dict:
        """Salva um requisito aprovado.

        - sem `base_id`: cria um requisito novo (R<n>, versão 1)
        - com `base_id`: cria a próxima versão daquele requisito

        Devolve {"requisito_id", "versao", "ligacoes", "em_revisao"}, onde
        `em_revisao` lista requisitos que dependem deste e precisam ser revistos.
        """
        momento = agora()
        temas = [slug(t) for t in conteudo.get("temas") or ["geral"]]
        titulo = conteudo.get("titulo") or "(sem título)"
        tipo = conteudo.get("tipo") or "negocio"
        em_revisao: list[str] = []

        with self.con:
            if base_id and self._um("SELECT id FROM requisito WHERE id = ?", (base_id,)):
                req_id = base_id
                versao = self._um("SELECT versao_atual FROM requisito WHERE id = ?", (req_id,))["versao_atual"] + 1
                self.con.execute(
                    """UPDATE requisito SET titulo = ?, tipo = ?, temas = ?, versao_atual = ?,
                       status = 'ativo', atualizado_em = ? WHERE id = ?""",
                    (titulo, tipo, json.dumps(temas), versao, momento, req_id),
                )
                # Quem depende da versão antiga precisa ser revisto.
                em_revisao += self._marcar_dependentes(req_id, momento)
            else:
                req_id = self._proximo_id_requisito()
                versao = 1
                self.con.execute(
                    """INSERT INTO requisito (id, titulo, tipo, temas, status, versao_atual, criado_em, atualizado_em)
                       VALUES (?, ?, ?, ?, 'ativo', 1, ?, ?)""",
                    (req_id, titulo, tipo, json.dumps(temas), momento, momento),
                )

            conteudo = {**conteudo, "temas": temas}
            self.con.execute(
                """INSERT INTO requisito_versao (requisito_id, versao, conteudo, fontes, motivo, conversa_id, aprovado_em)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (req_id, versao, json.dumps(conteudo, ensure_ascii=False),
                 json.dumps(fontes or [], ensure_ascii=False), motivo, conversa_id, momento),
            )

            salvas = []
            for lig in ligacoes or []:
                feita = self._inserir_ligacao(req_id, lig.get("tipo"), lig.get("alvo"), lig.get("motivo"), conversa_id, momento)
                if feita:
                    em_revisao += [r for r in feita.pop("em_revisao") if r not in em_revisao]
                    salvas.append(feita)

        return {"requisito_id": req_id, "versao": versao, "ligacoes": salvas, "em_revisao": em_revisao}

    def _inserir_ligacao(self, origem: str, tipo: str, destino: str, motivo: str | None,
                         conversa_id: str | None, momento: str) -> dict | None:
        if tipo not in TIPOS_LIGACAO or not destino or destino == origem:
            return None
        if not self._um("SELECT id FROM requisito WHERE id = ?", (destino,)):
            return None
        self.con.execute(
            """INSERT INTO requisito_ligacao (origem_id, tipo, destino_id, motivo, conversa_id, criado_em)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (origem, tipo, destino, motivo, conversa_id, momento),
        )
        em_revisao = []
        if tipo in STATUS_APOS_LIGACAO:
            self.con.execute(
                "UPDATE requisito SET status = ?, atualizado_em = ? WHERE id = ?",
                (STATUS_APOS_LIGACAO[tipo], momento, destino),
            )
            # O destino deixou de valer: quem dependia dele precisa ser revisto.
            em_revisao = self._marcar_dependentes(destino, momento, exceto=origem)
        return {"origem": origem, "tipo": tipo, "destino": destino, "em_revisao": em_revisao}

    def ligar(self, origem: str, tipo: str, destino: str, motivo: str | None = None,
              conversa_id: str | None = None) -> dict | None:
        """Cria uma ligação entre dois requisitos que já existem."""
        with self.con:
            return self._inserir_ligacao(origem, tipo, destino, motivo, conversa_id, agora())

    def _marcar_dependentes(self, requisito_id: str, momento: str, exceto: str | None = None) -> list[str]:
        """Coloca "em_revisao" quem tem `depende_de` apontando para o requisito."""
        marcados = []
        for dep in self._todos(
            "SELECT DISTINCT origem_id FROM requisito_ligacao WHERE destino_id = ? AND tipo = 'depende_de'",
            (requisito_id,),
        ):
            if dep["origem_id"] == exceto:
                continue
            self.con.execute(
                "UPDATE requisito SET status = 'em_revisao', atualizado_em = ? WHERE id = ? AND status = 'ativo'",
                (momento, dep["origem_id"]),
            )
            marcados.append(dep["origem_id"])
        return marcados

    def _montar_requisito(self, linha: dict) -> dict:
        linha["temas"] = json.loads(linha["temas"])
        versao = self._um(
            "SELECT * FROM requisito_versao WHERE requisito_id = ? AND versao = ?",
            (linha["id"], linha["versao_atual"]),
        )
        linha["conteudo"] = json.loads(versao["conteudo"])
        linha["fontes"] = json.loads(versao["fontes"])
        return linha

    def obter_requisito(self, id_: str) -> dict | None:
        linha = self._um("SELECT * FROM requisito WHERE id = ?", (id_,))
        return self._montar_requisito(linha) if linha else None

    def listar_requisitos(self, apenas_ativos: bool = False) -> list[dict]:
        sql = "SELECT * FROM requisito"
        if apenas_ativos:
            sql += " WHERE status IN ('ativo', 'em_revisao')"
        sql += " ORDER BY CAST(SUBSTR(id, 2) AS INTEGER)"
        return [self._montar_requisito(linha) for linha in self._todos(sql)]

    def versoes_requisito(self, id_: str) -> list[dict]:
        linhas = self._todos(
            "SELECT * FROM requisito_versao WHERE requisito_id = ? ORDER BY versao", (id_,)
        )
        for linha in linhas:
            linha["conteudo"] = json.loads(linha["conteudo"])
            linha["fontes"] = json.loads(linha["fontes"])
        return linhas

    def listar_ligacoes(self, requisito_id: str | None = None) -> list[dict]:
        if requisito_id:
            return self._todos(
                "SELECT * FROM requisito_ligacao WHERE origem_id = ? OR destino_id = ? ORDER BY id",
                (requisito_id, requisito_id),
            )
        return self._todos("SELECT * FROM requisito_ligacao ORDER BY id")

    def trilha(self, id_: str) -> list[dict]:
        """Responde "como chegamos neste requisito?".

        Segue as ligações de evolução para trás (R4 substitui R3, R3 divide
        R1...) e devolve os passos do mais antigo ao mais novo, incluindo as
        versões de cada requisito no caminho.
        """
        visitados: set[str] = set()
        passos: list[dict] = []
        fila = [id_]
        while fila:
            atual = fila.pop(0)
            if atual in visitados:
                continue
            visitados.add(atual)
            for versao in self.versoes_requisito(atual):
                passos.append({
                    "momento": versao["aprovado_em"],
                    "requisito_id": atual,
                    "evento": "versao",
                    "versao": versao["versao"],
                    "titulo": versao["conteudo"].get("titulo"),
                    "motivo": versao["motivo"],
                })
            for lig in self._todos(
                f"""SELECT * FROM requisito_ligacao WHERE origem_id = ?
                    AND tipo IN ({",".join("?" * len(LIGACOES_DE_EVOLUCAO))})""",
                (atual, *LIGACOES_DE_EVOLUCAO),
            ):
                passos.append({
                    "momento": lig["criado_em"],
                    "requisito_id": atual,
                    "evento": "ligacao",
                    "tipo": lig["tipo"],
                    "destino_id": lig["destino_id"],
                    "motivo": lig["motivo"],
                })
                fila.append(lig["destino_id"])
        # Empates no mesmo segundo: requisitos mais antigos (id menor) primeiro,
        # e a versão de um requisito antes das ligações que ele criou.
        return sorted(passos, key=lambda p: (
            p["momento"], int(p["requisito_id"][1:]), p["evento"] != "versao", p.get("versao", 0),
        ))

    def buscar_requisitos(self, texto: str, temas: list[str] | None = None, limite: int = 8) -> list[dict]:
        """Busca simples por palavras em comum e por tema (sem embeddings)."""
        procuradas = _palavras(texto)
        temas_slug = {slug(t) for t in temas or []}
        pontuados = []
        for req in self.listar_requisitos():
            c = req["conteudo"]
            texto_req = " ".join([c.get("titulo", ""), c.get("historia", ""), *c.get("criterios_aceite", [])])
            pontos = len(procuradas & _palavras(texto_req)) + 3 * len(temas_slug & set(req["temas"]))
            if pontos:
                pontuados.append((pontos, req))
        pontuados.sort(key=lambda p: -p[0])
        return [req for _, req in pontuados[:limite]]

    # ------------------------------------------------------------------ especialistas

    def criar_especialista(self, id_: str, tema: str, descricao: str, arquivo: str) -> dict:
        momento = agora()
        with self.con:
            self.con.execute(
                """INSERT INTO especialista (id, tema, descricao, arquivo, criado_em, atualizado_em)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (id_, tema, descricao, arquivo, momento, momento),
            )
        return self.obter_especialista(id_)

    def obter_especialista(self, id_: str) -> dict | None:
        return self._um("SELECT * FROM especialista WHERE id = ?", (id_,))

    def listar_especialistas(self) -> list[dict]:
        return self._todos("SELECT * FROM especialista ORDER BY tema")

    def contar_consulta(self, id_: str) -> None:
        with self.con:
            self.con.execute("UPDATE especialista SET consultas = consultas + 1 WHERE id = ?", (id_,))

    def registrar_conhecimento(self, id_: str, conteudo: str, motivo: str, origem: dict) -> int | None:
        """Guarda uma nova versão do conhecimento. Devolve a versão, ou None se nada mudou."""
        hash_ = hashlib.sha256(conteudo.encode("utf-8")).hexdigest()[:16]
        ultima = self._um(
            "SELECT hash FROM especialista_conhecimento WHERE especialista_id = ? ORDER BY versao DESC LIMIT 1",
            (id_,),
        )
        if ultima and ultima["hash"] == hash_:
            return None
        momento = agora()
        with self.con:
            versao = self._um("SELECT versao_conhecimento FROM especialista WHERE id = ?", (id_,))["versao_conhecimento"] + 1
            self.con.execute(
                """INSERT INTO especialista_conhecimento (especialista_id, versao, conteudo, hash, motivo, origem, criado_em)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (id_, versao, conteudo, hash_, motivo, json.dumps(origem, ensure_ascii=False), momento),
            )
            self.con.execute(
                "UPDATE especialista SET versao_conhecimento = ?, atualizado_em = ? WHERE id = ?",
                (versao, momento, id_),
            )
        return versao

    def datas_conhecimento(self, id_: str) -> list[str]:
        """Quando cada versão do conhecimento foi criada (para o painel reproduzir no tempo)."""
        return [l["criado_em"] for l in self._todos(
            "SELECT criado_em FROM especialista_conhecimento WHERE especialista_id = ? ORDER BY versao", (id_,))]

    def versoes_conhecimento(self, id_: str) -> list[dict]:
        linhas = self._todos(
            "SELECT * FROM especialista_conhecimento WHERE especialista_id = ? ORDER BY versao", (id_,)
        )
        for linha in linhas:
            linha["origem"] = json.loads(linha["origem"])
        return linhas
