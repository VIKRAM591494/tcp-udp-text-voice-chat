# TCP/UDP Text and Voice Chat — Desktop + Browser Editions

This repository contains the original Python desktop networking lab and an added browser-accessible edition for online demonstrations. The two editions are documented separately because their browser-compatible voice transport is different from a raw UDP socket program.

## Browser edition (online demo)

The browser edition lives in [`web/`](web/). It provides a two-person room, browser microphone capture, peer-to-peer WebRTC audio, WebSocket signaling and text messaging, connection statistics, a jitter chart, and CSV export.

- [Browser application overview and setup](web/README.md)
- [Render deployment configuration](render.yaml)
- [Browser automated tests](web/tests/)

### Run locally

Python 3.10+ is recommended. From the repository root:

```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r web/requirements-dev.txt
python -m uvicorn web.app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> in two browser windows, join the same room code, and allow microphone access. For browser tests, run in a separate terminal:

```bash
python -m pytest web/tests -q
node --check web/static/app.js
```

`/healthz` is the health endpoint. `/ice-config` supplies STUN servers and can add a configured TURN server.

### Deploy to Render

The root [`render.yaml`](render.yaml) defines a Python Web Service, installs `web/requirements.txt`, starts Uvicorn on the platform-provided port, and checks `/healthz`.

1. Open the [Render dashboard](https://dashboard.render.com/).
2. Choose **New → Blueprint** and connect this public GitHub repository: `VIKRAM591494/tcp-udp-text-voice-chat`.
3. Select branch `main` and apply the `render.yaml` configuration.
4. Wait for the first deploy to finish, then open the actual `onrender.com` URL shown by Render.
5. Test two browsers/devices. The app sends periodic WebSocket keepalives because a free service can spin down when idle.

I have not claimed a live URL: a Render service must be created in a hosting account before it exists. The free Render tier may sleep while idle, causing a slow first load. WebSocket support and service behavior are described in [Render's WebSocket documentation](https://render.com/docs/websocket).

### Browser networking: important distinction

- Text and WebRTC signaling travel over the application's WebSocket connection, which uses TCP on the server path.
- Audio is captured by the browser and sent with WebRTC peer-to-peer media. ICE normally tries direct UDP paths, while network restrictions can require a TURN relay over UDP/TCP/TLS.
- This is a web-compatible adaptation, **not** the original app's raw UDP packet protocol. Raw browser JavaScript cannot open arbitrary TCP/UDP sockets; WebRTC is the browser-supported media transport.
- The app uses public STUN endpoints by default. On some campus, corporate, mobile, or restrictive NAT networks, direct voice may fail until a TURN service is configured.

Optional deployment environment variables for TURN:

- `TURN_URLS`: comma-separated `turn:` / `turns:` URIs
- `TURN_USERNAME`: username issued by your TURN provider
- `TURN_CREDENTIAL`: credential issued by your TURN provider

The values are delivered to browser clients as part of ICE configuration. For a production service, prefer provider-issued short-lived credentials instead of permanent shared credentials.

## Original desktop edition (raw TCP + UDP)

The original desktop implementation remains at the repository root and is not replaced by the browser edition.

- `app.py`, `ui.py`: Tkinter entry point and GUI
- `text_tcp.py`, `protocol.py`: TCP text messages and framing
- `voice_udp.py`, `audio_io.py`: UDP PCM packets and audio devices
- `network_simulator.py`: UDP loss, delay, jitter, and reordering simulation
- `metrics.py`, `logger.py`, `workers.py`: metrics, events, and background workers
- `tests/`, `TEST_PLAN.md`: desktop tests and lab procedure
- [`DEPLOYMENT.md`](DEPLOYMENT.md): desktop networking limitations

Run the original desktop tests and local-socket self-test from the repository root:

```bash
python -m pip install -r requirements.txt
python -m pytest
python app.py --self-test
```

The desktop self-test uses actual localhost TCP and UDP sockets with synthetic/test audio; it does not verify real microphone/speaker behavior or a cross-internet path.

## Security and scope

Both editions are educational prototypes, not secure messaging products. The browser edition limits rooms to two connected participants, keeps room/chat state in server memory only, and does not persist messages. There is no user authentication or end-to-end chat identity; room codes should be shared privately and the app must not be used for sensitive communications. The server's in-memory room registry assumes a single service instance; do not horizontally scale it without shared signaling state or a shared message broker.