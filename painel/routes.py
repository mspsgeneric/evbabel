# painel/routes.py
from aiohttp import web
from .auth import require_auth
from evtranslator.db import list_glossario, upsert_glossario, delete_glossario



def _html(page_title: str, body: str, brand_hex: str) -> web.Response:
    return web.Response(
        text=f"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>{page_title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">

<style>
:root {{
  --bg: #2b2d31;
  --bg-elev: #313338;
  --bg-elev-2: #1e1f22;
  --text: #dbdee1;
  --muted: #a4a7ab;
  --brand: {brand_hex};
  --success: #23a55a;
  --danger: #f23f43;
  --border: #3f4147;
}}
* {{ box-sizing: border-box; }}
html, body {{ margin:0; padding:0; background:var(--bg); color:var(--text);
  font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, Noto Sans; }}
.container {{ max-width: 1100px; margin: 24px auto; padding: 0 16px; }}
h1 {{ font-size: 20px; font-weight: 700; margin: 0 0 16px; }}
.card {{ background: var(--bg-elev); border:1px solid var(--border); border-radius: 12px; padding: 16px; }}

/* Tabs */
.tabs {{ display:flex; gap:8px; margin-bottom:12px; }}
.tab {{ padding:8px 12px; border-radius:8px; border:1px solid var(--border);
  background:#3a3c43; color:var(--text); text-decoration:none; }}
.tab.active {{ background: var(--brand); border-color: transparent; }}

/* Listagem / ações */
.controls {{ display:flex; gap:8px; align-items:center; margin-bottom:12px; }}
.controls .search {{ flex:1; display:flex; gap:8px; }}
input[type=text], input[type=number], input[type=email] {{
  background:#1e1f22; color:var(--text); border:1px solid var(--border); border-radius:8px; padding:10px 12px; width:100%;
}}
button {{ font: inherit; }}
.btn {{ display:inline-flex; gap:6px; align-items:center; padding:10px 14px; border-radius:8px;
  border:1px solid var(--border); background:#3a3c43; color:var(--text); text-decoration:none; cursor:pointer; }}
.btn.primary {{ background: var(--brand); border-color: transparent; }}
.btn.secondary {{ background:#3a3c43; }}
.btn.danger {{ background:#3a3c43; border-color: rgba(242,63,67,.4); color:#ff7a7d; }}
.btn:hover {{ filter: brightness(1.05); }}
form.inline {{ display:inline; }}

/* Tabela */
.table-wrap {{ width:100%; overflow-x:auto; }}
table {{ width:100%; border-collapse: collapse; overflow: hidden; border-radius: 12px; min-width: 780px; }}
thead th {{ background: var(--bg-elev-2); font-weight:600; font-size:12px; letter-spacing:.3px; text-align:left; padding:10px;
  border-bottom:1px solid var(--border); white-space:nowrap; }}
tbody td {{ padding:10px; border-bottom:1px solid var(--border); vertical-align: middle; }}

/* Badges */
.badge {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; line-height: 18px; }}
.badge.on {{ background: rgba(35,165,90,.15); color: var(--success); border:1px solid rgba(35,165,90,.35); }}
.badge.off{{ background: rgba(242,63,67,.15); color: var(--danger);  border:1px solid rgba(242,63,67,.35); }}

/* Barra de uso */
.progress {{ position: relative; width: 160px; height: 8px; background:#202226; border-radius:999px; overflow:hidden; border:1px solid var(--border); }}
.progress > span {{ position:absolute; inset:0; width: var(--w,0%); background: linear-gradient(90deg, #4e5de2, var(--brand)); }}
.small {{ color: var(--muted); font-size: 12px; }}
hr {{ border:0; border-top:1px solid var(--border); margin: 12px 0; }}

/* Form */
.form {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
.form label {{ display: flex; flex-direction: column; gap: 6px; }}
.form .full {{ grid-column: 1 / -1; }}
.form .checkbox {{ flex-direction: row; align-items: center; gap: 8px; }}
.form .actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.form .actions .btn {{ flex: 1; text-align: center; }}

/* Mobile */
@media (max-width: 820px) {{
  .controls {{ flex-direction: column; align-items: stretch; }}
  .controls .search {{ width:100%; }}
  .controls .btn.primary {{ width:100%; justify-content:center; }}

  table, thead, tbody, th, tr, td {{ display:block; min-width: 0; }}
  thead {{ display:none; }}
  tbody tr {{ border:1px solid var(--border); border-radius:12px; margin-bottom:12px; background: var(--bg-elev); }}
  tbody td {{ border-bottom: 1px solid var(--border); display:flex; justify-content:space-between; gap:12px; padding:10px 12px; }}
  tbody td:last-child {{ border-bottom:none; }}
  tbody td::before {{ content: attr(data-label); color: var(--muted); font-size: 12px; min-width: 120px; }}
  .progress {{ width: 100%; }}
  .table-wrap {{ overflow: visible; }}
  .form {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="container">
  <div class="card">
    <h1>{page_title}</h1>
    {body}
  </div>
</div>
</body>
</html>""",
        content_type="text/html"
    )

def _ctx(request: web.Request):
    """Resolve bot, tabela, título e cor de marca."""
    bot = (request.query.get("bot") or "translator").strip().lower()
    if bot not in ("translator", "logger"):
        bot = "translator"

    if bot == "translator":
        return {
            "bot": "translator",
            "table": "emails_translator",
            "title": "EVbabel — Admin de Servidores",
            "brand": "#8b5cf6",  # roxo
        }
    else:
        return {
            "bot": "logger",
            "table": "emails",
            "title": "EVlogger — Admin de Servidores",
            "brand": "#22c55e",  # verde
        }


def _tabs(bot: str) -> str:
    gloss = ""
    if bot == "translator":
        gloss = f'<a class="tab" href="/admin/glossario?bot={bot}">Glossário</a>'
    return f"""
<div class="tabs">
  <a class="tab {'active' if bot=='logger' else ''}" href="/admin/guilds?bot=logger">EVlogger (logs)</a>
  <a class="tab {'active' if bot=='translator' else ''}" href="/admin/guilds?bot=translator">EVbabel (tradutor)</a>
  {gloss}
</div>
"""




def setup_painel_routes(app: web.Application, supabase, db_path: str, on_glossario_change=None):

    # ========= LISTAR =========
    async def admin_guilds_list(request: web.Request):
        require_auth(request)
        ctx = _ctx(request)
        bot, table, title, brand = ctx["bot"], ctx["table"], ctx["title"], ctx["brand"]
        q = (request.query.get("q") or "").strip().lower()

        # Select minimal por bot
        if bot == "translator":
            sel = "guild_id,guild_name,translate_enabled,char_limit,used_chars"
        else:
            sel = "guild_id,guild_name,email"

        rows = (supabase.table(table)
                .select(sel)
                .order("updated_at" if "updated_at" in sel else "created_at", desc=True)
                .limit(500)
                .execute()
                .data) or []

        if q:
            rows = [r for r in rows if q in (str(r.get("guild_id","")) + " " + str(r.get("guild_name",""))).lower()]

        trs = []
        for r in rows:
            gid = r.get("guild_id","")
            name = r.get("guild_name","") or ""

            if bot == "translator":
                enabled = bool(r.get("translate_enabled"))
                limit_  = int(r.get("char_limit") or 0)
                used    = int(r.get("used_chars") or 0)
                pct     = 0 if limit_ <= 0 else min(100, int(used * 100 / max(1, limit_)))
                trs.append(f"""
<tr>
  <td data-label="guild_id">{gid}</td>
  <td data-label="Nome">{name}</td>
  <td data-label="Tradutor"><span class="badge {'on' if enabled else 'off'}">{'Ativo' if enabled else 'Inativo'}</span></td>
  <td data-label="Limite">{limit_:,}</td>
  <td data-label="Carac. Usados">
    <div class="progress" style="--w:{pct}%"><span></span></div>
    <div class="small">{used:,} / {limit_:,} ({pct}%)</div>
  </td>
  <td data-label="Ações">
    <a class="btn" href="/admin/guilds/{gid}/edit?bot={bot}">Editar</a>
    <form class="inline" method="post" action="/admin/guilds/{gid}/delete?bot={bot}" onsubmit="return confirm('Remover este servidor?')">
      <button class="btn danger" type="submit">Excluir</button>
    </form>
  </td>
</tr>""")
            else:
                email = r.get("email") or "—"
                trs.append(f"""
<tr>
  <td data-label="guild_id">{gid}</td>
  <td data-label="Nome">{name}</td>
  <td data-label="Email log">{email}</td>
  <td data-label="Ações">
    <a class="btn" href="/admin/guilds/{gid}/edit?bot={bot}">Editar</a>
    <form class="inline" method="post" action="/admin/guilds/{gid}/delete?bot={bot}" onsubmit="return confirm('Remover este servidor?')">
      <button class="btn danger" type="submit">Excluir</button>
    </form>
  </td>
</tr>""")

        # Cabeçalhos por bot
        if bot == "translator":
            thead = "<tr><th>guild_id</th><th>Nome</th><th>Tradutor</th><th>Limite</th><th>Carac. Usados</th><th>Ações</th></tr>"
        else:
            thead = "<tr><th>guild_id</th><th>Nome</th><th>Email p/ log</th><th>Ações</th></tr>"

        body = f"""
{_tabs(bot)}
<form class="controls" method="get" action="/admin/guilds">
  <input type="hidden" name="bot" value="{bot}">
  <div class="search">
    <input type="text" name="q" value="{q}" placeholder="Buscar por ID ou nome..." />
    <button class="btn" type="submit">Buscar</button>
  </div>
  <a class="btn primary" href="/admin/guilds/new?bot={bot}">+ Novo</a>
</form>
<div class="table-wrap">
<table>
  <thead>{thead}</thead>
  <tbody>
    {''.join(trs) if trs else '<tr><td data-label="Info" class="small">Nenhum registro.</td></tr>'}
  </tbody>
</table>
</div>
"""
        return _html(title, body, brand)

    # ========= NOVO =========
    async def admin_guilds_new_get(request: web.Request):
        require_auth(request)
        ctx = _ctx(request)
        bot, table, title, brand = ctx["bot"], ctx["table"], ctx["title"], ctx["brand"]

        if bot == "translator":
            extra_fields = """
  <label class="checkbox full">
    <input type="checkbox" name="translate_enabled">
    <span>Habilitar tradutor</span>
  </label>
  <label class="full">
    <span>Limite de caracteres</span>
    <input name="char_limit" type="number" value="500000" min="0" step="1">
  </label>
"""
        else:
            extra_fields = """
  <label class="full">
    <span>Email para receber logs (opcional)</span>
    <input name="email" type="email" placeholder="ex.: meu-servidor@gmail.com">
  </label>
"""

        body = f"""
{_tabs(bot)}
<form method="post" class="form" action="/admin/guilds/new?bot={bot}">
  <label>
    <span>guild_id*</span>
    <input name="guild_id" required placeholder="ex.: 123456789012345678">
  </label>

  <label>
    <span>Nome (opcional)</span>
    <input name="guild_name" placeholder="ex.: Meu Servidor">
  </label>

  {extra_fields}

  <div class="actions full" style="margin-top:4px;">
    <button class="btn primary" type="submit">Salvar</button>
    <a class="btn secondary" href="/admin/guilds?bot={bot}">Voltar</a>
  </div>
</form>
"""
        return _html("Novo Servidor", body, brand)

    async def admin_guilds_new_post(request: web.Request):
        require_auth(request)
        ctx = _ctx(request)
        bot, table = ctx["bot"], ctx["table"]

        data = await request.post()
        gid = (data.get("guild_id") or "").strip()
        if not gid:
            return _html("Erro", f"<p>guild_id é obrigatório.</p><p><a class='btn' href='/admin/guilds/new?bot={bot}'>Voltar</a></p>", ctx["brand"])

        payload = {
            "guild_id": gid,
            "guild_name": (data.get("guild_name") or "").strip() or None,
        }

        if bot == "translator":
            payload.update({
                "translate_enabled": bool(data.get("translate_enabled")),
                "char_limit": int(data.get("char_limit") or 0),
            })
        else:
            # logger
            email = (data.get("email") or "").strip() or None
            payload.update({"email": email})

        try:
            supabase.table(table).insert(payload).execute()
        except Exception as e:
            return _html("Erro", f"<p>Erro ao inserir: {e}</p><p><a class='btn' href='/admin/guilds?bot={bot}'>Voltar</a></p>", ctx["brand"])
        raise web.HTTPFound(f"/admin/guilds?bot={bot}")

    # ========= EDITAR =========
    async def admin_guilds_edit_get(request: web.Request):
        require_auth(request)
        ctx = _ctx(request)
        bot, table, title, brand = ctx["bot"], ctx["table"], ctx["title"], ctx["brand"]
        gid = request.match_info["guild_id"]

        sel = "guild_id,guild_name,translate_enabled,char_limit,used_chars" if bot=="translator" else "guild_id,guild_name,email"
        row = (supabase.table(table)
               .select(sel)
               .eq("guild_id", gid).maybe_single().execute().data)
        if not row:
            return _html("Não encontrado", f"<p>Servidor não encontrado.</p><p><a class='btn' href='/admin/guilds?bot={bot}'>Voltar</a></p>", brand)

        if bot == "translator":
            used   = int(row.get("used_chars") or 0)
            limit_ = int(row.get("char_limit") or 0)
            pct    = 0 if limit_ <= 0 else min(100, int(used * 100 / max(1, limit_)))
            checked = "checked" if row.get("translate_enabled") else ""
            extra = f"""
  <label class="checkbox">
    <input type="checkbox" name="translate_enabled" {checked}>
    <span>Habilitar tradutor</span>
  </label>

  <label class="full">
    <span>Limite de caracteres</span>
    <input name="char_limit" type="number" value="{limit_}" min="0" step="1">
  </label>

  <label class="full">
    <span>Carac. Usados</span>
    <div class="progress" style="--w:{pct}%"><span></span></div>
    <div class="small">{used:,} / {limit_:,} ({pct}%)</div>
  </label>
"""
        else:
            extra = f"""
  <label class="full">
    <span>Email para receber logs (opcional)</span>
    <input name="email" type="email" value="{row.get('email') or ''}">
  </label>
"""

        body = f"""
{_tabs(bot)}
<form method="post" class="form" action="/admin/guilds/{gid}/edit?bot={bot}">
  <label class="full">
    <span>guild_id</span>
    <input value="{row.get('guild_id')}" disabled>
  </label>

  <label>
    <span>Nome</span>
    <input name="guild_name" value="{row.get('guild_name') or ''}">
  </label>

  {extra}

  <div class="actions full" style="margin-top:4px;">
    <button class="btn primary" type="submit">Salvar</button>
    <a class="btn secondary" href="/admin/guilds?bot={bot}">Voltar</a>
  </div>
</form>
"""
        return _html("Editar Servidor", body, brand)

    async def admin_guilds_edit_post(request: web.Request):
        require_auth(request)
        ctx = _ctx(request)
        bot, table = ctx["bot"], ctx["table"]
        gid = request.match_info["guild_id"]
        data = await request.post()

        payload = {
            "guild_name": (data.get("guild_name") or "").strip() or None,
        }

        if bot == "translator":
            payload.update({
                "translate_enabled": bool(data.get("translate_enabled")),
                "char_limit": int(data.get("char_limit") or 0),
            })
        else:
            payload.update({
                "email": (data.get("email") or "").strip() or None,
            })

        try:
            supabase.table(table).update(payload).eq("guild_id", gid).execute()
        except Exception as e:
            return _html("Erro", f"<p>Erro ao atualizar: {e}</p><p><a class='btn' href='/admin/guilds?bot={bot}'>Voltar</a></p>", ctx["brand"])
        raise web.HTTPFound(f"/admin/guilds?bot={bot}")

    # ========= EXCLUIR =========
    async def admin_guilds_delete_post(request: web.Request):
        require_auth(request)
        ctx = _ctx(request)
        bot, table = ctx["bot"], ctx["table"]
        gid = request.match_info["guild_id"]
        try:
            supabase.table(table).delete().eq("guild_id", gid).execute()
        except Exception as e:
            return _html("Erro", f"<p>Erro ao excluir: {e}</p><p><a class='btn' href='/admin/guilds?bot={bot}'>Voltar</a></p>", ctx["brand"])
        raise web.HTTPFound(f"/admin/guilds?bot={bot}")
    

        # ========= GLOSSÁRIO: LISTAR =========
    async def glossario_list(request: web.Request):
        require_auth(request)
        # força contexto visual do translator
        bot = "translator"
        brand = "#8b5cf6"

        q = (request.query.get("q") or "").strip().lower()
        only_enabled = (request.query.get("all") != "1")

        rows = await list_glossario(db_path, only_enabled=only_enabled)
        # rows: (id, termo_src, termo_dst, enabled, priority, updated_at, updated_by)

        if q:
            rows = [r for r in rows if q in (str(r[1]) + " " + str(r[2])).lower()]

        trs = []
        for (gid, src, dst, enabled, prio, _updated_at, _updated_by) in rows:
            badge = f"<span class='badge {'on' if enabled else 'off'}'>{'Ativo' if enabled else 'Inativo'}</span>"
            trs.append(f"""
<tr>
  <td data-label="ID">{gid}</td>
  <td data-label="Origem (EN)">{src}</td>
  <td data-label="Destino (PT)">{dst}</td>
  <td data-label="Status">{badge}</td>
  <td data-label="Prioridade">{prio}</td>
  <td data-label="Ações">
    <a class="btn" href="/admin/glossario/{gid}/edit?bot={bot}">Editar</a>
    <form class="inline" method="post" action="/admin/glossario/{gid}/delete?bot={bot}" onsubmit="return confirm('Excluir este termo?')">
      <button class="btn danger" type="submit">Excluir</button>
    </form>
  </td>
</tr>""")

        thead = "<tr><th>ID</th><th>Origem (EN)</th><th>Destino (PT)</th><th>Status</th><th>Prioridade</th><th>Ações</th></tr>"

        # destaca a aba "Glossário"
        tabbar = _tabs(bot).replace('>Glossário<', ' class="tab active">Glossário<')

        body = f"""
{tabbar}
<form class="controls" method="get" action="/admin/glossario">
  <input type="hidden" name="bot" value="{bot}">
  <div class="search">
    <input type="text" name="q" value="{q}" placeholder="Buscar por termo...">
    <button class="btn" type="submit">Buscar</button>
  </div>
  <div>
    <label class="small"><input type="checkbox" name="all" value="1" {'checked' if not only_enabled else ''} onchange="this.form.submit()"> Mostrar todos</label>
  </div>
  <a class="btn primary" href="/admin/glossario/new?bot={bot}">+ Novo termo</a>
</form>

<div class="table-wrap">
<table>
  <thead>{thead}</thead>
  <tbody>
    {''.join(trs) if trs else '<tr><td data-label="Info" class="small">Nenhum termo.</td></tr>'}
  </tbody>
</table>
</div>
"""
        

        return _html("EVbabel — Glossário (EN→PT)", body, brand)
    
    # ========= GLOSSÁRIO: NOVO (GET) =========
    async def glossario_new_get(request: web.Request):
        require_auth(request)
        brand = "#8b5cf6"
        bot = "translator"

        tabbar = _tabs(bot).replace('>Glossário<', ' class="tab active">Glossário<')

        body = f"""
{tabbar}
<form method="post" class="form" action="/admin/glossario/new?bot={bot}">
  <label>
    <span>Origem (EN)*</span>
    <input name="termo_src" required placeholder="ex.: Prince">
  </label>
  <label>
    <span>Destino (PT)*</span>
    <input name="termo_dst" required placeholder="ex.: Príncipe">
  </label>
  <label>
    <span>Prioridade</span>
    <input name="priority" type="number" value="100" min="1" step="1">
  </label>
  <label class="checkbox">
    <input type="checkbox" name="enabled" checked>
    <span>Ativo</span>
  </label>

  <div class="actions full" style="margin-top:4px;">
    <button class="btn primary" type="submit">Salvar</button>
    <a class="btn secondary" href="/admin/glossario?bot={bot}">Voltar</a>
  </div>
</form>
"""
        return _html("Novo termo — Glossário", body, brand)

    # ========= GLOSSÁRIO: NOVO (POST) =========
    async def glossario_new_post(request: web.Request):
        require_auth(request)
        data = await request.post()

        termo_src = (data.get("termo_src") or "").strip()
        termo_dst = (data.get("termo_dst") or "").strip()
        priority  = int(data.get("priority") or 100)
        enabled   = 1 if data.get("enabled") else 0

        if not termo_src or not termo_dst:
            return _html(
                "Erro",
                "<p>Campos obrigatórios: origem e destino.</p>"
                "<p><a class='btn' href='/admin/glossario/new?bot=translator'>Voltar</a></p>",
                "#8b5cf6"
            )

        try:
            # upsert case-insensitive por termo_src
            await upsert_glossario(db_path, termo_src, termo_dst, enabled=enabled, priority=priority, updated_by=None)
        except Exception as e:
            return _html(
                "Erro",
                f"<p>Erro ao salvar: {e}</p>"
                "<p><a class='btn' href='/admin/glossario?bot=translator'>Voltar</a></p>",
                "#8b5cf6"
            )

        # recarrega cache se foi fornecido callback
        if on_glossario_change:
            maybe_coro = on_glossario_change()
            if hasattr(maybe_coro, "__await__"):
                await maybe_coro

        raise web.HTTPFound("/admin/glossario?bot=translator")
    # ========= GLOSSÁRIO: EDITAR (GET) =========
    async def glossario_edit_get(request: web.Request):
        require_auth(request)
        brand = "#8b5cf6"
        bot = "translator"
        gid = int(request.match_info["gloss_id"])

        rows = await list_glossario(db_path, only_enabled=False)
        row = next((r for r in rows if r[0] == gid), None)
        if not row:
            return _html("Não encontrado", "<p>Termo não encontrado.</p><p><a class='btn' href='/admin/glossario?bot=translator'>Voltar</a></p>", brand)

        _, src, dst, enabled, prio, _updated_at, _updated_by = row
        checked = "checked" if enabled else ""
        tabbar = _tabs(bot).replace('>Glossário<', ' class="tab active">Glossário<')

        body = f"""
{tabbar}
<form method="post" class="form" action="/admin/glossario/{gid}/edit?bot={bot}">
  <label>
    <span>Origem (EN)*</span>
    <input name="termo_src" required value="{src}">
  </label>
  <label>
    <span>Destino (PT)*</span>
    <input name="termo_dst" required value="{dst}">
  </label>
  <label>
    <span>Prioridade</span>
    <input name="priority" type="number" value="{prio}" min="1" step="1">
  </label>
  <label class="checkbox">
    <input type="checkbox" name="enabled" {checked}>
    <span>Ativo</span>
  </label>

  <div class="actions full" style="margin-top:4px;">
    <button class="btn primary" type="submit">Salvar</button>
    <a class="btn secondary" href="/admin/glossario?bot={bot}">Voltar</a>
  </div>
</form>
"""
        return _html("Editar termo — Glossário", body, brand)

    # ========= GLOSSÁRIO: EDITAR (POST) =========
    async def glossario_edit_post(request: web.Request):
        require_auth(request)
        brand = "#8b5cf6"
        bot = "translator"
        gid = int(request.match_info["gloss_id"])
        data = await request.post()

        termo_src = (data.get("termo_src") or "").strip()
        termo_dst = (data.get("termo_dst") or "").strip()
        priority  = int(data.get("priority") or 100)
        enabled   = 1 if data.get("enabled") else 0

        if not termo_src or not termo_dst:
            return _html("Erro", "<p>Campos obrigatórios: origem e destino.</p><p><a class='btn' href='/admin/glossario?bot=translator'>Voltar</a></p>", brand)

        # obtém a versão atual (para detectar renomear de termo_src)
        rows = await list_glossario(db_path, only_enabled=False)
        row = next((r for r in rows if r[0] == gid), None)
        if not row:
            return _html("Não encontrado", "<p>Termo não encontrado.</p><p><a class='btn' href='/admin/glossario?bot=translator'>Voltar</a></p>", brand)

        _, old_src, _old_dst, _old_enabled, _old_prio, _u1, _u2 = row

        try:
            # upsert case-insensitive por termo_src (pode criar/atualizar outra linha)
            await upsert_glossario(db_path, termo_src, termo_dst, enabled=enabled, priority=priority, updated_by=None)

            # Se renomeou a chave (termo_src), removemos o registro antigo por ID
            if old_src.lower() != termo_src.lower():
                await delete_glossario(db_path, gid)

        except Exception as e:
            return _html("Erro", f"<p>Erro ao atualizar: {e}</p><p><a class='btn' href='/admin/glossario?bot=translator'>Voltar</a></p>", brand)

        # recarrega cache se houver callback
        if on_glossario_change:
            maybe_coro = on_glossario_change()
            if hasattr(maybe_coro, "__await__"):
                await maybe_coro

        raise web.HTTPFound("/admin/glossario?bot=translator")

    # ========= GLOSSÁRIO: EXCLUIR (POST) =========
    async def glossario_delete_post(request: web.Request):
        require_auth(request)
        brand = "#8b5cf6"
        gid = int(request.match_info["gloss_id"])

        try:
            await delete_glossario(db_path, gid)
        except Exception as e:
            return _html("Erro", f"<p>Erro ao excluir: {e}</p><p><a class='btn' href='/admin/glossario?bot=translator'>Voltar</a></p>", brand)

        # recarrega cache se houver callback
        if on_glossario_change:
            maybe_coro = on_glossario_change()
            if hasattr(maybe_coro, "__await__"):
                await maybe_coro

        raise web.HTTPFound("/admin/glossario?bot=translator")




    # Registrar rotas
    app.router.add_get("/admin/guilds", admin_guilds_list)
    app.router.add_get("/admin/guilds/new", admin_guilds_new_get)
    app.router.add_post("/admin/guilds/new", admin_guilds_new_post)
    app.router.add_get("/admin/guilds/{guild_id}/edit", admin_guilds_edit_get)
    app.router.add_post("/admin/guilds/{guild_id}/edit", admin_guilds_edit_post)
    app.router.add_post("/admin/guilds/{guild_id}/delete", admin_guilds_delete_post)
    app.router.add_get("/admin/glossario", glossario_list)
    app.router.add_get("/admin/glossario/new", glossario_new_get)
    app.router.add_post("/admin/glossario/new", glossario_new_post)
    app.router.add_get("/admin/glossario/{gloss_id}/edit", glossario_edit_get)
    app.router.add_post("/admin/glossario/{gloss_id}/edit", glossario_edit_post)
    app.router.add_post("/admin/glossario/{gloss_id}/delete", glossario_delete_post)

