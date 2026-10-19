# Real-Time TCP Text and UDP Voice Chat

An educational Python desktop application demonstrating reliable text messaging over TCP and real-time voice packets over UDP. The Tkinter interface includes packet inspection, UDP loss/delay/jitter/reordering simulation, metrics, CSV export, event logging, and automated tests.

## Source code

The cleaned complete Python project, including application modules, unit and integration tests, requirements, test plan, setup instructions, and a GitHub Actions test workflow, is available in the source package:

- [Download the complete source package](./voice_chat_project_source.tar.gz)
- [Deployment notes](./DEPLOYMENT.md)

Download and extract the archive, then enter the voice_chat_project directory.

## Quick start

Requirements: Python 3.10 or later, a desktop environment for the Tkinter interface, and microphone/speaker devices for real voice. Labelled test-audio mode exercises actual UDP sockets without audio hardware.

Create and activate a virtual environment, then install dependencies and launch the app:

    python -m venv .venv
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    python app.py

Run checks without the GUI:

    python -m pytest
    python app.py --self-test

## Verified in the available environment

On 2026-10-19, the original source snapshot passed all 13 automated tests. The headless self-test also passed, exchanging text over real localhost TCP sockets and sending silent PCM over real localhost UDP sockets with the impairment simulator enabled. These checks do not verify physical microphone/speaker operation or multi-computer internet connectivity.

## Architecture

TCP carries length-prefixed JSON text frames. UDP carries raw int16 PCM frames with sequence and timestamp metadata. An optional sender-side simulator introduces explicitly labelled UDP loss, delay, jitter, and reordering. The receiver uses bounded queues to prevent stale audio frames accumulating indefinitely.

## Limitations

This is a desktop socket prototype, not a browser app or hosted website. It does not provide encryption, authentication, NAT traversal, codec/RTP support, or production-grade VoIP security. GitHub Pages and other static hosts cannot run the desktop process or expose its UDP sockets. No public deployment URL exists yet. See Deployment Notes before attempting cross-internet use.