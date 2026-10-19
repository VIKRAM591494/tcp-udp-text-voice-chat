# Manual test plan

Use two copies of the app for all interactive tests. For the second local copy,
keep TCP port `51000`, set **Local UDP** to `51002`, and set **Peer UDP** to
`51001`. The first copy uses **Local UDP** `51001` and **Peer UDP** `51002`.

| Test case | Setup and action | Expected result | Observed in delivered build |
|---|---|---|---|
| TCP connection | Start HOST, then CONNECT from the peer | Both show `TCP: CONNECTED` | Passed by headless localhost self-test |
| TCP message delivery | Send one message each way | Each message arrives once with sender and timestamp | Passed by headless localhost self-test |
| Multiple TCP messages | Send 10 short messages rapidly | Receiver shows them in sent order | Run during local GUI review |
| UDP voice transmission | Start real voice on both peers | Packet counts rise; received PCM reaches speaker | Requires microphone/speaker test on the target computer |
| Missing microphone/speaker | Deny permission or disconnect device, then start real voice | Clear error; app remains usable; test-audio mode remains available | Error path implemented; hardware unavailable in validation environment |
| UDP packet loss | Choose `5% LOSS`, Apply UDP Conditions, start test audio | Inspector shows `SIMULATED LOSS`; newer packet traffic continues | Passed by headless localhost self-test with 20% simulated loss |
| UDP delay | Set delay to `200 ms`, start test audio | Estimated delay and queue behavior change; TCP chat still works | Run during local GUI review |
| UDP jitter | Set jitter to `80 ms`, start test audio | Arrival intervals vary; TCP remains ordered | Run during local GUI review |
| Packet reordering | Enable reordering plus jitter | UDP inspector may show out-of-order records | Run during local GUI review |
| Simultaneous lanes | Start test audio and send text quickly | UDP and TCP counters update independently | Passed by headless localhost self-test |
| Shutdown | Click Disconnect while voice is active | Audio/sockets stop; UI reports disconnected; no hang | Passed by headless localhost self-test cleanup |
| Restart peer | Disconnect client then reconnect it to listening host | Host returns to listening then connected state | Run during local GUI review |
| Invalid peer IP | Enter an invalid IP, click Connect | Validation error and no crash | Covered by configuration validation |
| Disconnected peer | Send text after peer disconnects | Clear send error; app remains responsive | Run during local GUI review |

For every manual run, use **EXPORT LOG** and **EXPORT METRICS**. Label screenshots
and values with the selected simulator conditions. Do not present simulation
outputs as measurements from a physical network experiment.