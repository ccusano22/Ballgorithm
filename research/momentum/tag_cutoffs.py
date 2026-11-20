import os
import sys, math, collections
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rw_eb import walk, shr
a = walk("2024-25", 15)[0]; b = walk("2025-26", 15)[0]; pool = a + b
for cuts in ([(0.4,"Hot"),(0.15,"Warming"),(-0.15,"Steady"),(-0.4,"Cooling"),(-99,"Cold")],
             [(0.75,"Hot"),(0.25,"Warming"),(-0.25,"Steady"),(-0.75,"Cooling"),(-99,"Cold")],
             [(1.0,"Hot"),(0.35,"Warming"),(-0.35,"Steady"),(-1.0,"Cooling"),(-99,"Cold")]):
    print([c for c,_ in cuts[:4]])
    for lab, sel in (("all", lambda d: True), ("Feb-Mar", lambda d: d[5:7] in ("02","03"))):
        g = collections.defaultdict(list); tot = 0
        for r in pool:
            if not sel(r["date"]): continue
            for side, sign in (("h", 1), ("a", -1)):
                mi, _ = shr(r[side], 1.0); tot += 1
                g[next(l for c, l in cuts if mi >= c)].append((sign * r["e"], mi))
        line = []
        for _, k in cuts:
            v = np.array([x for x, _ in g[k]]); m = np.array([y for _, y in g[k]])
            line.append(f"{k} {len(v)/tot*100:.0f}% idx{m.mean():+.2f} nxt{v.mean():+.2f}±{v.std()/math.sqrt(max(1,len(v))):.2f} {np.mean(v>0)*100:.1f}%")
        print("  ", lab, " | ".join(line))
