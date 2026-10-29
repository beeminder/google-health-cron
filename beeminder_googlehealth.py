#!/usr/bin/env python3
"""
Written entirely by Claude but the blurbs of UI-copy were vetted and edited by the humans of Beeminder.

DIY sync from the Google Health API to Beeminder goals.

  python3 beeminder_googlehealth.py setup      # once, interactive
  python3 beeminder_googlehealth.py            # from cron, as often as you like
  python3 beeminder_googlehealth.py --dry-run  # print what would be sent

Standard library only, Python 3.9+. See README.md.

Daily totals (steps etc) become one datapoint per day. Sleeps (naps too),
workouts and weigh-ins become one datapoint per event, the way the app shows
them, and the goal's own aggregation does the rest. Every
datapoint has a requestid, so Beeminder upserts instead of piling up
datapoints; and a local cache of what we last sent means a run where nothing
changed makes no Beeminder call at all.
"""

import argparse, json, os, re, sys, time
import datetime as dt
import urllib.error, urllib.parse, urllib.request
from pathlib import Path

CONFIG_DIR  = Path.home() / ".config" / "beeminder-googlehealth"
CLIENT_PATH = CONFIG_DIR / "client_secret.json" # downloaded from Google Cloud; read by setup
CONFIG_PATH = CONFIG_DIR / "config.json" # secrets; written only by setup
CACHE_PATH  = CONFIG_DIR / "cache.json"  # disposable; delete it to force a resend

BEEMINDER    = "https://www.beeminder.com/api/v1"
GHEALTH      = "https://health.googleapis.com/v4"
DISCOVERY    = "https://health.googleapis.com/$discovery/rest?version=v4" # machine-readable API docs
GOOGLE_AUTH  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
REDIRECT_URI = "https://www.google.com" # what Google's Health API setup guide says
USER_AGENT   = "beeminder-googlehealth-diy/0.1"

# Resend this many days (today included) so late device syncs still land.
LOOKBACK_DAYS = 7

COMMENT = "via Google Health"
# Beeminder only counts a requestid'd resend as a no-op if the comment matches
# exactly, so comments must be a pure function of the data (and an empty one
# won't do: nil != "").

ACTIVITY = "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"
MEASURES = "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly"
SLEEP    = "https://www.googleapis.com/auth/googlehealth.sleep.readonly"

def km(mm): return round(int(mm)/1e6, 2)
def mi(mm): return round(int(mm)/1609344, 2)

# metric -> (Google Health data type, OAuth scope, Google value -> Beeminder value)
# Google's JSON omits zero-valued fields, so zero steps arrives as {}.
METRICS = {
  # one datapoint per day, from dailyRollUp
  "steps":          ("steps",    ACTIVITY, lambda v: int(v.get("countSum", 0))),
  "floors":         ("floors",   ACTIVITY, lambda v: int(v.get("countSum", 0))),
  "km":             ("distance", ACTIVITY, lambda v: km(v.get("millimetersSum", 0))),
  "mi":             ("distance", ACTIVITY, lambda v: mi(v.get("millimetersSum", 0))),
  # one datapoint per weigh-in, sleep (naps included) or workout, from list
  "kg":             ("weight",   MEASURES, lambda w: round(w["weightGrams"]/1000, 2)),
  "lb":             ("weight",   MEASURES, lambda w: round(w["weightGrams"]/453.59237, 1)),
  "sleep_hours":    ("sleep",    SLEEP,    lambda s: int(s["summary"].get("minutesAsleep", 0))/60),
  "exercise_count": ("exercise", ACTIVITY, lambda x: 1),
  "exercise_km":    ("exercise", ACTIVITY, lambda x: km(x["metricsSummary"].get("distanceMillimeters", 0))),
  "exercise_mi":    ("exercise", ACTIVITY, lambda x: mi(x["metricsSummary"].get("distanceMillimeters", 0))),
  "exercise_hours": ("exercise", ACTIVITY, lambda x: float(x["activeDuration"].rstrip("s"))/3600),
}

# Per-event data types, and which of their times picks the Beeminder day.
# Sleep goes on the day you woke up (the only way Google lets you filter it),
# workouts on the day they started.
# "Civil" is Google's word for the user's local wall-clock date and time, as
# opposed to "physical" time, an instant in UTC.
EVENT_DAY = {"weight":   "sample_time.civil_time",
             "sleep":    "interval.civil_end_time",
             "exercise": "interval.civil_start_time"}

class ApiError(Exception):
  def __init__(self, method, url, status, body):
    self.status, self.body = status, body
    # drop the query string so an auth_token never ends up in cron mail
    super().__init__(f"{method} {url.split('?')[0]} -> HTTP {status}: {body}")

def http(method, url, form=None, json_body=None, bearer=None):
  """Parsed JSON response; raises ApiError on any non-2xx."""
  headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
  data = None
  if form is not None:
    data = urllib.parse.urlencode(form).encode()
    headers["Content-Type"] = "application/x-www-form-urlencoded"
  elif json_body is not None:
    data = json.dumps(json_body).encode()
    headers["Content-Type"] = "application/json"
  if bearer: headers["Authorization"] = "Bearer " + bearer
  req = urllib.request.Request(url, data=data, headers=headers, method=method)
  try:
    with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)
  except urllib.error.HTTPError as e:
    raise ApiError(method, url, e.code, e.read().decode(errors="replace")) from None

def write_json(path, obj):
  """Atomic write, readable only by you (config holds tokens)."""
  tmp = path.with_suffix(".tmp")
  fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
  with os.fdopen(fd, "w") as f: json.dump(obj, f, indent=2)
  os.replace(tmp, path)

# URLs and filters say active-zone-minutes and civil_end_time; JSON says
# activeZoneMinutes and civilEndTime
def camel(s): return re.sub(r"[-_](\w)", lambda m: m.group(1).upper(), s)

# A daystamp is Beeminder's name for a date as a YYYYMMDD string
def daystamp(d): return f"{d['year']:04d}{d['month']:02d}{d['day']:02d}"

def civil(day): return {"date": {"year": day.year, "month": day.month, "day": day.day}}

def window_start(today): return today - dt.timedelta(days=LOOKBACK_DAYS-1)

# end of the lookback window, exclusive; one extra day in case the user's
# "today" is ahead of this machine's, e.g. cron on a UTC server for someone in Tokyo
def window_end(today): return today + dt.timedelta(days=2)

# ------------------------------------------------------------------ Google ---

def refresh(g):
  """Google's token response to refreshing the access in g, a config["google"]."""
  return http("POST", GOOGLE_TOKEN, form={
    "grant_type":    "refresh_token",
    "refresh_token": g["refresh_token"],
    "client_id":     g["client_id"],
    "client_secret": g["client_secret"]})

def access_token(cfg, cache):
  """Cached access token, refreshed when it's within a minute of expiring."""
  if cache.get("access_token_expires", 0) > time.time() + 60:
    return cache["access_token"]
  try:
    tok = refresh(cfg["google"])
  except ApiError as e:
    if "invalid_grant" in e.body: # revoked, or the 7-day expiry of a Testing app
      sys.exit(f"Google no longer accepts your refresh token. Run setup again.\n{e}")
    raise
  cache["access_token"] = tok["access_token"]
  cache["access_token_expires"] = time.time() + tok["expires_in"]
  return tok["access_token"]

# Both fetchers return events as [(event id, daystamp, Google's value)].

def daily_events(token, data_type, today):
  """One event per day with data, id'd by its daystamp. Google does the day
  bucketing in the user's own civil time."""
  r = http("POST", f"{GHEALTH}/users/me/dataTypes/{data_type}/dataPoints:dailyRollUp",
           json_body={"range": {"start": civil(window_start(today)),
                                "end":   civil(window_end(today))},
                      "windowSizeDays": 1},
           bearer=token)
  assert "nextPageToken" not in r, r # 8 days is nowhere near a page
  field = camel(data_type)
  out = []
  for p in r.get("rollupDataPoints", []):
    if field in p:
      ds = daystamp(p["civilStartTime"]["date"])
      out.append((ds, ds, p[field]))
  return out

def list_events(token, data_type, today):
  """One event per weigh-in, sleep or workout, id'd by Google's data point id."""
  path = EVENT_DAY[data_type]
  f = f"{data_type}.{path}"
  q = {"filter": f'{f} >= "{window_start(today)}" AND {f} < "{window_end(today)}"'}
  out = []
  while True: # sleep and exercise pages hold at most 25
    r = http("GET", f"{GHEALTH}/users/me/dataTypes/{data_type}/dataPoints?" +
                    urllib.parse.urlencode(q), bearer=token)
    for p in r.get("dataPoints", []):
      v = when = p[camel(data_type)]
      for k in path.split("."): when = when[camel(k)]
      out.append((p["name"].rsplit("/", 1)[1], daystamp(when["date"]), v))
    if not r.get("nextPageToken"): return out
    q["pageToken"] = r["nextPageToken"]

# --------------------------------------------------------------- Beeminder ---

def datapoint(data_type, event_id, ds, value, raw):
  comment = f"{raw['displayName']}, {COMMENT}" if data_type == "exercise" else COMMENT
  return {"daystamp": ds, "value": value, "comment": comment,
          "requestid": f"googlehealth-{data_type}-{event_id}"}

def post_datapoints(auth_token, goalname, dps):
  """Upsert via create_all. Same requestid + same value/comment/daystamp =
  Beeminder ignores it (and doesn't regraph)."""
  r = http("POST", f"{BEEMINDER}/users/me/goals/{goalname}/datapoints/create_all.json",
           form={"auth_token": auth_token, "datapoints": json.dumps(dps)})
  # All good -> a plain list. Otherwise {"successes": [...], "errors": [...]},
  # where an identical resend is reported as an error "Duplicate request".
  if isinstance(r, dict):
    bad = [e for e in r["errors"]
           if not (isinstance(e, dict) and e.get("errors") == "Duplicate request")]
    if bad: raise RuntimeError(f"Beeminder create_all {goalname}: {bad}")

# -------------------------------------------------------------------- sync ---

def sync(cfg, cache, today, dry_run=False):
  """Send each goal the datapoints that differ from what we last sent it.
  Returns [(goalname, datapoint)] that were sent (or would be, if dry_run).
  Mutates cache; the caller saves it."""
  token = access_token(cfg, cache)
  events = {}
  for goalname, g in cfg["goals"].items():
    unknown = sorted(set(g["types"]) - set(cfg["exercise_types"]))
    # TODO: English copy. Meaning: "Goal <goalname>: as of your last setup,
    # Google has no kind of workout called <unknown>. Fix the spelling in
    # config.json, or re-run setup to get Google's latest list."
    assert not unknown, (f"Meta {goalname}: secundum ultimum tuum setup, Google nullum genus "
                         f"exercitationis nomine {', '.join(unknown)} habet. Corrige scripturam "
                         f"in config.json, aut setup iterum exsequere ut indicem novissimum "
                         f"Google accipias.")
    data_type = METRICS[g["metric"]][0]
    if data_type not in events:
      fetch = list_events if data_type in EVENT_DAY else daily_events
      events[data_type] = fetch(token, data_type, today)

  sent = cache.setdefault("sent", {}) # {goalname: {requestid: datapoint}}
  for goalname in list(sent):
    if goalname not in cfg["goals"]: del sent[goalname]
  oldest = window_start(today).strftime("%Y%m%d")
  out = []
  for goalname, g in cfg["goals"].items():
    data_type, _, convert = METRICS[g["metric"]]
    have = sent.setdefault(goalname, {})
    for rid in [rid for rid, dp in have.items() if dp["daystamp"] < oldest]: del have[rid]
    want = [datapoint(data_type, eid, ds, convert(raw), raw)
            for eid, ds, raw in events[data_type]
            if not g["types"] or raw["exerciseType"] in g["types"]]
    todo = sorted((dp for dp in want if have.get(dp["requestid"]) != dp),
                  key=lambda dp: (dp["daystamp"], dp["requestid"]))
    if not todo: continue
    if not dry_run:
      post_datapoints(cfg["beeminder_auth_token"], goalname, todo)
      have.update({dp["requestid"]: dp for dp in todo})
    out += [(goalname, dp) for dp in todo]
  return out

def load_cache():
  return json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}

def run_sync(cfg, dry_run=False):
  cache = load_cache()
  before = json.dumps(cache, sort_keys=True)
  try:
    return sync(cfg, cache, dt.date.today(), dry_run)
  finally: # keep whatever did get sent even if a later goal blew up
    if json.dumps(cache, sort_keys=True) != before: write_json(CACHE_PATH, cache)

def show(sent):
  for goalname, dp in sent: print(goalname, dp["daystamp"], dp["value"], dp["requestid"])

# ------------------------------------------------------------------- setup ---

def code_from_redirect(url):
  """The ?code=... from the URL Google redirected to. We take the whole URL
  rather than a hand-copied code since the code itself is percent-encoded."""
  q = urllib.parse.parse_qs(urllib.parse.urlparse(url.strip()).query)
  if "error" in q: sys.exit(f"Google: {q['error'][0]}")
  if "code" not in q:
    sys.exit("That address has no code in it. Copy the whole thing from the browser's address bar.")
  return q["code"][0]

def goal_lines(goals):
  """Lines to set up goals from: first each of these (stored) goals as the line
  that would set it up, shown as if typed, then whatever the user types."""
  for goalname, g in goals.items():
    line = " ".join([goalname, g["metric"], *g["types"]])
    print("Goal: " + line)
    yield line
  while True: yield input("Goal: ")

# Both of these return Google's token response, with a refresh_token.

def stored_access(old, web, scopes):
  """The last setup's Google access, freshly refreshed, if it's for this same
  OAuth client, Google still accepts it, and it covers all these scopes.
  Otherwise None."""
  if not old or old["google"]["client_id"] != web["client_id"]: return None
  g = dict(old["google"], client_secret=web["client_secret"])
  try:
    tok = refresh(g)
  except ApiError as e:
    if "invalid_grant" not in e.body: raise
    return None # revoked, unused for 6 months, or a Testing app's 7 days are up
  if not set(scopes) <= set(tok["scope"].split()): return None
  # a refresh response lists the scopes it covers but doesn't repeat the refresh token
  return dict(tok, refresh_token=g["refresh_token"])

def consent(web, scopes):
  """New Google access, from the user allowing these scopes on Google's
  consent screen."""
  print("\nOpen this link in a browser and allow access. You'll end up on\n"
        "google.com; copy the whole address from the address bar.\n")
  print(GOOGLE_AUTH + "?" + urllib.parse.urlencode({
    "client_id":     web["client_id"],
    "redirect_uri":  REDIRECT_URI,
    "response_type": "code",
    "scope":         " ".join(scopes),
    "access_type":   "offline", # we want a refresh token
    "prompt":        "consent", # ...every time, not just the first time
  }))
  code = code_from_redirect(input("\nPaste the google.com address here: "))
  tok = http("POST", GOOGLE_TOKEN, form={
    "grant_type":    "authorization_code",
    "code":          code,
    "redirect_uri":  REDIRECT_URI,
    "client_id":     web["client_id"],
    "client_secret": web["client_secret"]})

  # Google's consent screen lets people untick individual scopes
  missing = set(scopes) - set(tok["scope"].split())
  if missing:
    sys.exit("Some permissions are missing. Run setup again and tick every box "
             "on Google's consent screen. Missing: " + ", ".join(sorted(missing)))
  return tok

def setup():
  CONFIG_DIR.mkdir(parents=True, exist_ok=True)

  if not CLIENT_PATH.exists():
    sys.exit(f"Put the OAuth client JSON file you downloaded from Google Cloud "
             f"at {CLIENT_PATH} (README step 2f).")
  client = json.loads(CLIENT_PATH.read_text())
  if "web" not in client:
    sys.exit("That file is for a different kind of OAuth client. "
             "Create one of type \"Web application\" (README step 2f).")
  web = client["web"]
  # a re-run starts from the last setup's token and goals
  old = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}

  auth_token = input(
    "Your Beeminder auth token (from https://www.beeminder.com/api/v1/auth_token.json)" +
    (" (enter to use the stored token)" if old else "") + ": "
  ).strip() or old["beeminder_auth_token"]
  user = http("GET", f"{BEEMINDER}/users/me.json?" +
                     urllib.parse.urlencode({"auth_token": auth_token}))
  # Every kind of workout Google has, e.g. RUNNING. Saved in the config so sync
  # can check hand-edited goals against it too.
  exercise_types = http("GET", DISCOVERY)["schemas"]["Exercise"]["properties"]["exerciseType"]["enum"]

  # {goalname: {"metric": "exercise_km", "types": ["RUNNING"]}}; no types = all workouts
  goals = {}
  print("\nNow your goals, one per line: the goalname, then a metric. For\n"
        "exercise_* metrics you can add kinds of workout, like\n"
        "\"run exercise_km RUNNING TRAIL_RUN\"; with no kinds of workout specified, every workout counts.\n"
        "Metrics: " + ", ".join(METRICS) + "\n"
        "Press Enter on an empty line when you're done.")
  for parts in map(str.split, goal_lines(old.get("goals", {}))):
    if not parts: break
    goalname, metric, types = parts[0], parts[1] if len(parts) > 1 else None, parts[2:]
    if metric not in METRICS:
      print("The second word has to be one of these metrics: " + ", ".join(METRICS))
    elif types and METRICS[metric][0] != "exercise":
      print("Only the exercise_* metrics take kinds of workout.")
    elif not all(t in exercise_types for t in types):
      print("Spell kinds of workout the way Google does, in all-caps: RUNNING, BIKING, SWIMMING, ...")
    elif goalname not in user["goals"]:
      print(f"{user['username']} has no goal \"{goalname}\". Your goals: " + ", ".join(user["goals"]))
    else:
      goals[goalname] = {"metric": metric, "types": types}
  if not goals: sys.exit("No goals entered, so there's nothing to set up.")

  scopes = sorted({METRICS[g["metric"]][1] for g in goals.values()})
  tok = stored_access(old, web, scopes) or consent(web, scopes)
  try:
    http("GET", f"{GHEALTH}/users/me/identity", bearer=tok["access_token"])
  except ApiError as e:
    if "ACCOUNT_NOT_LINKED" in e.body:
      sys.exit("This Google account isn't linked to Google Health yet. Sign in to "
               "the Google Health app on your phone with it, then run setup again.")
    raise

  cfg = {"google": {"client_id":     web["client_id"],
                    "client_secret": web["client_secret"],
                    "refresh_token": tok["refresh_token"]},
         "beeminder_auth_token": auth_token,
         "goals": goals,
         "exercise_types": exercise_types}
  write_json(CONFIG_PATH, cfg)
  CACHE_PATH.unlink(missing_ok=True) # cached access token may lack new scopes
  print(f"\nSaved to {CONFIG_PATH}. First sync:")
  sent = run_sync(cfg)
  show(sent)
  if not sent: print("Nothing to send: Google has no data for the last 7 days.")

# -------------------------------------------------------------------- main ---

def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("command", nargs="?", default="sync", choices=["sync", "setup"])
  ap.add_argument("--dry-run", action="store_true")
  args = ap.parse_args()
  if args.command == "setup": return setup()
  if not CONFIG_PATH.exists():
    sys.exit(f"Run setup first: python3 {sys.argv[0]} setup")
  sent = run_sync(json.loads(CONFIG_PATH.read_text()), args.dry_run)
  # silent unless dry-run: cron mails any output, and the graph is the receipt
  if args.dry_run: show(sent)

if __name__ == "__main__": main()
