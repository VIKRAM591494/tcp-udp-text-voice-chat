(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const els = {
    joinForm: $('joinForm'), joinPanel: $('joinPanel'), roomPanel: $('roomPanel'), howRow: $('howRow'),
    name: $('nameInput'), room: $('roomInput'), newRoom: $('newRoomBtn'), join: $('joinBtn'),
    topStatus: $('topStatus'), roomLabel: $('roomLabel'), copyRoom: $('copyRoomBtn'), leave: $('leaveBtn'),
    messages: $('messages'), empty: $('emptyState'), chatForm: $('chatForm'), chatInput: $('chatInput'),
    messageCount: $('messageCount'), socketStatus: $('socketStatus'), selfAvatar: $('selfAvatar'), selfName: $('selfName'),
    remoteAvatar: $('remoteAvatar'), remoteName: $('remoteName'), remoteState: $('remoteState'),
    voicePanel: document.querySelector('.voice-panel'), voiceBadge: $('voiceBadge'), voiceTitle: $('voiceTitle'),
    voiceCopy: $('voiceCopy'), mute: $('muteBtn'), muteText: $('muteText'), muteIcon: $('muteIcon'),
    retryVoice: $('retryVoiceBtn'), remoteAudio: $('remoteAudio'), rtt: $('rttMetric'), loss: $('lossMetric'),
    jitter: $('jitterMetric'), packets: $('packetsMetric'), rttHint: $('rttHint'), lossHint: $('lossHint'),
    canvas: $('jitterChart'), export: $('exportBtn'), toast: $('toast')
  };

  let socket = null;
  let pc = null;
  let localStream = null;
  let clientId = null;
  let remotePeerId = null;
  let displayName = 'Guest';
  let roomCode = '';
  let isInitiator = false;
  let joined = false;
  let muted = false;
  let messageTotal = 0;
  let statsTimer = null;
  let makingOffer = false;
  let pendingCandidates = [];
  let statHistory = [];
  let lastInbound = { received: 0, lost: 0, bytes: 0 };
  let toastTimer = null;
  let heartbeatTimer = null;
  let iceServers = [
    { urls: 'stun:stun.l.google.com:19302' },
    { urls: 'stun:stun.cloudflare.com:3478' }
  ];
  let turnConfigured = false;
  const iceConfigPromise = fetch('/ice-config', { cache: 'no-store' })
    .then(response => response.ok ? response.json() : Promise.reject(new Error('ICE config unavailable')))
    .then(config => {
      if (Array.isArray(config.iceServers) && config.iceServers.length) iceServers = config.iceServers;
      turnConfigured = Boolean(config.turnConfigured ?? iceServers.some(server => {
        const urls = Array.isArray(server.urls) ? server.urls : [server.urls];
        return urls.some(url => typeof url === 'string' && /^turns?:/i.test(url));
      }));
    })
    .catch(() => {});

  function makeRoomCode() {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    const bytes = new Uint8Array(6);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, b => chars[b % chars.length]).join('');
  }
  function toast(message) {
    els.toast.textContent = message;
    els.toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => els.toast.classList.remove('show'), 2600);
  }
  function setTopStatus(text, connected = false) {
    const i = document.createElement('i');
    els.topStatus.replaceChildren(i, document.createTextNode(` ${text}`));
    els.topStatus.classList.toggle('connected', connected);
    if (connected) els.topStatus.style.color = 'var(--mint)';
    else els.topStatus.style.color = '';
  }
  function setSocketStatus(text, online = false) {
    els.socketStatus.replaceChildren();
    const dot = document.createElement('i');
    if (!online) dot.style.background = '#d0ad63';
    els.socketStatus.append(dot, document.createTextNode(` ${text}`));
  }
  function setVoiceStatus(title, copy, badge, connected = false) {
    els.voiceTitle.textContent = title;
    els.voiceCopy.textContent = copy;
    els.voiceBadge.classList.toggle('connected', connected);
    els.voiceBadge.classList.toggle('pending', !connected);
    els.voiceBadge.replaceChildren();
    const dot = document.createElement('i');
    els.voiceBadge.append(dot, document.createTextNode(` ${badge}`));
    els.voicePanel.classList.toggle('active', connected);
  }
  function showMessage({ name, text, timestamp, own = false, system = false }) {
    if (els.empty?.isConnected) els.empty.remove();
    if (system) {
      const item = document.createElement('div');
      item.className = 'system-message';
      item.textContent = text;
      els.messages.appendChild(item);
    } else {
      const item = document.createElement('article');
      item.className = `message${own ? ' own' : ''}`;
      const meta = document.createElement('div');
      meta.className = 'message-meta';
      const nameNode = document.createElement('strong');
      nameNode.textContent = name;
      const timeNode = document.createElement('time');
      timeNode.textContent = timestamp ? new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
      meta.append(nameNode, timeNode);
      const bubble = document.createElement('div');
      bubble.className = 'message-bubble';
      bubble.textContent = text;
      item.append(meta, bubble);
      els.messages.appendChild(item);
      messageTotal += 1;
      els.messageCount.textContent = String(messageTotal);
    }
    els.messages.scrollTop = els.messages.scrollHeight;
  }
  function send(payload) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(payload));
    return true;
  }
  function resetPeerState() {
    clearInterval(statsTimer);
    statsTimer = null;
    if (pc) {
      pc.ontrack = null;
      pc.onicecandidate = null;
      pc.onconnectionstatechange = null;
      pc.close();
    }
    pc = null;
    pendingCandidates = [];
    makingOffer = false;
    remotePeerId = null;
    els.remoteAudio.srcObject = null;
    els.remoteAvatar.textContent = '?';
    els.remoteName.textContent = 'Waiting for peer';
    els.remoteState.textContent = 'Not connected';
    els.retryVoice.disabled = true;
    els.rtt.textContent = '—';
    els.loss.textContent = '—';
    els.jitter.textContent = '—';
    els.packets.textContent = '0';
    els.rttHint.textContent = 'Waiting for stats';
    els.lossHint.textContent = 'Inbound audio';
    lastInbound = { received: 0, lost: 0, bytes: 0 };
    statHistory = [];
    drawChart();
    setVoiceStatus('Waiting for your peer', 'Voice connects when the other participant joins this room.', 'WAITING', false);
  }
  async function tryEnableMicrophone() {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Microphone access requires a modern browser on HTTPS or localhost.');
    if (localStream) return;
    localStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false
    });
    els.mute.disabled = false;
    els.muteText.textContent = 'Mute mic';
    els.muteIcon.textContent = '♬';
    setVoiceStatus('Microphone enabled', 'Waiting for the second participant to establish the audio link.', 'MIC READY', false);
  }
  function releaseLocalMedia() {
    if (localStream) for (const track of localStream.getTracks()) track.stop();
    localStream = null;
    muted = false;
    els.mute.disabled = true;
    els.muteText.textContent = 'Mute mic';
    els.muteIcon.textContent = '♬';
  }
  async function joinRoom(event) {
    event.preventDefault();
    if (joined) return;
    roomCode = els.room.value.trim();
    displayName = els.name.value.trim().slice(0, 30) || 'Guest';
    await iceConfigPromise;
    if (!/^[A-Za-z0-9_-]{3,32}$/.test(roomCode)) {
      toast('Use a room code between 3 and 32 letters, numbers, _ or -.');
      return;
    }
    els.join.disabled = true;
    els.join.querySelector('span').textContent = 'Requesting microphone…';
    let micReady = false;
    try {
      await tryEnableMicrophone();
      micReady = true;
    } catch (error) {
      const message = error?.message || 'Microphone permission was not granted.';
      const allowTextOnly = window.confirm(`${message}\n\nYou can still join for text chat, but voice won't work until you reload and allow the microphone. Join text-only?`);
      if (!allowTextOnly) {
        els.join.disabled = false;
        els.join.querySelector('span').textContent = 'Enable mic & join';
        setVoiceStatus('Microphone not enabled', 'Allow microphone access in your browser settings to use voice.', 'MIC OFF', false);
        return;
      }
      setVoiceStatus('Text-only mode', 'You joined without a microphone; local audio is unavailable.', 'TEXT ONLY', false);
    }
    els.join.querySelector('span').textContent = 'Connecting…';
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    socket = new WebSocket(`${scheme}://${location.host}/ws/${encodeURIComponent(roomCode)}?name=${encodeURIComponent(displayName)}`);
    socket.addEventListener('open', () => {
      setSocketStatus('SIGNALING CONNECTED', true);
      setTopStatus('CONNECTING', true);
      // Keep the signaling socket alive while the audio media flows peer-to-peer.
      clearInterval(heartbeatTimer);
      heartbeatTimer = setInterval(() => send({ type: 'ping' }), 25000);
    });
    socket.addEventListener('message', event => {
      let data;
      try { data = JSON.parse(event.data); } catch { return; }
      handleServerMessage(data).catch(err => {
        console.error('Message handling failed', err);
        toast('A signaling action failed. Try reconnecting audio.');
      });
    });
    socket.addEventListener('error', () => {
      setSocketStatus('CONNECTION ERROR', false);
      toast('Could not connect to the room server. Try again shortly.');
      if (!joined) {
        releaseLocalMedia();
        els.join.disabled = false;
        els.join.querySelector('span').textContent = 'Enable mic & join';
      }
    });
    socket.addEventListener('close', () => {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
      setSocketStatus('DISCONNECTED', false);
      if (joined) {
        setTopStatus('SIGNALING LOST', false);
        setVoiceStatus('Connection interrupted', 'The signaling connection closed. Leave and rejoin to start a fresh session.', 'OFFLINE', false);
      }
    });
    // local media is acquired before signaling so both peers can exchange audio tracks in the first offer/answer.
    if (!micReady) els.mute.disabled = true;
  }
  async function handleServerMessage(data) {
    switch (data.type) {
      case 'welcome': {
        clientId = data.client_id;
        isInitiator = Boolean(data.is_initiator);
        joined = true;
        els.joinPanel.classList.add('hidden');
        els.howRow.classList.add('hidden');
        els.roomPanel.classList.remove('hidden');
        els.roomLabel.textContent = roomCode;
        els.selfName.textContent = displayName;
        els.selfAvatar.textContent = displayName.slice(0, 1).toUpperCase();
        els.remoteName.textContent = 'Waiting for peer';
        setTopStatus('IN ROOM', true);
        setSocketStatus('SIGNALING CONNECTED', true);
        if (Array.isArray(data.peers) && data.peers.length) updateRemotePeer(data.peers[0]);
        setVoiceStatus(localStream ? 'Microphone ready' : 'Text-only room', localStream ? 'Waiting for your peer to establish the audio link.' : 'No local microphone stream. Voice is disabled.', localStream ? 'MIC READY' : 'TEXT ONLY', false);
        toast(`Joined room ${roomCode}`);
        break;
      }
      case 'peer_joined':
        updateRemotePeer(data.peer);
        showMessage({ system: true, text: `${data.peer.name} connected.`, timestamp: new Date().toISOString() });
        if (isInitiator) await startOffer();
        break;
      case 'signal':
        await handleSignal(data);
        break;
      case 'chat':
        showMessage({ name: data.name, text: data.text, timestamp: data.timestamp, own: data.client_id === clientId });
        break;
      case 'system':
        showMessage({ system: true, text: data.text, timestamp: data.timestamp });
        break;
      case 'peer_left':
        showMessage({ system: true, text: data.text });
        resetPeerState();
        isInitiator = true;
        setTopStatus('IN ROOM', true);
        break;
      case 'error':
        toast(data.message || 'Server rejected an operation.');
        if (!joined) {
          releaseLocalMedia();
          els.join.disabled = false;
          els.join.querySelector('span').textContent = 'Enable mic & join';
        }
        break;
      case 'pong':
        break;
      default:
        break;
    }
  }
  function updateRemotePeer(peer) {
    if (!peer?.id) return;
    remotePeerId = peer.id;
    els.remoteName.textContent = peer.name || 'Peer';
    els.remoteAvatar.textContent = (peer.name || '?').slice(0, 1).toUpperCase();
    els.remoteState.textContent = 'In room';
    els.retryVoice.disabled = false;
  }
  function createPeerConnection(peerId) {
    if (pc && remotePeerId === peerId) return pc;
    if (pc) {
      pc.close();
      pendingCandidates = [];
    }
    remotePeerId = peerId;
    // STUN assists NAT traversal. Some restrictive networks require a TURN relay configured by a deployment operator.
    pc = new RTCPeerConnection({ iceServers });
    if (localStream) for (const track of localStream.getAudioTracks()) pc.addTrack(track, localStream);
    pc.onicecandidate = event => {
      if (event.candidate && remotePeerId) send({ type: 'signal', peer_id: remotePeerId, kind: 'ice', data: event.candidate.toJSON() });
    };
    pc.ontrack = event => {
      const stream = event.streams?.[0];
      if (stream) els.remoteAudio.srcObject = stream;
      els.remoteAudio.play().catch(() => toast('Tap play on your browser if remote audio is blocked.'));
      els.remoteState.textContent = 'Audio received';
      setVoiceStatus('Voice connected', 'Peer audio is flowing through the WebRTC connection.', 'CONNECTED', true);
    };
    pc.onconnectionstatechange = () => {
      if (!pc) return;
      if (pc.connectionState === 'connected') {
        if (els.remoteAudio.srcObject) {
          setVoiceStatus('Voice connected', 'Peer-to-peer audio link is active. Connection stats update below.', 'CONNECTED', true);
          els.remoteState.textContent = 'Voice connected';
        } else {
          setVoiceStatus('Network path connected', 'ICE connected; waiting for a remote audio track. Check that both peers allowed microphone access.', 'NO AUDIO', false);
        }
        startStats();
      } else if (pc.connectionState === 'connecting') {
        setVoiceStatus('Connecting audio…', 'Negotiating an encrypted WebRTC media connection.', 'CONNECTING', false);
      } else if (pc.connectionState === 'disconnected') {
        setVoiceStatus('Audio path interrupted', 'The browser lost its media path. It may recover; if not, retry audio after checking both networks.', 'RETRY', false);
      } else if (pc.connectionState === 'failed') {
        const hint = turnConfigured
          ? 'WebRTC could not establish a media path even with TURN configured. Check the TURN credential/allowed transports, then retry on both browsers.'
          : 'This deployment has STUN only, with no TURN relay configured. Add TURN_URLS, TURN_USERNAME and TURN_CREDENTIAL in Render → Environment, save, then rejoin both browsers.';
        setVoiceStatus('Voice connection failed', hint, 'TURN NEEDED', false);
      }
    };
    return pc;
  }
  async function startOffer() {
    if (!remotePeerId || !localStream || makingOffer || !socket || socket.readyState !== WebSocket.OPEN) return;
    makingOffer = true;
    try {
      const connection = createPeerConnection(remotePeerId);
      if (connection.signalingState !== 'stable') return;
      const offer = await connection.createOffer();
      await connection.setLocalDescription(offer);
      send({ type: 'signal', peer_id: remotePeerId, kind: 'offer', data: connection.localDescription.toJSON() });
      setVoiceStatus('Calling your peer…', 'Waiting for the peer browser to answer and complete ICE checks.', 'CALLING', false);
    } catch (error) {
      console.error(error);
      toast('Could not start the voice connection. Try reconnecting audio.');
    } finally {
      makingOffer = false;
    }
  }
  async function handleSignal(data) {
    if (!data.peer_id || !data.kind) return;
    if (data.kind === 'offer') {
      remotePeerId = data.peer_id;
      const connection = createPeerConnection(data.peer_id);
      await connection.setRemoteDescription(new RTCSessionDescription(data.data));
      for (const candidate of pendingCandidates) await connection.addIceCandidate(candidate).catch(() => {});
      pendingCandidates = [];
      const answer = await connection.createAnswer();
      await connection.setLocalDescription(answer);
      send({ type: 'signal', peer_id: data.peer_id, kind: 'answer', data: connection.localDescription.toJSON() });
    } else if (data.kind === 'answer') {
      if (!pc) return;
      await pc.setRemoteDescription(new RTCSessionDescription(data.data));
      for (const candidate of pendingCandidates) await pc.addIceCandidate(candidate).catch(() => {});
      pendingCandidates = [];
    } else if (data.kind === 'ice') {
      const candidate = new RTCIceCandidate(data.data);
      if (!pc || !pc.remoteDescription) pendingCandidates.push(candidate);
      else await pc.addIceCandidate(candidate).catch(error => console.debug('ICE candidate skipped', error));
    }
  }
  async function reconnectAudio() {
    if (!joined || !remotePeerId) {
      toast('Invite a peer before reconnecting audio.');
      return;
    }
    const peerId = remotePeerId;
    const shouldOffer = isInitiator;
    resetPeerState();
    remotePeerId = peerId;
    if (localStream && peerId) {
      if (shouldOffer) await startOffer();
      else setVoiceStatus('Waiting for audio offer', 'The other participant will initiate a fresh audio negotiation.', 'CONNECTING', false);
    } else {
      toast('Enable your microphone before reconnecting voice.');
    }
  }
  function leaveRoom() {
    joined = false;
    clearInterval(statsTimer);
    statsTimer = null;
    if (socket) {
      const current = socket;
      socket = null;
      try { current.close(1000, 'user left'); } catch { /* already closed */ }
    }
    resetPeerState();
    releaseLocalMedia();
    clientId = null;
    remotePeerId = null;
    isInitiator = false;
    muted = false;
    els.mute.disabled = true;
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
    els.join.disabled = false;
    els.join.querySelector('span').textContent = 'Enable mic & join';
    els.joinPanel.classList.remove('hidden');
    els.howRow.classList.remove('hidden');
    els.roomPanel.classList.add('hidden');
    els.messages.replaceChildren();
    const empty = document.createElement('div');
    empty.id = 'emptyState';
    empty.className = 'empty-state';
    empty.innerHTML = '<div class="empty-orbit"><span>···</span></div><strong>Room is ready</strong><p>Send an invite, then say hello when your peer joins.</p>';
    els.messages.appendChild(empty);
    messageTotal = 0;
    els.messageCount.textContent = '0';
    setSocketStatus('DISCONNECTED', false);
    setTopStatus('READY', false);
  }
  async function sampleStats() {
    if (!pc || pc.connectionState !== 'connected') return;
    try {
      const reports = await pc.getStats();
      let inbound = null;
      let rtt = null;
      reports.forEach(report => {
        if (report.type === 'inbound-rtp' && (report.kind === 'audio' || report.mediaType === 'audio') && !report.isRemote) inbound = report;
        if (report.type === 'candidate-pair' && report.state === 'succeeded' && report.nominated && report.currentRoundTripTime != null) rtt = report.currentRoundTripTime * 1000;
      });
      if (inbound) {
        const received = inbound.packetsReceived || 0;
        const lost = Math.max(0, inbound.packetsLost || 0);
        const lossRate = received + lost ? (lost / (received + lost)) * 100 : 0;
        const jitter = (inbound.jitter || 0) * 1000;
        els.loss.textContent = lossRate.toFixed(2);
        els.jitter.textContent = jitter.toFixed(1);
        els.packets.textContent = received.toLocaleString();
        els.lossHint.textContent = lost ? `${lost.toLocaleString()} lost packet(s)` : 'No loss reported';
        statHistory.push({ time: Date.now(), jitter, loss: lossRate, rtt: rtt ?? null, received, lost, bytes: inbound.bytesReceived || 0 });
        if (statHistory.length > 60) statHistory.shift();
        drawChart();
      }
      if (rtt !== null) {
        els.rtt.textContent = rtt.toFixed(0);
        els.rttHint.textContent = 'Selected candidate path';
      }
    } catch (error) {
      console.debug('Unable to read WebRTC stats', error);
    }
  }
  function startStats() {
    if (statsTimer) return;
    sampleStats();
    statsTimer = setInterval(sampleStats, 2000);
  }
  function drawChart() {
    const canvas = els.canvas;
    if (!canvas) return;
    const width = Math.max(260, canvas.clientWidth || 400);
    const height = 76;
    const scale = window.devicePixelRatio || 1;
    canvas.width = width * scale;
    canvas.height = height * scale;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext('2d');
    ctx.scale(scale, scale);
    ctx.clearRect(0, 0, width, height);
    const left = 2, right = width - 2, top = 7, bottom = height - 12;
    ctx.strokeStyle = 'rgba(114,141,169,.17)';
    ctx.lineWidth = 1;
    for (let i = 0; i < 3; i++) {
      const y = top + (bottom - top) * i / 2;
      ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
    }
    if (!statHistory.length) {
      ctx.fillStyle = '#647991'; ctx.font = '10px DM Sans, sans-serif'; ctx.textAlign = 'center';
      ctx.fillText('Waiting for inbound audio stats', width / 2, height / 2 + 3);
      return;
    }
    const max = Math.max(4, ...statHistory.map(p => p.jitter)) * 1.15;
    const pts = statHistory.map((p, i) => ({
      x: left + (right - left) * (statHistory.length === 1 ? .5 : i / (statHistory.length - 1)),
      y: bottom - (Math.min(max, p.jitter) / max) * (bottom - top)
    }));
    const gradient = ctx.createLinearGradient(0, top, 0, bottom);
    gradient.addColorStop(0, 'rgba(117,242,195,.18)'); gradient.addColorStop(1, 'rgba(117,242,195,0)');
    ctx.beginPath(); pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
    ctx.lineTo(pts[pts.length - 1].x, bottom); ctx.lineTo(pts[0].x, bottom); ctx.closePath(); ctx.fillStyle = gradient; ctx.fill();
    ctx.beginPath(); pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
    ctx.lineWidth = 1.8; ctx.strokeStyle = '#75f2c3'; ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.stroke();
    const last = pts[pts.length - 1]; ctx.beginPath(); ctx.arc(last.x, last.y, 2.4, 0, Math.PI * 2); ctx.fillStyle = '#b4ffe1'; ctx.fill();
  }
  function exportCsv() {
    if (!statHistory.length) { toast('No connection stats to export yet.'); return; }
    const header = ['timestamp_iso', 'jitter_ms', 'rtt_ms', 'packet_loss_percent', 'packets_received', 'packets_lost', 'bytes_received'];
    const rows = statHistory.map(p => [new Date(p.time).toISOString(), p.jitter.toFixed(3), p.rtt == null ? '' : p.rtt.toFixed(3), p.loss.toFixed(4), p.received, p.lost, p.bytes]);
    const csv = [header, ...rows].map(row => row.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a'); a.href = url; a.download = `relay-${roomCode || 'room'}-stats.csv`; a.click(); URL.revokeObjectURL(url);
    toast('Connection stats exported.');
  }

  els.newRoom.addEventListener('click', () => { els.room.value = makeRoomCode(); });
  els.joinForm.addEventListener('submit', joinRoom);
  els.chatForm.addEventListener('submit', event => {
    event.preventDefault();
    const text = els.chatInput.value.trim();
    if (!text) return;
    if (!joined || !send({ type: 'chat', text })) { toast('Join the room before sending messages.'); return; }
    els.chatInput.value = '';
    els.chatInput.focus();
  });
  els.copyRoom.addEventListener('click', async () => {
    const invite = `${location.origin}/?room=${encodeURIComponent(roomCode)}`;
    try { await navigator.clipboard.writeText(invite); toast('Invite link copied. Send it to your peer.'); }
    catch { toast(`Share this room code: ${roomCode}`); }
  });
  els.leave.addEventListener('click', leaveRoom);
  els.mute.addEventListener('click', () => {
    if (!localStream) return;
    muted = !muted;
    for (const track of localStream.getAudioTracks()) track.enabled = !muted;
    els.muteText.textContent = muted ? 'Unmute mic' : 'Mute mic';
    els.muteIcon.textContent = muted ? '⊘' : '♬';
    toast(muted ? 'Microphone muted.' : 'Microphone unmuted.');
  });
  els.retryVoice.addEventListener('click', reconnectAudio);
  els.export.addEventListener('click', exportCsv);
  window.addEventListener('resize', drawChart);
  window.addEventListener('beforeunload', () => { if (socket) socket.close(); if (pc) pc.close(); if (localStream) localStream.getTracks().forEach(t => t.stop()); });

  els.room.value = new URLSearchParams(location.search).get('room') || makeRoomCode();
  drawChart();
})();