# Automatic certificates (ACME)

*Settings → Automatic certificates*. The Plex server's certificate from Let's Encrypt
(or another ACME CA: staging, step-ca, your own), renewed by itself when fewer
than `acme_days` (30) days are left - at most a third of its life.

1. Switch on *Certificates from an ACME CA*, fill in the e-mail, the name (`acme_node_name`,
   e.g. `plex.falco81.net`) and the way of validation; *Save the changes*.
2. *Try port 80* (for HTTP-01), *Check DNS*.
3. *Order it now*. The progress shows on the card; *What the last order did*.

## HTTP-01

The CA asks for `http://NAME/.well-known/acme-challenge/...`. nginx answers from
`/var/www/plexmon-acme` (install.sh added that to the Plex vhost); port 80 must
be reachable from the internet (the router forwards 80 to the server, firewalld service
`http`). A redirect to https does no harm - the CA follows it.

## DNS-01

For a server that cannot be reached from the internet: the CA reads the TXT record
`_acme-challenge.NAME`.

| `acme_dns` | |
|---|---|
| `desec` | deSEC.io for free: a domain `something.dedyn.io` + a token; in your DNS a CNAME `_acme-challenge.plex.example.cz` → to deSEC, once (the *In your DNS* card says exactly what) |
| `cloudflare` | an API token with Zone - DNS - Edit |
| `rfc2136` | nsupdate with a TSIG key (BIND, PowerDNS, Knot); `dnf install bind-utils` |
| `acmedns` | an acme-dns server |
| `manual` | the record is shown on the page; you put it in by hand at every order |

## What happens to the certificate

The certificate from the CA replaces the one in nginx (and in Plex, if `plex_cert` is on).
The one before (a commercial one, say) is kept: *Back to the one before*. A certificate
uploaded by hand is not overwritten by ACME until *Look after it* / *Order it now* is pressed.

Command line: `plexmon --acme-status`, `--acme-probe`, `--acme-order`.
