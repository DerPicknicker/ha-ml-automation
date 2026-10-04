# ML Automation

[![Validate](https://github.com/DerPicknicker/ha-ml-automation/actions/workflows/validate.yml/badge.svg)](https://github.com/DerPicknicker/ha-ml-automation/actions/workflows/validate.yml)
[![HACS custom repository](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A Home Assistant integration that learns when you use your devices and
switches them for you, so you don't have to write an automation for every one.

You tell it two things: **what to control** and **what to learn from**. There
are no thresholds, schedules or states to enter. It records the learning data,
works out by itself what "in use" looks like, finds the times at which that
repeats, and then

- switches the device **on a bit earlier** than you usually start (default 15 min),
- switches it **off a while after** you usually stop (default 60 min),
- and **never switches it off while it is in use**, e.g. while the TV still
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

Two steps, nothing optional:

1. **What should be controlled?** The entity to switch on and off: a switch, a
   light, a media player, a fan, a climate entity, a cover …
2. **What should it learn from?** One or more entities that show when it is in
   use. This is pre-filled with the controlled entity itself, plus the power
   (or current) sensor of the same device if it has one. Add or remove
   whatever you like: a power sensor, a media player, a motion sensor …

That's it. Everything else has a default and can be changed later under
*Configure*: how early and how late to switch, whether to switch on, off or
both, how many days to remember and how sure the model has to be.

### What "in use" means

- **Numbers** (power, current, brightness …): the integration looks at the
  recorded values and finds the level that separates "idle" from "active" on
  its own, e.g. 1 W standby versus 90 W watching. You can see the value it
  found in the attributes of the *Detected activity* sensor. A number that
  just drifts, like a temperature, has no such level and is ignored.
- **Everything else**: active unless the state is something like `off`,
  `standby`, `idle`, `closed` or `not_home`.
- **Several entities:** in use as soon as *any* of them is active.
- **The controlled entity itself** only counts when it is the only thing to
  learn from. As soon as there is other data, that is used instead, because a
  plug that the integration switched on itself says nothing about real use.

### Example: TV on a smart plug that is always on

- Control: `switch.tv_plug`
- Learn from: `switch.tv_plug` and `sensor.tv_plug_power` (suggested
  automatically)

The plug's state is always `on` and teaches nothing, but its power readings
show that you usually watch from about 18:00 to 19:00. After a few days the
plug is switched on at 17:45 and off at 20:00. If you are still watching at
20:00 the plug stays on; it is switched off once the TV has been idle for 15
minutes.

### Example: a lamp with nothing but itself

- Control: `light.reading_lamp`
- Learn from: `light.reading_lamp`

The lamp's own on/off times are learned. Since nothing else shows whether it is
really in use, it is switched off an hour after the learned time without
waiting.

## Entities

Each pattern is a device with these entities:

| Entity | Purpose |
| --- | --- |
| Status | `Learning`, `Ready`, `Controlling` or `Switch-off postponed` |
| Next switch-on / Next switch-off | When the controlled entity will be switched next |
| Learned patterns | Number of habits; the attributes list each with its time, days and confidence |
| Predicted state | Whether the pattern expects the controlled entity to be on right now; the attribute `probability` is the raw model output |
| Detected activity | Whether what is learned from is active right now. The attributes show every learning entity and the level that was learned for it. |
| Days of data | How many complete days the model is built from |
| Automation (switch) | Off = keep recording and learning, but don't switch anything |
| Switch on early / Switch off late | The two timings, adjustable from a dashboard |
| Predict now (button) | Retrain and put the controlled entity into the expected state right away |
| Re-learn (button) | Forget everything and start learning from scratch |

## How it learns

The model is a small **random forest** (an ensemble of decision trees), written
in plain Python so it installs on every Home Assistant system without extra
packages.

1. Once a minute every learning entity is sampled; the samples of each
   5-minute slot are averaged and stored. Short spikes disappear in the
   average.
2. For numeric entities the level between "idle" and "active" is derived from
   the stored values (Otsu's method: the split at which the two sides differ
   most).
3. Every slot of every observed day is labelled *in use* or *not in use*, and
   decision trees learn to predict that from the **time of day** and the **day
   of the week**. Each of the 20 trees is trained on a random selection of the
   observed days.
4. Averaging the trees gives the probability of use for every slot of the
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
- **Head start:** when a pattern is added, the recorder history of the
  learning entities is used, so there is usually enough data right away.
  Re-learn does not do that; it really starts empty.
- **Gaps are gaps.** Times at which Home Assistant was not running are left
  out, not counted as "not in use".
- **Today doesn't count yet.** The model is retrained at midnight on complete
  days only. Training takes a fraction of a second.
- **Its own actions are not habits.** The device being on during the lead time
  before a habit, or still being on until the delayed switch-off, is the
  integration's doing and is left out of the training data. Otherwise the
  habit would creep 15 minutes earlier every day.

Why not a neural network such as a GRU? It would need PyTorch or TensorFlow,
which do not install reliably on Home Assistant OS, and a few weeks of data
from a single device are far too little to train one well. A forest gets more
out of little data, and you can read what it learned in the *Learned patterns*
sensor.

### Good to know

- The time of day and the weekday are what the model predicts from. The
  learning entities define what "in use" is; they are not (yet) conditions
  such as "only when somebody is home".
- A power sensor is the best thing to learn from, because it shows real use.
  A device learned only from itself is, once the integration is in control,
  switched by the integration; shifts in your routine are then only picked up
  when you switch it yourself at other times, or after a Re-learn.
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
