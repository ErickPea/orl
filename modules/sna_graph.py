# -*- coding: utf-8 -*-
"""
Motor de Análisis de Redes Sociales y Círculo Cercano (SNA) para OSINT-Auto (Fase 5).

Construye un grafo dirigido y ponderado persona-a-persona a partir de interacciones
y comentarios almacenados en un caso:
  - Colapsa todos los handles del sujeto principal en el nodo Ego central.
  - Calcula el Índice de Cercanía (0–100) premiando reciprocidad bidireccional,
    frecuencia sostenida en múltiples días y peso por tipo de interacción.
  - Calcula centralidad de grado e intermediación (algoritmo exacto de Brandes).
  - Detecta comunidades (Label Propagation ponderado determinista + co-participación
    en hilos + etiquetado por tema dominante).
  - Construye un perfil verificable para cada vínculo con temas compartidos, tono
    predominante y citas literales de los comentarios intercambiados.
"""
import math
import re
from collections import Counter, defaultdict, deque
from datetime import datetime

from modules.text_analysis import (
    FULL_THEME_TAXONOMY,
    _norm,
    analyze_sentiment,
)

COMMUNITY_PALETTE = [
    "#38bdf8",  # cyan
    "#10b981",  # emerald
    "#a78bfa",  # violet
    "#f59e0b",  # amber
    "#f43f5e",  # rose
    "#2dd4bf",  # teal
    "#fb923c",  # orange
    "#818cf8",  # indigo
]

_KIND_WEIGHT = {
    "reply": 1.8,
    "comment": 1.6,
    "collab": 1.5,
    "mention": 1.0,
    "co_thread": 0.6,
}


def _clean_handle(h: str) -> str:
    return (h or "").strip().lstrip("@").lower()


def _extract_day(raw_date: str) -> str:
    s = (raw_date or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return ""


def _themes_in_texts(texts: list) -> list:
    counts = Counter()
    for raw in texts or []:
        nt = _norm(raw)
        if not nt:
            continue
        for cat, kws in FULL_THEME_TAXONOMY.items():
            if any(re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", nt) for kw in kws):
                counts[cat] += 1
    return [{"label": k, "count": v} for k, v in counts.most_common(4)]


def _brandes_betweenness(nodes: list, adj: dict) -> dict:
    """
    Algoritmo exacto de Brandes para centralidad de intermediación (grafo no dirigido)
    normalizado a [0.0, 1.0]. Sin dependencias externas.
    """
    cb = {v: 0.0 for v in nodes}
    n = len(nodes)
    if n <= 2:
        return cb

    for s in nodes:
        stack = []
        pred = {w: [] for w in nodes}
        sigma = dict.fromkeys(nodes, 0.0)
        sigma[s] = 1.0
        dist = dict.fromkeys(nodes, -1)
        dist[s] = 0
        q = deque([s])

        while q:
            v = q.popleft()
            stack.append(v)
            for w in adj.get(v, ()):
                if dist[w] < 0:
                    q.append(w)
                    dist[w] = dist[v] + 1
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)

        delta = dict.fromkeys(nodes, 0.0)
        while stack:
            w = stack.pop()
            for v in pred[w]:
                if sigma[w] > 0:
                    delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                cb[w] += delta[w]

    # Normalizar para grafo no dirigido: dividir por (n-1)*(n-2)
    scale = float((n - 1) * (n - 2))
    if scale > 0:
        for v in cb:
            cb[v] = round(cb[v] / scale, 4)
    return cb


def _detect_communities(alters: list, peer_adj: dict, alter_themes: dict) -> dict:
    """
    Detecta comunidades entre las cuentas del entorno (excluyendo el Ego central
    para que no absorba toda la red en un único clúster trivial):
      - Conecta cuentas que interactúan entre sí o co-participan en hilos.
      - Añade afinidad suave si comparten un mismo tema dominante de conversación.
      - Aplica Label Propagation ponderado determinista (orden estable por nombre).
    Devuelve `{handle: community_index}`.
    """
    if not alters:
        return {}

    # Construir pesos efectivos entre alters
    eff_adj = {u: defaultdict(float) for u in alters}
    for u in alters:
        for v, w in (peer_adj.get(u) or {}).items():
            if v in eff_adj and v != u:
                eff_adj[u][v] += w

    # Afinidad temática suave para agrupar cuentas sin co-hilo directo pero mismo contexto
    theme_to_users = defaultdict(list)
    for u in alters:
        top_t = alter_themes.get(u) or []
        if top_t:
            theme_to_users[top_t[0]["label"]].append(u)
    for members in theme_to_users.values():
        if 2 <= len(members) <= 25:
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    a, b = members[i], members[j]
                    eff_adj[a][b] += 0.45
                    eff_adj[b][a] += 0.45

    labels = {u: u for u in sorted(alters)}
    ordered = sorted(alters)

    for _ in range(15):
        changed = False
        for u in ordered:
            nbrs = eff_adj.get(u)
            if not nbrs:
                continue
            score_by_lbl = defaultdict(float)
            for v, w in nbrs.items():
                score_by_lbl[labels[v]] += w
            # Elegir etiqueta de mayor peso (desempate determinista lexicográfico)
            best_lbl = sorted(score_by_lbl.items(), key=lambda x: (-x[1], x[0]))[0][0]
            if best_lbl != labels[u]:
                labels[u] = best_lbl
                changed = True
        if not changed:
            break

    # Compactar IDs de comunidad ordenados por tamaño descendente
    groups = defaultdict(list)
    for u, lbl in labels.items():
        groups[lbl].append(u)
    sorted_groups = sorted(groups.values(), key=lambda g: (-len(g), sorted(g)[0]))
    handle_to_cid = {}
    for idx, members in enumerate(sorted_groups):
        for m in members:
            handle_to_cid[m] = idx
    return handle_to_cid


def compute_visual_proximity_score(
    dhash_matches_count: int = 0,
    shared_active_days: int = 0,
    shared_ner_count: int = 0,
    stored_visual_score: float = 0.0,
) -> float:
    """
    Calcula puntaje adicional de cercanía visual/contextual (0-100) combinando:
      - Coincidencias dHash (hasta 55 pts)
      - Co-ocurrencia temporal (hasta 25 pts)
      - NER / temas compartidos (hasta 20 pts)
    No reemplaza el Índice de Cercanía; lo complementa como evidencia adicional.
    """
    dhash_pts = min(55.0, float(max(0, int(dhash_matches_count))) * 22.0)
    time_pts = min(25.0, float(max(0, int(shared_active_days))) * 8.5)
    ner_pts = min(20.0, float(max(0, int(shared_ner_count))) * 6.5)
    computed = round(min(100.0, dhash_pts + time_pts + ner_pts), 1)
    return round(max(computed, min(100.0, float(stored_visual_score or 0.0))), 1)


def _summarize_relational_cues(cues: list) -> dict:
    """Resume señales de lenguaje relacional asociadas a un nodo sin afirmar relaciones definitivas."""
    if not cues:
        return {
            "total_signals": 0,
            "validated_count": 0,
            "explicit_count": 0,
            "ambiguous_count": 0,
            "inferred_count": 0,
            "categories_observed": [],
            "cues": [],
        }
    cats = list(dict.fromkeys(c["category"] for c in cues if c.get("category")))
    return {
        "total_signals": len(cues),
        "validated_count": sum(1 for c in cues if c.get("review_status") == "validated"),
        "explicit_count": sum(1 for c in cues if c.get("epistemic_type") == "explicit"),
        "ambiguous_count": sum(1 for c in cues if c.get("epistemic_type") == "ambiguous"),
        "inferred_count": sum(1 for c in cues if c.get("epistemic_type") == "model_inference"),
        "categories_observed": cats,
        "cues": cues[:8],
    }


def build_case_sna(inputs: dict) -> dict:
    """
    Construye el análisis SNA completo a partir de:
      - `subject_name`: nombre legible del sujeto
      - `subject_handles`: lista de handles que pertenecen al sujeto (se colapsan al nodo Ego)
      - `interactions`: filas de `interactions` del caso
      - `comments`: filas de `comments` del caso
      - `posts`: filas de `posts` del caso
    """
    subject_name = (inputs.get("subject_name") or "").strip()
    raw_handles = [_clean_handle(h) for h in (inputs.get("subject_handles") or []) if _clean_handle(h)]
    subject_handles = set(raw_handles)
    ego_id = raw_handles[0] if raw_handles else (_clean_handle(subject_name) or "sujeto")
    ego_label = subject_name or (f"@{ego_id}" if ego_id else "Sujeto")

    def canonical(h: str) -> str:
        c = _clean_handle(h)
        if not c or c in ("desconocido", "[deleted]", "automoderator"):
            return ""
        if c in subject_handles:
            return ego_id
        # Ignorar comunidades r/... como personas salvo que no haya otros nodos
        return c

    interactions = inputs.get("interactions") or []
    comments = inputs.get("comments") or []
    relational_signals = inputs.get("relational_signals") or []

    # Mapear señales relacionales no descartadas a cada alter involucrado con el ego
    alter_rel_signals = defaultdict(list)
    for rs in relational_signals:
        if (rs.get("review_status") or "pending").lower() == "dismissed":
            continue
        r_src = canonical(rs.get("source_handle"))
        r_tgt = canonical(rs.get("target_handle"))
        alter_rel = ""
        if r_src == ego_id and r_tgt and r_tgt != ego_id:
            alter_rel = r_tgt
        elif r_tgt == ego_id and r_src and r_src != ego_id:
            alter_rel = r_src
        if alter_rel:
            alter_rel_signals[alter_rel].append({
                "id": rs.get("id"),
                "category": rs.get("category") or "",
                "subcategory": rs.get("subcategory") or "",
                "matched_expression": rs.get("matched_expression") or "",
                "epistemic_type": rs.get("epistemic_type") or "ambiguous",
                "ambiguity_flags": rs.get("ambiguity_flags") or [],
                "confidence": rs.get("confidence") or "BAJA",
                "review_status": rs.get("review_status") or "pending",
                "context_excerpt": (rs.get("context_excerpt") or "")[:240],
                "evidence_url": rs.get("evidence_url") or "",
                "detected_at": rs.get("detected_at") or "",
            })

    # Acumuladores dirigidos: (src, tgt) -> {weight, count, kinds: Counter, platforms: set}
    dir_edges = defaultdict(lambda: {"weight": 0.0, "count": 0, "kinds": Counter(), "platforms": set()})
    # Evidencia por alter (usuario distinto de ego)
    alter_comments = defaultdict(list)  # alter -> list of {direction, author, target, content, sentiment, date}
    alter_days = defaultdict(set)
    alter_sentiments = defaultdict(list)
    post_participants = defaultdict(set)
    alter_visual_db_score = defaultdict(float)
    subject_days = set()
    for p in (inputs.get("posts") or []):
        p_day = str(p.get("published_at") or p.get("captured_at") or "")[:10]
        if p_day and len(p_day) == 10:
            subject_days.add(p_day)

    # 1) Procesar tabla `interactions`
    for ix in interactions:
        src = canonical(ix.get("source_handle"))
        tgt = canonical(ix.get("target_handle"))
        if not src or not tgt or src == tgt:
            continue
        vps = float(ix.get("visual_proximity_score") or 0.0)
        if vps > 0:
            if src != ego_id:
                alter_visual_db_score[src] = max(alter_visual_db_score[src], vps)
            if tgt != ego_id:
                alter_visual_db_score[tgt] = max(alter_visual_db_score[tgt], vps)
        kind = (ix.get("kind") or "mention").strip().lower()
        ev = max(1, int(ix.get("evidence_count") or 1))
        w = float(ix.get("weight") or (_KIND_WEIGHT.get(kind, 1.0) * ev))
        ed = dir_edges[(src, tgt)]
        ed["weight"] += w
        ed["count"] += ev
        ed["kinds"][kind] += ev
        plat = (ix.get("metadata") or {}).get("platform")
        if plat:
            ed["platforms"].add(str(plat))

    # 2) Procesar tabla `comments` para evidencia literal, fechas, sentimiento y co-hilos
    for cm in comments:
        src = canonical(cm.get("author_handle"))
        tgt = canonical(cm.get("target_handle"))
        content = (cm.get("content") or "").strip()
        pub = cm.get("published_at") or ""
        day = _extract_day(pub)
        sent_label = (cm.get("sentiment") or "").strip().upper()
        if sent_label not in ("POSITIVO", "NEUTRO", "CRÍTICO"):
            sent_obj = analyze_sentiment(content)
            sent_label = sent_obj["label"]
            sent_score = sent_obj["score"]
        else:
            sent_score = 0.7 if sent_label == "POSITIVO" else (-0.7 if sent_label == "CRÍTICO" else 0.0)

        pid = cm.get("post_id")
        if pid and src and src != ego_id:
            post_participants[pid].add(src)

        if src and tgt and src != tgt:
            # Si no venía ya en `interactions`, asegurar arista mínima
            if (src, tgt) not in dir_edges:
                ed = dir_edges[(src, tgt)]
                ed["weight"] += _KIND_WEIGHT["comment"]
                ed["count"] += 1
                ed["kinds"]["comment"] += 1

        # Asociar evidencia al alter correspondiente si involucra al ego
        if src == ego_id and tgt and tgt != ego_id:
            alter = tgt
            direction = "Sujeto → Persona"
        elif tgt == ego_id and src and src != ego_id:
            alter = src
            direction = "Persona → Sujeto"
        else:
            alter = ""
            direction = ""

        if alter:
            if day:
                alter_days[alter].add(day)
            alter_sentiments[alter].append((sent_label, sent_score))
            if content:
                alter_comments[alter].append({
                    "direction": direction,
                    "author": cm.get("author_handle") or "",
                    "target": cm.get("target_handle") or "",
                    "content": content[:260],
                    "sentiment": sent_label,
                    "published_at": pub,
                })

    # 3) Co-participación en el mismo post (conecta terceros que comentan el mismo hilo)
    peer_adj = defaultdict(lambda: defaultdict(float))
    for participants in post_participants.values():
        plist = sorted(participants)
        if 2 <= len(plist) <= 20:
            for i in range(len(plist)):
                for j in range(i + 1, len(plist)):
                    a, b = plist[i], plist[j]
                    peer_adj[a][b] += _KIND_WEIGHT["co_thread"]
                    peer_adj[b][a] += _KIND_WEIGHT["co_thread"]

    # Recolectar todos los alters
    alters_set = set()
    for (s, t), ed in dir_edges.items():
        if s != ego_id:
            alters_set.add(s)
        if t != ego_id:
            alters_set.add(t)
        if s != ego_id and t != ego_id:
            peer_adj[s][t] += ed["weight"]
            peer_adj[t][s] += ed["weight"]

    alters = sorted(alters_set)
    all_node_ids = [ego_id, *alters]

    # Grafo no dirigido completo para centralidad de Brandes
    undirected_adj = defaultdict(set)
    for (s, t) in dir_edges:
        undirected_adj[s].add(t)
        undirected_adj[t].add(s)
    for a, nbrs in peer_adj.items():
        for b in nbrs:
            undirected_adj[a].add(b)
            undirected_adj[b].add(a)

    betweenness = _brandes_betweenness(all_node_ids, undirected_adj)

    # Temas por alter desde sus comentarios mutuos
    alter_themes = {
        u: _themes_in_texts([c["content"] for c in alter_comments.get(u, [])])
        for u in alters
    }

    # Detección de comunidades entre los alters
    handle_to_cid = _detect_communities(alters, peer_adj, alter_themes)

    # Calcular fuerza cruda de cada alter hacia/desde el ego
    raw_metrics = {}
    max_strength = 0.0
    for u in alters:
        out_ed = dir_edges.get((ego_id, u))
        in_ed = dir_edges.get((u, ego_id))
        out_w = out_ed["weight"] if out_ed else 0.0
        in_w = in_ed["weight"] if in_ed else 0.0
        out_c = out_ed["count"] if out_ed else 0
        in_c = in_ed["count"] if in_ed else 0

        # Si el nodo solo interactúa con terceros (grado 2), darle un pequeño peso base
        peer_w = sum(peer_adj.get(u, {}).values())
        total_w = out_w + in_w
        is_reciprocal = out_c > 0 and in_c > 0
        reciprocity = round((2.0 * min(out_w, in_w)) / total_w, 2) if total_w > 0 and is_reciprocal else 0.0
        active_days = len(alter_days.get(u, set()))

        raw_strength = (
            total_w * (1.0 + 0.85 * reciprocity)
            + 0.6 * max(0, active_days - 1)
            + 0.25 * peer_w
        )
        if raw_strength > max_strength:
            max_strength = raw_strength

        kinds = Counter()
        platforms = set()
        if out_ed:
            kinds.update(out_ed["kinds"])
            platforms.update(out_ed["platforms"])
        if in_ed:
            kinds.update(in_ed["kinds"])
            platforms.update(in_ed["platforms"])

        raw_metrics[u] = {
            "out_w": out_w,
            "in_w": in_w,
            "out_c": out_c,
            "in_c": in_c,
            "total_w": round(total_w, 2),
            "total_c": out_c + in_c,
            "is_reciprocal": is_reciprocal,
            "reciprocity": reciprocity,
            "active_days": active_days,
            "raw_strength": raw_strength,
            "kinds": dict(kinds),
            "platforms": sorted(platforms),
        }

    # Normalizar cercanía 0..100 (escala logarítmica suave para que los líderes destaquen sin aplastar al resto)
    denom = math.log1p(max_strength) if max_strength > 0 else 1.0

    # Construir metadatos de comunidades
    comm_members = defaultdict(list)
    for u, cid in handle_to_cid.items():
        comm_members[cid].append(u)

    communities = []
    comm_info_by_id = {}
    for cid, members in sorted(comm_members.items()):
        all_comm_texts = []
        comm_sents = []
        for m in members:
            all_comm_texts.extend(c["content"] for c in alter_comments.get(m, []))
            comm_sents.extend(alter_sentiments.get(m, []))
        dom_themes = _themes_in_texts(all_comm_texts)
        top_theme = dom_themes[0]["label"] if dom_themes else ""
        label = f"{top_theme}" if top_theme else f"Grupo #{cid + 1}"
        color = COMMUNITY_PALETTE[cid % len(COMMUNITY_PALETTE)]

        pos_n = sum(1 for lbl, _ in comm_sents if lbl == "POSITIVO")
        neg_n = sum(1 for lbl, _ in comm_sents if lbl == "CRÍTICO")
        neu_n = sum(1 for lbl, _ in comm_sents if lbl == "NEUTRO")
        dom_tone = "POSITIVO" if pos_n > neg_n and pos_n > 0 else ("CRÍTICO" if neg_n > pos_n and neg_n > 0 else "NEUTRO")

        info = {
            "id": cid,
            "label": label,
            "color": color,
            "size": len(members),
            "members": sorted(members, key=lambda x: -raw_metrics[x]["raw_strength"]),
            "top_themes": dom_themes,
            "dominant_tone": dom_tone,
            "sentiment_counts": {"positive": pos_n, "neutral": neu_n, "critical": neg_n},
        }
        communities.append(info)
        comm_info_by_id[cid] = info

    # Construir nodos enriquecidos y ranking del Círculo Cercano
    nodes = [
        {
            "id": ego_id,
            "label": ego_label,
            "is_subject": True,
            "tier": "SUJETO",
            "closeness_score": 100,
            "reciprocity": 1.0,
            "is_reciprocal": True,
            "total_interactions": sum(m["total_c"] for m in raw_metrics.values()),
            "sent_to_them": sum(m["out_c"] for m in raw_metrics.values()),
            "received_from_them": sum(m["in_c"] for m in raw_metrics.values()),
            "betweenness": betweenness.get(ego_id, 0.0),
            "degree": len(undirected_adj.get(ego_id, ())),
            "community_id": -1,
            "community_label": "Entidad Principal",
            "color": "#38bdf8",
            "shared_themes": [],
            "relationship_sentiment": {"dominant": "NEUTRO", "positive": 0, "neutral": 0, "critical": 0, "avg_score": 0.0},
            "interaction_types": {},
            "platforms": [],
            "active_days": 0,
            "sample_comments": [],
        }
    ]

    close_circle = []
    for u in alters:
        m = raw_metrics[u]
        if max_strength > 0:
            score = int(round((math.log1p(m["raw_strength"]) / denom) * 92.0))
            if m["is_reciprocal"]:
                score = min(100, score + 8)
        else:
            score = 0
        score = max(1, min(100, score)) if m["raw_strength"] > 0 else 0

        if score >= 70 and (m["is_reciprocal"] or m["total_c"] >= 2):
            tier = "INTIMO"
        elif score >= 40:
            tier = "CERCANO"
        else:
            tier = "PERIFERICO"

        cid = handle_to_cid.get(u, 0)
        cinfo = comm_info_by_id.get(cid, {"label": "General", "color": COMMUNITY_PALETTE[0]})
        sents = alter_sentiments.get(u, [])
        pos_n = sum(1 for lbl, _ in sents if lbl == "POSITIVO")
        neg_n = sum(1 for lbl, _ in sents if lbl == "CRÍTICO")
        neu_n = sum(1 for lbl, _ in sents if lbl == "NEUTRO")
        avg_s = round(sum(sc for _, sc in sents) / len(sents), 2) if sents else 0.0
        dom_tone = "POSITIVO" if pos_n > neg_n and pos_n > 0 else ("CRÍTICO" if neg_n > pos_n and neg_n > 0 else "NEUTRO")

        node_obj = {
            "id": u,
            "label": f"@{u}",
            "is_subject": False,
            "tier": tier,
            "closeness_score": score,
            "reciprocity": m["reciprocity"],
            "is_reciprocal": m["is_reciprocal"],
            "total_interactions": m["total_c"],
            "sent_to_them": m["out_c"],
            "received_from_them": m["in_c"],
            "total_weight": m["total_w"],
            "betweenness": betweenness.get(u, 0.0),
            "degree": len(undirected_adj.get(u, ())),
            "community_id": cid,
            "community_label": cinfo["label"],
            "color": cinfo["color"],
            "shared_themes": alter_themes.get(u) or [],
            "relationship_sentiment": {
                "dominant": dom_tone,
                "positive": pos_n,
                "neutral": neu_n,
                "critical": neg_n,
                "avg_score": avg_s,
            },
            "interaction_types": m["kinds"],
            "platforms": m["platforms"],
            "active_days": m["active_days"],
            "sample_comments": alter_comments.get(u, [])[:8],
            "relational_cues": _summarize_relational_cues(alter_rel_signals.get(u, [])),
            "visual_proximity_score": compute_visual_proximity_score(
                dhash_matches_count=0,
                shared_active_days=len(alter_days.get(u, set()) & subject_days),
                shared_ner_count=len(alter_themes.get(u) or []),
                stored_visual_score=alter_visual_db_score.get(u, 0.0),
            ),
        }
        nodes.append(node_obj)
        close_circle.append(node_obj)

    close_circle.sort(key=lambda x: (-x["closeness_score"], -x["total_interactions"], x["id"]))

    # Construir aristas consolidadas (combinando pares recíprocos para renderizado limpio)
    edges = []
    seen_pairs = set()
    for (s, t), ed in dir_edges.items():
        pair = (min(s, t), max(s, t))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        rev = dir_edges.get((t, s))
        bidirectional = rev is not None
        total_w = round(ed["weight"] + (rev["weight"] if rev else 0.0), 2)
        total_c = ed["count"] + (rev["count"] if rev else 0)
        kinds = Counter(ed["kinds"])
        if rev:
            kinds.update(rev["kinds"])
        edges.append({
            "source": s,
            "target": t,
            "weight": total_w,
            "count": total_c,
            "bidirectional": bidirectional,
            "kinds": dict(kinds),
            "edge_type": "direct",
        })

    # Añadir aristas de co-participación entre alters que no tengan ya arista directa
    for a, nbrs in peer_adj.items():
        for b, w in nbrs.items():
            pair = (min(a, b), max(a, b))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            edges.append({
                "source": a,
                "target": b,
                "weight": round(w, 2),
                "count": 1,
                "bidirectional": True,
                "kinds": {"co_thread": 1},
                "edge_type": "co_thread",
            })

    stats = {
        "nodes_count": len(nodes),
        "alters_count": len(alters),
        "edges_count": len(edges),
        "reciprocal_count": sum(1 for x in close_circle if x["is_reciprocal"]),
        "intimo_count": sum(1 for x in close_circle if x["tier"] == "INTIMO"),
        "cercano_count": sum(1 for x in close_circle if x["tier"] == "CERCANO"),
        "periferico_count": sum(1 for x in close_circle if x["tier"] == "PERIFERICO"),
        "communities_count": len(communities),
        "relational_nodes_count": sum(1 for x in close_circle if (x.get("relational_cues") or {}).get("total_signals", 0) > 0),
    }

    return {
        "ego_id": ego_id,
        "ego_label": ego_label,
        "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stats": stats,
        "nodes": nodes,
        "edges": edges,
        "close_circle": close_circle,
        "communities": communities,
    }
