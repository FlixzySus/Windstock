"""
Dev Panel -- cheats for testing your own server, served by the World Manager at
http://127.0.0.1:8080/dev (localhost only, like the rest of the World Manager).

  * spawn any Kanto Pokemon, normal or shiny, at the trainer's feet (catchable,
    visible to that trainer only), or drop it straight into their collection
  * add or set PokeCoins
  * add any item, stardust or candy

About shinies: the 2016 client has no shiny concept, so a stock phone draws a
shiny exactly like a normal one. The server still records it as shiny, reports
it to a shiny agent over GET /shiny/state, and -- unless you untick it -- names
it "*Pikachu" so you can tell it apart in your collection. A real shiny model
needs a pm####_s bundle and the World Manager's "Custom models" panel.
"""
import contextlib
import json

from windstock.web import webui
from windstock.web.admin import DEX, GIVEABLE

# Everything GIVEABLE plus the items the 2016 client knows but the World
# Manager's give list leaves out.
DEV_ITEMS = sorted(set(GIVEABLE) | {(4, "Master Ball"),
                                    (901, "Egg Incubator (unlimited)")})

# "Stock up" button: a full bag of the useful stuff in one click.
KIT = [(1, 200), (2, 200), (3, 200), (4, 50), (101, 50), (102, 50), (103, 50),
       (104, 50), (201, 50), (202, 50), (701, 100), (301, 20), (401, 20),
       (501, 20), (902, 10)]


def _target(who):
    """Blank -> the trainer who last talked to the game server."""
    who = (who or "").strip()
    if who:
        return who
    from windstock.game import rpc
    return rpc._last_user[0] or ""


@contextlib.contextmanager
def _as(who):
    from windstock.game import world
    name = _target(who)
    if not name:
        raise KeyError("")
    with world.acting_as(name) as p:
        yield p


def _int(d, k, default, lo, hi):
    try:
        v = int(d.get(k, default))
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _status(p):
    from windstock.game import world
    from windstock.game import rpc
    pos = p.LAST_POS or (rpc._last_loc if rpc._last_user[0] == p.username else None)
    return {"trainer": p.username, "coins": p.COINS, "stardust": p.STARDUST,
            "level": world.stats()[0], "pokemon": len(p.CAUGHT),
            "max_pokemon": p.MAX_POKEMON,
            "shinies": sum(1 for c in p.CAUGHT if c.get("shiny")),
            "location": list(pos) if pos and (pos[0] or pos[1]) else None}


def handle(path, d):
    """One POST to /api/dev/<action>. Returns the JSON reply as a dict."""
    from windstock.game import world
    from windstock.game import protocol as P
    action = path.rsplit("/", 1)[-1]
    who = d.get("player")
    try:
        with _as(who) as p:
            name = p.username
            if action == "status":
                return dict(_status(p), ok=True, message="")

            if action in ("spawn", "pokemon"):
                pid = _int(d, "pokemon_id", 0, 0, 999)
                if not 1 <= pid <= 151:
                    return {"ok": False, "message": "pick a Pokemon (1-151)"}
                cp = _int(d, "cp", 0, 0, 9999)
                shiny = bool(d.get("shiny"))
                mark = bool(d.get("mark", True))
                count = _int(d, "count", 1, 1, 50)
                label = ("shiny " if shiny else "") + DEX[pid]
                if action == "spawn":
                    eids = P.dev_spawn(pid, cp, shiny, mark, min(count, 10),
                                       _int(d, "minutes", 15, 1, 120))
                    if eids is None:
                        return {"ok": False, "message":
                                f"no location for {name} yet -- open the game so "
                                "it reports where you are, then try again"}
                    msg = (f"spawned {len(eids)} x {label} next to {name} "
                           "(shows on the map within ~10 s)")
                else:
                    made = P.dev_give_pokemon(pid, cp, shiny, mark, count)
                    if not made:
                        return {"ok": False, "message":
                                f"{name}'s Pokemon storage is full"}
                    msg = f"added {made} x {label} to {name}'s collection"
                    if made < count:
                        msg += " (storage is now full)"
                return dict(_status(p), ok=True, message=msg)

            if action == "coins":
                amount = _int(d, "amount", 0, -10_000_000, 10_000_000)
                if d.get("mode") == "set":
                    world.add_coins(max(0, amount) - p.COINS)
                    msg = f"{name} now has {p.COINS} PokeCoins"
                else:
                    world.add_coins(max(-p.COINS, amount))
                    msg = f"gave {name} {amount} PokeCoins (now {p.COINS})"
                return dict(_status(p), ok=True, message=msg)

            if action == "items":
                names = dict(DEV_ITEMS)
                rows = KIT if d.get("kit") else [(d.get("item_id"), d.get("count"))]
                got = []
                for iid, cnt in rows:
                    try:
                        iid, cnt = int(iid), max(1, min(999, int(cnt)))
                    except (TypeError, ValueError):
                        return {"ok": False, "message": "bad item or count"}
                    if iid not in names:
                        return {"ok": False, "message": f"unknown item {iid}"}
                    total = world.add_item(iid, cnt)
                    got.append(f"{cnt} x {names[iid]} (now {total})")
                return dict(_status(p), ok=True,
                            message=f"gave {name} " + ", ".join(got))

            if action == "stardust":
                amount = _int(d, "amount", 0, 1, 10_000_000)
                total = world.add_stardust(amount)
                return dict(_status(p), ok=True,
                            message=f"gave {name} {amount} stardust (now {total})")

            if action == "candy":
                pid = _int(d, "pokemon_id", 0, 0, 999)
                if not 1 <= pid <= 151:
                    return {"ok": False, "message": "pick a Pokemon (1-151)"}
                fam = P.pokemon_family(pid)
                total = world.add_candy(fam, _int(d, "count", 1, 1, 9999))
                return dict(_status(p), ok=True,
                            message=f"{name} now has {total} {DEX[fam]} candy")
    except KeyError:
        if not _target(who):
            return {"ok": False, "message": "nobody has logged in yet -- open the "
                    "game, or type a trainer name"}
        return {"ok": False, "message": f"no account called {_target(who)!r}"}
    return {"ok": False, "message": f"unknown action {action!r}"}


def page():
    return (webui.render(PAGE)
            .replace("__DEX__", json.dumps(DEX))
            .replace("__ITEMS__", json.dumps(DEV_ITEMS)))


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Windstock Dev Panel</title>
<style>__CSS__
 .stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px;margin:0 0 14px}
 .stat{background:var(--card);border:1px solid var(--line);border-radius:var(--r-sm);
   padding:12px 14px;box-shadow:var(--shadow)}
 .stat small{display:block;font-size:11px;font-weight:800;letter-spacing:.08em;
   text-transform:uppercase;color:var(--ink3)}
 .stat b{font-family:var(--mono);font-size:18px;color:var(--ink)}
 .chk{display:inline-flex;align-items:center;gap:6px;font-size:13.5px;font-weight:700;
   color:var(--ink2);cursor:pointer}
 .chk input{width:17px;height:17px;accent-color:var(--brand)}
 #result{min-height:1.6em;font-weight:700}
 #result.ok{color:var(--grass)} #result.err{color:var(--red)}
 button.gold{background:#f5c33b;border-color:#e3ac1c;color:#4a3606}
 button.gold:hover{background:#e9b52a;border-color:#d39e12;color:#4a3606}
</style></head><body>
<header>
  <div class="brand">
    <div class="mark"><svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M5 3v18" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/>
      <path d="M5 6.6 L20 9 L20 13 L5 15.4 Z" fill="#fff"/>
      <path d="M10 7.3v6.9M14.4 7.9v5.6" stroke="#2358d8" stroke-width="1.4" stroke-linecap="round"/>
    </svg></div>
    <div class="word"><h1>Windstock</h1><small>Dev Panel</small></div>
  </div>
  <nav class="topnav"><a href="/">Manager</a><a href="/downloads">World Data</a><a class="active" href="/dev">Dev Panel</a></nav>
  <div class="spacer"></div>
</header>
<main class="wrap">

<h2>Trainer</h2>
<div class="bar">
  <input id="who" list="accounts" placeholder="blank = whoever is playing" size="22">
  <datalist id="accounts"></datalist>
  <button onclick="refresh()">Refresh</button>
  <span id="result"></span>
</div>
<div class="stats">
  <div class="stat"><small>Trainer</small><b id="s-name">&ndash;</b></div>
  <div class="stat"><small>Level</small><b id="s-level">&ndash;</b></div>
  <div class="stat"><small>PokeCoins</small><b id="s-coins" class="coins">&ndash;</b></div>
  <div class="stat"><small>Stardust</small><b id="s-dust">&ndash;</b></div>
  <div class="stat"><small>Pokemon</small><b id="s-mons">&ndash;</b></div>
  <div class="stat"><small>Shinies</small><b id="s-shiny">&ndash;</b></div>
</div>

<h2>Spawn a Pokemon</h2>
<div class="bar">
  <select id="mon"></select>
  <label class="inline-label">CP <input id="cp" type="number" value="0" min="0" max="9999" size="5" title="0 = random"></label>
  <label class="inline-label">How many <input id="count" type="number" value="1" min="1" max="10" size="3"></label>
  <label class="chk"><input id="shiny" type="checkbox"> Shiny</label>
  <label class="chk"><input id="mark" type="checkbox" checked> Name shinies "*Name"</label>
</div>
<div class="bar">
  <button class="on" onclick="spawn()">Spawn next to me</button>
  <label class="inline-label">stays for <input id="mins" type="number" value="15" min="1" max="120" size="3"> min</label>
  <button onclick="givemon()">Put straight in my collection</button>
</div>
<div class="hint">"Spawn next to me" puts it on the map a few metres from you, for you
only &mdash; tap it and catch it like any wild one (max 10 at a time). "Put straight in
my collection" skips the catch (up to 50 at once). CP 0 picks a normal CP for that
species. This 2016 client has no shiny graphics, so on a stock phone a shiny looks
normal: it is saved as shiny and, with the box ticked, named <b>*Pikachu</b> so you can
spot it. For a real shiny model, build <code>pm0025_s</code> with the Asset Editor and
pick it under <a href="/">Manager &rarr; Custom models</a>.</div>

<h2>PokeCoins</h2>
<div class="bar">
  <input id="coins" type="number" value="1000" min="0" max="10000000" size="9">
  <button class="gold" onclick="coins('add')">Add coins</button>
  <button onclick="coins('set')">Set to exactly</button>
  <button class="gold" onclick="quickCoins(100)">+100</button>
  <button class="gold" onclick="quickCoins(1000)">+1,000</button>
  <button class="gold" onclick="quickCoins(14500)">+14,500</button>
</div>

<h2>Items</h2>
<div class="bar">
  <select id="item"></select>
  <input id="qty" type="number" value="50" min="1" max="999" size="4">
  <button onclick="item()">Add item</button>
  <button class="on" onclick="kit()">Stock up (full bag of everything)</button>
</div>
<div class="bar">
  <input id="dust" type="number" value="100000" min="1" max="10000000" size="9">
  <button onclick="dust()">Add stardust</button>
  <select id="candymon"></select>
  <input id="candy" type="number" value="200" min="1" max="9999" size="5">
  <button onclick="candy()">Add candy</button>
</div>
<div class="hint">Items, candy and Pokemon show up in game within a few seconds. Coins
and stardust travel with your player data, which the 2016 client only asks for now and
then &mdash; if the number on screen hasn't moved, close and reopen the game. The "Stock up" button can go past your bag limit; that is fine
for testing, but spinning stops says "bag full" until you use some up.</div>
</main>
<script>
const DEX=__DEX__, ITEMS=__ITEMS__;
const $=i=>document.getElementById(i);
async function post(u,b){
  const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(Object.assign({player:$('who').value.trim()},b||{}))});
  return r.json();
}
function show(r){
  const el=$('result');
  if(r.message!==undefined){el.textContent=r.message;el.className=r.ok?'ok':'err';}
  if(r.ok&&r.trainer!==undefined){
    $('s-name').textContent=r.trainer; $('s-level').textContent=r.level;
    $('s-coins').textContent=r.coins.toLocaleString();
    $('s-dust').textContent=r.stardust.toLocaleString();
    $('s-mons').textContent=r.pokemon+' / '+r.max_pokemon;
    $('s-shiny').textContent=r.shinies;
  }
  return r;
}
async function refresh(){const r=await post('/api/dev/status');show(r);}
function monArgs(){return {pokemon_id:+$('mon').value,cp:+$('cp').value||0,
  count:+$('count').value||1,shiny:$('shiny').checked,mark:$('mark').checked};}
async function spawn(){show(await post('/api/dev/spawn',
  Object.assign(monArgs(),{minutes:+$('mins').value||15})));}
async function givemon(){show(await post('/api/dev/pokemon',monArgs()));}
async function coins(mode){show(await post('/api/dev/coins',{amount:+$('coins').value,mode}));}
async function quickCoins(n){show(await post('/api/dev/coins',{amount:n,mode:'add'}));}
async function item(){show(await post('/api/dev/items',{item_id:+$('item').value,count:+$('qty').value}));}
async function kit(){show(await post('/api/dev/items',{kit:true}));}
async function dust(){show(await post('/api/dev/stardust',{amount:+$('dust').value}));}
async function candy(){show(await post('/api/dev/candy',{pokemon_id:+$('candymon').value,count:+$('candy').value}));}

DEX.forEach((n,i)=>{if(!i)return;
  for(const sel of ['mon','candymon']){const o=document.createElement('option');
    o.value=i;o.textContent='#'+String(i).padStart(3,'0')+' '+n;$(sel).appendChild(o);}});
$('mon').value=25; $('candymon').value=25;
ITEMS.forEach(([id,label])=>{const o=document.createElement('option');
  o.value=id;o.textContent=label;$('item').appendChild(o);});
fetch('/api/accounts',{method:'POST',body:'{}'}).then(r=>r.json()).then(r=>{
  (r.accounts||[]).forEach(n=>{const o=document.createElement('option');o.value=n;
    $('accounts').appendChild(o);});});
refresh();
</script>
</body></html>
"""
