"""Searchable, offline explanations and a live view of the profile's folders."""
import tkinter as tk
from tkinter import ttk
from .guide_content import TOPICS

SHORT_TITLES = dict(zip(
    ("overview", "folders", "launch", "demo", "setup", "ranges", "outputs", "delivery", "checks", "results", "daily", "appearance", "troubleshooting", "internals"),
    ("Project overview", "Folder meanings", "Install and launch", "First demo", "Real machine setup", "Cell ranges", "Formats and manual fields", "Deliver to MES", "Check and run", "Results and queues", "Daily routine", "Layouts and themes", "Troubleshooting", "Project structure")))


def search_topics(query):
    words = query.casefold().split()
    return [topic for topic in TOPICS
            if all(word in (topic["title"] + " " + topic["body"]).casefold() for word in words)]


class UserGuide(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.current_id = None
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Search the guide").pack(side="left")
        self.query = tk.StringVar(self)
        ttk.Entry(bar, textvariable=self.query).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(bar, text="Clear", command=lambda: self.query.set("")).pack(side="left")
        self.count = ttk.Label(self, style="Small.TLabel")
        self.count.pack(anchor="w", pady=(0, 6))
        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        topics = ttk.Frame(panes)
        self.topics = ttk.Treeview(topics, show="tree", selectmode="browse")
        self.topics.column("#0", width=app.px(220), minwidth=130, stretch=True)
        scroll = ttk.Scrollbar(topics, orient="vertical", command=self.topics.yview)
        self.topics.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.topics.pack(fill="both", expand=True)
        panes.add(topics, weight=1)
        content = ttk.Frame(panes, padding=(10, 0, 0, 0))
        actions = ttk.Frame(content)
        actions.pack(fill="x", pady=(0, 6))
        self.related = ttk.Button(actions, text="Open related page", command=self.open_related)
        self.related.pack(side="left")
        ttk.Button(actions, text="Refresh current paths", command=self.refresh).pack(side="left", padx=5)
        self.text = tk.Text(content, wrap="word", state="disabled", relief="flat", padx=12, pady=8,
                            font=(app.font_families["body"], 10), width=48)
        self.text.reading_text = True
        scroll = ttk.Scrollbar(content, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        panes.add(content, weight=3)
        self.topics.bind("<<TreeviewSelect>>", self._selected)
        self.query.trace_add("write", lambda *_args: self.filter())
        self.filter()

    def filter(self):
        results = search_topics(self.query.get())
        self.topics.delete(*self.topics.get_children())
        for topic in results:
            self.topics.insert("", "end", iid=topic["id"], text=SHORT_TITLES.get(topic["id"], topic["title"]))
        self.count.configure(text=f"{len(results)} topics • available offline • search folders, ranges, API or demo")
        if results:
            selected = self.current_id if any(topic["id"] == self.current_id for topic in results) else results[0]["id"]
            self.topics.selection_set(selected)
            self.show_topic(selected)
        else:
            self.current_id = None
            self.related.configure(state="disabled")
            self._write("No matching topics. Try a shorter search, such as backup, CSV or token.")

    def _selected(self, _event=None):
        selected = self.topics.selection()
        if selected:
            self.show_topic(selected[0])

    def show_topic(self, topic_id):
        topic = next((topic for topic in TOPICS if topic["id"] == topic_id), None)
        if topic is None:
            return
        self.current_id = topic_id
        if self.topics.exists(topic_id) and self.topics.selection() != (topic_id,):
            self.topics.selection_set(topic_id)
            self.topics.see(topic_id)
        self.related.configure(state="normal")
        text = topic["title"] + "\n\n" + topic["body"]
        if topic_id == "folders":
            text += "\n\nCURRENT PROFILE PATHS (values shown in this window)\n"
            for label, key in (("Machine input", "source_folder"), ("Back-up originals", "backup_folder"),
                               ("Shared output", "shared_folder")):
                text += f"\n{label}: {self.app.v[key].get().strip() or 'Not set — choose Machine setup / Delivery'}"
            text += f"\n\nApplication data: {self.app.data_dir}"
            if self.app.demo_session:
                text += f"\nDemo workspace: {self.app.demo_session.workspace}"
        self._write(text)

    def _write(self, text):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.configure(state="disabled")
        self.text.yview_moveto(0)

    def refresh(self):
        if self.current_id:
            self.show_topic(self.current_id)

    def open_related(self):
        topic = next((topic for topic in TOPICS if topic["id"] == self.current_id), None)
        if topic:
            self.app.workspace.go_to(topic["page"])
