# Smart Navigation System — Testing & Reference Guide

This guide details how to launch, operate, test, and troubleshoot the Mac Brain and Raspberry Pi Kiosk system.

---

## 1. Network Architecture & IP Addresses

* **Mac Brain IP**: Dynamic (advertised automatically via periodic pings).
* **Pi Kiosk IP**: Hardcoded in `mac_server.py` as `192.168.245.8`.
* **ESP32 Button IP**: Dynamic (registers on the Mac when button is pressed).

> [!IMPORTANT]
> **Boot-up IP Discovery (Deadlock Fix)**:
> The Mac server automatically sends a status update ping to the Pi every 10 seconds. This allows the Pi to discover the Mac's IP within seconds of booting up (even if the Pi is started after the Mac). Once the Pi discovers the Mac's IP, it begins sending callbacks and heartbeats to port `5060` on the Mac, triggering the welcome greeting.

---

## 2. Running & Stopping the Servers

### Mac Brain (Host)
* **Start Server**:
  ```bash
  python3 /Users/gurjobansingh/Desktop/nav/mac_server.py
  ```
  *(To run in the background and log to a file)*:
  ```bash
  nohup python3 /Users/gurjobansingh/Desktop/nav/mac_server.py > /tmp/mac_nav.log 2>&1 &
  ```
* **Stop Server**:
  ```bash
  pkill -f mac_server.py
  ```

### Pi Kiosk (Remote)
* **Copy updated code to Pi**:
  ```bash
  scp /Users/gurjobansingh/Desktop/nav/pi_receiver.py mango@192.168.245.8:~/nav/
  ```
* **Start Kiosk**:
  ```bash
  python3 ~/nav/pi_receiver.py
  ```
* **Stop Kiosk**:
  ```bash
  pkill -f pi_receiver.py
  ```

---

## 3. Valid Destinations & Aliases
When you speak to the system, you can use the destination names or any of the mapped aliases listed below:

| Destination | Distance | Walking Time | Valid Aliases (Speech Matches) |
| :--- | :--- | :--- | :--- |
| **Turing Block** | 180m | 2 to 3 mins | `turing`, `during`, `touring`, `teuring`, `turing block`, `chew ring`, `uring`, `churing` |
| **DICE Lab** | 400m | 5 to 6 mins | `dice`, `data lab`, `dice lab`, `dies lab`, `dye lab`, `diess lab`, `dyce lab`, `dice block`, `dice-lab` |
| **Square One** | 300m | 4 to 5 mins | `square`, `square one`, `square 1`, `squire one`, `square-one` |
| **Tesla Block** | 600m | 7 to 8 mins | `tesla`, `tesla block`, `tasla block`, `tasla`, `tešla` |
| **Rockefeller Block** | 430m | 5 to 6 mins | `rockefeller`, `rocky feller`, `rockefeller block`, `rockefellar`, `rocker feller`, `rocky-feller` |

---

## 4. Voice Commands Reference
Voice commands are parsed **before** destination matching, ensuring they do not trigger a "location not found" error.

### General Commands
* **Cancel / Stop**:
  * *Keywords*: `"cancel"`, `"stop"`, `"quit"`, `"end"`, `"exit"`, `"never mind"`, `"clear route"`, `"cancel navigation"`
  * *Whisper Error Fallbacks*: `"cant sell it"`, `"can sell it"`, `"cant sell"`, `"can sell"`, `"cancel it"`
  * *Behavior*: Immediately aborts active navigation or speech and resets status to `READY`.
* **Repeat**:
  * *Keywords*: `"repeat"`, `"again"`, `"replay"`, `"one more time"`, `"say again"`
  * *Behavior*: Replays the current route step or the entire description.
* **Help**:
  * *Keywords*: `"help"`, `"commands"`, `"options"`, `"what can i say"`
  * *Behavior*: Lists available commands and destinations.

### Route Mode Selection
* **Short Mode**: Summarizes the destination, total distance, and ETA.
  * *Keywords*: `"short"`, `"brief"`, `"summary"`, `"summarize"`, `"overview"`, `"short route"`
* **Fast Mode**: Reads standard directions quickly.
  * *Keywords*: `"fast"`, `"quick"`, `"rapid"`, `"fast route"`
* **Full Mode**: Reads detailed step-by-step instructions.
  * *Keywords*: `"full"`, `"detailed"`, `"complete"`, `"step by step"`, `"all steps"`, `"full route"`

### Speech Speed
* **Slow Speed**: Slows down the TTS playback.
  * *Keywords*: `"slow"`, `"slowly"`, `"slower"`, `"slow down"`, `"speak slow"`
* **Normal Speed**: Resets speed to default.
  * *Keywords*: `"normal"`, `"regular"`, `"default"`, `"reset speed"`, `"speed up"`

### Hardware Volume Controls (Executed on Pi)
* **Volume Up**: Increases volume by 10%.
  * *Keywords*: `"volume up"`, `"speak louder"`, `"louder"`, `"increase volume"`
* **Volume Down**: Decreases volume by 10%.
  * *Keywords*: `"volume down"`, `"speak softer"`, `"quieter"`, `"lower volume"`
* **Volume Mute**: Mutes the audio.
  * *Keywords*: `"mute"`, `"silent"`, `"silence"`
* **Volume Unmute**: Restores audio.
  * *Keywords*: `"unmute"`, `"speak up"`, `"sound on"`
* **Volume Max**: Sets volume to 100%.
  * *Keywords*: `"max volume"`, `"volume max"`, `"full volume"`, `"highest volume"`, `"loudest"`

---

## 5. Testing & Verification Guide

### Test 1: Welcome Greeting (Deadlock Check)
1. Launch `pi_receiver.py` on the Pi first.
2. Launch `mac_server.py` on the Mac.
3. Within 10 seconds, the Mac should ping the Pi, the Pi will send its heartbeat, and the Bluetooth speaker will play the welcome message: *"Welcome to Chitkara University. The navigation system is ready..."*

### Test 2: Whisper Transcription Errors (Cancel Check)
1. Press the ESP button and deliberately say *"can't sell it"* or *"cancel route"*.
2. Verify that the Mac server logs show `Command: cancel` and that speech is immediately cancelled without saying *"Sorry, I could not find that location."*

### Test 3: Maximum Volume Check
1. Say *"volume max"* or *"max volume"*.
2. The Pi should log execution of `pactl set-sink-volume @DEFAULT_SINK@ 100%` and `amixer` commands, then play: *"Volume set to maximum."*

### Test 4: Ambient Heartbeat (Idle Ping)
1. Leave the system idle for 20–30 seconds.
2. Verify that a soft sonar pulse is played through the Bluetooth speaker every 15 seconds.

---

## 6. Testing the Mobile Companion Webpage

### Launching the Webpage
1. Ensure the Mac server is running.
2. Open your mobile phone's browser and navigate to either:
   * Your Vercel deployment URL (e.g., `https://smart-nav.vercel.app`)
   * Or locally served from the Mac at: `http://<mac-ip>:8000`

### Test Scenario 1: Offline Directory Mode
1. With the webpage open, do not tap "Sync".
2. Scroll down to the **All Locations** directory list.
3. Tap on **"Tesla Block"** or **"DICE Lab"**.
4. Verify that the step-by-step navigation card slides in.
5. Tap on the steps to check them off as you walk.
6. Refresh the page and confirm that the route and checkmarks **persist** (loaded from `localStorage`).
7. Tap **"Clear & Show Directory"** to return to the directory list.

### Test Scenario 2: Live Kiosk Cloud Sync Mode
1. Speak a destination to the kiosk (e.g., *"Square One"*).
2. The kiosk will process and announce the route. The Mac server will automatically upload this route to the secure cloud broker at `jsonblob.com`.
3. On the phone webpage (hosted on Vercel or locally), tap **"Sync with Kiosk"** (or click the green sync button at the top).
4. Verify that the webpage connects to the cloud broker, loads the **"Square One"** route, and displays its steps.
5. Tap steps to check them off. Tapping *"Clear & Show Directory"* will reset the screen.
