# ML Automation

[![Validate](https://github.com/DerPicknicker/ha-ml-automation/actions/workflows/validate.yml/badge.svg)](https://github.com/DerPicknicker/ha-ml-automation/actions/workflows/validate.yml)
[![HACS custom repository](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A Home Assistant integration that learns when you use your devices and
switches them for you, so you don't have to write an automation for every one.

You tell it two things: **what to learn from** and **what to control**. There
are no thresholds, schedules or states to enter. It records the learning data,
works out by itself what "in use" looks like, finds the times at which that
repeats, and then

- switches the device **on a bit earlier** than you usually start (default 15 min),
- switches it **off a while after** you usually stop (default 60 min),
- and **never switches it off while it is in use**, e.g. while the TV still
  draws a lot of power.

If you'd rather be asked first, switch the automation off: it then only
**suggests**, and one tap does it. If your routine changes for good, press
**Re-learn**.

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

1. **What should it learn from?** One or more entities that show when
   something is in use: a power sensor, the device itself, a media player, a
   motion sensor …
2. **What should be controlled?** The entity to switch on and off: a switch, a
   light, a media player, a fan, a climate entity, a cover … If what you learn
   from can be switched, or belongs to a device that can (the power sensor of
   a smart plug), that entity is already filled in.

That's it. Everything else has a default and can be changed later under
*Configure*: what to learn from, how early and how late to switch, how many
days to remember and how sure the model has to be. Whether it switches on, off
or both is decided with two switches on the device page.

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

- Learn from: `sensor.tv_plug_power`
- Control: `switch.tv_plug` (suggested automatically)

The plug's state is always `on` and would teach nothing, but its power readings
show that you usually watch from about 18:00 to 19:00. After a few days the
plug is switched on at 17:45 and off at 20:00. If you are still watching at
20:00 the plug stays on; it is switched off once the TV has been idle for 15
minutes.

### Example: a lamp with nothing but itself

- Learn from: `light.reading_lamp`
- Control: `light.reading_lamp` (suggested automatically)

The lamp's own on/off times are learned. Since nothing else shows whether it is
really in use, it is switched off an hour after the learned time without
waiting.

## Examples

| You control | It learns from | What you get |
| --- | --- | --- |
| The plug of a TV or hi-fi | The plug's power sensor | Powered shortly before you usually watch, standby cut afterwards, never in the middle of a film |
| The plug of a coffee machine | Its power sensor | Warmed up before your usual first coffee, off after the morning |
| The plug of a desk, PC or monitor | Its power sensor | On before you usually start working, off once the PC has been idle |
| The plug of a washing machine or dryer | Its power sensor | Standby cut outside your usual laundry times, never during a cycle |
| A lamp | The lamp itself | On and off at your usual times |
| A hallway or bathroom light | A motion sensor | On before the usual morning and evening traffic, off after, never while there is motion |
| Heating, a water heater or a towel rail | A person or presence sensor | On before you are usually home, off after you usually leave |
| Blinds | The cover itself | Opened and closed at your usual times |
| The plug of speakers or an amplifier | A media player | On before you usually listen, off after, never while something plays |
| A router's guest Wi-Fi switch, a charger, a pump … | Whatever shows that it is in use | The same idea: ready before, off after |

It fits anything that is used at roughly the same times. It does not react to
events ("motion now → light now") and it does not set values such as
brightness or temperature.

## Entities

Each pattern is a device with these entities:

| Entity | Purpose |
| --- | --- |
| Status | Where the pattern stands, see below |
| Suggestion | `Switch on`, `Switch off` or `Nothing`: what you probably want right now |
| Apply suggestion (button) | Does what is suggested. Unavailable while there is nothing to suggest. |
| Automation (switch) | Master switch. On = switches by itself. Off = keeps learning and only suggests. |
| Switch on automatically (switch) | Off = never switches on by itself, only suggests it |
| Switch off automatically (switch) | Off = never switches off by itself, only suggests it |
| Probability of use | The model's prediction for right now, in percent |
| Next switch-on / Next switch-off | When the controlled entity will be switched next |
| Learned patterns | Number of habits; the attributes list each with its time, days and confidence |
| Predicted state | Whether the pattern expects the controlled entity to be on right now; the attribute `probability` is the raw model output |
| Detected activity | Whether what is learned from is active right now. The attributes show every learning entity and the level that was learned for it. |
| Days of data | How many complete days the model is built from |
| Switch on early / Switch off late | The two timings, adjustable from a dashboard |
| Predict now (button) | Retrain and put the controlled entity into the expected state right away |
| Re-learn (button) | Forget what was learned and learn again from the last 7 days |

### Status

| Status | Meaning |
| --- | --- |
| Collecting data | Fewer than 3 complete days are recorded. The attributes show how many there are. |
| No activity seen yet | There is enough data, but the learning entities were never active in it, e.g. the power never rose above standby. |
| No regular pattern yet | There was activity, but not at similar times on enough days. |
| Ready, suggesting only | A pattern was found; the automation is off. |
| Controlling | A pattern was found and the entity is switched automatically. |
| Switch-off postponed | It is past the usual time, but the entity is still in use. |

*Next switch-on*, *Next switch-off* and *Predicted state* show "unknown" until
a pattern was found; there is nothing to show before that.

### Suggestions

A suggestion appears when the pattern calls for switching and the integration
is not doing it itself, i.e. the automation or the switch for that direction
is off, and only when the model is at least 70 % sure. To only cut standby
power but never power anything up, turn *Switch on automatically* off. Put the *Apply
suggestion* buttons of your patterns into an entity-filter or conditional card
and your dashboard shows them only when there is something to suggest.

Every new suggestion also fires the event `ml_automation_suggestion` with
`name`, `entity_id`, `suggestion` (`switch_on` / `switch_off`), `confidence`
and `apply_button`. That is enough to send it to your phone with a button; an
example to adapt (untested, replace the notify service):

```yaml
automation:
  - alias: "ML Automation: suggestions on my phone"
    mode: parallel
    triggers:
      - trigger: event
        event_type: ml_automation_suggestion
    actions:
      - action: notify.mobile_app_my_phone
        data:
          title: "{{ trigger.event.data.name }}"
          message: >-
            {{ 'Switch on now?' if trigger.event.data.suggestion == 'switch_on'
               else 'Switch off now?' }}
          data:
            actions:
              - action: "ML_APPLY_{{ trigger.event.data.entry_id }}"
                title: "Yes"
      - wait_for_trigger:
          - trigger: event
            event_type: mobile_app_notification_action
            event_data:
              action: "ML_APPLY_{{ trigger.event.data.entry_id }}"
        timeout: "01:00:00"
        continue_on_timeout: false
      - action: button.press
        target:
          entity_id: "{{ trigger.event.data.apply_button }}"
```

## On your dashboard

Everything the integration predicts is an ordinary entity with its own icon, so
it works in any card. Two examples for
[Bubble Card](https://github.com/Clooos/Bubble-Card); they follow its
documentation and are a starting point to adapt. Replace the entity ids with
yours (they are derived from the name of the controlled entity and from your
language).

**The device with its prediction.** A normal Bubble button for the plug. Next
to it: the suggestion, which only appears when there is one and applies it on
tap, the probability of use (labelled, because a bare percentage could be
anything), and the next switch-on.

```yaml
type: custom:bubble-card
card_type: button
button_type: switch
entity: switch.tv_plug
name: TV
sub_button:
  - entity: sensor.tv_suggestion
    show_state: true
    visibility:
      - condition: state
        entity: sensor.tv_suggestion
        state_not: none
    tap_action:
      action: perform-action
      perform_action: button.press
      target:
        entity_id: button.tv_apply_suggestion
  - entity: sensor.tv_probability_of_use
    name: Use
    show_name: true
    show_state: true
    show_icon: false
    show_background: false
    tap_action:
      action: more-info
  - entity: sensor.tv_next_switch_on
    show_state: true
    show_background: false
```

**A suggestion bubble, like on a phone.** One card per pattern that is only
there while something is suggested; a tap does it. Stack several of them at the
top of a view and it stays empty until the integration has something to offer.

```yaml
type: custom:bubble-card
card_type: button
button_type: state
entity: sensor.tv_suggestion
name: TV
show_state: true
visibility:
  - condition: state
    entity: sensor.tv_suggestion
    state_not: none
tap_action:
  action: perform-action
  perform_action: button.press
  target:
    entity_id: button.tv_apply_suggestion
```

Suggestions appear for whatever the integration does not do by itself, so turn
*Automation* (or one of the two direction switches) off for the patterns you
want to be asked about.

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
  **Re-learn** button, which starts over from the last 7 days only.
- **Head start:** when a pattern is added, the recorder history of the
  learning entities is used, so there is usually enough data right away. Home
  Assistant keeps 10 days of history by default.
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
  when you switch it yourself at other times.
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
