"""
Cards: in a theme with cards, a framed box is white on a grey page. The plain widgets inside it have to be white
too, or each would show a grey patch. Rather than give every frame and label in the program a style for it, this
walks the finished window once and gives the plain ones inside a box the matching Inner.* style from the theme.
"""
import re
import tkinter as tk

from .. import theme

# (widget class, its style now) -> the style it gets inside a card
INSIDE_A_CARD = {
    ("TFrame", ""): "Inner.TFrame",
    ("TLabel", ""): "Inner.TLabel",
    ("TLabel", "Small.TLabel"): "Inner.Small.TLabel",
    ("TCheckbutton", ""): "Inner.TCheckbutton",
    ("TRadiobutton", ""): "Inner.TRadiobutton",
}
TTK_CLASSES = {cls for cls, _ in INSIDE_A_CARD}


def restyle_cards(widget, inside=False):
    """Give every plain widget below a labelled box its card style. Returns how many were changed."""
    changed = 0
    for child in widget.winfo_children():
        cls = child.winfo_class()
        if cls == "Toplevel":
            changed += restyle_cards(child, False)  # a window of its own starts on the page again
            continue
        within = inside or cls == "TLabelframe"
        if cls == "TLabelframe" and str(child.cget("style")) in ("", "TLabelframe"):
            make_card(child)
        if within and cls in TTK_CLASSES:
            new = INSIDE_A_CARD.get((cls, str(child.cget("style"))))
            if new:
                child.configure(style=new)
                changed += 1
        changed += restyle_cards(child, within)
    return changed


def make_card(box):
    """A labelled box as a card: the title inside it without its number, and a thin edge all round.

    ttk cannot draw a box's border without running it through the title, so the box's own border is drawn in the
    card's colour and four one-pixel lines are laid over it. Being placed in the box, they hide and show with it.
    """
    box.configure(style="CardBox.TLabelframe",  # " 5  Shared folder " -> "Shared folder": cards are not numbered
                  text=re.sub(r"^\s*\d+\s+", "", str(box.cget("text"))).strip())
    box.card_edges = []
    for where in (dict(relwidth=1, height=1), dict(rely=1, y=-1, relwidth=1, height=1),
                  dict(relheight=1, width=1), dict(relx=1, x=-1, relheight=1, width=1)):
        line = tk.Frame(box.master, bg=theme.BORDER, takefocus=False)
        line.place(in_=box, bordermode="outside", **where)
        line.lift(box)
        box.card_edges.append(line)
