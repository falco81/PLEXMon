# Plex library

`https://plex.example.cz/smart/settings/#library` - the first tab of Settings,
only once signed in. What `plex_tools.py` did from a Windows console runs right
on the Plex server.

## How it asks Plex

- Only through the HTTP API of Plex Media Server on `127.0.0.1:32400` (when Plex
  requires a secure connection, the same over HTTPS on 127.0.0.1).
- With the server's own token (`PlexOnlineToken` in `Preferences.xml`) - nobody
  signs in to plex.tv, no Home users, no PIN. The token never leaves the
  daemon; the pages get only the items and scaled-down pictures.
- It never touches Plex's files or its database, and restarts nothing.
- At most three requests to Plex at once, writes spaced out, metadata 40
  items to a request (a show with 300 episodes = 8 requests), always only
  one task with progress and a Stop button.
- Anything that writes shows the plan first; every change is in Events.
- Posters are made smaller by Plex's transcoder, kept in `/var/cache/plexmon/img`
  (at most 400 MB), and the browser caches them for a week.

## What it does

| | |
|---|---|
| Library | posters, search (`*`, `?`), filters (unwatched, in progress, label, genre, country, without a rating), sorting, size; a title's detail: background, ratings, 4K/HDR, actors with photos, files, labels, tracks; for a show, its seasons and episodes |
| Many at once | *Select* (or Ctrl-click, Shift for a range): watched, unwatched, audio and subtitles, labels, ratings, titles, watchlist, refresh metadata, export the titles to .txt |
| Audio and subtitles | the default track of a movie, a whole show, a season or the ticked episodes - a particular track (external .srt × embedded, forced × SDH); where it is missing: leave it, switch the subtitles off, another track; *Scan the files again* for freshly added subtitles |
| Labels | add / take off on many titles, rename, remove from the whole library |
| Titles | finds titles in another language and converts them - the title only (locked, the ending *- Viki* is kept), the metadata language + a refresh, or Fix Match to their own entry; split items and locked titles are skipped |
| Ratings | IMDb and Rotten Tomatoes (OMDb), TMDB, TVmaze, Trakt; only the missing ones or all; locked or not; a preview first |
| Watchlist | the server owner's list (Plex Discover), what of it is on the server, add / remove |

The keys for the ratings: *Settings → Plex library* (TMDB, OMDb, Trakt;
TVmaze needs no key).

## Command line

```
plexmon --plex                                   the server and its libraries
plexmon --plex-items Movies --search matrix      the titles of a library
plexmon --plex-show "Breaking Bad"               everything about a title
plexmon --plex-tracks "Breaking Bad"             what tracks it has
plexmon --plex-tracks "Breaking Bad" --audio eng --subs cze --dry-run
plexmon --plex-watched "Dune" "Alien"
plexmon --plex-label "Dune" --label-add 4K
plexmon --plex-titles Shows                      titles that are not in English
plexmon --plex-titles Shows --fix                ... and convert them (only from their own entry)
plexmon --plex-ratings Movies --dry-run          the missing ratings, only shown
plexmon --plex-watchlist
plexmon --plex-scan Movies                       scan the library's files
```

In the console (`plexmon --console`): *11 The Plex library*.

## Since 2.5.0 - the rest of plex_tools

- **Watched**: *Flip each* (each movie and episode on its own - the watched
  ones unwatched, the others watched) in the selection dock, for a season, for
  ticked episodes and as `plexmon --plex-flip-watched TITLE…`. Episodes stay
  ticked from season to season; the ticked ones can be marked watched,
  unwatched, flipped or given their tracks together.
- **Selection**: *Invert* and *Unselect the titles shown* (plex_tools' `i`
  and `-`) in the library, Titles and Ratings.
- **Labels**: the filter *without a label*; *As Plex keeps them* (the exact
  strings and ids, case variants marked); renaming a label on the selected
  titles only.
- **F5 / F9**: Plex's activities followed to their end in a panel at the foot
  of the page.
- **Audio and subtitles**: replacement after replacement where the track
  chosen is missing (then the next, then the next).
- **Titles**: *Other ways…* shows every title before it changes (now → after,
  held back, without an entry of its own); locks filtered by title, sort
  title, either, any field; *Refresh their metadata* of the ticked ones.
- **Ratings**: *Refresh their metadata* of the ticked ones.
- **Watchlist**: several taken off at once; a bookmark on the posters of the
  titles on it and a filter in the library; adding sends only what is not on
  it yet (and says how many were); the full detail of a title not on this
  server from Plex Discover (plot, ratings, genres, director, cast).
