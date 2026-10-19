# Deployment Notes

## What can be deployed as-is?

The current application is a Python desktop program with a Tkinter GUI. It creates a TCP text-chat connection and UDP voice endpoints. It is not an HTTP server, so a static website host cannot run it and a generic web-app host will not automatically expose its UDP socket.

## Lowest-effort college demonstration

1. Install Python 3.10+ on two computers on the same LAN.
2. Install the requirements with `python -m pip install -r requirements.txt`.
3. On the host instance, allow inbound TCP port `51000` and its selected UDP voice port in the OS firewall.
4. On the client instance, allow its selected UDP voice port in the firewall.
5. Configure reciprocal UDP ports and use the host computer's LAN IPv4 address as the client's peer IP.
6. Start the host first, connect the client, test TCP text, then use **START TEST AUDIO** to verify UDP packet flow before testing physical audio devices.

For local-only testing, use `127.0.0.1` and the loopback settings in the README. The included `python app.py --self-test` command validates localhost TCP/UDP without a GUI or sound hardware.

## Why a public URL is not provided

No hosting service was connected or authorized for deployment. In addition, the existing design requires direct TCP and UDP socket reachability. Internet access would require a reachable host, suitable TCP/UDP firewall and router rules, and NAT-compatible paths for both voice endpoints. The prototype does not implement NAT traversal, encryption, or authentication, so publicly exposing it is not recommended.

A browser-accessible deployment would require a separate WebRTC adaptation (with signaling and possibly STUN/TURN). Such an adaptation must be tested as a separate architecture; it should not be described as the existing raw UDP implementation.