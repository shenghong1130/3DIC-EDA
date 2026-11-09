# Input-only 3DIC router v0.12

Run: bash run.sh --def input.def --lef input.lef --thermal-report heat.json --output result.guide
Python 3.10+ standard library only. Two dies, M1-M20 under the official scope.
No saved GUIDE, scoring artifacts, network access or case-name lookup.

v0.12 reserves usable interface tracks on both M10/M11 for TSV demand rather
than applying an M10-pitch cap to M11. This is conservative for GCells larger
than the physical TSV and remains a resource heuristic, not an official scorer.
An input-driven parallel-layer escape pass lifts congested corridors by two
layers while retaining physical PIN contacts and lower-layer connections.
Accepted candidates preserve exact TSV candidates and pass single-net geometry
checks. The final full GUIDE validation includes hard TSV PIN/OBS exclusion.
The layer escape precedes the existing bounded congestion repair. All search
parameters apply uniformly to every input; hidden-case quality is unverified.

This is a practice package. No official score is claimed for this source ZIP.
The complete final contest documentation remains a separate deliverable.
