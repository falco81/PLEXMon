# Insights and notifications (2.12.0)

The **Insights** tab (after Library integrity) shows what happens on the Plex server.
**Settings → Notifications** says how plexmon tells you when something needs you.

## How it keeps Plex safe

- **Read only.** Plex's own API on 127.0.0.1 with its own token, and - for Transcoding,
  Subtitles and audio, Duplicates and Episodes - a copy of its database made with SQLite's
  online backup, exactly as Library integrity does it. These copies run in the slot of
  Settings → The database: never beside a copy, a restore or a fix.
- **A sleeping disk is never woken.** Only *New files* reads the data disks, and only a disk
  plexmon knows to be awake; a file on a sleeping disk, or on one plexmon cannot tell about,
  waits for the next look. A file written less than ten minutes ago is left alone.
- **Nothing of the library is deleted.** Duplicates only shows. The cleanup of *Plex server*
  takes only files of Plex's caches, inside Plex's own folder, older than said, never through a
  symbolic link.
- **The two things that change Plex** - a restart and an update of plexmediaserver - are buttons
  that ask first, and say so when someone is watching. The watchdog restarts Plex by itself only
  when that is chosen (see below).

## Watching

Once a minute plexmon reads `/status/sessions` and keeps each play in its own database
(table `plays`, 400 days): who, the player, whether it was played directly, streamed directly or
transcoded, and why (the video, the audio, subtitles burned in, the container), and how long it
was watched. The plays per day and the people come from Plex's own history
(`/status/sessions/history/all`), so they go back before 2.12.0; *how* they were played only
from 2.12.0 on (the grey part of the bars).

## Transcoding

- **Transcoded lately**: from the plays seen, with the reasons and what helps.
- **The library**: every file of a copy of the database, counted by what most often makes a
  player ask for a transcode - HEVC, AV1, VP9, MPEG-2, VC-1, 4K, more than 40 Mb/s, TrueHD or
  DTS audio, picture subtitles (PGS, VobSub), old containers (AVI, WMV, TS...). A file is not
  transcoded on every player for these; they are where to look.

## Episodes

Every show of Plex is looked up on TVmaze by its TheTVDB or IMDb number (TVmaze must be switched
on in Settings → Plex library; no key is needed). Aired episodes that are not in Plex are listed
per show, specials (season 0) left out; what comes in the next 60 days is an agenda. A show is
asked again after a day, one that has ended after a week (`/var/lib/plexmon/insights/`).

## Subtitles and audio

The languages wanted are set in Settings → Notifications → *Plex Media Server watched*:
`ins_sub_langs` (default `cs`) and `ins_audio_langs` (empty: not looked at), as two- or
three-letter codes (`cs` also matches `cze` and `ces`). Read from the streams Plex knows of - a
subtitle file beside the video counts.

## Duplicates

A film or an episode with more than one media item (versions), the one to keep marked by the
highest resolution, then the highest bit rate; and films of one library with the same match
that Plex did not merge.

## New files

Every hour (setting `ins_newfiles`), up to twenty of the files Plex added lately are read with
`ffprobe -show_entries format=duration:stream=codec_type,codec_name`: *reads right*, *with a
warning*, *damaged* (does not open, no picture), *short* (more than a minute and 10 % shorter
than Plex says - a download not finished). A damaged or short file is an event and a
notification. Needs ffprobe (`dnf install ffmpeg`, from RPM Fusion).

## Plex server

- Whether it answers (`/identity`), its processor (of all cores together) and memory from
  `/proc`, what it is busy with, its version.
- **The watchdog** (`plex_watch`): *a message only* (default), *restarted, and a message*, or
  off. Plex is taken for hung only when its service runs and it has not answered for
  `plex_watch_min` minutes (default 5) in a row. It is never restarted in maintenance, while
  plexmon itself has stopped it (P1 of Library integrity), while its database is put back, or
  more than once in half an hour. Over 90 % of the processor for 15 minutes with nobody watching
  and nothing of its own running is a warning too.
- **A new version** (`plex_update_check`): `dnf check-update plexmediaserver` once a day, in a
  transient unit of its own, through the proxy if Settings → Proxy says so. It is put in only by
  *Update Plex Media Server* (`dnf upgrade plexmediaserver`).
- **Plex's folder**: its size folder by folder, and what of its caches can go:
  `Cache/PhotoTranscoder` (older than 30 days), `Cache/Transcode` (older than a day, and only when
  nobody is transcoding), `Crash Reports`, `Diagnostics`, `Updates` (older than 14 days).

## Notifications

E-mail through an SMTP server (STARTTLS, TLS or none), Telegram (a bot of your own and a chat ID)
and ntfy (a topic, optionally a token). Passwords and tokens are kept sealed. Telegram and ntfy
go through the proxy when *Notifications* is switched on in Settings → Proxy; e-mail goes
straight to its server.

What is sent, each a switch:

| | when |
|---|---|
| Disks | the dashboard warns of something new (SMART, temperature, space) - and when all is well again |
| Plex Media Server | it has not answered for the time set, or its service stopped by itself |
| Backups | a backup or a copy of a database failed |
| Library integrity | the look on a schedule finds more than the last one |
| Certificates | a renewal failed, a certificate runs out |
| New files | a new file does not read right |
| New versions | a new plexmediaserver is there |

The same thing is sent once in six hours at most, twenty messages an hour at most. *Send a test*
beside each way; *What was sent* lists the last sixty, with how each way answered.

## Library integrity on a schedule

`ix_auto`: every night or every week (`ix_auto_every`, `ix_auto_day`) at `ix_auto_at`, the
database only or the awake disks too (`ix_auto_mode`) - the same look as *Look through it* in
Library integrity. Nothing is put right by itself. A notification only when the look finds more
of P1, P2, P5, P6 or P7 than the last one. `ix_auto_optimize`: Plex's own Optimize on the first of
the month, a checked copy of its database first.
