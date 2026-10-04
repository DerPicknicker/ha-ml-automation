# ML Automation

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

**HACS:** add this repository as a custom repository (type *Integration*),
install *ML Automation* and restart Home Assistant.

**Manually:** copy `custom_components/ml_automation` into the
`custom_components` folder of your Home Assistant configuration and restart.

Then go to *Settings → Devices & services → Add integration → ML Automation*.
Add the integration once per pattern you want to learn.

## Setting up a pattern

| Step | What you choose |
| --- | --- |
| Entity to learn from | Anything: a power sensor, a switch, a media player, a person, a door sensor … |
| When is it active? | Numeric entities: a threshold ("active above 20 W"). Others: the states that count as active (`on`, `playing`, `home` …). Short spikes can be ignored. |
| What should be switched? | Any number of switches, lights, media players, fans, climate entities, covers … Leave empty to only learn and predict. Switching on and switching off can be enabled separately. |
| How early / how late | Minutes before the learned switch-on time and after the learned switch-off time. |
| In-use guard | Switch-off is postponed while this entity is active. By default the entity that is learned from is used. |
| Presence condition | Optional: only switch on while e.g. a person is home. |
| How should it learn? | Which days look alike, how much history to keep, and how regular something must be to count as a habit. |

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
| Learned patterns | Number of habits; the attributes list each with its time and confidence |
| Predicted state | Whether the pattern expects the targets to be on right now |
| Detected activity | Whether the learned-from entity currently counts as active. Use it to check your threshold. |
| Days of data | How many complete days the model is built from |
| Automation (switch) | Off = keep learning, but don't switch anything |
| Switch on early / Switch off late | The two timings, adjustable from a dashboard |
| Predict now (button) | Re-evaluate the pattern and put the targets into the expected state right away |
| Re-learn (button) | Forget everything and start learning from scratch |

## How it learns

There is no black box. Every time the learned-from entity becomes active or
inactive, the time of day is stored. Once a day the stored times are searched
for a time window in which the same change happened on most days. Such a window
is a habit, and its median time is what the integration acts on.

- **Days:** workdays and weekend are learned separately by default. You can
  also treat all days alike or learn every weekday on its own (needs more
  weeks of data).
- **How regular is regular?** By default a habit has to show up on at least 3
  days and on at least 60 % of the observed days, within ±45 minutes.
- **Forgetting:** only the last 28 days count, so slow changes are followed
  automatically. For sudden changes there is the Re-learn button.
- **Head start:** when a pattern is added, the recorder history of the entity
  is used, so there is usually enough data right away. Re-learn does not do
  that; it really starts empty.
- **Today doesn't count yet.** The model is rebuilt at midnight from complete
  days only.
- **Its own actions are not habits.** If the integration switches something and
  the learned-from entity follows (e.g. you learn from the same light you
  switch), that is counted as "the habit happened at its usual time", not as a
  new, earlier habit. If you undo the action within 30 minutes, it doesn't
  count at all, and a habit you keep rejecting fades away.

### Good to know

- A power sensor is the best thing to learn from, because it shows real use.
  If you learn from the same entity that is switched, the integration can only
  see its own switching once it is in control, and shifts in your routine are
  no longer picked up without a Re-learn.
- Nothing is switched if Home Assistant was not running at the time.
- A postponed switch-off is dropped after 12 hours or when the next switch-on
  is due.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest
```

`learner.py` contains the learning logic and has no Home Assistant
dependencies; `manager.py` connects it to Home Assistant.
