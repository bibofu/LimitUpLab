"""Group exactly identical text without losing visible locations."""

from collections.abc import Sequence


def prepare_visible_surfaces(answer: str, drafts: Sequence[str]) -> tuple[
    list[dict], dict[str, str], dict[str, list[str]]
]:
    """Return compact wire surfaces, every original text, and duplicate-group IDs.

    The final answer wins canonical identity; other groups use their first draft.
    Equality is exact: no trimming, Unicode normalization, substring elimination,
    or interpretation of a candidate's content. Alias metadata is evaluator-owned.
    """
    texts = {"final": answer, **{f"draft_{index}": text for index, text in enumerate(drafts)}}
    surfaces, canonical_by_text, groups = [], {}, {}
    for surface_id, text in texts.items():
        canonical = canonical_by_text.get(text)
        if canonical is None:
            canonical_by_text[text] = surface_id
            groups[surface_id] = [surface_id]
            surfaces.append({"surface_id": surface_id, "text": text})
        else:
            groups[canonical].append(surface_id)
    aliases = {canonical: ids for canonical, ids in groups.items() if len(ids) > 1}
    for surface in surfaces:
        if surface["surface_id"] in aliases:
            surface["equivalent_surface_ids"] = list(aliases[surface["surface_id"]])
    return surfaces, texts, aliases
