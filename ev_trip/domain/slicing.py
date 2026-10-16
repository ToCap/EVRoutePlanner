"""Trip split into slices of at most 10 km, cut at each road-type change."""
from __future__ import annotations

from ev_trip.domain.models import MicroSegment, Slice

_EPS = 1e-9


def smooth_types(segments: list[MicroSegment], min_run_km: float) -> list[MicroSegment]:
    """Absorbs runs shorter than `min_run_km` surrounded by the same road type."""
    if min_run_km <= 0 or len(segments) < 3:
        return list(segments)

    # Grouping into runs (type, start, end).
    runs: list[list] = []
    for seg in segments:
        if runs and runs[-1][0] == seg.road_type:
            runs[-1][2] = seg.end_km
        else:
            runs.append([seg.road_type, seg.start_km, seg.end_km])

    changed = True
    while changed:
        changed = False
        for i in range(1, len(runs) - 1):
            rtype, start, end = runs[i]
            if end - start < min_run_km - _EPS and runs[i - 1][0] == runs[i + 1][0]:
                runs[i - 1][2] = runs[i + 1][2]
                del runs[i : i + 2]
                changed = True
                break
    return [MicroSegment(s, e, t) for t, s, e in runs]


def build_slices(segments: list[MicroSegment], max_slice_km: float = 10.0) -> list[Slice]:
    """Merges consecutive same-type segments into slices of at most `max_slice_km`."""
    if max_slice_km <= 0:
        raise ValueError("La longueur maximale d'une tranche doit être > 0.")

    slices: list[Slice] = []
    cur_type: str | None = None
    cur_start = cur_end = 0.0

    def close() -> None:
        nonlocal cur_type
        if cur_type is not None and cur_end - cur_start > _EPS:
            slices.append(Slice(len(slices) + 1, cur_start, cur_end, cur_type))
        cur_type = None

    for seg in segments:
        pos = seg.start_km
        while seg.end_km - pos > _EPS:
            if cur_type is not None and seg.road_type != cur_type:
                close()
            if cur_type is None:
                cur_type, cur_start, cur_end = seg.road_type, pos, pos
            room = max_slice_km - (cur_end - cur_start)
            if room <= _EPS:
                close()
                continue
            take = min(room, seg.end_km - pos)
            pos += take
            cur_end = pos
    close()
    return slices
