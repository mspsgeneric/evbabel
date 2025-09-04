# painel/routes.py
from aiohttp import web
from .auth import require_auth

def _html(page_title: str, body: str) -> web.Response:
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
  --brand: #5865F2;
  --success: #23a55a;
  --danger: #f23f43;
  --border: #3f4147;
}}
* {{ box-sizing: border-box; }}
html, body {{ margin:0; padding:0; background:var(--bg); color:var(--text); font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, Noto Sans; }}
.container {{ max-width: 1100px; margin: 24px auto; padding: 0 16px; }}
h1 {{ font-size: 20px; font-weight: 700; margin: 0 0 16px; }}
.card {{ background: var(--bg-elev); border:1px solid var(--border); border-radius: 12px; padding: 16px; }}

/* Listagem / ações */
.controls {{ display:flex; gap:8px; align-items:center; margin-bottom:12px; }}
.controls .search {{ flex:1; display:flex; gap:8px; }}
input[type=text], input[type=number] {{ background:#1e1f22; color:var(--text); border:1px solid var(--border); border-radius:8px; padding:10px 12px; width:100%; }}
button {{ font: inherit; }}
.btn {{ display:inline-flex; gap:6px; align-items:center; padding:10px 14px; border-radius:8px; border:1px solid var(--border); background:#3a3c43; color:var(--text); text-decoration:none; cursor:pointer; }}
.btn.primary {{ background: var(--brand); border-color: transparent; }}
.btn.secondary {{ background:#3a3c43; }}
.btn.danger {{ background:#3a3c43; border-color: rgba(242,63,67,.4); color:#ff7a7d; }}
.btn:hover {{ filter: brightness(1.05); }}
form.inline {{ display:inline; }}

/* Tabela */
.table-wrap {{ width:100%; overflow-x:auto; }}
table {{ width:100%; border-collapse: collapse; overflow: hidden; border-radius: 12px; min-width: 780px; }}
thead th {{ background: var(--bg-elev-2); font-weight:600; font-size:12px; letter-spacing:.3px; text-align:left; padding:10px; border-bottom:1px solid var(--border); white-space:nowrap; }}
tbody td {{ padding:10px; border-bottom:1px solid var(--border); vertical-align: middle; }}

/* Badges */
.badge {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; line-height: 18px; }}
.badge.on {{ background: rgba(35,165,90,.15); color: var(--success); border:1px solid rgba(35,165,90,.35); }}
.badge.off{{ background: rgba(242,63,67,.15); color: var(--danger);  border:1px solid rgba(242,63,67,.35); }}

/* Barra de uso */
.progress {{ position: relative; width: 160px; height: 8px; background:#202226; border-radius:999px; overflow:hidden; border:1px solid var(--border); }}
.progress > span {{ position:absolute; inset:0; width: var(--w,0%); background: linear-gradient(90deg, #4e5de2, #5865F2); }}
.small {{ color: var(--muted); font-size: 12px; }}
hr {{ border:0; border-top:1px solid var(--border); margin: 12px 0; }}

/* ====== Form (novo/editar) ====== */
.form {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
.form label {{ display: flex; flex-direction: column; gap: 6px; }}
.form .full {{ grid-column: 1 / -1; }}
.form .checkbox {{ flex-direction: row; align-items: center; gap: 8px; }}
.form .actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.form .actions .btn {{ flex: 1; text-align: center; }}

/* ====== Mobile layout ====== */
@media (max-width: 820px) {{
  .controls {{ flex-direction: column; align-items: stretch; }}
  .controls .search {{ width:100%; }}
  .controls .btn.primary {{ width:100%; justify-content:center; }}

  /* Tabela -> cards */
  table, thead, tbody, th, tr, td {{ display:block; min-width: 0; }}
  thead {{ display:none; }}
  tbody tr {{ border:1px solid var(--border); border-radius:12px; margin-bottom:12px; background: var(--bg-elev); }}
  tbody td {{ border-bottom: 1px solid var(--border); display:flex; justify-content:space-between; gap:12px; padding:10px 12px; }}
  tbody td:last-child {{ border-bottom:none; }}
  tbody td::before {{
    content: attr(data-label);
    color: var(--muted);
    font-size: 12px;
    min-width: 120px;
  }}
  .progress {{ width: 100%; }}
  .table-wrap {{ overflow: visible; }}

  /* Form em 1 coluna */
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

def setup_painel_routes(app: web.Application, supabase):
    # ========= LISTAR =========
    async def admin_guilds_list(request: web.Request):
        require_auth(request)
        q = (request.query.get("q") or "").strip().lower()
        rows = (supabase.table("emails")
                .select("guild_id,guild_name,translate_enabled,char_limit,used_chars")
                .order("updated_at", desc=True)
                .limit(500)
                .execute()
                .data) or []

        if q:
            rows = [r for r in rows if q in (str(r.get("guild_id","")) + " " + str(r.get("guild_name",""))).lower()]

        trs = []
        for r in rows:
            gid = r.get("guild_id","")
            name = r.get("guild_name","") or ""
            enabled = bool(r.get("translate_enabled"))
            limit_ = int(r.get("char_limit") or 0)
            used   = int(r.get("used_chars") or 0)
            pct = 0 if limit_ <= 0 else min(100, int(used * 100 / max(1, limit_)))
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
    <a class="btn" href="/admin/guilds/{gid}/edit">Editar</a>
    <form class="inline" method="post" action="/admin/guilds/{gid}/delete" onsubmit="return confirm('Remover este servidor?')">
      <button class="btn danger" type="submit">Excluir</button>
    </form>
  </td>
</tr>""")

        body = f"""
<form class="controls" method="get">
  <div class="search">
    <input type="text" name="q" value="{q}" placeholder="Buscar por ID ou nome..." />
    <button class="btn" type="submit">Buscar</button>
  </div>
  <a class="btn primary" href="/admin/guilds/new">+ Novo</a>
</form>
<div class="table-wrap">
<table>
  <thead>
    <tr>
      <th>guild_id</th><th>Nome</th><th>Tradutor</th>
      <th>Limite</th><th>Carac. Usados</th><th>Ações</th>
    </tr>
  </thead>
  <tbody>
    {''.join(trs) if trs else '<tr><td data-label="Info" class="small">Nenhum registro.</td></tr>'}
  </tbody>
</table>
</div>
"""
        return _html("EVbabel — Admin de Servidores", body)

    # ========= NOVO =========
    async def admin_guilds_new_get(request: web.Request):
        require_auth(request)
        body = """
<form method="post" class="form">
  <label>
    <span>guild_id*</span>
    <input name="guild_id" required placeholder="ex.: 123456789012345678">
  </label>

  <label>
    <span>Nome (opcional)</span>
    <input name="guild_name" placeholder="ex.: Meu Servidor">
  </label>

  <label class="checkbox full">
    <input type="checkbox" name="translate_enabled">
    <span>Habilitar tradutor</span>
  </label>

  <label class="full">
    <span>Limite de caracteres</span>
    <input name="char_limit" type="number" value="500000" min="0" step="1">
  </label>

  <div class="actions full" style="margin-top:4px;">
    <button class="btn primary" type="submit">Salvar</button>
    <a class="btn secondary" href="/admin/guilds">Voltar</a>
  </div>
</form>
"""
        return _html("Novo Servidor", body)

    async def admin_guilds_new_post(request: web.Request):
        require_auth(request)
        data = await request.post()

        gid = (data.get("guild_id") or "").strip()
        if not gid:
            return _html("Erro", "<p>guild_id é obrigatório.</p><p><a class='btn' href='/admin/guilds/new'>Voltar</a></p>")

        payload = {
            "guild_id": gid,
            "guild_name": (data.get("guild_name") or "").strip() or None,
            "translate_enabled": bool(data.get("translate_enabled")),
            "char_limit": int(data.get("char_limit") or 0),
        }
        try:
            supabase.table("emails").insert(payload).execute()
        except Exception as e:
            return _html("Erro", f"<p>Erro ao inserir: {e}</p><p><a class='btn' href='/admin/guilds'>Voltar</a></p>")
        raise web.HTTPFound("/admin/guilds")

    # ========= EDITAR =========
    async def admin_guilds_edit_get(request: web.Request):
        require_auth(request)
        gid = request.match_info["guild_id"]
        row = (supabase.table("emails")
               .select("guild_id,guild_name,translate_enabled,char_limit,used_chars")
               .eq("guild_id", gid).maybe_single().execute().data)
        if not row:
            return _html("Não encontrado", "<p>Servidor não encontrado.</p><p><a class='btn' href='/admin/guilds'>Voltar</a></p>")

        used = int(row.get("used_chars") or 0)
        limit_ = int(row.get("char_limit") or 0)
        pct = 0 if limit_ <= 0 else min(100, int(used * 100 / max(1, limit_)))
        checked = "checked" if row.get("translate_enabled") else ""
        body = f"""
<form method="post" class="form">
  <label class="full">
    <span>guild_id</span>
    <input value="{row.get('guild_id')}" disabled>
  </label>

  <label>
    <span>Nome</span>
    <input name="guild_name" value="{row.get('guild_name') or ''}">
  </label>

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

  <div class="actions full" style="margin-top:4px;">
    <button class="btn primary" type="submit">Salvar</button>
    <a class="btn secondary" href="/admin/guilds">Voltar</a>
  </div>
</form>
"""
        return _html("Editar Servidor", body)

    async def admin_guilds_edit_post(request: web.Request):
        require_auth(request)
        gid = request.match_info["guild_id"]
        data = await request.post()
        payload = {
            "guild_name": (data.get("guild_name") or "").strip() or None,
            "translate_enabled": bool(data.get("translate_enabled")),
            "char_limit": int(data.get("char_limit") or 0),
        }
        try:
            supabase.table("emails").update(payload).eq("guild_id", gid).execute()
        except Exception as e:
            return _html("Erro", f"<p>Erro ao atualizar: {e}</p><p><a class='btn' href='/admin/guilds'>Voltar</a></p>")
        raise web.HTTPFound("/admin/guilds")

    # ========= EXCLUIR =========
    async def admin_guilds_delete_post(request: web.Request):
        require_auth(request)
        gid = request.match_info["guild_id"]
        try:
            supabase.table("emails").delete().eq("guild_id", gid).execute()
        except Exception as e:
            return _html("Erro", f"<p>Erro ao excluir: {e}</p><p><a class='btn' href='/admin/guilds'>Voltar</a></p>")
        raise web.HTTPFound("/admin/guilds")

    # Registrar rotas
    app.router.add_get("/admin/guilds", admin_guilds_list)
    app.router.add_get("/admin/guilds/new", admin_guilds_new_get)
    app.router.add_post("/admin/guilds/new", admin_guilds_new_post)
    app.router.add_get("/admin/guilds/{guild_id}/edit", admin_guilds_edit_get)
    app.router.add_post("/admin/guilds/{guild_id}/edit", admin_guilds_edit_post)
    app.router.add_post("/admin/guilds/{guild_id}/delete", admin_guilds_delete_post)
