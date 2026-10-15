# channels.m3u

`build_sports_m3u.py` turns an Xtream Codes account into a sports-only M3U
playlist: live sports channels from every country, HD and SD only, grouped by
country. Python 3 standard library only.

## Usage

```sh
export XTREAM_SERVER=http://your-server.example   # as entered in your player
export XTREAM_USER=...
export XTREAM_PASS=...
python3 build_sports_m3u.py -o sports.m3u --report sports.md
```

Options:

| Flag | Purpose |
|---|---|
| `--api URL` | Query the API on a different address than the one written into the playlist (e.g. the `https://` one) |
| `--ext m3u8` | Use HLS links instead of MPEG-TS (`ts`, the default) |
| `--cache DIR` | Save the API responses; add `--offline` to rebuild from them without contacting the server |
| `--report FILE` | Write a Markdown summary: channels per group and why the rest were left out |

The playlist header points players at the server's XMLTV guide.

## What gets kept

- **Sports channels only**, recognised by channel name (Sky Sports, beIN, ESPN,
  Eurosport, Arena Sport, TRT Spor, SuperSport, ...), so sports channels filed
  under general country categories are found too.
- **HD and SD only.** FHD, UHD/4K/8K, HEVC and RAW variants are dropped. Channels
  without a quality tag are kept as the standard feed.
- **No event slots.** PPV channels, per-game feeds, team feeds, replays, F1/MotoGP
  onboard feeds, time-shifted copies and 24/7 loops are dropped: their names
  change per event and they are empty most of the time.
- **One entry per channel and quality.** Backups and the same channel listed
  in several categories are collapsed, keeping the copy with guide data.
- **Grouped by country**, A to Z, then World, Cricket, Horse Racing and
  Motorsport. Channels are sorted naturally inside each group (1, 2, ... 10).

The rules (keywords, quality tags, country mapping) are plain constants at the
top of the script.

## Keep playlists out of git

Every stream link contains the account's username and password, so generated
playlists are ignored by `.gitignore`. Don't publish them.
