#!/usr/bin/env python3
"""python3 -m unittest -v  (no network; both APIs are faked)"""

import datetime as dt, json, unittest, urllib.parse
from unittest import mock
import beeminder_googlehealth as bgh

TODAY = dt.date(2026, 10, 27)

def civ(ds): return {"date": {"year": int(ds[:4]), "month": int(ds[4:6]), "day": int(ds[6:])}}

def point(ds, field, value):
  """One dailyRollUp response entry; value=None means a day with no data."""
  p = {"civilStartTime": dict(civ(ds), time={})}
  if value is not None: p[field] = value
  return p

def sleep(sid, start, end, minutes, nap=False):
  return {"name": f"users/u1/dataTypes/sleep/dataPoints/{sid}",
          "sleep": {"interval": {"civilStartTime": civ(start), "civilEndTime": civ(end)},
                    "metadata": {"nap": nap}, "summary": {"minutesAsleep": str(minutes)}}}

def weighin(wid, ds, grams):
  return {"name": f"users/u1/dataTypes/weight/dataPoints/{wid}",
          "weight": {"sampleTime": {"physicalTime": "2026-10-27T14:00:00Z",
                                    "utcOffset": "-14400s", "civilTime": civ(ds)},
                     "weightGrams": grams}}

def workout(wid, start, kind, mm, secs):
  return {"name": f"users/u1/dataTypes/exercise/dataPoints/{wid}",
          "exercise": {"interval": {"civilStartTime": civ(start), "civilEndTime": civ(start)},
                       "exerciseType": kind, "displayName": kind.title(),
                       "metricsSummary": {"distanceMillimeters": mm},
                       "activeDuration": f"{secs}s"}}

class FakeAPIs:
  """Stands in for bgh.http. rollups: {data_type: [rollup points]};
  sessions: {data_type: [[data points on page 1], [page 2], ...]}."""
  def __init__(self, rollups={}, sessions={}, create_all_response=None):
    self.rollups, self.sessions, self.calls = rollups, sessions, []
    self.create_all_response = create_all_response
  def __call__(self, method, url, form=None, json_body=None, bearer=None):
    self.calls.append((method, url, form, json_body))
    if url == bgh.GOOGLE_TOKEN: return {"access_token": "at", "expires_in": 3599}
    data_type = url.split("/dataTypes/")[-1].split("/")[0]
    if url.endswith(":dailyRollUp"):
      return {"rollupDataPoints": self.rollups[data_type]}
    if "/dataPoints?" in url:
      q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
      pages, i = self.sessions[data_type], int(q.get("pageToken", ["0"])[0])
      r = {"dataPoints": pages[i]} if pages[i] else {}
      if i+1 < len(pages): r["nextPageToken"] = str(i+1)
      return r
    if url.endswith("/create_all.json"):
      if self.create_all_response is not None: return self.create_all_response
      return [dict(d, id="x", status="created") for d in json.loads(form["datapoints"])]
    raise AssertionError(url)
  def posts(self):
    """[(slug, [datapoints])] for every create_all call."""
    return [(url.split("/goals/")[1].split("/")[0], json.loads(form["datapoints"]))
            for _, url, form, _ in self.calls if url.endswith("/create_all.json")]
  def count(self, needle): return sum(needle in url for _, url, _, _ in self.calls)

def cfg(**goals):
  """cfg(walk="steps", run=("exercise_km", "RUNNING"))"""
  return {"google": {"client_id": "cid", "client_secret": "sec", "refresh_token": "rt"},
          "beeminder_auth_token": "bt",
          "goals": {slug: {"metric": m if isinstance(m, str) else m[0],
                           "types": [] if isinstance(m, str) else list(m[1:])}
                    for slug, m in goals.items()}}

CFG = cfg(walk="steps")

class TestDailyEvents(unittest.TestCase):
  def test_parses_and_skips_days_without_data(self):
    fake = FakeAPIs({"steps": [point("20261025", "steps", {"countSum": "3822"}),
                               point("20261026", "steps", {}), # zero steps
                               point("20261027", "steps", None)]}) # no data
    with mock.patch.object(bgh, "http", fake):
      got = bgh.daily_events("at", "steps", TODAY)
    self.assertEqual(got, [("20261025", "20261025", {"countSum": "3822"}),
                           ("20261026", "20261026", {})])
    body = fake.calls[0][3]
    self.assertEqual(body["range"]["start"]["date"], {"year": 2026, "month": 10, "day": 21})
    self.assertEqual(body["range"]["end"]["date"],   {"year": 2026, "month": 10, "day": 29})
    self.assertEqual(body["windowSizeDays"], 1)

  def test_hyphenated_data_types_use_camelcase_field(self):
    self.assertEqual(bgh.camel("active-zone-minutes"), "activeZoneMinutes")
    self.assertEqual(bgh.camel("steps"), "steps")
    self.assertEqual(bgh.camel("civil_end_time"), "civilEndTime")

class TestListEvents(unittest.TestCase):
  def fetch(self, data_type, pages):
    fake = FakeAPIs(sessions={data_type: pages})
    with mock.patch.object(bgh, "http", fake):
      got = bgh.list_events("at", data_type, TODAY)
    filters = [urllib.parse.parse_qs(urllib.parse.urlparse(c[1]).query)["filter"][0]
               for c in fake.calls]
    return got, filters

  def test_sleep_is_dated_by_wake_up_day_and_naps_are_their_own_events(self):
    night, nap = sleep("night1", "20261026", "20261027", 450), \
                 sleep("nap1", "20261027", "20261027", 30, nap=True)
    got, filters = self.fetch("sleep", [[night, nap]])
    self.assertEqual([(e, ds) for e, ds, _ in got], [("night1", "20261027"), ("nap1", "20261027")])
    self.assertEqual(filters, ['sleep.interval.civil_end_time >= "2026-10-21" AND '
                               'sleep.interval.civil_end_time < "2026-10-29"'])

  def test_exercise_is_dated_by_start_and_follows_pages(self):
    pages = [[workout(f"w{i}", "20261026", "RUNNING", 0, 60) for i in range(25)],
             [workout("w25", "20261025", "WALKING", 0, 60)]]
    got, filters = self.fetch("exercise", pages)
    self.assertEqual(len(got), 26)
    self.assertEqual(got[-1][:2], ("w25", "20261025"))
    self.assertEqual(len(filters), 2)
    self.assertTrue(filters[0].startswith('exercise.interval.civil_start_time >= "2026-10-21"'))

  def test_each_weigh_in_is_its_own_event_dated_by_civil_sample_time(self):
    got, filters = self.fetch("weight", [[weighin("am", "20261027", 70100.0),
                                          weighin("pm", "20261027", 70900.0)]])
    self.assertEqual([(e, ds) for e, ds, _ in got], [("am", "20261027"), ("pm", "20261027")])
    self.assertEqual(filters, ['weight.sample_time.civil_time >= "2026-10-21" AND '
                               'weight.sample_time.civil_time < "2026-10-29"'])

  def test_no_sessions_at_all(self):
    self.assertEqual(self.fetch("sleep", [[]])[0], [])

class TestMetrics(unittest.TestCase):
  def conv(self, metric, v): return bgh.METRICS[metric][2](v)
  def test_daily_conversions(self):
    self.assertEqual(self.conv("steps", {"countSum": "3822"}), 3822)
    self.assertEqual(self.conv("steps", {}), 0)
    self.assertEqual(self.conv("floors", {"countSum": "12"}), 12)
    self.assertEqual(self.conv("km", {"millimetersSum": "5432100"}), 5.43)
    self.assertEqual(self.conv("mi", {"millimetersSum": "1609344"}), 1.0)
  def test_event_conversions(self):
    w = weighin("w", "20261027", 70123.4)["weight"]
    self.assertEqual(self.conv("kg", w), 70.12)
    self.assertEqual(self.conv("lb", w), 154.6)
    s = sleep("n", "20261026", "20261027", 450)["sleep"]
    self.assertEqual(self.conv("sleep_hours", s), 7.5)
    x = workout("w", "20261027", "RUNNING", 5432100.0, 1800)["exercise"]
    self.assertEqual(self.conv("exercise_count", x), 1)
    self.assertEqual(self.conv("exercise_km", x), 5.43)
    self.assertEqual(self.conv("exercise_hours", x), 0.5)
    x["metricsSummary"] = {} # e.g. weights: no distance
    self.assertEqual(self.conv("exercise_km", x), 0)
  def test_missing_values_fail_loudly(self):
    with self.assertRaises(KeyError): self.conv("kg", {})
    with self.assertRaises(KeyError): self.conv("exercise_hours", {})

class TestSync(unittest.TestCase):
  def run_sync(self, fake, cache, cfg=CFG, dry_run=False):
    with mock.patch.object(bgh, "http", fake):
      return bgh.sync(cfg, cache, TODAY, dry_run)
  def sent(self, result): return [(slug, dp["daystamp"], dp["value"]) for slug, dp in result]

  def test_first_run_upserts_one_datapoint_per_day(self):
    fake = FakeAPIs({"steps": [point("20261026", "steps", {"countSum": "9000"}),
                               point("20261027", "steps", {"countSum": "1234"})]})
    cache = {}
    self.assertEqual(self.sent(self.run_sync(fake, cache)),
                     [("walk", "20261026", 9000), ("walk", "20261027", 1234)])
    [(slug, dps)] = fake.posts()
    self.assertEqual(slug, "walk")
    self.assertEqual(dps, [
      {"daystamp": "20261026", "value": 9000, "comment": bgh.COMMENT,
       "requestid": "googlehealth-steps-20261026"},
      {"daystamp": "20261027", "value": 1234, "comment": bgh.COMMENT,
       "requestid": "googlehealth-steps-20261027"}])
    self.assertNotIn("timestamp", dps[0]) # it would override daystamp
    self.assertEqual(sorted(cache["sent"]["walk"]),
                     ["googlehealth-steps-20261026", "googlehealth-steps-20261027"])

  def test_cron_every_two_minutes_with_no_new_data_never_calls_beeminder(self):
    fake = FakeAPIs({"steps": [point("20261027", "steps", {"countSum": "1234"})]},
                    {"sleep": [[sleep("n1", "20261026", "20261027", 450)]]})
    cache = {}
    for _ in range(720): self.run_sync(fake, cache, cfg(walk="steps", zzz="sleep_hours"))
    self.assertEqual(len(fake.posts()), 2) # one per goal, on the first run
    self.assertEqual(fake.count(bgh.GOOGLE_TOKEN), 1) # access token is cached

  def test_growing_total_upserts_the_same_requestid(self):
    steps = [point("20261026", "steps", {"countSum": "9000"}),
             point("20261027", "steps", {"countSum": "1234"})]
    fake = FakeAPIs({"steps": steps})
    cache = {}
    self.run_sync(fake, cache)
    steps[1] = point("20261027", "steps", {"countSum": "5678"})
    self.assertEqual(self.sent(self.run_sync(fake, cache)), [("walk", "20261027", 5678)])
    second = fake.posts()[1][1]
    self.assertEqual([d["requestid"] for d in second], ["googlehealth-steps-20261027"])

  def test_each_sleep_and_nap_is_its_own_datapoint(self):
    fake = FakeAPIs(sessions={"sleep": [[sleep("nap1", "20261027", "20261027", 30, nap=True),
                                         sleep("night1", "20261026", "20261027", 450)]]})
    self.run_sync(fake, {}, cfg(zzz="sleep_hours"))
    [(_, dps)] = fake.posts()
    self.assertEqual(dps, [
      {"daystamp": "20261027", "value": 0.5, "comment": bgh.COMMENT,
       "requestid": "googlehealth-sleep-nap1"},
      {"daystamp": "20261027", "value": 7.5, "comment": bgh.COMMENT,
       "requestid": "googlehealth-sleep-night1"}])

  def test_two_runs_in_a_day_are_two_datapoints_and_types_filter(self):
    fake = FakeAPIs(sessions={"exercise": [[
      workout("am", "20261027", "RUNNING", 5000000, 1500),
      workout("pm", "20261027", "RUNNING", 3000000, 900),
      workout("dog", "20261027", "WALKING", 2000000, 1800)]]})
    self.run_sync(fake, {}, cfg(run=("exercise_km", "RUNNING")))
    [(_, dps)] = fake.posts()
    self.assertEqual([(d["requestid"], d["value"], d["comment"]) for d in dps], [
      ("googlehealth-exercise-am", 5.0, "Running, " + bgh.COMMENT),
      ("googlehealth-exercise-pm", 3.0, "Running, " + bgh.COMMENT)])

  def test_two_weigh_ins_in_a_day_are_two_datapoints(self):
    fake = FakeAPIs(sessions={"weight": [[weighin("pm", "20261027", 70900.0),
                                          weighin("am", "20261027", 70100.0)]]})
    self.run_sync(fake, {}, cfg(fat="kg"))
    [(_, dps)] = fake.posts()
    self.assertEqual(dps, [
      {"daystamp": "20261027", "value": 70.1, "comment": bgh.COMMENT,
       "requestid": "googlehealth-weight-am"},
      {"daystamp": "20261027", "value": 70.9, "comment": bgh.COMMENT,
       "requestid": "googlehealth-weight-pm"}])
    self.assertEqual(fake.count(":dailyRollUp"), 0)

  def test_no_types_means_every_workout(self):
    fake = FakeAPIs(sessions={"exercise": [[workout("a", "20261027", "RUNNING", 0, 60),
                                            workout("b", "20261027", "YOGA", 0, 60)]]})
    self.assertEqual(self.sent(self.run_sync(fake, {}, cfg(gym="exercise_count"))),
                     [("gym", "20261027", 1), ("gym", "20261027", 1)])

  def test_only_the_changed_session_is_resent(self):
    nights = [sleep("nap1", "20261027", "20261027", 30, nap=True),
              sleep("night1", "20261026", "20261027", 450)]
    fake = FakeAPIs(sessions={"sleep": [nights]})
    cache, c = {}, cfg(zzz="sleep_hours")
    self.run_sync(fake, cache, c)
    nights[1] = sleep("night1", "20261026", "20261027", 465) # stages finished processing
    self.assertEqual(self.sent(self.run_sync(fake, cache, c)), [("zzz", "20261027", 7.75)])

  def test_dry_run_neither_posts_nor_records(self):
    fake = FakeAPIs({"steps": [point("20261027", "steps", {"countSum": "1234"})]})
    cache = {}
    self.assertEqual(self.sent(self.run_sync(fake, cache, dry_run=True)),
                     [("walk", "20261027", 1234)])
    self.assertEqual(fake.posts(), [])
    self.assertEqual(cache["sent"], {"walk": {}})

  def test_goals_sharing_a_data_type_share_one_google_call(self):
    fake = FakeAPIs(sessions={"exercise": [[workout("w", "20261027", "RUNNING", 1609344, 600)]]})
    got = self.run_sync(fake, {}, cfg(runmi=("exercise_mi", "RUNNING"),
                                      runhrs=("exercise_hours", "RUNNING")))
    self.assertEqual(sorted(self.sent(got)),
                     [("runhrs", "20261027", 600/3600), ("runmi", "20261027", 1.0)])
    self.assertEqual(fake.count("/exercise/dataPoints?"), 1)

  def test_prunes_old_days_and_unconfigured_goals(self):
    fake = FakeAPIs({"steps": []})
    old = {"daystamp": "20261001"}
    keep = {"daystamp": "20261021"}
    cache = {"sent": {"walk": {"a": old, "b": keep}, "gone": {"c": keep}}}
    self.run_sync(fake, cache)
    self.assertEqual(cache["sent"], {"walk": {"b": keep}})

  def test_failed_post_is_not_recorded_so_next_run_retries(self):
    fake = FakeAPIs({"steps": [point("20261027", "steps", {"countSum": "1234"})]},
                    create_all_response={"successes": [], "errors": [{"status": "error"}]})
    cache = {}
    with self.assertRaises(RuntimeError): self.run_sync(fake, cache)
    self.assertEqual(cache["sent"], {"walk": {}})

class TestPostDatapoints(unittest.TestCase):
  def post(self, response):
    fake = FakeAPIs(create_all_response=response)
    with mock.patch.object(bgh, "http", fake):
      bgh.post_datapoints("bt", "walk", [{"daystamp": "20261027", "value": 1}])
  def test_duplicate_request_errors_are_fine(self):
    self.post({"successes": [], "errors": [
      {"errors": "Duplicate request", "requestid": "u1-googlehealth-steps-20261027"}]})
  def test_any_other_error_raises(self):
    with self.assertRaises(RuntimeError):
      self.post({"successes": [], "errors": [
        {"errors": "Duplicate request"}, {"daystamp": "x", "status": "error"}]})
    with self.assertRaises(RuntimeError):
      self.post({"successes": [], "errors": ["Did you forget the datapoints parameter?"]})

class TestAuth(unittest.TestCase):
  def test_code_from_redirect_decodes_the_code(self):
    url = "https://www.google.com/?code=4%2F0AbCd&scope=x%20y"
    self.assertEqual(bgh.code_from_redirect(url), "4/0AbCd")
  def test_code_from_redirect_without_code_exits(self):
    with self.assertRaises(SystemExit): bgh.code_from_redirect("https://www.google.com/")
    with self.assertRaises(SystemExit):
      bgh.code_from_redirect("https://www.google.com/?error=access_denied")

  def test_invalid_grant_exits_with_rerun_setup(self):
    def fake(*a, **k): raise bgh.ApiError("POST", bgh.GOOGLE_TOKEN, 400,
                                          '{"error": "invalid_grant"}')
    with mock.patch.object(bgh, "http", fake), self.assertRaises(SystemExit):
      bgh.access_token(CFG, {})

  def test_unexpired_access_token_is_reused(self):
    cache = {"access_token": "old", "access_token_expires": 2e9}
    self.assertEqual(bgh.access_token(CFG, cache), "old")

  def test_api_errors_never_echo_the_query_string(self):
    e = bgh.ApiError("GET", "https://www.beeminder.com/api/v1/users/me.json?auth_token=SECRET",
                     401, "{}")
    self.assertNotIn("SECRET", str(e))

if __name__ == "__main__": unittest.main()
