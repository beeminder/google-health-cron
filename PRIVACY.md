# Privacy policy

This policy covers any copy of the
[DIY Google Health → Beeminder](https://github.com/beeminder/[TODO: repo name])
script. Each copy is a private app, set up and run by one person, for their
own data, on a computer they control.

**What it reads.** The Google Health data that its owner chose during setup
(steps, floors, distance, weight, sleep or workouts), from their own Google
account. It only reads. It never changes or deletes anything in Google.

**Where the data goes.** To the owner's own Beeminder goals, as datapoints,
and nowhere else. Nothing is sent to the script's authors or to anyone else.

**What it keeps.** Two files on the computer it runs on: a settings file
with the owner's Google and Beeminder credentials, and a record of the
datapoints it sent in the last 7 days.

**How to stop it.** Remove its access at
<https://myaccount.google.com/connections>, then delete the
`~/.config/beeminder-googlehealth/` folder.
