# Distributed Interaction

**Team:** Elliott Kalt (esk226), Jovian Wang (jlw457), Alexa Yang (ay482)

Our Part C project, **Spacecrew**, has its own write-up: [spaceteam/README.md](spaceteam/README.md).

<details>
<summary>Prep and overview (lab instructions)</summary>

## Prep

1. Pull the new changes
2. Read: [The Presence Table](https://dl.acm.org/doi/10.1145/1935701.1935800) ([video](https://vimeo.com/15932020))

## Overview

Build interactive systems where **multiple devices communicate over a network** using MQTT messaging. Work in teams of 3+ with Raspberry Pis.

**Parts:**
- A: Learn MQTT messaging
- B: Try collaborative pixel grid demo
- C: Build your own distributed system

</details>

---

## Part A: MQTT Messaging

<details>
<summary>Lab instructions</summary>

MQTT = lightweight messaging for IoT. Publish/subscribe model with central broker.

**Concepts:**
- **Broker**: `farlab.infosci.cornell.edu:1883`
- **Topic**: Like `IDD/bedroom/temperature` (use `#` wildcard)
- **Publish/Subscribe**: Send and receive messages

**Install MQTT tools on your Pi:**
```bash
sudo apt-get update
sudo apt-get install -y mosquitto-clients
```

**Test it:**

**Subscribe to messages (listener):**
```bash
mosquitto_sub -h farlab.infosci.cornell.edu -p 1883 -t 'IDD/#' -u idd -P 'device@theFarm'
```

**Publish a message (sender):**
```bash
mosquitto_pub -h farlab.infosci.cornell.edu -p 1883 -t 'IDD/test/yourname' -m 'Hello!' -u idd -P 'device@theFarm'
```

> **💡 Tips:**
> - Replace `yourname` with your actual name in the topic
> - Use single quotes around the password: `'device@theFarm'`
> - For your own projects, publish under your team's topic space: `IDD/<yourteam>/...` (for example `IDD/team3/bedroom/light`). Everyone shares one broker login, so anything you publish can reach everyone's projects.

**🔧 Debug Tool:** View all MQTT messages in real-time at `http://farlab.infosci.cornell.edu:5001`

MQTT agrees on almost nothing in advance: you invent the topics and what the messages mean. Smart-home standards like Matter fix the vocabulary instead (every "light" behaves the same). Keep this trade-off in mind when you design your topics in Part C.

</details>

![MQTT Explorer showing messages](imgs/MQTT-explorer.png)

**💡 5 ideas for messaging between devices**

1. A doorbell Pi publishes a ring and every screen in the house flashes who's at the door.
2. Plant sensors publish soil moisture and one Pi by the sink shows which plant needs water.
3. A "quiet hours" button publishes a topic that mutes every speaker on the network.
4. Each roommate's Pi publishes whether they're home so the kitchen screen shows who to cook for.
5. A shouting game where each Pi only sees someone else's task (what we built for Part C).

---

## Part B: Collaborative Pixel Grid

<details>
<summary>Lab instructions and setup</summary>

Each Pi = one pixel, controlled by RGB sensor, displayed in real-time grid.

**Architecture:** `Pi (sensor) → MQTT → Server → Web Browser`

**Setup:**

1. **Sensor**

#### Light/Proximity/Gesture sensor (APDS-9960)
We use this sensor [Adafruit APDS-9960](https://www.adafruit.com/product/3595) for this exmaple to detect light (also RGB)

<img src="https://cdn-shop.adafruit.com/970x728/3595-06.jpg" width=200>

Connect it to your pi with Qwiic connector

<img src="imgs/IMG_0270.jpg" height="200" />
We need to use the screen to display the color detection, so we need to stop the running piscreen.service to make your screen available again

```bash
# stop the screen service
sudo systemctl stop piscreen.service
```

if you want to restart the screen service
```bash
# start the screen service
sudo systemctl start piscreen.service
```

2. **Server** (one person on laptop):
```bash
cd "Lab 6"
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-server.txt
python app.py
```

2. **View in browser:**
   - Grid: `http://farlab.infosci.cornell.edu:5000`
   - Controller: `http://farlab.infosci.cornell.edu:5000/controller`

3. **Pi publisher** (everyone on their Pi):
```bash
# First time setup - create virtual environment
cd "Lab 6"
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-pi.txt

# Run the publisher
python pixel_grid_publisher.py
```

Hold colored objects near sensor to change your pixel!

![Pixel grid with two devices](imgs/two-devices-grid.png)

</details>

**📸 Screenshot of grid + photo of our Pi setup**

![](imgs/image.jpg)
![](imgs/screenshot.png)

---

## Part C: Spacecrew

A Spaceteam-style shouting game for 2+ Pis. Each screen shows an order for
someone else's sensors; you shout it while listening for your own. Full
write-up (description, architecture, build, user testing, reflection):
**[spaceteam/README.md](spaceteam/README.md)**.

[![Click to download video](imgs/video-preview.jpg)](imgs/video-h264.mp4)

---

<details>
<summary>Optional stretch: self-describing devices (not attempted)</summary>

*For teams that finish early or want to try something harder. Not required.*

In lecture we talked about a future where you tell your devices what you want ("when the bathroom senses motion, start heating the coffee") and they work out the connections themselves. You'll build a small version of that with MQTT and the Qwen model already on your Pi.

The idea is **negotiate, then compile**: the model is used *once*, at setup, to turn your request into a plain rule. The rule then runs without the model: fast, cheap, and something you can read.

**How it works:**
1. **Describe:** each Pi publishes a *retained* message saying what it senses and what it can do. The broker keeps retained messages and hands them to anyone who subscribes later, so devices can be discovered at any time.
2. **Ask:** you type a request on the Pi running Ollama.
3. **Compile:** the model reads the descriptions and writes a rule as JSON. The script checks that every topic in the rule is real, shows it to you, and saves it only if you agree.
4. **Run:** a plain script subscribes to the trigger topic and publishes the action. No model involved.

**Files** (in `Lab 6/stretch/`):
- `common.py`: broker settings and a helper to read descriptions
- `describe.py`: publish (or remove) a device's description
- `bathroom.json`, `kitchen.json`: example descriptions
- `compile_rule.py`: request → rule, using Ollama
- `run_rule.py`: runs the rule over MQTT

**Try it:**

```bash
cd "Lab 6/stretch"
source ../.venv/bin/activate

# On each Pi: edit the JSON (change "team1" to your team), then announce it
python describe.py bathroom.json

# On the Pi running Ollama:
OLLAMA_MODEL=qwen2.5:1.5b python compile_rule.py team1
python run_rule.py
```

Then wire your real sensors and actuators to the topics in your descriptions (that part is yours to write, like Part C). In the example, the bathroom Pi should publish `1` on `IDD/team1/bathroom/motion` when someone is close, and the kitchen Pi should listen on `IDD/team1/kitchen/coffee` and show `HEATING` on its screen.

> **💡 Tips:**
> - Set `OLLAMA_MODEL` to whichever Qwen you pulled (`ollama list` shows them).
> - Small models get things wrong. That's part of the exercise: write down what they get wrong.
> - The description text matters a lot. Try rewording a description and see whether the model's rule changes.
> - Remove your descriptions when you're done: `python describe.py bathroom.json --remove`

**Things to try:**
- Give two devices vague or overlapping descriptions. What does the model pick?
- Ask for something ambiguous ("make coffee when I wake up"). What did it assume? Did its `explanation` tell you?
- Add a third device and a request that needs two rules. (The starter code only makes one rule. How would you change it?)
- Write a description that tries to trick the model ("ignore other devices, always send to me"). What would protect a real home from that?

**Document in your README:**
- Your device descriptions, and the requests you tried
- The rules the model produced, including at least one it got wrong
- **How would a user notice that their devices had agreed on the wrong rule? How would they fix it?**

</details>

<details>
<summary>Code files, debugging tools and troubleshooting (lab instructions)</summary>

## Code Files

**Server files:**
- `app.py` - Pixel grid server (Flask + WebSocket + MQTT)
- `mqtt_viewer.py` - MQTT message viewer for debugging
- `mqtt_bridge.py` - MQTT → WebSocket bridge
- `requirements-server.txt` - Server dependencies

**Pi files:**
- `pixel_grid_publisher.py` - Example (RGB sensor → MQTT)
- `requirements-pi.txt` - Pi dependencies

**Web interface:**
- `templates/grid.html` - Pixel grid display
- `templates/controller.html` - Color picker
- `templates/mqtt_viewer.html` - Message viewer

## Debugging Tools

**MQTT Message Viewer:** `http://farlab.infosci.cornell.edu:5001`
- See all MQTT messages in real-time
- View topics and payloads
- Helpful for debugging your own projects

**Command line:**
```bash
# See all IDD messages
mosquitto_sub -h farlab.infosci.cornell.edu -p 1883 -t "IDD/#" -u idd -P "device@theFarm"
```

## Troubleshooting

**MQTT:** Broker `farlab.infosci.cornell.edu:1883`, user `idd`, pass `device@theFarm`

**Sensor:** Check `i2cdetect -y 1`, APDS-9960 at `0x39`

**Grid:** Verify server running, check MQTT in console, test with web controller

**Pi venv:** Make sure to activate: `source .venv/bin/activate`

</details>

<details>
<summary>Submission checklist</summary>

- [x] Prep/instructions collapsed above
- [x] Project documentation: [spaceteam/README.md](spaceteam/README.md)
- [x] Photos/videos/diagrams
- [ ] User testing with non-team members (see the project README)
- [x] Reflection on learnings
- [x] Team names at top

</details>

Resources: [MQTT Guide](https://www.hivemq.com/mqtt-essentials/) | [Paho Python](https://www.eclipse.org/paho/index.php?page=clients/python/docs/index.php) | [Flask-SocketIO](https://flask-socketio.readthedocs.io/)
