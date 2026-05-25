# Smart Navigation System — Ultimate Viva & Testing Reference Guide

This document serves as the **ultimate study and testing guide** for the Chitkara University Smart Navigation System project. It outlines how every component talks to one another, answers critical viva questions, explains the code architecture in simple terms, details how to update IP addresses, and provides end-to-end testing procedures.

---

## 1. ASCII Network Flow Diagram

Below is the complete data flow showing how the **ESP32 Button**, **Mac Brain**, **Pi Kiosk**, and **Mobile Companion Webpage** interact:

```text
                     [Wi-Fi / LAN Network]
                     
  +------------------+                   +--------------------+
  |   ESP32 Button   |                   |  Raspberry Pi Kiosk|
  |  (Mic & Trigger) |                   |  (TUI & Speaker)   |
  +--------+---------+                   +---------+----------+
           |                                       ^
           | TCP port 12345 (Raw Audio PCM)        |
           |                                       | TCP port 5000 (TTS MP3)
           v                                       | TCP port 5050 (State Updates)
  +--------+---------+                             |
  |    Mac Brain     |-----------------------------+
  | (Orchestrator &  |
  |  Whisper STT)    |<----------------------------+
  +--------+---------+                             | TCP port 5060 (Heartbeats & Playback Done)
           |                                       
           | HTTP PUT (JSON data payload)          
           v                                       
  +--------+---------+                             
  |    Cloud Broker  |                             
  | (jsonblob.com)   |                             
  +--------+---------+                             
           |                                       
           | HTTP GET (via CORS Proxy)             
           v                                       
  +--------+---------+                             
  |  Mobile Web App  |                             
  | (Vercel/Browser) |                             
  +------------------+                             
```

---

## 2. Core Q&A: How Connections Work

### Q1: How does the ESP32 Button talk to the Mac Brain?
* **Protocol**: TCP Sockets over local Wi-Fi.
* **Mechanism**:
  1. The user presses and holds the button on the ESP32. The onboard microphone records raw voice audio (PCM format, 16kHz, mono).
  2. When the user releases the button, the ESP32 opens a TCP socket connection to the **Mac Brain** on port `12345`.
  3. It streams the raw audio bytes and closes the connection. The Mac receiver thread immediately pushes these bytes into a processing queue.

### Q2: How does the Mac Brain talk to the Raspberry Pi Kiosk?
* **Protocol**: TCP Sockets over local Wi-Fi.
* **Ports Used**:
  * **Audio Channel (Port `5000`)**: The Mac Brain transcribes speech, generates TTS audio (Edge TTS), merges chimes (FFmpeg), and sends the final audio file directly to the Pi.
  * **Status Channel (Port `5050`)**: The Mac Brain runs a background thread that batches and sends state updates (e.g., active destination, distance, ETA, current step index) to the Pi. The Pi uses these variables to render the Terminal UI.

### Q3: How does the Raspberry Pi Kiosk talk back to the Mac Brain?
* **Protocol**: TCP Sockets over local Wi-Fi.
* **Port Used**: Callback Channel (Port `5060`).
* **Mechanism**:
  * **Heartbeats**: Every 10 seconds, the Pi connects to the Mac Brain on port `5060` and sends a `"HEARTBEAT"` string to verify connectivity.
  * **Playback Confirmation (Anti-Step-Skipping)**: When the Pi finishes playing an audio file (e.g., step 1 directions), it sends `"PLAYBACK_DONE:<playback_id>"` back to the Mac. The Mac waits for this specific ID confirmation before proceeding to read the next step. This prevents steps from overlapping or skipping.

### Q4: How does the Mobile Companion Webpage sync directions?
* **Local HTTP (Port `8000`)**: The Mac Brain runs a standard Python HTTP server. If loaded locally, the phone connects directly to `http://<mac-ip>:8000/api/active-route`.
* **Cloud Broker (jsonblob.com)**: Since public URLs hosted on Vercel run on HTTPS, they cannot call local HTTP APIs (blocked as "Mixed Content"). To solve this:
  1. The Mac Brain automatically pushes the active route to a secure public cloud broker at `jsonblob.com` in a background thread.
  2. When the user taps "Sync" on the phone, the webpage fetches the route from the cloud broker.
  3. **CORS Bypass**: To bypass Cross-Origin Resource Sharing (CORS) security restrictions in mobile browsers, the webpage uses a multi-proxy fallback mechanism (`Codetabs` and `Corsproxy.io`) to retrieve the JSON route payload securely.

### Q5: How is the "Welcome Deadlock" solved?
* If the Pi boots up first, it doesn't know the Mac's IP because the Mac's IP is dynamic.
* **Solution**: The Mac server sends status updates (pings) to the Pi's static IP (`10.221.234.8`) on port `5050` every 10 seconds. On the first incoming packet, the Pi reads the source address, discovers the Mac's IP automatically, launches its heartbeats/callbacks, and triggers the welcome greeting.

---

## 3. How to Update IP Addresses If They Change

Because local Wi-Fi networks assign dynamic IP addresses, you may need to update IPs when setting up the system in a new lab or room.

### Part A: If the Mac Brain's IP changes
No code updates are needed! The system has **automatic IP discovery**:
1. When the Mac boots, it pings the Raspberry Pi Kiosk.
2. The Pi automatically reads the Mac's new IP from the incoming network package and remembers it.
3. *Note*: The only place this needs to be checked is on the **ESP32 device**, as its code has the target Mac IP hardcoded. You must flash the ESP32 code with the new Mac IP if it changes.

### Part B: If the Raspberry Pi's IP changes
If the Pi gets a new IP from the router, you must update the IP inside the Mac Brain code:
1. Open [mac_server.py](file:///Users/gurjobansingh/Desktop/nav/mac_server.py).
2. Locate the line near the top (around Line 42):
   ```python
   PI_IP           = "10.221.234.8"
   ```
3. Replace `"10.221.234.8"` with the Pi's new IP address.
4. Save the file and restart the Mac server.

### Part C: Changing Wi-Fi credentials on Pi or Mac
Ensure both the Mac and the Raspberry Pi are connected to the **same local Wi-Fi network (or router)**. The system relies on direct local socket communication; if they are on different networks (or one is on a Guest Wi-Fi that blocks client-to-client connections), they will not be able to talk to each other.

---

## 4. Code Architecture: Simple Explanation of Every File

### 1. `nav_protocol.py` (The Shared Rules)
* **What it does**: This file contains the shared configurations, network ports, status constants, and terminal colors.
* **Why it's there**: Instead of copying ports and variables into both Mac and Pi files (which leads to copy-paste bugs), both scripts import them from here. It acts as the single source of truth.

### 2. `mac_server.py` (The Central Brain)
* **ESP32 Audio Listener**: Runs a background thread (`receiver_thread`) that listens on port `12345` for button press recordings from the ESP32.
* **Whisper speech-to-text**: Takes the raw audio wav file and transcribes it into text. It is primed with an `initial_prompt` containing all valid landmarks and commands so it recognizes regional accents accurately.
* **Custom Fuzzy Matcher**: Takes transcribed text, applies phonetic corrections (e.g. "days lab" $\rightarrow$ "dice lab"), and calculates Jaccard word-overlap and containment scores. If containment is high (e.g., `"where is the tesla block building"`), it successfully matches the route.
* **Edge TTS Speech Synthesizer**: Converts route directions into high-quality spoken audio files.
* **Chime Merger**: Uses `ffmpeg` to append chime sounds to the start and end of the spoken audio.
* **HTTP Server**: Serves the companion website on port `8000` and handles REST API queries.
* **Cloud Sync Worker**: Pushes route data to the internet (`jsonblob.com`) so the mobile companion site can download it.

### 3. `pi_receiver.py` (The Kiosk Interface)
* **Audio Server**: Listens on port `5000` for audio files sent by the Mac. Plays them immediately using local terminal commands (`aplay`, `paplay`, `ffplay`).
* **Status Server**: Listens on port `5050` for text updates from the Mac (such as current navigation metrics or step numbers).
* **Terminal UI (TUI)**: Draws a live panel on the monitor showing a header, system status, active navigation steps, and a live clock.
* **WAV Cleanup**: Converts and plays audio fallback streams, automatically cleaning up temporary WAV files after playback finishes to prevent disk space leaks.
* **Heartbeat & Callbacks**: Periodic threads that keep the connection alive and send confirmations back to the Mac when audio finishes.

### 4. `index.html` (The Mobile Companion App)
* **Visual Presentation**: Styled with HSL CSS design tokens, a minimalist soft-pastel UI, and a progress checklist.
* **Multi-Language Selector**: A button bar at the top allowing the user to select English, Hindi, or Punjabi. A `t(key)` helper replaces all text nodes, checklist directions, and ETAs dynamically using a built-in translation dictionary.
* **Offline Directory**: Contains a hardcoded local database of routes. If the phone has no internet connection, it still displays interactive walkthroughs of the 5 campus locations.
* **Kiosk Syncer**: Connects to the cloud broker via CORS proxies to download and sync live kiosk routes.

---

## 5. End-to-End Testing & Verification Checklist

Perform these tests prior to your viva to verify system health:

| Test Scenario | Step-by-Step Instructions | Expected Result |
| :--- | :--- | :--- |
| **1. Boot & Welcome Greeting** | Start `pi_receiver.py` on the Pi, then start `mac_server.py` on the Mac. | Within 10 seconds, the Mac pings the Pi, the Pi discovers the Mac, and the speaker plays: *"Welcome to Chitkara University..."* |
| **2. Whisper Priming & Phonetic Match** | Press the button, say *"take me to touring block building"* or *"days lab"*. | The system transcribes the accent correctly and matches the destination. Mac prints: `Speech match -> Turing Block (or DICE Lab)`. |
| **3. Volume Control Command** | Speak *"volume max"* or *"louder"*. | The Pi executes system mixer volume changes and plays: *"Volume set to maximum."* |
| **4. Navigation & Sync** | Speak *"Square One"*. Tap **"Sync with Kiosk"** on a phone loaded with the Vercel site. | The webpage instantly downloads the Square One route steps, displaying them in a checklist timeline. |
| **5. Multi-Language Switch** | Tap **"हिन्दी"** or **"ਪੰਜਾਬੀ"** on the webpage. | The entire interface, location names, steps, and ETAs instantly translate to the chosen language. |
| **6. Ambient Heartbeat** | Let the kiosk sit idle for 30 seconds. | A soft sonar heartbeat sound plays every 15 seconds indicating the kiosk is alive. |
