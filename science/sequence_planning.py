"""Cheap discovery ranking only; scientific acceptance remains FITS/calibration based."""
import json, math
import observation_service as observations

def rank_candidates(ids, ra, dec, budget=100):
    rows={}
    with observations.db() as db:
        for start in range(0,len(ids),400):
            part=ids[start:start+400]
            rows.update((key,json.loads(record)) for key,record in db.execute(
                'SELECT id,record FROM products WHERE id IN ('+','.join('?' for _ in part)+')',part))
    def time(key):
        v=rows.get(key,{}).get('sia',{}).get('t_min')
        return float(v) if isinstance(v,(int,float)) and math.isfinite(v) else None
    def quality(key):
        record=rows.get(key,{});v=record.get('validation',{})
        return 0 if v.get('scientific_acceptance',{}).get('accepted') is True else 2 if v.get('scientific_acceptance',{}).get('accepted') is False else 1
    # Farthest-first epochs within each detector, interleaved across detectors.
    groups={}
    for key in dict.fromkeys(ids):groups.setdefault(rows.get(key,{}).get('sia',{}).get('energy_bandpassname','unknown'),[]).append(key)
    ranked=[]
    for detector,group in sorted(groups.items()):
        remaining=sorted(group,key=lambda k:(quality(k),time(k) if time(k) is not None else math.inf,k));ordered=[];epochs=[]
        gaps={k:0. for k in remaining}
        while remaining and len(ordered)<budget:
            key=min(remaining,key=lambda k:(quality(k),-gaps[k],k))
            remaining.remove(key);ordered.append(key)
            if time(key) is not None:
                for other in remaining:
                    if time(other) is not None:
                        distance=abs(time(other)-time(key))
                        gaps[other]=min(gaps[other],distance) if epochs else distance
                epochs.append(time(key))
        ranked.append(ordered)
    return [group[i] for i in range(max(map(len,ranked),default=0)) for group in ranked if i<len(group)]
