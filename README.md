# meet_recorder
Desktop app to record and transcribe online meetings locally

# MeetRecorder - Installation Guide

## What it does

App for recording screens and meetings.
Records screen + audio, and automatically transcribes via Whisper.

**Output saved per meeting:**
- `meeting_<date>.mp4` - video with audio
- `meeting_<date>.mp3` - audio only
- `transcript.txt` - transcription

---

## Step 1 - Install Python

Download Python 3.9 or newer: https://www.python.org/downloads/

⚠️ During install, make sure to check **"Add Python to PATH"**

Verify - open Command Prompt (Win+R → `cmd`) and type:
```
python --version
```
Should show a version like `Python 3.11.4`.

---

## Step 2 - Install ffmpeg

1. Download: https://www.gyan.dev/ffmpeg/builds/ → **ffmpeg-release-essentials.zip**
2. Extract to e.g. `C:\ffmpeg`
3. Add to PATH:
   - Start → "Environment variables" → **Edit the system environment variables**
   - **Environment Variables** → under "System variables" → **Path** → **Edit**
   - **New** → type `C:\ffmpeg\bin` → OK everywhere
4. Verify in a new Command Prompt:
   ```
   ffmpeg -version
   ```

---

## Step 3 - Install VB-Cable

VB-Cable is needed to record the other participants' audio.

1. Download free: https://vb-audio.com/Cable/
2. Extract the archive
3. Run **VBCABLE_Setup_x64.exe** as Administrator → **Install Driver**
4. Restart your computer

### Set up "Listen" (so you can hear the meeting through headphones):
1. Right-click the sound icon in the system tray → **Sound settings**
2. Scroll down → **More sound settings**
3. **Recording** tab → **CABLE Output** → right-click → **Properties**
4. **Listen** tab → check **"Listen to this device"**
5. Select your headphones → OK

---

## Step 4 - Install dependencies

Put all files from this folder in one place on your computer.

Open Command Prompt in that folder:
- Open the folder in File Explorer → click the address bar → type `cmd` → Enter

Run:
```
install.bat
```

First install takes 5–10 minutes - the Whisper model is being downloaded.

---

## Run

Double-click **run.bat**

---

## How to use

**Recording a live meeting:**
1. Choose a folder to save recordings
2. Select your microphone from the list
3. Open Google Meet
4. Click **⏺ START RECORDING**
5. After the meeting - **⏹ STOP RECORDING**
6. Wait - the transcript will be created automatically and the folder will open

**Transcribing an existing file:**

You can also transcribe audio or video recorded outside the app (e.g. a Zoom cloud recording, a voice memo, or any MP3/MP4/WAV file):
1. Choose a folder to save the output
2. Click **Load...** and select your file
3. The app will extract the audio and run transcription automatically

---

## Common issues

**Empty transcript** → check that VB-Cable is installed and there are no errors in the log during recording

**Can't hear meeting audio** → set up "Listen" on CABLE Output (Step 3)

**"python is not recognized"** → reinstall Python with "Add Python to PATH" checked

**ffmpeg error** → add the ffmpeg path to PATH and open a new Command Prompt
