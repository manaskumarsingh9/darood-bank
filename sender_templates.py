"""
Deterministic per-sender template parsing (no AI).

Some senders post an identical, rigidly-structured daily message that the LLM
labeler cannot segment reliably — most notably the dot-delimited, whitespace-free
template from `+91 99264 85966`:

    09.04.2026.sufi.molana...abid.bhai.300.martba.darood.sharif.sabnam.bee.500.
    martba.darood.sharif.500.msrtba.surs.iklas.500.martba.kalma.sharif.

Because the shape is fixed, the counts can be read deterministically and assigned
to chants BY POSITION, which is more robust than parsing the corrupted chant text.
This removes the single biggest source of run-to-run divergence.

A message only uses the template when the number of chant counts it contains
(after stripping the date) exactly matches the template length; otherwise this
returns None and the message falls through to the normal labeling path / review.
"""
import reconcile  # reuse the date-stripping number extractor

# sender (as it appears in blocks `sender`) -> ordered chant per count slot
SENDER_TEMPLATES = {
    "+91 99264 85966": ["DAROOD", "DAROOD", "SURAH IKHLAS", "KALMA SHARIF"],
}

# Known per-sender count corruptions, applied after positional assignment:
# sender -> {(chant, corrupt_value): correct_value}
# `+91 99264 85966` sometimes writes their SURAH IKHLAS 500 as "50p"/"50".
SENDER_COUNT_FIX = {
    "+91 99264 85966": {("SURAH IKHLAS", 50): 500},
}


def is_template_sender(sender):
    return sender.strip() in SENDER_TEMPLATES


def extract(sender, text):
    """Return [{'chant','count'}, ...] for a known template sender, or None.

    None means "shape did not match the template" — do not force it; let the
    message go to the normal path / review.
    """
    sender = sender.strip()
    template = SENDER_TEMPLATES.get(sender)
    if template is None:
        return None

    counts = reconcile.numbers_in(text)  # date/list-marker stripped
    if len(counts) != len(template):
        return None  # shape mismatch -> not safe to apply positionally

    fixes = SENDER_COUNT_FIX.get(sender, {})
    out = []
    for chant, count in zip(template, counts):
        count = fixes.get((chant, count), count)
        out.append({"chant": chant, "count": count})
    return out
