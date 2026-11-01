# Certificates from acme (acme push)

**acme push** lets *acme* - the certificate manager - put certificates into
plexmon and renew them by itself. plexmon is the **receiver** of acme push
protocol 1; acme is the sender (its target *An application of ours (acme
push)*). The protocol itself - the requests, the signature, the sealing of the
key, the test vectors - is acme's document *acme push - certificates from acme
to our other applications (protocol 1)*; this page says what plexmon does with
it.

## Additive only

acme push is one more way for a certificate to arrive. Nothing else changes:

- upload (PEM, PFX / PKCS#12), the request (CSR), self-signed, the TLS
  settings, download, *Let go*, Automatic certificates (plexmon's own ACME),
  Plex Media Server's own certificate, backups and the command line work
  exactly as before;
- it is **off by default**; switched off, `hello` says only that it is off and
  every other request is refused;
- a pushed certificate is stored exactly where an upload stores it (the TLS
  profile of this server), goes through the same path (the files of the Plex
  vhost, `nginx -t`, reload, everything put back on a refusal; Plex Media
  Server given it too when `plex_cert` is on), and counts as uploaded by hand:
  Automatic certificates leave it alone from then on (*Look after it* there
  gives it back);
- an upload, a request, a self-signed certificate or Automatic certificates
  can replace it at any time; acme's inventory then shows the slot as kept
  that way;
- switching off, forgetting acme's key, or deleting acme's target leaves
  plexmon working with the certificate it has.

## Setting it up

1. Settings → **Certificates and TLS** → **Certificates from acme**:
   **Take certificates from acme** on (a full administrator), then **Save the
   changes** at the bottom. Every change on the card - the two switches, a key
   pasted, an acme forgotten - waits for that button, as every other setting
   does; *Put them back* throws them away.
2. **Pair with an acme**: a code `XXXX-XXXX-XXXX-XXXX` is shown once; it lasts
   15 minutes and is gone after 5 wrong tries (*Take it back* drops it).
3. In acme: Targets → *An application of ours (acme push)*; the address is the
   one the card shows (**The address for acme**, e.g.
   `https://plex.example.com/smart/settings/`), the code in *Pairing code*;
   **Try**. acme then appears under **Paired with**.
4. In acme: **Discover** shows plexmon's slots; make the certificate and its
   deployment there.

Instead of the code, acme's key (`acme-push-ed25519 AAAA… comment`) can be
pasted: **Or paste acme's key** (taken on Save). At most 8 acme keys are
kept; **Forget it** ends one (on Save).

## The address and the requests

The requests go to the Settings page, before any sign-in:

| request | |
|---|---|
| `GET  /smart/settings/?api=acme-push-hello` | unsigned: protocol, `app` = `plexmon`, version; switched on also `app_id`, `enc_key`, the slots |
| `POST /smart/settings/?api=acme-push-pair` | signed with the key in the body, with the HMAC proof of the code |
| `POST /smart/settings/?api=acme-push-inventory` | signed: the slots and what is in them |
| `POST /smart/settings/?api=acme-push-install` | signed: a certificate, its key sealed to plexmon's X25519 key |

The page decides nothing: it hands the raw body and the five
`X-Acme-Push-*` headers to the daemon (`/api/acme-push/OP` on localhost, with
the daemon's token) and passes the answer back. Bodies are at most 1 MiB.
nginx's access list of the pages still applies: acme's address must be able
to reach `/smart/settings/`.

## The slots

| slot | names | managed_by | |
|---|---|---|---|
| `node:<host>` | the name of these pages (`acme_node_name`, else the name of the certificate there, else the machine's full name) | `none`, `manual` (uploaded, self-signed, a request), `own-acme`, `acme-push` | takes a certificate; dual (RSA + ECDSA) |
| `plex` (only with `plex_cert` on) | `plex_cert_domain`, else the pages' name | `follows:node:<host>` | not installable: Plex Media Server takes the certificate of the pages by itself |

plexmon is one machine: `cluster.on` is false and there is no `vip` slot.
`slots` of an install may be `auto`, `this`, `nodes`, `node:<host>` or the
host's name alone.

## What plexmon checks and does on install

1. The signature, in the protocol's order: the version, the headers
   (`unsigned`), a paired key (`unknown_key`), the time within 300 s
   (`stale`), Ed25519 (`bad_signature`), a nonce not seen in 900 s
   (`replay`; kept in the database, meta `acme_push:nonces`).
2. The fingerprint is the SHA-256 of the certificate's DER; the sealed key
   opens (`bad_key`), is the certificate's (`key_mismatch`), the certificate is
   valid now (`expired`, `not_yet_valid`) and holds the slot's names
   (`names`) - unless **Take certificates of other names** is on and acme says
   `check_names: false`.
3. A dual one (`alt`): the RSA certificate is the main one (nginx's first, and
   the one Plex Media Server takes), the ECDSA one goes beside it.
4. The same certificate (and twin) already put there by acme push is
   `already` and nothing is touched, unless `force`.
5. Stored like an upload with `how = "acme push: <acme's name>"`, Automatic
   certificates told to leave it alone, an event written (which slot, names,
   issuer, until when, which acme); nginx takes it within seconds.

Refusals are logged with their code only; the body is never logged. The card
shows the last push and the last refusal.

## Keys and where they are kept

| | meta key | |
|---|---|---|
| plexmon's `app_id` and X25519 key | `acme_push:box` | made once; the private half sealed with the installation's key |
| acme's keys | `acme_push:keys` | Ed25519 public keys, at most 8 |
| the pairing code | `acme_push:code` | sealed, 15 minutes, 5 tries |
| the switches | `acme_push:conf` | `on`, `other_names` |
| seen nonces | `acme_push:nonces` | 900 s, at most 20 000 |
| the last push / refusal | `acme_push:last`, `acme_push:refused` | for the card |

The cryptography (Ed25519, X25519, HKDF-SHA256, AES-256-GCM) uses the Python
package `cryptography` where it is installed and plain Python otherwise; both
are checked against the protocol's test vectors. The card says which one is
used.
