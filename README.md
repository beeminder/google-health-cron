# DIY Google Health → Beeminder

This is a little helper script meant to be used as a template for folks who might want to roll their own Google Health to Beeminder data integration.
It was inspired by Google 
[shutting down the Fitbit API in 2026](https://blog.beeminder.com/unfitbit "Beeminder blog post about the end of our Fitbit integration").
Beeminder doesn't support direct Google Health integration because of their paternalistic ideas about owning and using one's own data. 
So I'm not offering to maintain this script for you or run tech support for you, but here's what it would look like if you were to set up an API client "for personal use" to send your own data over to Beeminder. 

The rest of this README, and the code, was written by Claude (see the sourcery.html document to see my contribution), but I have done some minimal quality testing -- I went through all the steps of setting up the client and configuring the script etc and I can confirm that it appears to work as far as proof of concept goes. 

## Take it away, Claude....

The script, `beeminder_googlehealth.py`, reads your data from the Google
Health API (the successor to the Fitbit Web API) and sends it to your
Beeminder goals. You run it on your own computer, as often as you like,
usually from cron. It's a single Python file with no dependencies.

What it can send:

| Metric           | What each datapoint is                          | How many datapoints              |
|------------------|-------------------------------------------------|----------------------------------|
| `steps`          | steps that day                                  | one per day                      |
| `floors`         | floors climbed that day                         | one per day                      |
| `km`, `mi`       | distance covered that day                       | one per day                      |
| `kg`, `lb`       | your weight                                     | one per weigh-in                 |
| `sleep_hours`    | hours asleep in one sleep (naps count)          | one per sleep, on the day you woke up |
| `exercise_count` | 1                                               | one per workout                  |
| `exercise_km`, `exercise_mi` | distance of the workout             | one per workout                  |
| `exercise_hours` | duration of the workout, not counting pauses    | one per workout                  |

The exercise metrics can be limited to certain kinds of workout (only runs,
say). More on that below.


## Before you start

You'll need:

- **To sign in to the Google Health app on your phone.** This should be the same
  Google account you use to create the Client (below) and that you sync any
  fitness devices with (if you're using a Google Fitbit or Google watch).
  The API won't talk to your account if you haven't signed into the app.
- **Python 3.9 or newer.** On the computer you run the script from. 
  Check with `python3 --version`. There's nothing to `pip install`.
- **A computer that's on when it matters.** The script can only send data
  when it runs. If you cron it on a laptop that's asleep at your goal's
  deadline, your data won't get to Beeminder in time. An always-on machine
  like a home server, a Raspberry Pi, or a cheap cloud VM is ideal.
- About 20 minutes.

### Why do I have to make my own Google "app"?

Google doesn't hand out personal API keys for health data. The only way for
a program to read your data is OAuth, the "Sign in with Google, allow
access?" flow. That needs an app registered with Google. So you'll register
your own private app, used by exactly one person: you. Nobody else is
involved. Your data goes straight from Google to your computer to Beeminder.

Because it's a private app that Google has never reviewed, you'll see a
"Google hasn't verified this app" warning when you connect it. That's
expected. It's your own app.


## 1. Get the script

Put it somewhere other than `~/Documents`, `~/Desktop` or `~/Downloads`.
On a Mac, cron isn't allowed to read those folders.

```sh
git clone https://github.com/beeminder/[TODO: repo name].git ~/beeminder-googlehealth
cd ~/beeminder-googlehealth
```

Or download the ZIP from GitHub and unzip it to the same place. The only
file you need is `beeminder_googlehealth.py`.


## 2. Create your Google app

All of this happens in the Google Cloud console. Sign in with the same
Google account your health data is in. If the console asks you to agree to
terms of service, do.

### 2a. Make a project

A "project" is just a container for your app's settings.

1. Go to <https://console.cloud.google.com/projectcreate>.
2. Name it anything, e.g. `beeminder-sync`, and click **Create**.
3. Make sure the project picker at the top of the page shows your new
   project before you continue. Every step below happens inside it.

### 2b. Turn on the Google Health API

1. Go to <https://console.cloud.google.com/apis/library/health.googleapis.com>.
2. Click **Enable**.

### 2c. Describe your app

1. Go to <https://console.cloud.google.com/auth/overview> and click
   **Get started**.
2. **App information:** any app name, e.g. `My Beeminder sync`. For
   **User support email**, pick your own address.
3. **Audience:** choose **External**. "Internal" is only for Google
   Workspace organizations.
4. **Contact information:** your email again.
5. Agree to the user data policy and click **Create**.
6. Go to <https://console.cloud.google.com/auth/branding> (**Branding**).
   Google won't let you publish the app (step 2e) until you fill in two
   links under **App domain**:
   - **Application home page:** a page on your own website if you have one.
     Otherwise use this project's GitHub page,
     `https://github.com/beeminder/[TODO: repo name]`, or your fork of it.
   - **Application privacy policy link:** your own, or the one that comes
     with this project:
     `https://github.com/beeminder/[TODO: repo name]/blob/main/PRIVACY.md`.
     It says, truthfully, that your copy of the script sends your data to
     your Beeminder account and nowhere else.

   You can leave **Terms of service** blank.
7. Under **Authorized domains**, add the domain those links are on: your
   own domain, or `github.com`.
8. Skip the **App logo**. Google only shows logos for apps it has reviewed,
   and uploading one starts that review.
9. Click **Save**.

### 2d. Say what data the app can ask for

1. Go to <https://console.cloud.google.com/auth/scopes> (**Data Access**) and
   click **Add or remove scopes**.
2. At the bottom of the panel, under **Manually add scopes**, paste these
   three lines. Add all three even if you only plan to use steps; that way
   you won't have to come back here if you change your mind later.

   ```
   https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly
   https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly
   https://www.googleapis.com/auth/googlehealth.sleep.readonly
   ```

   (Steps, floors, distance and workouts need the first, weight the second,
   sleep the third.)
3. Click **Add to table**, then **Update**.
4. You'll see your three scopes listed under "Your restricted scopes". 
   Click **Save** at the bottom of the page.

Google calls these "restricted" scopes. That only matters for apps with more
than 100 users.

### 2e. Add yourself as a user, then publish

1. Go to <https://console.cloud.google.com/auth/audience> (**Audience**).
2. Under **Test users**, click **Add users**, enter your own Google address,
   and save.
3. Under **Publishing status**, click **Publish app** and confirm. If it's
   greyed out and says to complete your configuration on the Branding page,
   go back to 2c and fill in the home page and privacy policy links.

**Don't skip publishing.** While an app is in "Testing", Google makes its
logins expire after 7 days, and the script would stop working every week.
Publishing doesn't make your app public or findable. It just ends the
testing period. Google may warn that your app needs verification. It
doesn't: verification is only required to have more than 100 users.

If Google won't let you publish, the script still works in Testing mode,
but you'll have to re-run setup (step 4) once a week.

### 2f. Create the OAuth client and download it

The "client" holds the ID and secret the script uses to identify itself as
your app.

1. Go to <https://console.cloud.google.com/auth/clients> and click
   **Create client**.
2. **Application type:** **Web application**. (Not "Desktop app". The
   script uses the web-style flow so it also works on a computer with no
   browser.)
3. **Name:** anything.
4. Leave **Authorized JavaScript origins** empty. Those are for web pages
   that run Google sign-in code themselves, and the script doesn't. (If you
   already put something there, it does no harm.)
5. Under **Authorized redirect URIs**, click **Add URI** and enter exactly:

   ```
   https://www.google.com
   ```

   No trailing slash, nothing else. This is where Google sends your browser
   after you approve access. The script copies a code out of that address
   (step 4). The console will add `google.com` to your authorized domains
   by itself. That's expected.
6. Click **Create**.
7. In the dialog that pops up, click **Download JSON**. **Do it now**:
   Google only lets you download the client secret while this dialog is
   open. (If you miss it, open the client, click **Add secret**, and
   download that one.)

You'll get a file named something like
`client_secret_1234-abcd.apps.googleusercontent.com.json`.

The script keeps its settings in one fixed folder in your home directory,
`~/.config/beeminder-googlehealth/`, no matter where the script itself
lives. Keeping them out of the script's folder means you can't commit your
passwords to git by accident.

Move the downloaded file into that folder and rename it
`client_secret.json`. Setup looks for it there under exactly that name, so
you never have to type the long one. Setup reads it again if you ever
re-run it, so leave it there.

```sh
mkdir -p ~/.config/beeminder-googlehealth
mv ~/Downloads/client_secret_*.json ~/.config/beeminder-googlehealth/client_secret.json
```

Treat this file like a password.


## 3. Get your Beeminder side ready

### Your goals

You need one Beeminder goal for each metric you want to send. A goal can be
new or one you already have, but it shouldn't be connected to an autodata
integration. This script is its data source now.

A goal's **goalname** is the last part of its URL: for
`https://www.beeminder.com/alice/walk` the goalname is `walk`.

Some pointers:

- `steps`, `floors`, `km`/`mi`, `sleep_hours` and the `exercise_*` metrics
  suit **Do More** goals. Do More goals add up a day's datapoints, which is
  what you want for two runs or a night's sleep plus a nap.
- `kg`/`lb` suit **weight** goals. When you weigh in more than once a day,
  the goal's **aggregation** setting decides which number counts for the
  day: the last, the lowest, the average, and so on. Pick whichever you
  like; the script sends every weigh-in either way.
- For `sleep_hours`, a goal in hours lets Beeminder show values as hours and
  minutes.

### Your personal auth token

The script uses a "personal auth token" to add datapoints to your account.

1. Log in to Beeminder.
2. Visit <https://www.beeminder.com/api/v1/auth_token.json>. You'll see
   something like `{"username":"alice","auth_token":"aBcD1234efGH"}`.
3. Copy the `auth_token` value (`aBcD1234efGH` here), without the quotes.

Anyone with this token can do anything to your Beeminder account, so keep it
private.


## 4. Run setup

```sh
python3 beeminder_googlehealth.py setup
```

Setup reads `~/.config/beeminder-googlehealth/client_secret.json` from step
2f, then asks you a few things, in order.

**1. Your Beeminder auth token** from step 3.

**2. Your goals, one per line.** Each line is the goalname, then a metric
from the table at the top:

```
walk steps
weight kg
zzz sleep_hours
```

For the `exercise_*` metrics, you can add the kinds of workout to count:

```
run exercise_km RUNNING TRAIL_RUN TREADMILL
workouts exercise_count
```

The second line has no kinds, so it counts every workout of any kind.

The kinds must be spelled exactly as Google spells them, in capitals. Common
ones are `RUNNING`, `TRAIL_RUN`, `TREADMILL`, `WALKING`, `HIKING`, `BIKING`,
`OUTDOOR_BIKE`, `STATIONARY_BIKE`, `SWIMMING`, `SWIMMING_POOL`,
`SWIMMING_OPEN_WATER`, `YOGA`, `WEIGHTLIFTING`, `STRENGTH_TRAINING`, `HIIT`,
`ELLIPTICAL`, `ROWING_MACHINE`, and `OTHER`. The full list (about 200) is
[here](https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints#Exercise.ExerciseType).

Setup checks each kind against Google's current list. If one isn't
on it, setup says so and you type that goal's line again.

Press Enter on an empty line when you're done.

**3. Connecting to Google.** Setup prints a long link.

1. Open it in a browser. It doesn't have to be on the same computer: if
   you're setting this up over SSH on a server, open it on your laptop or
   phone.
2. Choose your Google account.
3. You'll see **Google hasn't verified this app**. Click **Advanced**, then
   **Go to *your app name* (unsafe)**. It's your own app from step 2.
4. Google asks which data to share. **Tick every box.** The script only
   asks for what your chosen metrics need, and it stops if anything is left
   unticked.
5. Click **Continue**. Google sends you to the regular google.com search
   page. That looks wrong, but it's what's supposed to happen. Ignore the
   page and copy the **whole address from the address bar**. It looks like
   `https://www.google.com/?code=4/0AVG7fiQ…&scope=…`.
6. Paste it into the terminal and press Enter. Do it within a few minutes,
   because the code in it expires quickly and only works once. If it fails,
   just run setup again.

Setup then saves everything to `~/.config/beeminder-googlehealth/config.json`.
It does a first sync and prints each datapoint it sent, one per line:
goalname, date, value, and an ID. Look at your goals on Beeminder and you should
see the last week of data.


## 5. Run it on a schedule

The script only sends something when your data has changed since the last
run, so running it often costs nothing. Your phone syncs your device with
Google every 15 minutes or so while the Google Health app is open, so every
10 to 15 minutes is plenty.

### Mac and Linux: cron

Find the full path to your Python, because cron doesn't search your usual
paths:

```sh
which python3
```

Then open your crontab:

```sh
crontab -e
```

Add this line, swapping in your Python path and where you put the script.
It means "every 15 minutes":

```
*/15 * * * * /usr/bin/python3 /Users/alice/beeminder-googlehealth/beeminder_googlehealth.py >> /Users/alice/beeminder-googlehealth/sync.log 2>&1
```

Save and quit. (If `crontab -e` opens vim: press `i` to type, then `Esc`,
`:wq`, Enter to save.)

The script prints nothing when all is well. If something goes wrong, the
error goes to `sync.log` (that's what the `>> … 2>&1` part does), and the
script keeps failing loudly on every run until it's fixed. Look there first
if your data stops showing up.

### Windows

Use Task Scheduler. Make a task that runs
`python C:\path\to\beeminder_googlehealth.py` every 15 minutes.

### Checking on it

```sh
python3 beeminder_googlehealth.py --dry-run
```

This prints what the script *would* send right now, without sending
anything. If it prints nothing, Beeminder is up to date.


## How it behaves

- **Each run looks at the last 7 days.** If your watch doesn't sync for a
  few days, the data still arrives once it does. Anything older than a week
  is left alone.
- **Datapoints update in place.** Today's step count is one datapoint that
  grows during the day, not a new datapoint every run. If you edit a
  weigh-in or workout in the app, its Beeminder datapoint changes to match.
  Under the hood, every datapoint carries an ID that Beeminder uses to
  update instead of duplicate.
- **It never deletes anything.** If you delete a workout or weigh-in in the
  app, delete its Beeminder datapoint yourself.
- **It only touches its own datapoints.** You can still add datapoints to
  these goals by hand.
- **If you edit or delete one of *its* datapoints on Beeminder**, it won't
  notice until that day's data changes in Google. To make it resend the
  last 7 days, delete `~/.config/beeminder-googlehealth/cache.json`. That
  file only holds what was last sent, so deleting it is always safe.
- **Days run midnight to midnight**, your local time. Sleep counts toward
  the day you woke up, and a workout toward the day it started. If your goal's
  deadline is after midnight, anything after midnight counts toward the next
  day.
- **New data shows up only after your device syncs** to the Google Health app.


## Changing things later

Your settings are in `~/.config/beeminder-googlehealth/config.json`:

```json
{
  "google": {
    "client_id": "1234-abcd.apps.googleusercontent.com",
    "client_secret": "GOCSPX-…",
    "refresh_token": "1//0g…"
  },
  "beeminder_auth_token": "aBcD1234efGH",
  "goals": {
    "walk": {"metric": "steps", "types": []},
    "run":  {"metric": "exercise_km", "types": ["RUNNING", "TRAIL_RUN"]}
  },
  "exercise_types": ["EXERCISE_TYPE_UNSPECIFIED", "AEROBIC_WORKOUT", "ARCHERY", "…"]
}
```

You can edit `goals` by hand, for instance to point a metric at a different
goal or change the kinds of workout. The exception is adding a goal that
uses a new kind of Google data (sleep, or weight, if you didn't have them
before). For that, re-run setup so Google can ask your permission for it.
Re-running setup is always fine. It replaces the whole file, so you'll
enter your goals again.

Setup also saves Google's list of every kind of workout, as
`exercise_types`. Each run checks your goals' kinds of workout against that
list and stops with an error if one isn't on it. Either fix the spelling,
or, if it's a kind Google added since your last setup, re-run setup to get
the latest list.

This file holds your Google login and your Beeminder token. It's readable
only by your user. Don't share it, and don't copy it into the repo.


## Troubleshooting

**Google shows "Error 400: redirect_uri_mismatch" during setup.**
The redirect URI on your client (step 2f) isn't exactly
`https://www.google.com`. Fix it in the console, wait a minute, and run
setup again.

**Google shows "Access blocked" or "Error 403: access_denied".**
The app is still in Testing and your address isn't on the test-users list.
Add yourself, or publish the app (step 2e).

**Setup says your account isn't linked.**
Sign in to the Google Health app on your phone with this Google account,
then run setup again.

**Setup says some permissions are missing.**
You left a box unticked on Google's consent screen. Run setup again and
tick them all.

**The script says Google no longer accepts your refresh token.**
Run setup again. Usually this means one of three things:
- The app is still in Testing (see step 2e).
- You removed its access in your Google account.
- The script hasn't run in about six months.

**Errors mentioning HTTP 401 from beeminder.com.**
The Beeminder auth token is wrong. Get it again (step 3), fix it in
`config.json`, or re-run setup.

**Errors mentioning HTTP 404 from beeminder.com.**
A goalname in `config.json` doesn't match a goal of yours, or the goal is
archived.

**On a Mac, `sync.log` says "Operation not permitted".**
The script is inside `~/Documents`, `~/Desktop` or `~/Downloads`, which cron
can't read. Move it (step 1) and update your crontab.

**My data is late.**
Open the Google Health app so your device syncs. The script can only send
what Google has.


## Uninstalling

1. Remove the line from your crontab (`crontab -e`).
2. Delete `~/.config/beeminder-googlehealth/`.
3. Remove the app's access to your Google data at
   <https://myaccount.google.com/connections>.
4. Optionally, delete the Google Cloud project at
   <https://console.cloud.google.com/iam-admin/settings> (**Shut down**).
