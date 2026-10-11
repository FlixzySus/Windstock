# Playing on Android

Windstock talks to **Pokémon GO 0.29.0** (July 2016). On Android that APK
needs one small patch to run on Android 7 or newer, and a certificate so the
phone trusts the server. No root. This page is the whole route, start to end.

> **You need your own copy of the 0.29.0 APK.** It is Niantic's copyrighted
> code and is not in this repository. Patch only your own copy and keep it to
> yourself.

| Your phone | What to install |
|---|---|
| **Android 7 or newer** (almost every phone) | the **patched** APK from step 2 |
| Android 5 or 6 | the **original** 0.29.0 APK, no patch needed |
| An x86 emulator (BlueStacks, LDPlayer, MEmu, Nox) | doesn't work: the game's network plugin is ARM-only and crashes the emulator's ARM translation layer. Use a real phone. |

---

## 0. What you need

| | |
|---|---|
| A Windows / macOS / Linux computer | with **Python 3.8+** |
| An Android phone on the **same Wi-Fi** as the computer | or a USB cable: see [USB instead of Wi-Fi](#usb-instead-of-wi-fi) |
| Your own `pokemon-go-0.29.0.apk` | package `com.nianticlabs.pokemongo`, the ARM (`armeabi-v7a`) build |

## 1. Get the server running

Clone the repo, then:

- **Windows:** double-click **`src\server\start.bat`**. It asks for Administrator,
  installs the one dependency, opens the firewall for the phone and starts the server.
- **macOS / Linux:** `pip install -r requirements.txt`, then `cd src/server && sudo python3 run.py`.

The window prints the address to use on the phone (**Point the phone's DNS at**)
and the **Phone setup page** (`http://<that IP>/`). The first start also makes the
server's certificate.

## 2. Patch the APK (Android 7+ only, one time)

```
py src\tools\patch_apk.py C:\where\it\is\pokemon-go-0.29.0.apk       # Windows
python3 src/tools/patch_apk.py ~/pokemon-go-0.29.0.apk               # macOS / Linux
```

It writes `pokemon-go-0.29.0-windstock.apk` next to the input and verifies its
signature. Four things happen, nothing else changes:

1. **Plugin path.** Android 7+ refuses `System.load("libNianticLabsPlugin.so")`
   without an absolute path, which is why the stock APK dies on launch there. One
   string inside the game's metadata becomes `/data/data/com.nianticlabs.pokemongo/lib/…`.
2. **Login hostname.** The PTC login URLs move from `sso.pokemon.com` to
   `pgorelease.nianticlabs.com`, the hostname that reliably resolves to this server
   (a phone's second resolver or encrypted DNS likes to win the race for
   `pokemon.com`, which looks like *unable to authenticate* with nothing in the
   server log). `--keep-sso-host` skips this.
3. **Repackage** byte for byte, dropping the old signature.
4. **Sign** with APK Signature Scheme v2, using a key made on first run and kept in
   `src/server/data/apk_sign/`. Keep that folder: an APK signed with a different key
   can't update one that's installed.

Needs only Python. No Android SDK, Java or Node.

## 3. Install the APK on the phone

1. **Uninstall the official Pokémon GO** first (the signer differs, so Android
   refuses it as an update). Trainer data lives on the server; nothing is lost.
2. Copy the APK to the phone (USB, or `adb install pokemon-go-0.29.0-windstock.apk`)
   and open it. Allow installs from your file manager when asked.
   - *"App was built for an older version of Android"* on Android 14+: the game
     targets API 23, which is allowed, so this shouldn't appear. If it does:
     `adb install --bypass-low-target-sdk-block …`.

## 4. Point the phone's DNS at the server

Wi-Fi → your network → **Modify** (long-press on some phones) → Advanced →
**IP settings: Static**. Keep the IP and gateway it shows, set **DNS 1 = the
IP the server printed**, and **leave DNS 2 empty** (a second server lets the phone
go around Windstock). Save.

**Android 9 or newer, also:** Settings → Network & internet → **Private DNS → Off**.
"Automatic" can send lookups to an encrypted resolver instead of the server.

## 5. Trust the certificate

In Chrome open **`http://<the server's IP>/`** (plain http). It's the setup page:

1. Tap **Download certificate**.
2. Settings → Security (& privacy) → **Encryption & credentials** → **Install a
   certificate** → **CA certificate** → *Install anyway* → pick the downloaded
   `windstock-ca.crt`. (Not "VPN & app user certificate".)
3. Open **`http://pgorelease.nianticlabs.com/`**: the same page, now with a tick
   under step 2 (DNS works). Then **`https://pgorelease.nianticlabs.com/check`**:
   a green tick means the certificate works too.

The game targets API 23, so it trusts this user-installed certificate on every
Android version, including 14 and 15.

## 6. Location

The map follows the phone's GPS. Outdoors that's automatic. Indoors, or to stay
put, use a mock-location app: Settings → Developer options → **Select mock
location app** → that app; and set Location mode to **GPS only** (not "High
accuracy", which blends in Wi-Fi positioning and makes the map jump).

## 7. Play

Open Pokémon GO → **Pokémon Trainer Club** → any username and password. Your
first login sets that trainer's password, so keep using the same one. On the
computer, `http://127.0.0.1:8080/dev` is the [Dev Panel](IOS_SETUP.md#6-the-dev-panel-spawns-shinies-coins-items)
(spawns, shinies, coins, items).

## USB instead of Wi-Fi

If you can't change the Wi-Fi DNS (school or work network, or a PC without
admin rights), the phone can be reverse-tethered through the USB cable with
`py run.py --gnirehtet`: no DNS change on the phone, no Administrator on the PC.
Steps 2, 3 and 5 above still apply. Walkthrough: [`GNIREHTET.md`](GNIREHTET.md).

## Pokémon GO 0.35 on Android

Also supported, but it is the harder route: 0.35 pins certificates and its login
code trusts no certificate we can serve, so the APK goes through the
`src/patcher/patcher.py` GUI (needs Node.js and Java) and its login crosses the
plain-HTTP door on port 80. Start with 0.29; it is the build this server was
written against.

## Troubleshooting

| Symptom | Fix |
|---|---|
| The game closes on launch (Android 7+) | That's the unpatched APK (`System.load` rule). Install the patched one from step 2. |
| *App not installed* / parse error on Android 5 or 6 | The patched APK carries a v2 signature those versions can't read. They don't need the patch: install the original APK. |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | Uninstall the official Pokémon GO first. |
| *Unable to authenticate*, nothing in the server window | DNS isn't reaching the server: DNS 2 not empty, Private DNS on, or the wrong IP. Re-do step 4 and the two checks in step 5. |
| *Unable to authenticate*, the server window shows the login | A different password from that trainer's first login. The Dev Panel's Manager page can reset it. |
| Certificate shows as installed but `/check` warns | It was installed as a VPN/app certificate. Remove it and install as **CA certificate**. |
| Spawns appear but the map jumps around | Location mode → GPS only; or use a mock-location app (step 6). |
| Pokémon are flat 2D sprites | Normal without the 2016 asset bundles; everything still works. See the README's *3D models* note. |
