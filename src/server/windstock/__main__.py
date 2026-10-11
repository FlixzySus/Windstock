"""
Windstock launcher -- ONE process that runs everything: the DNS redirector
(UDP 53) and the game server (TCP 443, fake PTC SSO + RPC), plus the localhost
World Manager and the interactive slash console.

  Local (same Wi-Fi as the phone):
      py run.py                      # auto-detects this PC's LAN IP
  Remote (server with a public IP / VPS):
      py run.py 203.0.113.7          # the IP the phone should be pointed at
      RUN_IP=203.0.113.7 py run.py

  USB / gnirehtet (no Wi-Fi DNS change, no Administrator rights on this PC):
      py run.py --gnirehtet          # reverse-tether over USB (see GNIREHTET.md)

Default (Wi-Fi DNS) method: point the phone's Wi-Fi DNS at the IP this prints.
Both 53/udp and 443/tcp must be reachable from the phone (open them in the
firewall; on Windows low ports are fine without admin, but a firewall *allow*
rule for inbound may be needed). `--gnirehtet` only ADDS the cable transport;
the Wi-Fi DNS redirector is unchanged and still runs by default.

Build a standalone .exe (no Python needed on the target):
      py -m PyInstaller --onefile --name pogo-server \
         --add-data "certs;certs" --collect-all s2sphere run.py
"""
import os
import socket
import sys
import threading
import time

# Allow both ``py run.py`` and ``python -m windstock``: make sure the folder that
# CONTAINS the package is importable before we touch any package module.
if __package__ in (None, ""):
    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)

from windstock.cli.console import (  # noqa: E402
    start_console, show_banner, rule, kv, paint)
from windstock.cli.logfile import _setup_logging  # noqa: E402
from windstock.cli.network import detect_ip, _local_ips, _usable  # noqa: E402
from windstock.cli import gnirehtet as gnirehtet_mode  # noqa: E402
from windstock.cli.theme import (  # noqa: E402
    BOLD, DIM, YELLOW, MAGENTA, BRED, BGREEN, BYELLOW, BBLUE, BMAGENTA, BCYAN)


def _parse_launcher_args(argv):
    """Split the launcher's argv into (gnirehtet, ip).

    Backwards compatible: a bare argument is still the IP the phone should be
    pointed at (the Wi-Fi DNS method). ``--gnirehtet`` only ADDS the cable
    transport -- it changes nothing about the default behaviour.
    """
    gnirehtet = False
    ip = None
    for arg in argv:
        if arg == "--gnirehtet":
            gnirehtet = True
        elif arg in ("-h", "--help"):
            _print_launcher_help()
            raise SystemExit(0)
        elif arg.startswith("-"):
            print(paint(f"  unknown option {arg!r} -- ignored", DIM))
        elif ip is None:
            ip = arg
        else:
            print(paint(f"  ignoring extra argument {arg!r}", DIM))
    return gnirehtet, ip


def _check_ports():
    """Try the three listening ports before anything starts, so a taken or
    privileged port is named in one line instead of a traceback from a
    background thread a second later. Exits when one cannot be bound."""
    checks = [("udp", int(os.environ["DNS_PORT"]), "the DNS redirector"),
              ("tcp", int(os.environ["PORT"]), "the game server"),
              ("tcp", int(os.environ.get("SSO_BRIDGE_PORT", "80")),
               "the phone setup page")]
    problems = []
    for proto, port, what in checks:
        s = socket.socket(socket.AF_INET,
                          socket.SOCK_DGRAM if proto == "udp" else socket.SOCK_STREAM)
        try:
            if proto == "tcp" and os.name != "nt":
                # What the real listener does (socketserver's allow_reuse_address):
                # a port left in TIME_WAIT by the last run must not count as
                # taken. On Windows that flag would hide a REAL listener, so
                # the probe stays strict there.
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((os.environ.get("BIND", "0.0.0.0"), port))
        except PermissionError:
            problems.append((proto, port, what, "needs Administrator / root"))
        except OSError:
            problems.append((proto, port, what, "is already in use by another program"))
        finally:
            s.close()
    if not problems:
        return
    for proto, port, what, why in problems:
        print(paint("!! ", BOLD, BRED)
              + paint(f"port {proto} {port} ({what}) {why}", BRED))
    if any("Administrator" in p[3] for p in problems):
        print(paint("   Windows: right-click start.bat -> Run as administrator."
                    "   macOS / Linux: sudo python3 run.py", DIM))
    if any("in use" in p[3] for p in problems):
        print(paint("   Find out what has it --", DIM))
        print(paint("     Windows:       netstat -ano | findstr :PORT   "
                    "(the last column is the PID; Task Manager -> Details)", DIM))
        print(paint("     macOS / Linux: sudo lsof -i :PORT", DIM))
        print(paint("   Usual suspects: VMware Workstation on 443, IIS / Skype / "
                    "'World Wide Web Publishing' on 80, another copy of this "
                    "server, or Docker / WSL / a DNS tool on 53.", DIM))
    sys.exit(1)


def _print_launcher_help():
    print()
    print("  " + paint("Windstock launcher", BOLD, BBLUE))
    print("    py run.py                     detect this PC's LAN IP (Wi-Fi DNS method)")
    print("    py run.py 192.168.1.50        use this IP for the DNS redirect")
    print("    py run.py --gnirehtet         reverse-tether over USB (no Wi-Fi DNS, no admin)")
    print("    py src/scripts/INSTALL_GNIREHTET.py   get gnirehtet (one time)")
    print()


def main(ui_stream=None):
    """Run everything. Blocks in server.main(). A GUI window can call this on a
    background thread and pass its activity pane as ``ui_stream``."""
    _setup_logging(ui_stream)
    gnirehtet, arg_ip = _parse_launcher_args(sys.argv[1:])

    redirect_ip = arg_ip or os.environ.get("RUN_IP") or detect_ip()
    # Show the alternatives. When the phone can't reach the server, the first
    # thing to check is whether this is even the right address for the network
    # the phone is on -- so make it impossible to miss.
    others = [ip for ip in _local_ips()
              if _usable(ip) and ip != redirect_ip]
    if others:
        print(paint("  (this PC is also reachable at: ", DIM)
              + paint(", ".join(others), BYELLOW)
              + paint(" -- if the phone can't connect, run this with the right"
                      " one as an argument)", DIM))

    # Hand config to the imported modules via env (read at their import time).
    os.environ["REDIRECT_IP"] = redirect_ip
    os.environ.setdefault("PORT", "443")
    os.environ.setdefault("BIND", "0.0.0.0")
    os.environ.setdefault("DNS_PORT", "53")
    from windstock.config import paths
    os.environ.setdefault("CERT_DIR", paths.CERT_DIR)
    # Serve the real 2016 game master so the client has Pokemon templates and will
    # actually request + render the wild Pokemon (asset bundles now shipped in
    # assets/). Set SERVE_GAME_MASTER=0 to fall back to the bare template set.
    os.environ.setdefault("SERVE_GAME_MASTER", "1")

    _check_ports()

    from windstock.net import server
    from windstock.net import dns_redirect
    from windstock.net import sso_bridge

    if not (os.path.exists(server.CERT) and os.path.exists(server.KEY)):
        # First start: make this install's own CA + server certificate. The
        # phones fetch the CA from the setup page (http://<ip>/ca.crt).
        print(paint("   no TLS certificate yet -- generating one (first start only)", DIM))
        try:
            from windstock.tools import gen_certs
            gen_certs.main()
        except Exception as e:
            print(paint("!! ", BOLD, BRED)
                  + paint(f"could not generate the TLS certificates: {e}", BRED))
            print(paint("   Run gen_certs.py by hand, or copy a certs/ folder "
                        f"to {os.environ['CERT_DIR']}.", DIM))
            sys.exit(1)

    # World Manager (localhost only -- never exposed to the phone/network)
    try:
        from windstock.config import settings as _cfg
        admin_port = _cfg.get("server", "world_manager_port", env="ADMIN_PORT",
                              cast=int)
    except Exception:
        admin_port = int(os.environ.get("ADMIN_PORT", "8080"))
    try:
        from windstock.web import admin
        admin.start(port=admin_port)
        admin_url = f"http://127.0.0.1:{admin_port}"
    except Exception as e:
        admin_url = f"(failed to start: {e})"

    show_banner()
    rule("=")
    print("  " + paint("WINDSTOCK", BOLD, BBLUE)
          + paint("  /  Pokemon GO 0.29 private server", DIM))
    rule("-")
    print(kv("World Manager (this PC)", admin_url, BGREEN))
    print(kv("Point the phone's DNS at", redirect_ip, BCYAN))
    print(kv("Game server", f"https://{redirect_ip}:{os.environ['PORT']}", BGREEN))
    print(kv("DNS redirect", f"udp {redirect_ip}:{os.environ['DNS_PORT']}", BYELLOW))
    print(kv("Dev Panel (this PC)", f"{admin_url}/dev", BGREEN))
    rule("-")
    print(kv("Phone setup page", f"http://{redirect_ip}/", BCYAN))
    print("  " + paint("phone:", BOLD, BCYAN)
          + paint(f" Wi-Fi DNS = {redirect_ip} (the only entry), then open that page"
                  " in the phone's browser and follow its three steps", DIM))
    if gnirehtet:
        print(kv("Reverse tether", "gnirehtet (USB cable)", BCYAN))
    rule("-")
    print("  " + paint("[ready]", BOLD, BGREEN)
          + paint("  Ctrl-C to stop  --  everything below is live server activity",
                  DIM))
    print("  " + paint("[console]", BOLD, BCYAN)
          + paint("  Type /help + Enter for World Manager commands "
                  "(/log on|off toggles server-log.txt)", DIM))
    rule("=")
    print()

    # gnirehtet (opt-in): reverse-tether the phone through this PC over the USB
    # cable AND point the phone's DNS at us, so no Wi-Fi DNS change, no
    # Administrator rights and no phone-side DNS app are needed. See GNIREHTET.md.
    gn_state = None
    if gnirehtet:
        ca = os.path.join(os.environ["CERT_DIR"], "ca.crt")
        gn_state = gnirehtet_mode.start(cert=ca, dns_ip=redirect_ip)
        print("  " + paint("phone:", BOLD, BCYAN)
              + paint(" accept the VPN prompt, install the CA, then start"
                      " Pokemon GO", DIM))
        rule("-")

    # DNS in a supervised background thread (auto-restarts if it ever dies)
    def dns_loop():
        while True:
            try:
                dns_redirect.main()
            except Exception as e:
                print(paint("[dns] ", BOLD, BYELLOW)
                      + paint(f"restarting after: {e}", YELLOW), flush=True)
                time.sleep(1)
    threading.Thread(target=dns_loop, daemon=True).start()

    # Plain-HTTP door for the 0.35 client's PTC login, supervised the same way.
    # 0.35 dropped the permissive cert-validation callback 0.29 had, so its
    # Mono/C# login trusts NO certificate we can serve -- not our CA and not a
    # real Let's Encrypt one (Unity 5.3.5's root store predates ISRG Root X1).
    # The 0.35 build's SSO URLs are patched to http:// and land here; the hop is
    # inside the tailnet, so it is still encrypted end to end. 0.29 is untouched
    # and keeps using HTTPS. Only /sso is served; everything else gets a 404.
    def sso_bridge_loop():
        while True:
            try:
                sso_bridge.serve(int(os.environ.get("SSO_BRIDGE_PORT", "80")),
                                 int(os.environ["PORT"]))
            except Exception as e:
                print(paint("[bridge] ", BOLD, BMAGENTA)
                      + paint(f"restarting after: {e}", MAGENTA), flush=True)
                time.sleep(1)
    threading.Thread(target=sso_bridge_loop, daemon=True).start()

    # The World Manager, typed: /help lists the commands. Harmless when there is
    # no console to read (the windowed exe), so it can never break a GUI launch.
    start_console()

    # HTTPS server on the main thread (blocks until Ctrl-C)
    try:
        server.main()
    except KeyboardInterrupt:
        print()
        print(paint("  shutting down.", DIM))
    finally:
        if gn_state:
            gnirehtet_mode.teardown(gn_state)


if __name__ == "__main__":
    main()
