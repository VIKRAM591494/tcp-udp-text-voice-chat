"""Polished Tkinter laboratory dashboard for the dual-transport prototype."""

from __future__ import annotations

import queue
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Callable

from config import AppConfig, NetworkConfig
from network_simulator import SimulatorConfig
from workers import SessionController


BG = "#0b1220"
PANEL = "#111c2e"
PANEL_2 = "#16243a"
TEXT = "#e6edf7"
MUTED = "#98a8bd"
UDP = "#34d399"
TCP = "#60a5fa"
ACCENT = "#fbbf24"
ERROR = "#fb7185"


class LineGraph(tk.Canvas):
    """Small native canvas graph of actual in-session observations."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, height=130, bg=PANEL_2, highlightthickness=0)
        self._series: list[float] = []
        self._label = "No measured samples yet"
        self.bind("<Configure>", lambda _event: self._draw())

    def set_data(self, label: str, values: list[float]) -> None:
        self._label = label
        self._series = values[-80:]
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        width, height = max(self.winfo_width(), 1), max(self.winfo_height(), 1)
        self.create_text(12, 12, anchor="w", text=self._label, fill=MUTED, font=("Arial", 9))
        if len(self._series) < 2:
            self.create_text(width / 2, height / 2 + 8, text="Waiting for measured traffic", fill=MUTED,
                             font=("Arial", 10))
            return
        low, high = min(self._series), max(self._series)
        spread = high - low or 1.0
        points: list[float] = []
        for index, value in enumerate(self._series):
            x = 12 + index * (width - 24) / (len(self._series) - 1)
            y = 32 + (height - 44) * (1 - (value - low) / spread)
            points.extend((x, y))
        self.create_line(*points, fill=UDP, width=2, smooth=True)
        self.create_text(width - 12, 12, anchor="e", text=f"min {low:.1f}  max {high:.1f}", fill=TEXT,
                         font=("Arial", 9))


class VoiceChatApp(tk.Tk):
    """The interactive desktop UI; networking work remains in background workers."""

    def __init__(self, project_dir: Path) -> None:
        super().__init__()
        self.project_dir = project_dir
        self.title("Voice Chat Application using UDP with Text Chat")
        self.geometry("1460x930")
        self.minsize(1180, 760)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._configure_style()
        self.controller = SessionController(AppConfig.defaults(), project_dir / "logs")
        self._shown_voice_rows = 0
        self._shown_text_rows = 0
        self._muted = False
        self._demo_steps = [
            "1. Enter matching TCP/UDP ports in two instances, then click HOST here and CONNECT in the other.",
            "2. Start Voice or Start Test Audio in both instances. UDP packet counts will increase independently.",
            "3. Send a text message. TCP Message Inspector records reliable ordered frames.",
            "4. Select 5% LOSS or HIGH JITTER, then Apply UDP Conditions. Only voice packets are affected.",
            "5. Compare UDP loss/queue metrics with TCP delivery. Click Reset Demo before another run.",
        ]
        self._demo_index = 0
        self._build()
        self.after(150, self._refresh)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Card.TFrame", background=PANEL_2)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Arial", 10))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=("Arial", 10))
        style.configure("Card.TLabel", background=PANEL_2, foreground=TEXT, font=("Arial", 10))
        style.configure("Header.TLabel", background=BG, foreground=TEXT, font=("Arial", 20, "bold"))
        style.configure("Subtle.TLabel", background=BG, foreground=MUTED, font=("Arial", 10))
        style.configure("Section.TLabel", background=PANEL, foreground=TEXT, font=("Arial", 11, "bold"))
        style.configure("UDP.TLabel", background=PANEL_2, foreground=UDP, font=("Arial", 10, "bold"))
        style.configure("TCP.TLabel", background=PANEL_2, foreground=TCP, font=("Arial", 10, "bold"))
        style.configure("Metric.TLabel", background=PANEL_2, foreground=TEXT, font=("Arial", 10, "bold"))
        style.configure("TButton", font=("Arial", 10, "bold"), padding=(9, 5), background="#21334e", foreground=TEXT)
        style.map("TButton", background=[("active", "#304b70")])
        style.configure("Udp.TButton", background="#0d5e4e", foreground="#ecfdf5")
        style.map("Udp.TButton", background=[("active", "#0f766e")])
        style.configure("Tcp.TButton", background="#1d4e89", foreground="#eff6ff")
        style.map("Tcp.TButton", background=[("active", "#2563eb")])
        style.configure("Danger.TButton", background="#8b2635", foreground="#fff1f2")
        style.map("Danger.TButton", background=[("active", "#be3144")])
        style.configure("TEntry", fieldbackground="#0b1628", foreground=TEXT, insertcolor=TEXT)
        style.configure("TCombobox", fieldbackground="#0b1628", foreground=TEXT, background="#21334e")
        style.configure("Treeview", background="#0b1628", fieldbackground="#0b1628", foreground=TEXT,
                        rowheight=23, borderwidth=0, font=("Arial", 9))
        style.map("Treeview", background=[("selected", "#25415f")])
        style.configure("Treeview.Heading", background="#21334e", foreground=TEXT, font=("Arial", 9, "bold"))
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background="#16243a", foreground=MUTED, padding=(10, 5))
        style.map("TNotebook.Tab", background=[("selected", "#21334e")], foreground=[("selected", TEXT)])

    def _build(self) -> None:
        self._build_header()
        self._build_connection_controls()
        middle = ttk.Frame(self, style="TFrame", padding=(12, 5, 12, 5))
        middle.pack(fill="both", expand=True)
        middle.columnconfigure(0, weight=4)
        middle.columnconfigure(1, weight=5)
        middle.columnconfigure(2, weight=3)
        middle.rowconfigure(0, weight=1)
        self._build_transport_lane(middle).grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self._build_chat(middle).grid(row=0, column=1, sticky="nsew", padx=7)
        self._build_voice_controls(middle).grid(row=0, column=2, sticky="nsew", padx=(7, 0))
        bottom = ttk.Frame(self, style="TFrame", padding=(12, 5, 12, 12))
        bottom.pack(fill="both", expand=True)
        bottom.columnconfigure(0, weight=5)
        bottom.columnconfigure(1, weight=4)
        bottom.columnconfigure(2, weight=5)
        bottom.rowconfigure(0, weight=1)
        self._build_metrics(bottom).grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._build_network_lab(bottom).grid(row=0, column=1, sticky="nsew", padx=6)
        self._build_log(bottom).grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        self._build_inspectors().pack(fill="both", padx=12, pady=(0, 12))

    def _build_header(self) -> None:
        header = ttk.Frame(self, style="TFrame", padding=(16, 14, 16, 3))
        header.pack(fill="x")
        ttk.Label(header, text="Voice Chat Application using UDP with Text Chat", style="Header.TLabel").pack(anchor="w")
        ttk.Label(header, text="Educational laboratory prototype  |  Voice freshness over UDP  |  Ordered chat over TCP",
                  style="Subtle.TLabel").pack(anchor="w", pady=(2, 0))

    def _build_connection_controls(self) -> None:
        frame = ttk.Frame(self, style="Panel.TFrame", padding=10)
        frame.pack(fill="x", padx=12, pady=(6, 8))
        self.local_ip = tk.StringVar(value="0.0.0.0")
        self.peer_ip = tk.StringVar(value="127.0.0.1")
        self.tcp_port = tk.StringVar(value="51000")
        self.local_udp = tk.StringVar(value="51001")
        self.peer_udp = tk.StringVar(value="51002")
        self.name = tk.StringVar(value="Student")
        labels = (("Local IP", self.local_ip, 12), ("Peer IP", self.peer_ip, 12), ("TCP port", self.tcp_port, 7),
                  ("Local UDP", self.local_udp, 7), ("Peer UDP", self.peer_udp, 7), ("Name", self.name, 10))
        for index, (label, variable, width) in enumerate(labels):
            ttk.Label(frame, text=label, style="Panel.TLabel").grid(row=0, column=index, sticky="w", padx=(0, 3))
            ttk.Entry(frame, textvariable=variable, width=width).grid(row=1, column=index, sticky="ew", padx=(0, 8))
        ttk.Button(frame, text="HOST", style="Tcp.TButton", command=self._host).grid(row=1, column=6, padx=3)
        ttk.Button(frame, text="CONNECT", style="Tcp.TButton", command=self._connect).grid(row=1, column=7, padx=3)
        ttk.Button(frame, text="DISCONNECT", style="Danger.TButton", command=self._disconnect).grid(row=1, column=8, padx=3)
        self.role_status = tk.StringVar(value="Role: IDLE")
        self.link_status = tk.StringVar(value="TCP: DISCONNECTED  |  UDP: INACTIVE  |  VOICE: STOPPED")
        ttk.Label(frame, textvariable=self.role_status, style="Panel.TLabel").grid(row=0, column=6, columnspan=3, sticky="e")
        ttk.Label(frame, textvariable=self.link_status, style="Panel.TLabel").grid(row=2, column=0, columnspan=9, sticky="w", pady=(7, 0))

    def _lane_step(self, frame: ttk.Frame, text: str, style: str) -> None:
        ttk.Label(frame, text=text, style=style, anchor="center", justify="center").pack(fill="x", padx=12, pady=2)

    def _build_transport_lane(self, master: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(master, style="Panel.TFrame", padding=12)
        ttk.Label(frame, text="Live transport architecture", style="Section.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Separate worker lanes stay responsive when the other lane changes.",
                  style="Panel.TLabel", foreground=MUTED).pack(anchor="w", pady=(2, 8))
        voice = ttk.Frame(frame, style="Card.TFrame", padding=8)
        voice.pack(fill="x", pady=(0, 9))
        ttk.Label(voice, text="VOICE LANE  ·  UDP DATAGRAMS", style="UDP.TLabel").pack(anchor="w")
        for step in ("Microphone / test PCM", "↓  Audio capture worker", "↓  UDP packet: header + PCM payload",
                     "↓  Network simulator (optional)", "↓  UDP receive worker", "↓  Bounded playout queue", "↓  Speaker"):
            self._lane_step(voice, step, "Card.TLabel")
        self.udp_activity = tk.StringVar(value="UDP activity: waiting for packets")
        ttk.Label(voice, textvariable=self.udp_activity, style="UDP.TLabel").pack(anchor="w", pady=(7, 0))
        text = ttk.Frame(frame, style="Card.TFrame", padding=8)
        text.pack(fill="x")
        ttk.Label(text, text="TEXT LANE  ·  TCP FRAMED STREAM", style="TCP.TLabel").pack(anchor="w")
        for step in ("Text input", "↓  TCP send worker", "↓  4-byte length + UTF-8 JSON", "↓  TCP receive worker", "↓  Thread-safe chat queue", "↓  Chat window"):
            self._lane_step(text, step, "Card.TLabel")
        self.tcp_activity = tk.StringVar(value="TCP activity: waiting for messages")
        ttk.Label(text, textvariable=self.tcp_activity, style="TCP.TLabel").pack(anchor="w", pady=(7, 0))
        ttk.Label(frame, text="Scope: no encryption, authentication, NAT traversal, or guaranteed QoS.",
                  style="Panel.TLabel", foreground=ACCENT, wraplength=330).pack(anchor="w", pady=(10, 0))
        return frame

    def _build_chat(self, master: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(master, style="Panel.TFrame", padding=12)
        ttk.Label(frame, text="Reliable TCP text chat", style="Section.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Length-prefixed UTF-8 messages. TCP controls retransmission and ordering.",
                  style="Panel.TLabel", foreground=MUTED).pack(anchor="w", pady=(2, 8))
        self.chat = scrolledtext.ScrolledText(frame, height=20, bg="#0b1628", fg=TEXT, insertbackground=TEXT,
                                              relief="flat", wrap="word", font=("Arial", 10), state="disabled")
        self.chat.pack(fill="both", expand=True)
        entry_row = ttk.Frame(frame, style="Panel.TFrame")
        entry_row.pack(fill="x", pady=(8, 0))
        self.message_text = tk.StringVar()
        entry = ttk.Entry(entry_row, textvariable=self.message_text)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        entry.bind("<Return>", lambda _event: self._send_chat())
        ttk.Button(entry_row, text="SEND TCP", style="Tcp.TButton", command=self._send_chat).pack(side="right")
        self.chat_state = tk.StringVar(value="Chat: TCP connection required")
        ttk.Label(frame, textvariable=self.chat_state, style="Panel.TLabel", foreground=MUTED).pack(anchor="w", pady=(7, 0))
        return frame

    def _build_voice_controls(self, master: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(master, style="Panel.TFrame", padding=12)
        ttk.Label(frame, text="Voice controls", style="Section.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Real PCM microphone → UDP → bounded queue → speaker", style="Panel.TLabel", foreground=MUTED,
                  wraplength=260).pack(anchor="w", pady=(2, 10))
        ttk.Button(frame, text="START REAL VOICE", style="Udp.TButton", command=lambda: self._start_voice(False)).pack(fill="x", pady=3)
        ttk.Button(frame, text="START TEST AUDIO", command=lambda: self._start_voice(True)).pack(fill="x", pady=3)
        self.mute_button = ttk.Button(frame, text="MUTE OUTBOUND VOICE", command=self._toggle_mute)
        self.mute_button.pack(fill="x", pady=3)
        ttk.Button(frame, text="STOP VOICE", style="Danger.TButton", command=self._stop_voice).pack(fill="x", pady=3)
        self.voice_detail = tk.StringVar(value="Audio status: stopped")
        ttk.Label(frame, textvariable=self.voice_detail, style="Panel.TLabel", foreground=UDP, wraplength=270).pack(anchor="w", pady=(10, 8))
        demo = ttk.Frame(frame, style="Card.TFrame", padding=9)
        demo.pack(fill="x", pady=3)
        ttk.Label(demo, text="DEMO MODE", style="Card.TLabel", font=("Arial", 10, "bold")).pack(anchor="w")
        self.demo_text = tk.StringVar(value=self._demo_steps[0])
        ttk.Label(demo, textvariable=self.demo_text, style="Card.TLabel", foreground=MUTED, wraplength=250,
                  justify="left").pack(anchor="w", pady=(4, 6))
        row = ttk.Frame(demo, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="NEXT STEP", command=self._next_demo).pack(side="left", padx=(0, 5))
        ttk.Button(row, text="RESET DEMO", command=self._reset_demo).pack(side="left")
        ttk.Button(frame, text="ABOUT / VIVA", command=self._show_viva).pack(fill="x", pady=(8, 0))
        return frame

    def _build_metrics(self, master: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(master, style="Panel.TFrame", padding=10)
        ttk.Label(frame, text="Measured metrics", style="Section.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Values remain N/A until the app observes traffic.", style="Panel.TLabel", foreground=MUTED).pack(anchor="w")
        self.metric_values: dict[str, tk.StringVar] = {}
        fields = [
            ("UDP sent", "voice_packets_sent"), ("UDP received", "voice_packets_received"),
            ("Detected loss", "voice_loss_percent"), ("Late / dropped", "voice_late"),
            ("Out of order", "voice_out_of_order"), ("Queue depth", "queue_depth"),
            ("Estimated playout", "estimated_capture_to_playout_ms"), ("TCP sent", "text_messages_sent"),
            ("TCP received", "text_messages_received"), ("Text latency", "average_text_send_to_render_ms"),
            ("Workers", "active_workers"), ("Uptime", "uptime_seconds"),
        ]
        grid = ttk.Frame(frame, style="Panel.TFrame")
        grid.pack(fill="both", expand=True, pady=(5, 0))
        for index, (label, key) in enumerate(fields):
            row, column = divmod(index, 2)
            item = ttk.Frame(grid, style="Card.TFrame", padding=5)
            item.grid(row=row, column=column, sticky="ew", padx=(0 if column == 0 else 4, 4 if column == 0 else 0), pady=2)
            ttk.Label(item, text=label, style="Card.TLabel", foreground=MUTED).pack(anchor="w")
            value = tk.StringVar(value="N/A")
            self.metric_values[key] = value
            ttk.Label(item, textvariable=value, style="Metric.TLabel").pack(anchor="w")
            grid.columnconfigure(column, weight=1)
        self.graph = LineGraph(frame)
        self.graph.pack(fill="x", pady=(5, 0))
        return frame

    def _build_network_lab(self, master: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(master, style="Panel.TFrame", padding=10)
        ttk.Label(frame, text="UDP network simulator", style="Section.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Applied only before real UDP sendto(). TCP text never enters this path.",
                  style="Panel.TLabel", foreground=MUTED, wraplength=330).pack(anchor="w", pady=(2, 8))
        self.loss = tk.DoubleVar(value=0)
        self.delay = tk.DoubleVar(value=0)
        self.jitter = tk.DoubleVar(value=0)
        self.reorder = tk.BooleanVar(value=False)
        for label, variable, maximum in (("Packet loss (%)", self.loss, 100), ("Delay (ms)", self.delay, 500),
                                          ("Jitter (ms)", self.jitter, 300)):
            row = ttk.Frame(frame, style="Panel.TFrame")
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, style="Panel.TLabel").pack(side="left")
            ttk.Spinbox(row, from_=0, to=maximum, textvariable=variable, width=8).pack(side="right")
        ttk.Checkbutton(frame, text="Allow simulated reordering", variable=self.reorder).pack(anchor="w", pady=(4, 8))
        button_row = ttk.Frame(frame, style="Panel.TFrame")
        button_row.pack(fill="x")
        ttk.Button(button_row, text="APPLY UDP CONDITIONS", style="Udp.TButton", command=self._apply_simulator).pack(side="left")
        self.preset = tk.StringVar(value="NORMAL")
        preset = ttk.Combobox(button_row, textvariable=self.preset, width=14, state="readonly",
                               values=("NORMAL", "1–2% LOSS", "5% LOSS", "10% LOSS", "HIGH JITTER", "HIGH DELAY", "SEVERE NETWORK"))
        preset.pack(side="right")
        preset.bind("<<ComboboxSelected>>", lambda _event: self._apply_preset())
        ttk.Label(frame, text="Simulation outcomes are labelled as simulated. Metrics come from real local socket events.",
                  style="Panel.TLabel", foreground=ACCENT, wraplength=330, justify="left").pack(anchor="w", pady=(10, 0))
        return frame

    def _build_log(self, master: tk.Misc) -> ttk.Frame:
        frame = ttk.Frame(master, style="Panel.TFrame", padding=10)
        ttk.Label(frame, text="Live event log", style="Section.TLabel").pack(anchor="w")
        self.log = scrolledtext.ScrolledText(frame, height=16, bg="#0b1628", fg=TEXT, relief="flat", wrap="word",
                                             font=("Menlo", 9), state="disabled")
        self.log.pack(fill="both", expand=True, pady=(5, 5))
        row = ttk.Frame(frame, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="CLEAR LOG", command=self._clear_log).pack(side="left")
        ttk.Button(row, text="EXPORT LOG", command=self._export_log).pack(side="left", padx=5)
        ttk.Button(row, text="EXPORT METRICS", command=self._export_metrics).pack(side="left")
        return frame

    def _build_inspectors(self) -> ttk.Notebook:
        notebook = ttk.Notebook(self)
        voice = ttk.Frame(notebook, style="Panel.TFrame", padding=8)
        text = ttk.Frame(notebook, style="Panel.TFrame", padding=8)
        notebook.add(voice, text="UDP Voice Packet Inspector")
        notebook.add(text, text="TCP Message Inspector")
        self.voice_inspector = self._create_tree(voice, (
            ("sequence", "Sequence", 75), ("timestamp", "Timestamp", 165), ("payload", "Payload", 75),
            ("arrival", "Arrival", 165), ("delay", "Estimated delay", 110), ("status", "Status", 150),
        ))
        self.voice_inspector.pack(fill="both", expand=True)
        self.text_inspector = self._create_tree(text, (
            ("id", "Message ID", 80), ("timestamp", "Timestamp", 165), ("size", "Size", 75),
            ("direction", "Direction", 85), ("delivery", "Delivery", 110), ("latency", "Send-to-render", 130),
        ))
        self.text_inspector.pack(fill="both", expand=True)
        return notebook

    def _create_tree(self, master: tk.Misc, columns: tuple[tuple[str, str, int], ...]) -> ttk.Treeview:
        tree = ttk.Treeview(master, columns=tuple(item[0] for item in columns), show="headings", height=7)
        for name, heading, width in columns:
            tree.heading(name, text=heading)
            tree.column(name, width=width, anchor="center")
        return tree

    def _read_network_settings(self) -> NetworkConfig:
        return NetworkConfig(
            local_ip=self.local_ip.get().strip(), peer_ip=self.peer_ip.get().strip(),
            tcp_port=int(self.tcp_port.get()), local_udp_port=int(self.local_udp.get()),
            peer_udp_port=int(self.peer_udp.get()), display_name=self.name.get().strip(),
        )

    def _host(self) -> None:
        try:
            self.controller.apply_network_config(self._read_network_settings())
            self.controller.start_host()
        except (ValueError, RuntimeError, OSError) as exc:
            self._error(str(exc))

    def _connect(self) -> None:
        try:
            self.controller.apply_network_config(self._read_network_settings())
            self.controller.start_client()
        except (ValueError, RuntimeError, OSError) as exc:
            self._error(str(exc))

    def _disconnect(self) -> None:
        self.controller.disconnect()

    def _start_voice(self, simulation: bool) -> None:
        try:
            self.controller.start_voice(simulation_audio=simulation)
        except Exception as exc:
            self._error(str(exc))

    def _stop_voice(self) -> None:
        self.controller.stop_voice()

    def _toggle_mute(self) -> None:
        self._muted = not self._muted
        self.controller.set_muted(self._muted)
        self.mute_button.configure(text="UNMUTE OUTBOUND VOICE" if self._muted else "MUTE OUTBOUND VOICE")

    def _send_chat(self) -> None:
        if self.controller.send_chat(self.message_text.get()):
            self.message_text.set("")
        else:
            self._error("Chat message was not sent. Confirm that TCP shows CONNECTED.")

    def _apply_simulator(self) -> None:
        try:
            self.controller.update_simulator(SimulatorConfig(
                loss_percent=float(self.loss.get()), delay_ms=float(self.delay.get()),
                jitter_ms=float(self.jitter.get()), reordering=bool(self.reorder.get()),
            ))
        except ValueError as exc:
            self._error(str(exc))

    def _apply_preset(self) -> None:
        config = self.controller.apply_preset(self.preset.get())
        self.loss.set(config.loss_percent)
        self.delay.set(config.delay_ms)
        self.jitter.set(config.jitter_ms)
        self.reorder.set(config.reordering)

    def _next_demo(self) -> None:
        self._demo_index = (self._demo_index + 1) % len(self._demo_steps)
        self.demo_text.set(self._demo_steps[self._demo_index])

    def _reset_demo(self) -> None:
        self.controller.update_simulator(SimulatorConfig())
        self.loss.set(0)
        self.delay.set(0)
        self.jitter.set(0)
        self.reorder.set(False)
        self._clear_log()
        self._demo_index = 0
        self.demo_text.set(self._demo_steps[0])
        self.controller.logger.log("APP", "Demo reset: UDP conditions restored to normal")

    def _show_viva(self) -> None:
        window = tk.Toplevel(self)
        window.title("About / Viva explanation")
        window.geometry("680x580")
        window.configure(bg=BG)
        content = scrolledtext.ScrolledText(window, bg="#0b1628", fg=TEXT, relief="flat", wrap="word",
                                            font=("Arial", 11))
        content.pack(fill="both", expand=True, padx=12, pady=12)
        content.insert("1.0", """Why UDP for voice?
Voice frames lose value when late. This app sends each PCM frame in a UDP datagram and never retransmits old audio. The bounded playout queue evicts stale frames when necessary.

Why TCP for text?
Typed messages remain valuable after delay. TCP gives reliable, in-order byte delivery; the app adds a 4-byte length prefix because TCP does not preserve message boundaries.

Why separate sockets, threads, and queues?
UDP receiver, audio capture/playback, TCP receive, and the UI operate independently. Thread-safe queues protect handoffs. A stalled chat receive cannot block audio playout.

Why sequence numbers and timestamps?
The UDP header exposes packet sequence, creation time, and payload length. The receiver can show loss gaps, duplicates, and out-of-order traffic. Capture-to-playout is estimated, especially across unsynchronised clocks.

What happens during loss and delay?
The optional simulator sits only before UDP sendto(). It can drop, delay, jitter, and reorder voice frames. The app continues with newer frames and records the event. TCP chat is outside the simulator and remains ordered.

Limitations and TRL
This is an educational laboratory prototype at TRL 4. It does not provide encryption, authentication, NAT traversal, codec compression, RTP interoperability, or production jitter control. A production system would need all of those plus monitoring and security review.""")
        content.configure(state="disabled")

    def _clear_log(self) -> None:
        self.controller.logger.clear_ui_queue()
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _export_log(self) -> None:
        messagebox.showinfo("Log saved", f"Timestamped event log:\n{self.controller.log_path}")

    def _export_metrics(self) -> None:
        path = self.controller.export_metrics(self.project_dir / "logs")
        messagebox.showinfo("Metrics exported", f"Measured inspector records exported to:\n{path}")

    def _error(self, text: str) -> None:
        self.controller.logger.log("ERROR", text)
        messagebox.showerror("Voice Chat Laboratory", text)

    def _refresh(self) -> None:
        try:
            self._drain_queues()
            self._update_metrics()
            self._update_inspectors()
        finally:
            if self.winfo_exists():
                self.after(150, self._refresh)

    def _drain_queues(self) -> None:
        while True:
            try:
                message = self.controller.chat_queue.get_nowait()
            except queue.Empty:
                break
            sent_at = datetime.fromtimestamp(message.timestamp_ns / 1_000_000_000).strftime("%H:%M:%S")
            self.chat.configure(state="normal")
            self.chat.insert("end", f"[{sent_at}] {message.sender}: {message.text}\n")
            self.chat.see("end")
            self.chat.configure(state="disabled")
        while True:
            try:
                event = self.controller.logger.events.get_nowait()
            except queue.Empty:
                break
            self.log.configure(state="normal")
            self.log.insert("end", event + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        while True:
            try:
                state = self.controller.state_queue.get_nowait()
            except queue.Empty:
                break
            self.chat_state.set(f"Chat: {state}")

    def _update_metrics(self) -> None:
        metrics = self.controller.metrics_snapshot()
        suffixes = {
            "voice_loss_percent": "%", "estimated_capture_to_playout_ms": " ms",
            "average_text_send_to_render_ms": " ms", "uptime_seconds": " s",
        }
        for key, variable in self.metric_values.items():
            variable.set(f"{metrics.get(key, 'N/A')}{suffixes.get(key, '')}")
        self.role_status.set(f"Role: {self.controller.role.upper()}")
        self.link_status.set(
            f"TCP: {self.controller.tcp_state}  |  UDP: {self.controller.udp_state}  |  VOICE: {self.controller.voice_state}"
        )
        simulation = "Simulation / audio test" if self.controller.simulation_audio else "Real audio"
        self.voice_detail.set(f"Audio status: {self.controller.voice_state.lower()}  |  {simulation}")
        self.udp_activity.set(
            f"UDP activity: voice packet #{metrics['voice_packets_sent']} sent  |  #{metrics['voice_packets_received']} received"
        )
        self.tcp_activity.set(
            f"TCP activity: {metrics['text_messages_sent']} sent  |  {metrics['text_messages_received']} received  |  {metrics['tcp_status']}"
        )
        history = self.controller.metrics.metric_history()
        self.graph.set_data("Measured UDP arrival interval (ms)", history["arrival_interval_ms"])

    def _update_inspectors(self) -> None:
        voice_rows = list(self.controller.metrics.voice_rows)
        # Repaint the bounded recent history so rows still advance after the
        # 200-record deque reaches its maximum size.
        self.voice_inspector.delete(*self.voice_inspector.get_children())
        for row in voice_rows:
            self.voice_inspector.insert("", "end", values=(
                row.sequence, self._format_ns(row.timestamp_ns), row.payload_size, self._format_ns(row.arrival_ns),
                "N/A" if row.delay_ms is None else f"{row.delay_ms:.1f} ms", row.status,
            ))
        text_rows = list(self.controller.metrics.text_rows)
        self.text_inspector.delete(*self.text_inspector.get_children())
        for row in text_rows:
            self.text_inspector.insert("", "end", values=(
                row.message_id, self._format_ns(row.timestamp_ns), row.size, row.direction, row.status,
                "N/A" if row.latency_ms is None else f"{row.latency_ms:.1f} ms",
            ))

    @staticmethod
    def _format_ns(value: int | None) -> str:
        if value is None:
            return "N/A"
        return datetime.fromtimestamp(value / 1_000_000_000).strftime("%H:%M:%S.%f")[:-3]

    def _close(self) -> None:
        self.controller.shutdown()
        self.destroy()


def launch(project_dir: Path) -> None:
    """Start the desktop program."""
    VoiceChatApp(project_dir).mainloop()