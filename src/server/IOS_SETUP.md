# Playing on iPhone

Windstock talks to the **2016** iOS Pokémon GO client: app version **1.0.x**
(= Android 0.29) or **1.5.0** (= Android 0.35). The iPhone needs **no jailbreak
and no patching**. You give it three things:

1. the old Pokémon GO app,
2. Windstock's certificate, trusted,
3. a DNS server that sends the Niantic hostnames to your PC.

> **Read this first.** The 2016 app is **not** in this repository and never
> will be (it is Niantic's copyrighted code). You need your own copy, tied to
> your own Apple ID (see step 1). Two things are outside this project's control:
> Apple may no longer serve a version that old, and a 2016 build may not open on
> the newest iOS. If the app crashes on launch, nothing on the server side can
> fix that.

---

## 0. What you need

| | |
|---|---|
| A computer to run the server | Windows, macOS or Linux, with **Python 3.10+** |
| An iPhone | on the **same Wi-Fi** as the computer, or using **Tailscale** to play anywhere |
| The 2016 Pokémon GO app | v1.0.x or v1.5.0, from your own Apple ID |

## 1. Get the 2016 app onto the iPhone

Apple only lets you download a free app's old versions if that app is already
in your Apple ID's **purchase history**. Every account that ever installed
Pokémon GO has it there.

1. Install [`ipatool`](https://github.com/majd/ipatool) (2.2 or newer) on your computer and sign
   in with **your** Apple ID: `ipatool auth login -e you@example.com`
2. List the versions Apple still has:
   `ipatool list-versions -b com.nianticlabs.pokemongo`
   Look for the version id of **1.0.x** or **1.5.0**. Apple doesn't say which id
   is which: `ipatool get-version-metadata -b com.nianticlabs.pokemongo
   --external-version-id <id>` shows the version number for an id.
3. Download it:
   `ipatool download -b com.nianticlabs.pokemongo --external-version-id <id> -o pogo.ipa`
4. Install it **without re-signing it**. Delete the current Pokémon GO first.
   Then use **Apple Configurator 2** (Mac: drag `pogo.ipa` onto the phone) or
   `ideviceinstaller -i pogo.ipa` (Windows/Linux, from libimobiledevice). The
   iPhone must be signed into the **same Apple ID** that downloaded the file.

   > **Not AltStore, Sideloadly, TrollStore or Xcode.** The file is encrypted
   > (FairPlay) for your Apple ID, and those tools re-sign it. A re-signed
   > encrypted app crashes the instant it opens, before it ever contacts the
   > server. That is the number one cause of "it just closes".
5. Turn **off** automatic app updates (Settings → App Store → App Updates).
   Otherwise iOS will replace the old version with today's.

## 2. Install and start the server

On the computer:

```bash
git clone https://github.com/FlixzySus/Windstock.git
cd Windstock
python3 -m pip install -r requirements.txt     # just s2sphere; osmium is optional
cd src/server
```

Find the address the iPhone will use to reach this computer:

- **Same Wi-Fi:** the computer's LAN IP (`ipconfig` on Windows,
  `ipconfig getifaddr en0` on macOS, `hostname -I` on Linux), e.g. `192.168.1.50`.
- **Play anywhere:** install [Tailscale](https://tailscale.com/download) on the
  computer and the iPhone, signed into the same account, and use the computer's
  `100.x.y.z` address (`tailscale ip -4`).

Start it. It needs ports **53** (DNS), **443** (game) and **80**, so it must run
as admin/root. The first start also generates the server's certificate (a
minute at most) and prints the address to use on the phone:

**Windows:** double-click **`src/server/start.bat`**. It asks for Administrator
rights, installs the dependency, opens the Windows Firewall for the phone (UDP
53, TCP 443, TCP 80; the rules are kept) and starts the server. To pin the
address, run it from a prompt as `start.bat 192.168.1.50`. No OpenSSL is needed
on Windows: the certificate is generated in pure Python.

**macOS / Linux:**

```bash
sudo python3 run.py 192.168.1.50      # your IP here
```

On macOS, click **Allow** when it asks whether Python may accept incoming connections.

If the launcher stops with *port ... is already in use*, another program holds
that port. On Windows the usual ones are VMware on 443 and IIS or Skype on 80;
`netstat -ano | findstr :443` shows the PID, and Task Manager → Details names it.

## 3. Trust Windstock's certificate on the iPhone

The running server has a setup page the phone can open **before** it trusts
anything: in Safari, go to **`http://<the computer's IP>/`** (plain http, the
address the server printed). It walks through the rest, and the certificate is
one tap away at `http://<ip>/ca.crt`. In full:

1. Tap **Download certificate** on that page, then **Allow**. iOS says
   *Profile Downloaded*. (No setup page? AirDrop `src/server/certs/ca.crt`
   instead. **Only `ca.crt`**: `ca.key` lets anyone read every trusting phone's
   traffic.)
2. **Settings → General → VPN & Device Management →** *PoGO Private CA* →
   **Install**.
3. **Settings → General → About → Certificate Trust Settings →** switch **on**
   *PoGO Private CA*.

Step 3 is the one people miss. Without it the app says *Unable to authenticate*.

## 4. Point the iPhone's DNS at the server

**Same Wi-Fi:** Settings → Wi-Fi → tap ⓘ next to your network → **Configure DNS →
Manual**. Delete every server listed and add only the computer's IP. Delete all
the others: a second DNS server lets the phone go around Windstock.

**Tailscale (anywhere, including mobile data):** open the Tailscale admin
console → **DNS → Nameservers → Add nameserver → Custom** → the computer's
`100.x` address, and turn on **Override local DNS**. Keep the Tailscale app
connected on the phone.

Quick check: open `http://pgorelease.nianticlabs.com/` in Safari. The setup
page should load again, now with a tick under step 2: DNS is working. Then open
**`https://pgorelease.nianticlabs.com/check`**. A green tick there means the
certificate is trusted too, and you are ready to play.

## 5. Play

Open Pokémon GO → **Pokémon Trainer Club** → enter any username and password.
The first login creates that trainer **and sets its password**, so use the same
password every time. You should see the server's console log the login and the
map loading.

GPS: iPhones can't fake their location, so the map follows where you really
are. That needs a GPS fix, so play outdoors or near a window. If nothing is
spawning around you, use the Dev Panel below.

## 6. The Dev Panel (spawns, shinies, coins, items)

On the **computer** running the server, open **http://127.0.0.1:8080/dev**.

| Section | What it does |
|---|---|
| **Trainer** | Leave blank for whoever is playing, or type a name. Shows their coins, stardust, Pokémon count and shinies. |
| **Spawn a Pokémon** | Any of the 151, any CP (0 = a normal CP for that species), shiny or not. **Spawn next to me** puts up to 10 on the map a few metres from you, visible only to you; you still have to catch them. **Put straight in my collection** adds up to 50 at once, no catch. |
| **PokéCoins** | Add coins, set an exact amount, or use the +100 / +1,000 / +14,500 buttons. |
| **Items** | Any item, including Master Balls. **Stock up** fills the bag with a large stack of everything. You can also add stardust and candy. |

**About shinies:** the 2016 app came out before shinies existed, so it has no
shiny graphics. A shiny from the panel is still saved as shiny, counted under
*Shinies* and reported to shiny agents. By default it is also nicknamed
**`*Pikachu`**, so you can spot it in your collection. To make it really look
shiny you need a shiny model bundle (`pm0025_s`, built with the Asset Editor
from genuine iOS bundles) and **Manager → Custom models**. Note that a custom
model applies to every Pikachu, not just one.

The panel is only reachable from the computer itself: it has no password, so it
is never put on the network. If the server runs on another machine (a VPS),
tunnel it: `ssh -L 8080:127.0.0.1:8080 you@server`, then open the same URL.

## Troubleshooting

| Symptom | Fix |
|---|---|
| *Unable to authenticate* | The certificate isn't fully trusted (step 3.3), or you typed a different password from that trainer's first login. Open `https://pgorelease.nianticlabs.com/check` in Safari: a warning there means the certificate. |
| Stuck on the loading screen | DNS isn't pointed at the server. Re-check step 4 and the Safari test. |
| Nothing shows in the server console | The phone can't reach the computer: firewall, wrong IP, or the phone isn't on the same Wi-Fi / Tailscale. |
| The app closes as soon as it opens, and the server window shows nothing | The phone never reached the server, so this is the app itself. Read its crash log: Settings → Privacy & Security → Analytics & Improvements → Analytics Data → the newest `pokemongo-…` entry, and look at the first lines. **Code signing / FairPlay** (`Termination Reason: CODESIGNING`, or an error mentioning `Invalid Page`): the IPA is encrypted for the Apple ID that downloaded it, so it must be installed with the phone signed into **that** Apple ID and must not be re-signed by a sideloading tool (AltStore, Sideloadly, Xcode). **`EXC_BAD_ACCESS` / `EXC_CRASH` inside the app's own code** (`UnityFramework`, `libil2cpp`, `pokemongo`): the 2016 build doesn't run on this iOS version; the server can't fix that. |
| Coins don't change on screen | Close and reopen the game. Coins only refresh when the app asks for your player data again. |
