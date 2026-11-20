# Library integrity (the Library integrity tab, 2.11.0)

Finds where Plex Media Server's library no longer agrees with itself or with the disks, and
puts right what can be put right safely. The problems are P1 to P8 below.

## How it keeps Plex safe

- **Read from a copy.** Every finding comes from a copy of the library database, made with
  SQLite's own online backup (read-only on Plex's file). Plex goes on; nothing of its file is
  written to look. The copy is made where the copies of Plex's database are kept and deleted
  after.
- **A sleeping disk is never woken - unless asked.** The disks are read (P5, P6, P8 on the
  disk) with *The disks that are awake* (the default), and then only the folders of disks that
  are awake now. *All disks - wake them first* (2.11.1, asked first) spins every data disk up
  with the dashboard's own Wake and waits until each disk really turns - an O_DIRECT read of
  its probe file returns only then (2.11.2; five minutes at most) - and reads them all. *The database only* reads no disk. plexmon knows
  that from its own disk monitor; it is asked again just before each folder is read. A disk
  asleep, or one plexmon cannot tell about, is left out and named. A library folder that is
  not there, is empty while it sits on the system disk (what an unmounted disk leaves under its
  mount point), or misses more than 5 % (and at least ten) of its files is taken for a disk not
  mounted, never for deleted files. An empty folder on a disk of its own that is mounted is a
  folder made ready and not filled yet.
- **One write into Plex's database: P1.** Everything else goes through Plex's own HTTP API.
- **A checked copy before anything is changed.** Before P1, P2 and Plex's own Optimize, a copy
  of Plex's database is made and checked (Settings → The database lists it, and it can be put
  back from there). If the database is not whole even now, nothing is done.
- **Never a file deleted.** `DELETE /library/metadata/{id}/media/{mediaId}` is never called.
- **Merge and Split only when Plex is quiet.** `/activities` must stay empty for a while
  (30 s, 60 s before the last look). P2 waits up to four hours (2.11.4): after merges and scans
  Plex analyses the library for an hour or more. Before the split, Stop stops it and nothing is
  done; past the split the job is finished, never stopped halfway.
- **One job at a time**, shared with Settings → The database: no copy or restore of Plex's
  database runs during a fix, and no fix runs during one. Every step goes to Events, and the
  fixes are listed under *What was put right here*.

## The problems

| | What | Class | Done here |
|---|---|---|---|
| P1 | A media item points at an episode that is not there: Plex shows the file nowhere | safe | Plex stopped → Plex SQLite → scan |
| P2 | A file from one show's folder attached to another show on the same match | needs your yes | Split + Merge through the API |
| P3 | Several shows on one match in one library | information | advice: a library of its own |
| P4 | In Plex's logs: written back after a merge destroyed it; a merge or split while analyses were scheduled | information | - |
| P5 | A video file on the disk that Plex has no record of, with its likely cause | information | a scan of the folder ("not scanned yet" only) |
| P6 | A file Plex has that is not on the disk | safe | scan + empty the trash, with guards |
| P7 | Empty seasons and shows, episodes with no file; children without a parent | safe / information | empty the trash (the empty ones) |
| P8 | Gaps in the numbering of a season | information | a missing number found on the disk is marked P1, P2 or P5 |

Before P1, plexmon checks that the indexes of `metadata_items` and `media_items` agree with
their tables: `count(*)` against `count(*) WHERE id+0 > 0`. If they do not, rows Plex cannot
find by their number would look like P1, so nothing is put right and the page says so.

### P1, step by step

1. A fresh copy is looked at again. Only what is still an orphan is touched.
2. A checked copy of Plex's database is made.
3. Plex Media Server is stopped. Whoever is watching is told first: if someone is, it has to be
   asked again with *Stop it anyway*. A marker is written, so that if plexmon itself does not
   live to start Plex again, Plex is started when plexmon starts.
4. `com.plexapp.plugins.library.db`, `-wal` and `-shm` are copied aside to
   `/var/lib/plexmon/plex-db/before-integrity-<time>/`.
5. One transaction through **Plex SQLite as Plex's own user** (the owner of the database
   folder), with `.bail on` and the finding itself as the guard:
   `media_streams`, `media_parts`, `media_items` of the chosen ids whose `metadata_item_id` is
   not in `metadata_items`, and parts whose media item is not there. Nothing else.
6. `PRAGMA integrity_check` must say `ok`. Otherwise the files are put back as they were.
7. Plex Media Server is started, whatever happened before.
8. Their folders are scanned (`refresh?path=`), only folders on disks that are awake. A file on a
   sleeping disk is taken in at Plex's next scan of the library.
9. Looked at again once Plex is quiet. If the show is one of several on one match (P3), the file
   may go under another show: that is P2.

### P2, step by step (only Plex's API)

Quiet → a checked copy → the shows of the match read (`allLeaves`) → **Split** of the show the
files went to → the new shows found **by their folders**, never by their number (anything
unexpected: STOP, nothing merged) → **Merge** of the new small show into the folder's owner
(the owner keeps its title, poster and locked fields) → the show's own folders merged together
again when it had more than one → the titles that were locked put back → looked at again,
the episodes counted against what was expected.

A case is not for a button when the two shows are not on one match, or when a folder's files
are split evenly between shows (its owner is not clear). The way to be rid of P2 and P3 for good
is a library of its own for each version: Plex matches only within one library.

### The trash (P6, P7)

The trash of a library is emptied only when **every** folder of the library is there and awake,
and after its folders were scanned a fresh copy of the database shows **no more than 5 %** of any
folder in the trash (fewer than ten files always pass: a few files gone from a small folder are
files gone, not a disk). Otherwise nothing is emptied, and the page says why.

## Plex's own Optimize and Clean Bundles

Settings → The database runs them as a job followed to the end. Optimize makes a checked copy of
the database first. Both are asked of Plex, and their progress is read from `/activities` (its
`progress`) and from the database files until Plex is done. The bar is shown beside the buttons,
and the last run of each is listed with its time and, for Optimize, the size before and after.

## A package for analysis (2.11.2)

*A package for analysis* puts nothing right: a fresh copy of the database is read and written
out in full into a ZIP to download - every finding without the page's limit, every episode and
file of the shows on one match, the tables and their rows, the schema, the index check, the lines
of Plex's logs that matter with tokens taken out, Plex, plexmon and every library folder with its
disk, the history, and what a fix of each P2 case would do. Not the database, no token, no
password; it does hold the names and paths of the library. The last three are kept in
`/var/lib/plexmon/integrity/packages/`.

## The report

*Download the report* gives a text file with every finding: the folders and their state, every
P1 row with its media item and missing metadata, every P2 case with its files, and so on. It
holds no secrets, but it does hold the names and paths of your library.
