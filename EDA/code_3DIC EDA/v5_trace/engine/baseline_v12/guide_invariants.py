"""Input-only comparisons of net membership and TSV candidate geometry."""
from itertools import zip_longest
from guide_rules import tsv_candidates
from validate import read_guide


def interface_signature(rects):
    return tuple(sorted(r for r in rects if r[4] in (10, 11)))


def check_frozen_interfaces(reference, candidate):
    nets = rectangles = 0
    for old, new in zip_longest(read_guide(reference), read_guide(candidate)):
        if old is None or new is None or old[0] != new[0]:
            raise ValueError('Reference/candidate net order or membership differs')
        if interface_signature(old[1]) != interface_signature(new[1]):
            raise ValueError(f'{old[0]}: M10/M11 differs from official accepted reference')
        nets += 1
        rectangles += len(interface_signature(old[1]))
    return dict(status='FROZEN_INTERFACE_PASS', nets=nets,
                interface_rectangles=rectangles,
                scope='M10/M11 geometry only; requires separate complete validation')


def check_tsv_geometry(reference, candidate):
    """Preserve exact candidate boxes; ordinary interface signals may change."""
    nets=0
    for old,new in zip_longest(read_guide(reference),read_guide(candidate)):
        if old is None or new is None or old[0]!=new[0]:
            raise ValueError('Reference/candidate net order or membership differs')
        if tsv_candidates(old[1])!=tsv_candidates(new[1]):
            raise ValueError(f'{old[0]}: TSV candidate geometry changed')
        nets+=1
    return dict(status='TSV_GEOMETRY_PASS',nets=nets,
                scope='Exact candidate boxes only; complete pin/connectivity checks required')


