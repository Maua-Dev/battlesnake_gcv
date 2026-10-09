# Bem-vindo ao
# __________         __    __  .__                               __
# \______   \_____ _/  |__/  |_|  |   ____   ______ ____ _____  |  | __ ____
#  |    |  _/\__  \   __\   __\  | _/ __ \ /  ___//    \__  \ |  |/ // __ \
#  |    |   \ / __ \|  |  |  | |  |_\  ___/ \___ \|   |  \/ __ \|    <\  ___/
#  |________/(______/__|  |__| |____/\_____>______>___|__(______/__|__\_____>
#
# Documentação: https://docs.battlesnake.com

import random
import logging
from collections import deque
from .models import GameState, MoveResponse

logger = logging.getLogger(__name__)
# O runtime Python da Lambda deixa o logger raiz em WARNING: sem esta linha
# as jogadas nao aparecem no CloudWatch.
logger.setLevel(logging.INFO)

DIRECOES = {
    "up": (0, 1),
    "down": (0, -1),
    "left": (-1, 0),
    "right": (1, 0),
}

# Ajustes do modo de ataque (só vale com 2 cobras no tabuleiro)
MARGEM_ATAQUE = 1   # quantos segmentos a mais precisamos ter para atacar
FOME_MINIMA = 40    # abaixo dessa vida, comer vem antes de atacar


def info() -> dict:
    """GET / : chamado quando você cadastra a cobra e a cada partida."""
    logger.info("INFO")
    return {
        "apiversion": "1",
        "author": "",          # TODO: coloque aqui o SEU usuário do Battlesnake
        "color": "#8B0000",
        "head": "tiger-king",
        "tail": "hook",
        "version": "1.1.0",
    }


def start(state: GameState) -> None:
    logger.info("JOGO COMEÇOU (partida %s)", state.game.id)


def end(state: GameState) -> None:
    logger.info("FIM DE JOGO após %d turnos", state.turn)


# ---------------------------------------------------------------------------
# Funções auxiliares
# ---------------------------------------------------------------------------

def proxima(pos, direcao):
    dx, dy = DIRECOES[direcao]
    return (pos[0] + dx, pos[1] + dy)


def dentro(pos, largura, altura):
    return 0 <= pos[0] < largura and 0 <= pos[1] < altura


def celulas_ocupadas(state):
    """Corpos de todas as cobras (inclusive a nossa).
    A cauda sai do lugar no próximo turno, então fica livre,
    a menos que a cobra tenha acabado de comer (cauda duplicada)."""
    ocupadas = set()
    for snake in state.board.snakes:
        corpo = [(p.x, p.y) for p in snake.body]
        if len(corpo) >= 3 and corpo[-1] != corpo[-2]:
            corpo = corpo[:-1]
        ocupadas.update(corpo)
    return ocupadas


def espaco_livre(inicio, ocupadas, largura, altura):
    """Flood fill: quantas casas dá para alcançar a partir de 'inicio'.
    Serve para não entrar em becos menores que a cobra."""
    vistos = {inicio}
    fila = deque([inicio])
    while fila:
        atual = fila.popleft()
        for d in DIRECOES:
            viz = proxima(atual, d)
            if dentro(viz, largura, altura) and viz not in ocupadas and viz not in vistos:
                vistos.add(viz)
                fila.append(viz)
    return len(vistos)


def distancia_ate(inicio, alvos, ocupadas, largura, altura):
    """BFS: menor número de passos até qualquer alvo, desviando dos corpos.
    Devolve None se nenhum alvo for alcançável."""
    if inicio in alvos:
        return 0
    vistos = {inicio}
    fila = deque([(inicio, 0)])
    while fila:
        atual, dist = fila.popleft()
        for d in DIRECOES:
            viz = proxima(atual, d)
            if viz in alvos:
                return dist + 1
            if dentro(viz, largura, altura) and viz not in ocupadas and viz not in vistos:
                vistos.add(viz)
                fila.append((viz, dist + 1))
    return None


# ---------------------------------------------------------------------------
# Jogada
# ---------------------------------------------------------------------------

def get_move(state: GameState) -> MoveResponse:
    """POST /move : chamado a cada turno. Devolve "up", "down", "left" ou "right"."""
    largura, altura = state.board.width, state.board.height
    eu = state.you
    cabeca = (eu.body[0].x, eu.body[0].y)
    meu_tamanho = len(eu.body)
    ocupadas = celulas_ocupadas(state)
    adversarias = [s for s in state.board.snakes if s.id != eu.id]

    # Casas para onde as cabeças adversárias podem ir neste turno.
    # Cabeça com cabeça: a menor morre, se empatar morrem as duas.
    perigosas = set()  # cobra igual ou maior pode chegar aqui
    presas = set()     # cobra menor pode chegar aqui
    for snake in adversarias:
        cab = (snake.body[0].x, snake.body[0].y)
        for d in DIRECOES:
            casa = proxima(cab, d)
            if len(snake.body) >= meu_tamanho:
                perigosas.add(casa)
            else:
                presas.add(casa)

    # 1. Jogadas que não matam na hora: parede, nosso corpo e corpo das outras
    seguras = [
        d for d in DIRECOES
        if dentro(proxima(cabeca, d), largura, altura)
        and proxima(cabeca, d) not in ocupadas
    ]

    if not seguras:
        fallback = random.choice(list(DIRECOES))
        logger.info("MOVE %d: sem saída! emergência -> %s", state.turn, fallback)
        return MoveResponse(move=fallback)

    # 2. Não entrar em espaço menor que o nosso tamanho (evita se trancar)
    espaco = {d: espaco_livre(proxima(cabeca, d), ocupadas, largura, altura) for d in seguras}
    com_folga = [d for d in seguras if espaco[d] >= meu_tamanho]
    candidatas = com_folga or [max(seguras, key=lambda d: espaco[d])]

    # 3. Evitar cabeça com cabeça contra cobra igual ou maior, se der
    sem_choque = [d for d in candidatas if proxima(cabeca, d) not in perigosas]
    if sem_choque:
        candidatas = sem_choque

    # 4. Objetivo: atacar (só com 2 cobras e sendo maior) ou comer
    modo_ataque = (
        len(state.board.snakes) == 2
        and len(adversarias) == 1
        and meu_tamanho >= len(adversarias[0].body) + MARGEM_ATAQUE
        and eu.health > FOME_MINIMA
    )

    if modo_ataque:
        # Ir para as casas onde a cabeça da adversária pode entrar
        alvos = {c for c in presas if dentro(c, largura, altura) and c not in ocupadas}
        modo = "ataque"
    else:
        alvos = {(f.x, f.y) for f in state.board.food}
        modo = "comida"

    escolha = None
    if alvos:
        distancias = {}
        for d in candidatas:
            dist = distancia_ate(proxima(cabeca, d), alvos, ocupadas, largura, altura)
            if dist is not None:
                distancias[d] = dist
        if distancias:
            menor = min(distancias.values())
            empatadas = [d for d, dist in distancias.items() if dist == menor]
            # No empate, fica com a que deixa mais espaço livre
            escolha = max(empatadas, key=lambda d: espaco[d])

    # Sem alvo alcançável: vai para onde tem mais espaço
    if escolha is None:
        escolha = max(candidatas, key=lambda d: espaco[d])
        modo = "espaço"

    logger.info("MOVE %d: %s (%s)", state.turn, escolha, modo)
    return MoveResponse(move=escolha)