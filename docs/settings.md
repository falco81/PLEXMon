# Settings

`https://<server>/smart/settings/` - the administration taken over from otp: the same
pages, the same controls (header, tabs, the bar on the left, windows, the *Save the
changes* bar at the bottom). The daemon always decides; the PHP pages only ask it on 127.0.0.1.

## Accounts

- **full admin** may do everything, **read only** only looks.
- A password set by someone else is changed by its owner at the first sign-in.
- *Second step* of signing in: `admin_2fa` (Accounts → the settings at the bottom) - a code
  from an authenticator app; reset: the button by the account or `plexmon --reset-2fa NAME`.
- **Passkeys - Face ID, Touch ID, Windows Hello** (`passkeys`, off by default): an account
  signed in with its password adds a passkey of its device in *Your account → Add a passkey of
  this device*; the sign-in page then offers *Sign in with a passkey* - no name, no password,
  the face, a finger or the device's PIN. Only on the pages opened by their **name** over
  **HTTPS** with a certificate the browser trusts (`https://plex.example.com/smart/settings/`,
  not an address), the same name to add and to sign in; on an iPad the iCloud Keychain on.
  With the second step on, a passkey is both steps (`passkey_is_2fa`, on by default; off: the
  code is asked for after it). The server keeps only the public half; a full administrator sees
  and takes away anybody's passkeys by the key button beside the account (a lost iPad).
  Command line: `plexmon --set passkeys=1`, `plexmon --passkeys [NAME]`,
  `plexmon --passkey-forget ID|all`.
- *Signed in now* - ending a sign-in elsewhere; *Locked out* - addresses and names
  with failed attempts, freed with one click.
- *Columns of the tables* - everyone lays the tables out their own way (your name → Edit the
  columns); export / import of your own settings as JSON.

## Certificates and TLS

The card of the server's certificate: who issued it, until when it is good, the names (SAN), the chain,
the root, the state in nginx. The buttons:

| | |
|---|---|
| Upload | PEM (certificate, key, chain, root - in any order) or PFX / PKCS#12 with its password; a **dual** certificate (RSA + ECDSA of the same names) as both pairs in PEM, two PFX files, or the ZIP *Download → Both certificates* gives - RSA is the main one, ECDSA goes beside it, each with its own chain |
| Request (CSR) | the key is made here and stays here; the request goes to your CA, you upload only the certificate |
| Self-signed | for a try, or until the real one comes |
| TLS settings | Standard (TLS 1.2 + 1.3, ECDHE), Mozilla intermediate / modern, custom; DH parameters; the server picks the cipher; HSTS |
| Download | in the format chosen (PEM, DER, PKCS#7, PFX, with or without the private key); for a dual certificate **Both certificates** gives the RSA and the ECDSA one - each with its own chain and key - in one ZIP |
| Let go | plexmon stops looking after TLS, nginx keeps the files |

The certificate is written to the files the Plex vhost names
(`/etc/nginx/certs/server.pem` + `.key`), the TLS settings to
`/etc/nginx/plexmon/tls.conf`; nginx is asked (`nginx -t`) and reloaded; if it
refuses, everything is put back.

### Certificates from acme (acme push)

A card of its own, **off by default**: switched on, an *acme* - the
certificate manager - may put its certificates in here and renew them by
itself. It is one more way for a certificate to arrive and replaces nothing:
upload, request, self-signed, the TLS settings and Automatic certificates work
as before. The details are in [acme-push.md](acme-push.md).

### Plex Media Server uses this certificate too

Plex Media Server (port 32400) has its own certificate `*.plex.direct`. With
`plex_cert` on, it also gets plexmon's certificate as its *custom certificate*:
`/var/lib/plexmediaserver/plexmon/plexmon.p12` (a random password, sealed);
`customCertificatePath`, `customCertificateKey`
and `customCertificateDomain` are set in `Preferences.xml` (the domain = `plex_cert_domain`, otherwise the certificate's CN).
Plex reads the certificate only when it starts: with `plex_cert_restart` (the default) it is restarted
(playback stops for a moment). Every further certificate - renewed through
ACME too - goes to Plex as well. *Plex back to its own* removes the names from Preferences.xml.
In Plex then set *Custom server access URLs* to `https://plex.example.cz:32400`.

## On an iPad

*Share → Add to Home Screen* in Safari: plexmon gets its own icon and opens in a window of
its own, without Safari's bars, turning with the iPad (the dashboard can be added the same
way, as *Plex status*). The home-screen app keeps its own sign-in: sign in there once (with a
passkey it is one tap and the face). With a finger, what the mouse points at opens with a tap:
the counts of the library (*in 10 of 12*) open their card, tapped again or anywhere else it
closes; a button's explanation comes by holding the finger on it, and the button itself works
with the first tap. A popup is never taller than what the keyboard on the screen leaves. An
iPad with a trackpad or a mouse points as a PC does.

## Languages

The pages are written in English; `lang/cs.json` (`/var/www/html/smart/lang/`) says
what each sentence is in Czech - the same engine as isemon and otp. The default language:
*About → Language of the pages* (`ui_lang`, for the dashboard too); everyone may
choose their own (your name → Language, on the dashboard the EN / CS switch).

The file can be edited: change only `text`, never `source`; an empty `text` = English.
`{0:jeden|dva až čtyři|pět a víc}` picks the Czech form by the number. A new language =
a new file with its own `code`; it appears in the menus by itself.
