# Settings

`https://<server>/smart/settings/` - the administration taken over from otp: the same
pages, the same controls (header, tabs, the bar on the left, windows, the *Save the
changes* bar at the bottom). The daemon always decides; the PHP pages only ask it on 127.0.0.1.

## Accounts

- **full admin** may do everything, **read only** only looks.
- A password set by someone else is changed by its owner at the first sign-in.
- *Second step* of signing in: `admin_2fa` (Accounts → the settings at the bottom) - a code
  from an authenticator app; reset: the button by the account or `plexmon --reset-2fa NAME`.
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

## Languages

The pages are written in English; `lang/cs.json` (`/var/www/html/smart/lang/`) says
what each sentence is in Czech - the same engine as isemon and otp. The default language:
*About → Language of the pages* (`ui_lang`, for the dashboard too); everyone may
choose their own (your name → Language, on the dashboard the EN / CS switch).

The file can be edited: change only `text`, never `source`; an empty `text` = English.
`{0:jeden|dva až čtyři|pět a víc}` picks the Czech form by the number. A new language =
a new file with its own `code`; it appears in the menus by itself.
