# Knihovna Plexu

`https://plex.example.cz/smart/settings/#library` - první záložka Nastavení,
jen po přihlášení. To, co uměl `plex_tools.py` z konzole Windows, běží přímo
na Plex serveru.

## Jak se ptá Plexu

- Jen přes HTTP API Plex Media Serveru na `127.0.0.1:32400` (když Plex
  vyžaduje zabezpečené spojení, totéž přes HTTPS na 127.0.0.1).
- Vlastním tokenem serveru (`PlexOnlineToken` v `Preferences.xml`) - nikdo se
  nepřihlašuje k plex.tv, žádní uživatelé Home, žádný PIN. Token neopouští
  démona; stránky dostávají jen položky a zmenšené obrázky.
- Nikdy nesahá na soubory Plexu ani na jeho databázi, nic nerestartuje.
- Nejvýš tři dotazy na Plex najednou, zápisy s odstupem, metadata po 40
  položkách v jednom dotazu (seriál s 300 epizodami = 8 dotazů), vždy jen
  jedna úloha s průběhem a tlačítkem Přestat.
- Cokoli, co zapisuje, nejdřív ukáže plán; každá změna je v Událostech.
- Plakáty dělá menší transkodér Plexu, drží se v `/var/cache/plexmon/img`
  (nejvýš 400 MB) a prohlížeč je má týden v cache.

## Co umí

| | |
|---|---|
| Knihovna | plakáty, hledání (`*`, `?`), filtry (nezhlédnuté, rozkoukané, štítek, žánr, země, bez hodnocení), řazení, velikost; detail titulu: pozadí, hodnocení, 4K/HDR, herci s fotkami, soubory, štítky, stopy; u seriálu série a epizody |
| Více najednou | *Vybrat* (nebo Ctrl-klik, Shift pro rozsah): zhlédnuto, nezhlédnuto, zvuk a titulky, štítky, hodnocení, názvy, seznam ke zhlédnutí, obnovit metadata, export názvů do .txt |
| Zvuk a titulky | výchozí stopa filmu, celého seriálu, série nebo zaškrtnutých epizod - konkrétní stopa (externí .srt × vložené, vynucené × SDH); kde chybí: ponechat, vypnout titulky, jiná stopa; *Prohledat soubory znovu* pro čerstvě přidané titulky |
| Štítky | přidat / odebrat u více titulů, přejmenovat, odstranit z celé knihovny |
| Názvy | najde názvy v jiném jazyce a převede je - jen název (zamčený, koncovka *- Viki* zůstane), jazyk metadat + obnovení, nebo Opravit spárování na vlastní položku; rozdělené položky a zamčené názvy se vynechají |
| Hodnocení | IMDb a Rotten Tomatoes (OMDb), TMDB, TVmaze, Trakt; jen chybějící nebo všechna; zamknout nebo ne; nejdřív náhled |
| Seznam ke zhlédnutí | seznam vlastníka serveru (Plex Discover), co z něj je na serveru, přidat / odebrat |

Klíče pro hodnocení: *Nastavení → Knihovna Plexu* (TMDB, OMDb, Trakt;
TVmaze bez klíče).

## Příkazová řádka

```
plexmon --plex                                   server a knihovny
plexmon --plex-items Filmy --search matrix       tituly knihovny
plexmon --plex-show "Breaking Bad"               vše o titulu
plexmon --plex-tracks "Breaking Bad"             jaké stopy má
plexmon --plex-tracks "Breaking Bad" --audio eng --subs cze --dry-run
plexmon --plex-watched "Dune" "Alien"
plexmon --plex-label "Dune" --label-add 4K
plexmon --plex-titles Seriály                    názvy, které nejsou anglicky
plexmon --plex-titles Seriály --fix              ... a převést (jen z vlastní položky)
plexmon --plex-ratings Filmy --dry-run           chybějící hodnocení, jen ukázat
plexmon --plex-watchlist
plexmon --plex-scan Filmy                        prohledat soubory knihovny
```

V konzoli (`plexmon --console`): *11 The Plex library*.
