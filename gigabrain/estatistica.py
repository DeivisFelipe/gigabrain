"""Estatística para comparar os modos (sem dependências externas).

Com várias repetições por projeto, cada modo ganha uma média por projeto. A
comparação entre dois modos é pareada pelo projeto:

- teste de Wilcoxon dos postos sinalizados (bicaudal, p exato para n <= 25)
- tamanho de efeito: correlação rank-biserial pareada (de -1 a 1)
  r > 0 quer dizer que o primeiro modo tende a ser maior
"""

from __future__ import annotations

import math
from itertools import combinations


def media(valores: list[float]) -> float | None:
    v = [x for x in valores if x is not None]
    return sum(v) / len(v) if v else None


def desvio(valores: list[float]) -> float | None:
    """Desvio-padrão amostral."""
    v = [x for x in valores if x is not None]
    if len(v) < 2:
        return 0.0 if v else None
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def _postos(valores: list[float]) -> list[float]:
    """Postos 1..n com média nos empates."""
    ordem = sorted(range(len(valores)), key=lambda i: valores[i])
    postos = [0.0] * len(valores)
    i = 0
    while i < len(ordem):
        j = i
        while j + 1 < len(ordem) and valores[ordem[j + 1]] == valores[ordem[i]]:
            j += 1
        for k in range(i, j + 1):
            postos[ordem[k]] = (i + j) / 2 + 1
        i = j + 1
    return postos


def wilcoxon_pareado(a: list[float], b: list[float]) -> dict:
    """Compara a e b pareados. Diferenças zero são descartadas (método de Wilcoxon)."""
    difs = [x - y for x, y in zip(a, b) if x is not None and y is not None and abs(x - y) > 1e-12]
    n = len(difs)
    if n == 0:
        return {"n": 0, "w_mais": 0.0, "w_menos": 0.0, "p": 1.0, "efeito": 0.0}
    postos = _postos([abs(d) for d in difs])
    w_mais = sum(r for r, d in zip(postos, difs) if d > 0)
    w_menos = sum(r for r, d in zip(postos, difs) if d < 0)
    total = n * (n + 1) / 2

    if n <= 25:
        # Distribuição exata de W+ sob H0 (sinais equiprováveis); postos dobrados viram inteiros.
        inteiros = [round(r * 2) for r in postos]
        dist = {0: 1}
        for r in inteiros:
            novo = dict(dist)
            for soma, c in dist.items():
                novo[soma + r] = novo.get(soma + r, 0) + c
            dist = novo
        casos = 2 ** n
        w = round(min(w_mais, w_menos) * 2)
        p = min(1.0, 2 * sum(c for soma, c in dist.items() if soma <= w) / casos)
    else:
        m = total / 2
        s = math.sqrt(n * (n + 1) * (2 * n + 1) / 24)
        z = (min(w_mais, w_menos) - m) / s
        p = min(1.0, 2 * 0.5 * math.erfc(-z / math.sqrt(2)))
    return {"n": n, "w_mais": w_mais, "w_menos": w_menos, "p": round(p, 4), "efeito": round((w_mais - w_menos) / total, 3)}


METRICAS_COMPARADAS = ["f1", "precisao", "revocacao", "classe", "subtipo",
                       "repeticoes_evitadas", "mudancas_capturadas", "mudancas_ligadas"]


def resumir(linhas: list[dict]) -> dict:
    """linhas: uma por (projeto, modo, repetição). Devolve médias por modo e comparações pareadas."""
    por_projeto: dict[tuple, dict[str, list]] = {}
    for l in linhas:
        chave = (l["projeto"], l["modo"])
        for m in METRICAS_COMPARADAS:
            if l.get(m) is not None:
                por_projeto.setdefault(chave, {}).setdefault(m, []).append(l[m])
    medias = {chave: {m: media(v) for m, v in ms.items()} for chave, ms in por_projeto.items()}
    modos = sorted({modo for _, modo in medias}, key=lambda m: ("conselho", "agente_unico", "llm_puro").index(m) if m in ("conselho", "agente_unico", "llm_puro") else 9)
    projetos = sorted({p for p, _ in medias})

    por_modo = {}
    for modo in modos:
        por_modo[modo] = {}
        for m in METRICAS_COMPARADAS:
            valores = [medias[(p, modo)].get(m) for p in projetos if (p, modo) in medias]
            valores = [v for v in valores if v is not None]
            dentro = [desvio(por_projeto[(p, modo)].get(m, [])) for p in projetos if (p, modo) in por_projeto]
            if valores:
                por_modo[modo][m] = {
                    "media": round(media(valores), 4),
                    "desvio_entre_projetos": round(desvio(valores), 4),
                    "desvio_entre_repeticoes": round(media([d for d in dentro if d is not None]) or 0.0, 4),
                    "projetos": len(valores),
                }

    comparacoes = []
    for a, b in combinations(modos, 2):
        comuns = [p for p in projetos if (p, a) in medias and (p, b) in medias]
        for m in METRICAS_COMPARADAS:
            pares = [(medias[(p, a)].get(m), medias[(p, b)].get(m)) for p in comuns]
            pares = [(x, y) for x, y in pares if x is not None and y is not None]
            if len(pares) < 2:
                continue
            teste = wilcoxon_pareado([x for x, _ in pares], [y for _, y in pares])
            comparacoes.append({"a": a, "b": b, "metrica": m, "projetos": len(pares),
                                "media_a": round(media([x for x, _ in pares]), 4), "media_b": round(media([y for _, y in pares]), 4), **teste})
    return {"por_modo": por_modo, "comparacoes": comparacoes, "projetos": projetos, "modos": modos}
