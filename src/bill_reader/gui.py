"""A window over write_csv: choose the bills, choose the CSV, start."""

from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .cli import NO_BILLS, collect_files, default_output, write_csv
from .reader import SUPPORTED_SUFFIXES

_TITLE = "Taipower Bill Reader"
_BILL_FILES = [("Bills", " ".join(f"*{suffix}" for suffix in sorted(SUPPORTED_SUFFIXES))), ("All files", "*.*")]
_POLL_MS = 100


def work(paths: list[str], out: Path, events: queue.Queue) -> None:
    """Runs in its own thread: what happens goes on ``events`` as (kind, value), ending in done or error."""
    try:
        files = collect_files(paths)
        if not files:
            events.put(("error", NO_BILLS))
            return
        events.put(("total", len(files)))
        started = time.time()
        summary = write_csv(files, out, say=lambda text: events.put(("say", text)), on_file=lambda done: events.put(("file", done)))
        events.put(("done", f"{summary.text()} in {time.time() - started:.1f} s"))
    except PermissionError:
        events.put(("error", f"Cannot write {out}\nIf it is open in Excel, close it and try again."))
    except (Exception, SystemExit) as error:  # noqa: BLE001 - the window stays open to say so
        events.put(("error", str(error) or type(error).__name__))


class ReaderWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.inputs: list[str] = []
        self.output_chosen = False
        self.worker: threading.Thread | None = None
        self.events: queue.Queue = queue.Queue()
        self.input_text = tk.StringVar(value="Nothing chosen yet")
        self.output_text = tk.StringVar()
        self.status = tk.StringVar(value="Choose the input and output, then press Start")

        root.title(f"{_TITLE} {__version__}")
        root.geometry("760x520")
        root.minsize(560, 380)
        root.protocol("WM_DELETE_WINDOW", self.close)
        ttk.Style().configure(".", font=("Segoe UI", 10))

        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(4, weight=1)

        ttk.Label(frame, text="Input").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(frame, textvariable=self.input_text, state="readonly").grid(row=0, column=1, sticky="ew")
        self.pick_folder_button = ttk.Button(frame, text="Folder...", command=self.pick_folder)
        self.pick_folder_button.grid(row=0, column=2, padx=(8, 0))
        self.pick_files_button = ttk.Button(frame, text="Files...", command=self.pick_files)
        self.pick_files_button.grid(row=0, column=3, padx=(4, 0))

        ttk.Label(frame, text="Output").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        self.output_entry = ttk.Entry(frame, textvariable=self.output_text)
        self.output_entry.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        self.pick_output_button = ttk.Button(frame, text="Save as...", command=self.pick_output)
        self.pick_output_button.grid(row=1, column=2, columnspan=2, sticky="ew", padx=(8, 0), pady=(8, 0))

        self.start_button = ttk.Button(frame, text="Start", command=self.start)
        self.start_button.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(12, 0))

        self.progress = ttk.Progressbar(frame)
        self.progress.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 8))

        box = ttk.Frame(frame)
        box.grid(row=4, column=0, columnspan=4, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.log = tk.Text(box, height=10, state="disabled", wrap="word", font=("Segoe UI", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(box, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

        bottom = ttk.Frame(frame)
        bottom.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, textvariable=self.status).grid(row=0, column=0, sticky="w")
        self.open_csv_button = ttk.Button(bottom, text="Open CSV", command=self.open_csv, state="disabled")
        self.open_csv_button.grid(row=0, column=1, padx=(8, 0))
        self.open_folder_button = ttk.Button(bottom, text="Open folder", command=self.open_folder, state="disabled")
        self.open_folder_button.grid(row=0, column=2, padx=(4, 0))

    def _choose_inputs(self, paths: list[str]) -> None:
        self.inputs = paths
        self.input_text.set(paths[0] if len(paths) == 1 else f"{len(paths)} files ({Path(paths[0]).parent})")
        if not self.output_chosen:
            self.output_text.set(str(default_output(paths)))

    def pick_folder(self) -> None:
        if folder := filedialog.askdirectory(title="Choose the folder of Taipower bills"):
            self._choose_inputs([folder])

    def pick_files(self) -> None:
        if files := filedialog.askopenfilenames(title="Choose bill files", filetypes=_BILL_FILES):
            self._choose_inputs(list(files))

    def pick_output(self) -> None:
        # The dialog asks before replacing a file, so start need not ask again.
        current = Path(self.output_text.get()) if self.output_text.get() else None
        chosen = filedialog.asksaveasfilename(
            title="Output CSV", defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialdir=current.parent if current else None, initialfile=current.name if current else "bills.csv",
        )
        if chosen:
            self.output_text.set(chosen)
            self.output_chosen = True

    def _output(self) -> Path | None:
        text = self.output_text.get().strip()
        if not text:
            return None
        out = Path(text)
        return out / "bills.csv" if out.is_dir() else out

    def start(self) -> None:
        out = self._output()
        if not self.inputs or out is None:
            messagebox.showinfo(_TITLE, "Choose the input and output first.")
            return
        if out.exists() and not messagebox.askyesno(_TITLE, f"{out}\nalready exists. Replace it?"):
            return
        self.output_text.set(str(out))
        self._busy(True)
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.progress.configure(value=0)
        self.status.set("Reading... the first bill takes longer, as the models load")
        self.worker = threading.Thread(target=work, args=(self.inputs, out, self.events), daemon=True)
        self.worker.start()
        self.root.after(_POLL_MS, self._poll)

    def _busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for widget in (self.start_button, self.pick_folder_button, self.pick_files_button, self.pick_output_button, self.output_entry):
            widget.configure(state=state)
        for button in (self.open_csv_button, self.open_folder_button):
            button.configure(state="disabled")

    def _say(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self) -> None:
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "total":
                self.progress.configure(maximum=value)
            elif kind == "say":
                self._say(value)
            elif kind == "file":
                self.progress.configure(value=value)
                self.status.set(f"{value} of {int(self.progress['maximum'])} files done")
            else:
                self._finish(kind, value)
                return
        self.root.after(_POLL_MS, self._poll)

    def _finish(self, kind: str, message: str) -> None:
        self._busy(False)
        if kind == "done":
            out = self.output_text.get()
            self._say(f"\n{message}\nOutput: {out}\nLog: {Path(out).with_suffix('.log')}")
            self.status.set(message)
            self.open_csv_button.configure(state="normal")
            self.open_folder_button.configure(state="normal")
        else:
            self._say(f"\nFailed: {message}")
            self.status.set("Failed")
            messagebox.showerror(_TITLE, message)

    def open_csv(self) -> None:
        os.startfile(self.output_text.get())  # noqa: S606 - the file this window just wrote

    def open_folder(self) -> None:
        os.startfile(Path(self.output_text.get()).parent)  # noqa: S606 - the folder of that file

    def close(self) -> None:
        running = self.worker is not None and self.worker.is_alive()
        if not running or messagebox.askokcancel(_TITLE, "Still reading. Closing now stops it; what was read stays in the CSV."):
            self.root.destroy()


def run() -> int:
    try:  # sharp text on a scaled screen
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except (ImportError, AttributeError, OSError):
        pass
    root = tk.Tk()
    ReaderWindow(root)
    root.mainloop()
    return 0
