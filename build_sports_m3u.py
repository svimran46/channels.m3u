#!/usr/bin/env python3
"""Build a sports-only M3U playlist (HD and SD only) from an Xtream Codes account.

Credentials come from the environment, never from this file:

    XTREAM_SERVER=http://example.com XTREAM_USER=... XTREAM_PASS=... \
        python3 build_sports_m3u.py -o sports.m3u

What ends up in the playlist:
  * live channels whose *name* identifies a sports channel (Sky Sports, beIN,
    ESPN, Eurosport, Arena Sport, TRT Spor, ...), from every category;
  * only HD or SD variants: anything tagged FHD / UHD / 4K / 8K / HEVC / RAW
    is dropped, untagged channels are kept (they are the standard feed);
  * no PPV / event slots, game feeds, replays, team feeds or 24/7 loops, whose
    names change per event and are empty most of the time;
  * one entry per channel and quality (backup feeds are collapsed), grouped
    by country, sorted naturally inside each group.
"""

import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

# Quality tags, matched against tokens of the NFKC-normalised name, so the
# provider's superscript tags (ᴴᴰ, ᵁᴴᴰ, ᴿᴬᵂ, ʰᵉᵛᶜ) are understood too.
OTHER_Q = {"FHD", "UHD", "4K", "8K", "HEVC", "H265", "RAW", "1080", "1080P",
           "1080I", "1440P", "2160P", "3840P", "1920P", "FULLHD"}
HD_Q = {"HD", "720", "720P"}
SD_Q = {"SD", "LQ", "LOW", "LOWER", "480", "480P", "576", "576P"}

# A channel is a sports channel when its name (country prefix removed) matches.
SPORT_RE = re.compile(r"""
    SPORT | \bSPOR\b | DEPORT | DESPORT | \bESPN | \bDAZN
  | \bBEIN\s*(SPORT|NEWS|MAX|XTRA|AFC|\d) | \bTSN\s*\d*\b | \bRDS\b
  | \bGOLF(?!O) | TENNIS | \bTENIS\b | \bCRIC | CR\s?LIFE | \bWILLOW\b | \bRUGBY
  | \bSNOOKER | \bDARTS\b | \bCYCLING | RACING | \bATG\b | \bEQUIDIA
  | \bTVG\s+NETWORK | \bHORSE | CLIPMYHORSE | \bHPI\s?TV | \bTJK\b | FANDUEL
  | \bHOCKEY | FOTBOLL | \bTV\s?4\s+MOTOR | MOTOWIZJA | AUTOMOTO | \bMAV\s?TV
  | \bFUEL\s?TV | RED\s?BULL\s?TV | \bUNBEATEN\b | \bCOMBATE?\b | \bNBA\b
  | \bNESN | \bMASN\b | \bMSG\b | \bYES\s+NETWORK | \bCSN\b | SPACE\s+CITY\s+HOME
  | \bACCN\b | L'?\s?EQUIPE | \bA\s?BOLA\b | \bPRO\s+ARENA | \bASTRO\s+ARENA
  | PREMIER\s*LEAGUE | GRANDSTAND | BADMINTON | (\bHUB|STARHUB)\s+PREMIER
  | ^PREMIERE\s*(\d|CLUBES) | \bTEN\s*[1-5]\b | \bGEO\s+SUPER | \bIDMAN\b
  | \bFBTV\b | (VIZION|CABLENET)\s+SP\s?\d | \bARENA\s+(ADRENALIN|FIGHT)
  | FIFA\s*TV | COPA\s+DEL\s+REY | BAYERN\s+TV | MANCHESTER\s+UNITED
  | \bNBA\s*TV | \bNFL\s*(NETWORK|RED\s*ZONE) | \bREDZONE | \bNHL\s*NETWORK
  | \bMLB\s*(NETWORK|STRIKE) | \bFS[12]\b | \bBTN\b | \bBIG\s*TEN
  | \b(SEC|ACC)\s*NETWORK | \bLONGHORN | \bPAC-?12 | \bSTADIUM\b
  | \bFIGHT | \bUFC\b | \bWWE\b | \bBOXING | BOXNATION
  | MOTORVISION | \bF1\b | FORMULA\s*1 | MOTO\s*GP | \bNASCAR | \bINDYCAR
  | \bGOL\b | \bGOLTV | \bTYC\b | \bTUDN\b | F[UÚ]TBOL | FUTEBOL | FOOTBALL
  | SOCCER | CALCIO | LA\s*LIGA | LALIGA | LIGA\s+DE\s+CAMPEONES
  | \bLIGUE\s*1 | BUNDESLIGA | \bSSC\b | AL\s*KASS | THMANYAH | \bSPOTV
  | \bSETANTA | \bSONY\s+(TEN|SIX) | \bMUTV\b | \bLFC\s*TV | \bCHELSEA\s*TV
  | \bBAR[CÇ]A\s*TV | REAL\s*MADRID\s*TV | \bBENFICA\s*TV | \bOLYMPIC
""", re.X)

# Names that match SPORT_RE but are not sports channels.
NOT_SPORT_RE = re.compile(
    r"\bE-?SPORTS?|WILLIAMSPORT|TRANSPORT|PASSPORT|\bRDS (SOCIAL )?TV")

# Event slots and other things that are not a 24/7 channel.
SLOT_RE = re.compile(r"""
    \bPPV\d*\b | \bEVENTS?\d*\b | LIVE-?EVENT | \bREPLAY | REPETICI | \bENDED\b
  | ^END\s*\| | \bEXCLUSIVE\b | \bNO\s+EVENT | \bTEAM\s*\| | \bONBOARD
  | NUR\s+W[AÄ]HREND | \bKWALI | \bRADIO\b
  | \b\d{1,2}:\d{2}\b | \b\d{1,2}\s?(AM|PM)\b | \d{4}-\d{2}-\d{2}
  | \b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+\d{1,2}\b
  | \sVS?\.?\s | \s@\s | :\s*$ | \([^)]*\bMATCH\b | LIVE\s+ONLY
  | \+\d+H\b                                     # time-shifted copies
  | (^|\|)\s*[A-Z+]{2,6}\s*\d{1,3}\s*:          # game slots: "NCAAF 02: ESPN2"
  | (^|\|)\s*MLS\s+(?!NETWORK)                   # MLS team feeds
    # F1 / MotoGP multi-feeds: onboards, driver cams, data and timing
  | \bF1\s*CAR\b | ON\s?B+OARD | \bOB\s?\d | \bHELI | TRACKER | \bDATA\b
  | LIVE\s+TIM | LIVE\s+TRACK | PIT\s?LANE | \bAMBIENT | \bF1[-\s]?TV\b
  | MOTOGP\s+(360|COMMENTARY) | FORMULA\s*1\s*(\||DRIVER|PIT) | F1\s+TEAM
  | REDBULL\s?RACING | (^|\|)\s*[A-Z]{3,4}\s+[A-Z]+\s+(MERCEDES|FERRARI
      |MCLAREN|WILLIAMS|ALPINE|ASTON|ALFA|ALPHA|HAAS|SAUBER|CADILLAC)
""", re.X)

# Whole categories that only hold slots, loops, replays or non-sport content.
SKIP_CATEGORY_RE = re.compile(
    r"\bPPV\b|\bEVENTS?\b|REPLAY|REPETICI|RED BUTTON|ONBOARD|24/7|\+\d+H\b|"
    r"ON ?DEMAND|ENTERTAINMENT|CINEMA|\bRADIO\b|ADULT|XXX")

# Numbered feeds ("Sky Sport+ 1..40", "ESPN PLAY EVENTS 1..98"): when one
# category has at least this many numbers for the same base name, they are
# event slots rather than channels.
SLOT_SIBLINGS = 12

# Bracketed backup markers that are collapsed into one entry: (bk) [B] (C) (B1)
BACKUP_RE = re.compile(r"[\[(]\s*(BK|B\d?|C|D|S|F)\s*[\])]|\*+$", re.I)

# Category prefix -> group. LAT/AFR/EU categories are named after a country
# and use that name instead when they are (see group_for_category).
PREFIX_GROUP = {
    "BEE": "Arabic", "AR": "Arabic", "US": "USA", "NA": "USA", "CA": "Canada",
    "UK": "United Kingdom", "ES": "Spain", "DE": "Germany",
    "CH": "Switzerland", "AT": "Austria", "PT": "Portugal", "IT": "Italy",
    "NL": "Netherlands", "BE": "Belgium", "IR": "Ireland", "PL": "Poland",
    "GR": "Greece", "CY": "Cyprus", "AM": "Armenia", "MT": "Malta",
    "FI": "Finland", "AL": "Albania", "KO": "Kosovo", "SL": "Slovenia",
    "BH": "Bosnia", "RS": "Serbia", "HR": "Croatia", "MK": "North Macedonia",
    "CG": "Montenegro", "EXYU": "Balkans", "LT": "Lithuania", "LA": "Latvia",
    "EST": "Estonia", "HU": "Hungary", "CZ": "Czechia", "RO": "Romania",
    "BG": "Bulgaria", "RU": "Russia", "BY": "Belarus", "UKR": "Ukraine",
    "GE": "Georgia", "KA": "Kazakhstan", "AU": "Australia",
    "NZ": "New Zealand", "BR": "Brazil", "LAT": "Latin America",
    "CRB": "Caribbean", "FR": "France", "ASIA": "South Asia",
    "BAN": "Bangladesh", "NP": "Nepal", "TR": "Turkey", "AFG": "Afghanistan",
    "AZ": "Azerbaijan", "UZ": "Uzbekistan", "TJ": "Tajikistan",
    "KU": "Kurdistan", "IS": "Israel", "SG": "Singapore", "HK": "Hong Kong",
    "SK": "South Korea", "ID": "Indonesia", "TH": "Thailand", "TAI": "Taiwan",
    "VT": "Vietnam", "MY": "Malaysia", "JP": "Japan", "PH": "Philippines",
    "SU": "Suriname", "AFR": "Africa", "WC": "Cricket", "MU": "Music",
}
NAMED_COUNTRY_PREFIXES = {"LAT", "AFR", "EU", "AR", "ASIA"}
COUNTRY_NAMES = {
    "ARGENTINA", "CHILE", "COLOMBIA", "COSTA RICA", "ECUADOR", "GUATEMALA",
    "HONDURAS", "PUERTO RICO", "EL SALVADOR", "MEXICO", "NICARAGUA", "PANAMA",
    "PERU", "URUGUAY", "VENEZUELA", "MAURITIUS", "LUXEMBOURG", "NETHERLANDS",
    "MOROCCO", "ALGERIA", "TUNISIA", "EGYPT", "SYRIA", "LEBANON", "IRAQ",
    "JORDAN", "MAURITANIA", "SUDAN", "LIBYA", "OMAN", "QATAR", "KUWAIT",
    "YEMEN", "BAHRAIN", "PAKISTAN", "INDIA",
}
COUNTRY_FIXES = {"R. DOMINICANA": "Dominican Republic", "SOMALI": "Somalia",
                 "ETHOPIA": "Ethiopia", "MEXICO VIP": "Mexico",
                 "NETHERLANDS SD": "Netherlands", "PALASTINA": "Palestine",
                 "SAUDIA ARABIA": "Saudi Arabia", "UAE": "UAE",
                 "INDIA NEWS": "India", "INDIA EU": "India"}
# Categories without a "XX|" prefix, matched on their first word(s).
NO_PREFIX_GROUP = {"SWEDEN": "Sweden", "DENMARK": "Denmark",
                   "NORWAY": "Norway", "FINLAND": "Finland",
                   "ICELAND": "Iceland", "FORMULA 1": "Motorsport"}
# Section headers inside "WORLD LIVE SPORTS" ("###### UK ######").
SECTION_GROUP = {"ARABIC": "Arabic", "UK": "United Kingdom",
                 "NETHERLAND": "Netherlands", "BLUE SPORT": "Switzerland",
                 "CHILE": "Chile", "BOSNIA": "Bosnia"}
# Channel prefixes used inside the Balkan (EXYU) categories.
BALKAN_PREFIX = {"RS": "Serbia", "HR": "Croatia", "SI": "Slovenia",
                 "BA": "Bosnia", "MK": "North Macedonia", "ME": "Montenegro"}
# Categories whose prefix is misleading or that mix several countries.
CATEGORY_GROUP = {"UK| WORLD SPORTS": "World", "HR| HORSE RACING": "Horse Racing",
                  "CH| CHINA / HONGKONG": "Hong Kong", "IR| IRAN": "Iran"}
# Channels in the "World" group that clearly belong to one country.
WORLD_ROUTES = [(re.compile(r"^NOW HK"), "Hong Kong"),
                (re.compile(r"SUPER\s?SPORT"), "Africa"),
                (re.compile(r"\bAU\b"), "Australia"),
                (re.compile(r"^ASTRO"), "Malaysia")]
# Non-country groups go after the countries.
TRAILING_GROUPS = ["World", "Cricket", "Horse Racing", "Motorsport"]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def norm(text):
    text = unicodedata.normalize("NFKC", text or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def tokens(text):
    return {t.rstrip("+") for t in re.findall(r"[A-Z0-9+]+", text.upper())}


def quality_of(text):
    """'HD', 'SD', 'other', or None when the text carries no quality tag."""
    t = tokens(text)
    if t & OTHER_Q or "FULL HD" in text.upper():
        return "other"
    if t & HD_Q:
        return "HD"
    if t & SD_Q:
        return "SD"
    return None


def category_quality(text):
    """Like quality_of, but a mix of kept and dropped tags means unknown."""
    t = tokens(text)
    keep, drop = bool(t & (HD_Q | SD_Q)), bool(t & OTHER_Q)
    if drop and not keep:
        return "other"
    if keep and not drop:
        return "HD" if t & HD_Q else "SD"
    return None


def strip_prefix(name):
    """'UK| SKY SPORTS ACTION HD' -> 'SKY SPORTS ACTION HD' (also 'UK| UK| ...')."""
    return re.sub(r"^(\s*[^|]{1,12}\|\s*){1,2}", "", name)


def channel_prefix(name):
    m = re.match(r"\s*([A-Z]{2,4})\s*\|", name)
    return m.group(1) if m else None


def display_name(name):
    """Channel name without country prefix, quality, symbols or backup markers."""
    base = BACKUP_RE.sub("", strip_prefix(norm(name)))
    base = "".join(ch for ch in base if unicodedata.category(ch) != "So")
    drop = HD_Q | SD_Q | {"25FPS", "30FPS", "50FPS", "60FPS"}
    words = [w for w in base.split() if w.upper().strip("()[].,") not in drop]
    return re.sub(r"\s+", " ", " ".join(words)).strip(" -|")


def dedupe_key(name):
    """'BEIN SPORT 01' and 'beIN Sports 1' are the same channel."""
    key = re.sub(r"SPORTS", "SPORT", name.upper())
    key = re.sub(r"\b0+(\d)", r"\1", key)
    return re.sub(r"[^A-Z0-9]", "", key)


def natural_key(text):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", text.upper())]


def group_for_category(cat_name):
    name = norm(cat_name).upper()
    if name in CATEGORY_GROUP:
        return CATEGORY_GROUP[name]
    m = re.match(r"\s*([^|]+?)\s*\|\s*(.*)", name)
    if not m:
        for key, group in NO_PREFIX_GROUP.items():
            if name.startswith(key):
                return group
        return None
    prefix, rest = m.group(1).strip(), m.group(2).strip()
    if prefix in NAMED_COUNTRY_PREFIXES:
        if rest in COUNTRY_FIXES:
            return COUNTRY_FIXES[rest]
        if rest in COUNTRY_NAMES:
            return rest.title()
    return PREFIX_GROUP.get(prefix)


# --------------------------------------------------------------------------
# Xtream API
# --------------------------------------------------------------------------

def fetch(api_base, user, password, action, cache_dir, offline):
    path = cache_dir / f"{action}.json" if cache_dir else None
    if offline:
        return json.loads(path.read_text())
    query = urllib.parse.urlencode(
        {"username": user, "password": password, "action": action})
    url = f"{api_base.rstrip('/')}/player_api.php?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    if path:
        path.write_text(json.dumps(data))
    return data


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------

def select(categories, streams):
    """Return (kept entries by group, reasons Counter for dropped channels)."""
    cat_by_id = {str(c["category_id"]): norm(c["category_name"]) for c in categories}
    dropped = Counter()

    # Pass 1: per-channel classification, in provider order so that section
    # headers inside mixed categories apply to the channels that follow them.
    section = {}
    candidates = []
    for s in sorted(streams, key=lambda s: (str(s.get("category_id")), s.get("num") or 0)):
        cid = str(s.get("category_id"))
        cat = cat_by_id.get(cid, "")
        name = norm(s.get("name"))
        upper = name.upper()

        if upper.startswith("#"):
            header = upper.strip("# ").strip()
            section[cid] = SECTION_GROUP.get(header, header.title())
            continue
        if s.get("stream_type") != "live" or s.get("is_adult"):
            dropped["radio / adult"] += 1
            continue
        bare = strip_prefix(upper)
        if not SPORT_RE.search(bare) or NOT_SPORT_RE.search(bare):
            dropped["not sports"] += 1
            continue
        if SKIP_CATEGORY_RE.search(cat.upper()):
            dropped["PPV / event / replay category"] += 1
            continue
        if SLOT_RE.search(upper):
            dropped["PPV / event slot"] += 1
            continue

        quality = quality_of(name)
        if quality is None:
            quality = category_quality(cat)
        if quality == "other":
            dropped["FHD / 4K / HEVC / RAW"] += 1
            continue

        if cid in section and cat.upper().startswith("WORLD LIVE SPORTS"):
            group = section[cid]
        elif cat.upper().startswith("EXYU"):
            group = BALKAN_PREFIX.get(channel_prefix(upper), "Balkans")
        else:
            group = group_for_category(cat)
        if not group:
            dropped["no country"] += 1
            continue
        display = display_name(name)
        if group == "World":
            group = next((g for rx, g in WORLD_ROUTES if rx.search(display.upper())),
                         group)

        candidates.append({
            "stream": s, "cid": cid, "group": group, "quality": quality,
            "name": display,
        })

    # Pass 2: event feeds that come in long series within one category:
    # numbered ("Sky Sport+ 1..40") or per market ("NFL NETWORK (N507) ...").
    variants = defaultdict(set)
    for c in candidates:
        upper = c["name"].upper()
        m = re.match(r"(.*?)\s*(\d+)$", upper)
        if m:
            c["slot_key"] = (c["cid"], m.group(1))
            variants[c["slot_key"]].add(m.group(2))
        elif "(" in upper:
            c["slot_key"] = (c["cid"], upper.split("(")[0].strip())
            variants[c["slot_key"]].add(upper)
    slot_bases = {k for k, v in variants.items() if len(v) >= SLOT_SIBLINGS}

    # Pass 3: collapse duplicates (backups, same channel listed twice),
    # preferring the copy with a guide id, then with catch-up.
    best = {}
    for c in candidates:
        if c.get("slot_key") in slot_bases:
            dropped["PPV / event slot"] += 1
            continue
        key = (c["group"], dedupe_key(c["name"]), c["quality"])
        s = c["stream"]
        rank = (bool(s.get("epg_channel_id")), bool(s.get("tv_archive")))
        if key in best:
            dropped["duplicate / backup"] += 1
            if rank <= best[key][0]:
                continue
        best[key] = (rank, c)

    groups = defaultdict(list)
    for _, c in best.values():
        groups[c["group"]].append(c)
    qrank = {"HD": 0, "SD": 1, None: 2}
    for entries in groups.values():
        entries.sort(key=lambda c: (natural_key(c["name"]), qrank[c["quality"]]))
    return groups, dropped


def group_order(groups):
    countries = sorted(g for g in groups if g not in TRAILING_GROUPS)
    return countries + [g for g in TRAILING_GROUPS if g in groups]


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def attr(value):
    return str(value or "").replace('"', "'")


def write_m3u(path, groups, server, user, password, ext):
    base = server.rstrip("/")
    q = urllib.parse.urlencode({"username": user, "password": password})
    epg = f"{base}/xmltv.php?{q}"
    lines = [f'#EXTM3U url-tvg="{epg}" x-tvg-url="{epg}"']
    for group in group_order(groups):
        for c in groups[group]:
            s = c["stream"]
            title = c["name"] + (f" {c['quality']}" if c["quality"] else "")
            lines.append(
                f'#EXTINF:-1 tvg-id="{attr(s.get("epg_channel_id"))}" '
                f'tvg-name="{attr(title)}" tvg-logo="{attr(s.get("stream_icon"))}" '
                f'group-title="{attr(group)}",{title}')
            lines.append(f"{base}/live/{user}/{password}/{s['stream_id']}.{ext}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(path, groups, dropped, total):
    kept = sum(len(v) for v in groups.values())
    q = Counter(c["quality"] for v in groups.values() for c in v)
    out = [
        "# Sports playlist", "",
        f"{kept:,} channels kept out of {total:,} "
        f"({q['HD']:,} HD, {q['SD']:,} SD, {q[None]:,} untagged) "
        f"in {len(groups)} groups.", "",
        "| Group | Channels | HD | SD | Untagged |", "|---|---:|---:|---:|---:|",
    ]
    for g in group_order(groups):
        c = Counter(e["quality"] for e in groups[g])
        out.append(f"| {g} | {len(groups[g])} | {c['HD']} | {c['SD']} | {c[None]} |")
    out += ["", "## Left out", "", "| Reason | Channels |", "|---|---:|"]
    out += [f"| {r} | {n:,} |" for r, n in dropped.most_common()]
    out += ["", "## Channels", ""]
    for g in group_order(groups):
        names = ", ".join(e["name"] + (f" {e['quality']}" if e["quality"] else "")
                          for e in groups[g])
        out += [f"**{g}** ({len(groups[g])}): {names}", ""]
    Path(path).write_text("\n".join(out), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-o", "--out", default="sports.m3u")
    ap.add_argument("--report", help="also write a Markdown summary here")
    ap.add_argument("--server", default=os.environ.get("XTREAM_SERVER"),
                    help="server URL used in the playlist (env XTREAM_SERVER)")
    ap.add_argument("--api", help="URL to query the API on, if different "
                    "(e.g. the https:// address)")
    ap.add_argument("--user", default=os.environ.get("XTREAM_USER"))
    ap.add_argument("--password", default=os.environ.get("XTREAM_PASS"))
    ap.add_argument("--ext", default="ts", choices=["ts", "m3u8"],
                    help="stream container (default: ts)")
    ap.add_argument("--cache", type=Path, help="directory to cache API JSON in")
    ap.add_argument("--offline", action="store_true",
                    help="read the API JSON from --cache instead of the server")
    args = ap.parse_args()
    if not (args.server and args.user and args.password):
        ap.error("set XTREAM_SERVER, XTREAM_USER and XTREAM_PASS (or pass flags)")
    if args.offline and not args.cache:
        ap.error("--offline needs --cache")
    if args.cache:
        args.cache.mkdir(parents=True, exist_ok=True)

    api = args.api or args.server
    get = lambda action: fetch(api, args.user, args.password, action,
                               args.cache, args.offline)
    categories, streams = get("get_live_categories"), get("get_live_streams")
    groups, dropped = select(categories, streams)
    write_m3u(args.out, groups, args.server, args.user, args.password, args.ext)
    if args.report:
        write_report(args.report, groups, dropped, len(streams))
    kept = sum(len(v) for v in groups.values())
    print(f"{kept} sports channels in {len(groups)} groups -> {args.out}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
