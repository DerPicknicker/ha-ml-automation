# ML Automation

[![Validate](https://github.com/DerPicknicker/ha-ml-automation/actions/workflows/validate.yml/badge.svg)](https://github.com/DerPicknicker/ha-ml-automation/actions/workflows/validate.yml)
[![HACS custom repository](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A Home Assistant integration that learns the habits of an entity and acts on
them, so you don't have to write an automation for every device.

Point it at an entity, for example the power sensor of your TV. It watches when
that entity becomes active and inactive, finds the times that repeat, and then

- switches things **on a bit earlier** than you usually start (default 15 min),
- switches things **off a while after** you usually stop (default 60 min),
- and **leaves them alone while they are in use**, e.g. while the TV still
  draws a lot of power.

If your routine changes, press **Re-learn** and it starts from scratch.

## Installation

**HACS:** click the button to open the repository in your own Home Assistant,
then choose *Download* and restart Home Assistant.

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=DerPicknicker&repository=ha-ml-automation&category=integration)

If the button does not work for you: open HACS, choose *⋮ → Custom
repositories*, add `https://github.com/DerPicknicker/ha-ml-automation` with
type *Integration*, then install *ML Automation* and restart Home Assistant.

**Manually:** copy `custom_components/ml_automation` into the
`custom_components` folder of your Home Assistant configuration and restart.

After the restart, add a pattern with this button, or go to *Settings → Devices
& services → Add integration → ML Automation*. Add the integration once per
pattern you want to learn.

[![Open your Home Assistant instance and start setting up ML Automation.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ml_automation)

## Setting up a pattern

| Step | What you choose |
| --- | --- |
| Entity to learn from | Anything: a power sensor, a switch, a media player, a person, a door sensor … |
| When is it active? | Numeric entities: a threshold ("active above 20 W"). Others: the states that count as active (`on`, `playing`, `home` …). Short spikes can be ignored. |
| What should be switched? | Any number of switches, lights, media players, fans, climate entities, covers … Leave empty to only learn and predict. Switching on and switching off can be enabled separately. |
| How early / how late | Minutes before the learned switch-on time and after the learned switch-off time. |
| In-use guard | Switch-off is postponed while this entity is active. By default the entity that is learned from is used. |
| Presence condition | Optional: only switch on while e.g. a person is home. |
| How should it learn? | How much history to keep, how many days of data are needed, and how sure the model must be before something counts as a habit. |

Everything except the learned-from entity can be changed later via *Configure*.

### Example: TV on a smart plug

- Learn from: `sensor.tv_power`, active above `20 W`
- Switch: `switch.tv_plug`

You usually start watching around 18:00 and stop around 19:00. After a few days
the plug is switched on at 17:45 and off at 20:00. If you are still watching at
20:00 the plug stays on; it is switched off once the TV has been idle for 15
minutes.

## Entities

Each pattern is a device with these entities:

| Entity | Purpose |
| --- | --- |
| Status | `Learning`, `Ready`, `Controlling` or `Switch-off postponed` |
| Next switch-on / Next switch-off | When the targets will be switched next |
| Learned patterns | Number of habits; the attributes list each with its time, days and confidence |
| Predicted state | Whether the pattern expects the targets to be on right now; the attribute `probability` is the raw model output |
| Detected activity | Whether the learned-from entity currently counts as active. Use it to check your threshold. |
| Days of data | How many complete days the model is built from |
| Automation (switch) | Off = keep learning, but don't switch anything |
| Switch on early / Switch off late | The two timings, adjustable from a dashboard |
| Predict now (button) | Re-evaluate the pattern and put the targets into the expected state right away |
| Re-learn (button) | Forget everything and start learning from scratch |

## How it learns

The model is a small **random forest** (an ensemble of decision trees), written
in plain Python so it installs on every Home Assistant system without extra
packages.

1. Every observed day is cut into 5-minute slots, each labelled *active* or
   *not active*.
2. Decision trees learn to predict that label from the **time of day** and the
   **day of the week**. Each of the 20 trees is trained on a random selection
   of the observed days.
3. Averaging the trees gives the probability of activity for every slot of the
   week. Where it rises above the confidence threshold (default 50 %), a
   switch-on habit begins; where it falls below, a switch-off habit.

What follows from that:

- **It finds out on its own which days are alike.** If your weekends differ
  from your workdays, or Wednesday is different from every other day, the
  trees split on the weekday. If all days look the same, they don't. Telling
  days apart needs at least 3 days (configurable) on each side, so one unusual
  evening does not become a rule.
- **One-offs are ignored.** Something that happened on one day out of ten has a
  probability of about 10 % and stays below the threshold.
- **Recent days count more.** Only the last 28 days are kept, and within them a
  day's influence halves every 14 days. Slow changes, like watching longer in
  winter, are followed automatically. For sudden changes there is the
  **Re-learn** button.
- **Head start:** when a pattern is added, the recorder history of the entity
  is used, so there is usually enough data right away. Re-learn does not do
  that; it really starts empty.
- **Today doesn't count yet.** The model is retrained at midnight on complete
  days only. Training takes a fraction of a second.
- **Its own actions are not habits.** If the integration switches something and
  the learned-from entity follows (e.g. you learn from the same light you
  switch), that is counted as "the habit happened at its usual time", not as a
  new, earlier habit. If you undo the action within 30 minutes, it doesn't
  count at all, and a habit you keep rejecting fades away.

Why not a neural network such as a GRU? It would need PyTorch or TensorFlow,
which do not install reliably on Home Assistant OS, and a few weeks of data
from a single device are far too little to train one well. A forest gets more
out of little data, and you can read what it learned in the *Learned patterns*
sensor.

### Good to know

- A power sensor is the best thing to learn from, because it shows real use.
  If you learn from the same entity that is switched, the integration can only
  see its own switching once it is in control, and shifts in your routine are
  no longer picked up without a Re-learn.
- Until there is enough data to tell days apart (about two weeks for
  workdays versus weekend), all days are treated alike.
- Nothing is switched if Home Assistant was not running at the time; press
  **Predict now** to catch up.
- A postponed switch-off is dropped after 12 hours or when the next switch-on
  is due.

## Problems and ideas

Please use the [issue forms](https://github.com/DerPicknicker/ha-ml-automation/issues/new/choose). For bugs, attach the diagnostics
of the pattern (*⋮ → Download diagnostics* on the pattern under *Settings →
Devices & services → ML Automation*).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The code is explained in
[AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
