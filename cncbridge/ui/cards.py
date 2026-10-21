"""
Cards: in a theme with cards, a framed box is white on a grey page. The plain widgets inside it have to be white
too, or each would show a grey patch. Rather than give every frame and label in the program a style for it, this
walks the finished window once and gives the plain ones inside a box the matching Inner.* style from the theme.
"""
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
        if within and cls in TTK_CLASSES:
            new = INSIDE_A_CARD.get((cls, str(child.cget("style"))))
            if new:
                child.configure(style=new)
                changed += 1
        changed += restyle_cards(child, within)
    return changed
