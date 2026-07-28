#!/usr/bin/env python3
"""refiner.py (akdeniz, daily) — whole-sky map, refine the one spot, export an EMPIRICAL per-pixel
per-channel TRANSMITTANCE MAP (spot_tmap.npy) + a depth-vs-level curve (scurve) so the corrector
can DIVIDE by an exposure-resolved transmittance. Conservative: a failed fit keeps the prev model."""
import cv2, numpy as np, glob, os, json, csv, datetime
from collections import defaultdict
HOME=os.path.expanduser("~"); FR=HOME+"/refiner/frames"; OUT=HOME+"/refiner"; PHOT=OUT+"/photometry.csv"
SKY_ROWS=640; GW=480; gh=int(GW*SKY_ROWS/3840); FULL_W=3840; FULL_H=2160; NMAX=500
SEED_FX,SEED_FY=0.303,0.087; DRIFT_WARN=40.0; WINDOW_DAYS=7; MAP_DAYS=2; MAP_HALF=150
def srgb_lin(v): v=v/255.0; return np.where(v<=0.04045,v/12.92,((v+0.055)/1.055)**2.4)
def blur1d(a,s,ax):
    r=int(3*s); x=np.arange(-r,r+1); k=np.exp(-0.5*(x/s)**2); k/=k.sum()
    return np.apply_along_axis(lambda m:np.convolve(np.pad(m,r,mode="reflect"),k,"valid"),ax,a)
def build_map():
    cutoff=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=WINDOW_DAYS)).strftime("%Y%m%d")
    fs=sorted(f for f in glob.glob(FR+"/*.png") if os.path.basename(f)[:8]>=cutoff)
    if len(fs)>NMAX: fs=fs[::max(1,len(fs)//NMAX)][:NMAX]
    yy,xx=np.mgrid[0:gh,0:GW]; xn=(xx/GW).ravel(); yn=(yy/gh).ravel()
    E=np.column_stack([np.ones_like(xn),xn,yn,xn*xn,xn*yn,yn*yn,xn**3,xn*xn*yn,xn*yn*yn,yn**3])
    rs=[]
    for f in fs:
        im=cv2.imread(f)
        if im is None: continue
        g=srgb_lin(cv2.resize(im[:SKY_ROWS,:,1],(GW,gh)).astype(float))
        if np.median(g)<0.02 or np.median(g)>0.95: continue
        v=g.ravel(); c,*_=np.linalg.lstsq(E,v,rcond=None); bg=E@c
        rr=v-bg; keep=np.abs(rr)<1.5*(np.median(np.abs(rr-np.median(rr)))*1.4826+1e-6)
        c,*_=np.linalg.lstsq(E[keep],v[keep],rcond=None); bg=(E@c).reshape(gh,GW)
        rs.append(np.clip(g/np.maximum(bg,1e-4),0,2))
    return (np.clip(1-np.median(np.stack(rs),axis=0),-0.1,1.0), len(rs)) if len(rs)>=10 else (None,len(rs))
def refine_spot():
    D,n=build_map()
    if D is None: return {"gate":"map-fail"},n
    Dr=D-blur1d(blur1d(D,18,0),18,1)
    # RECURSIVE anchor (2026-07-28): search around the PREVIOUS fit, not the hardcoded
    # seed -- the blemish walked >120px from the seed and the fixed window/gate froze
    # the model while the served images kept showing the (moving) spot.
    try:
        _pm=json.load(open(OUT+"/spot_model.json"))["spots"][0]
        pfx,pfy=float(_pm["fx"]),float(_pm["fy"])
    except Exception:
        pfx,pfy=SEED_FX,SEED_FY
    yy,xx=np.mgrid[0:gh,0:GW]; ex=pfx*GW; ey=pfy*FULL_H/SKY_ROWS*gh
    win=np.sqrt((xx-ex)**2+(yy-ey)**2)<18
    Dw=np.where(win,np.clip(Dr,0,None),0); tot=float(Dw.sum())
    if Dw.max()<0.005 or tot<=0: return {"gate":"no-spot"},n
    cx=float((xx*Dw).sum()/tot); cy=float((yy*Dw).sum()/tot)
    sig=float(np.sqrt((((xx-cx)**2+(yy-cy)**2)*Dw).sum()/tot/2.0)); peak=float(Dr[int(round(cy)),int(round(cx))])
    fx=cx/GW; fy=(cy/gh*SKY_ROWS)/FULL_H; r_px=sig*(FULL_W/GW); fxo,fyo=fx*FULL_W,fy*FULL_H
    ok=abs(fxo-pfx*FULL_W)<60 and abs(fyo-pfy*FULL_H)<60 and 0.01<=peak<=0.15 and 20<=r_px<=160
    return {"gate":"pass" if ok else "reject","fx":round(fx,5),"fy":round(fy,5),"amp_pct":round(peak*100,2),"r_px":round(r_px,1),"full":[round(fxo),round(fyo)]},n
def build_tmap(cxf,cyf):
    cutoff=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=MAP_DAYS)).strftime("%Y%m%d")
    fs=sorted(f for f in glob.glob(FR+"/*.png") if os.path.basename(f)[:8]>=cutoff)
    if len(fs)>400: fs=fs[::max(1,len(fs)//400)][:400]
    h=MAP_HALF; x0,y0=int(round(cxf-h)),int(round(cyf-h))
    if x0<0 or y0<0: return None
    yy,xx=np.mgrid[0:2*h,0:2*h]; rb=np.sqrt((xx-h)**2+(yy-h)**2); ring=(rb>=105)&(rb<130); ay,ax=np.where(ring)
    A=np.column_stack([np.ones_like(ax),ax,ay,ax*ax,ax*ay,ay*ay]).astype(float)
    E=np.column_stack([np.ones((2*h)*(2*h)),xx.ravel(),yy.ravel(),(xx*xx).ravel(),(xx*yy).ravel(),(yy*yy).ravel()]).astype(float)
    acc={0:[],1:[],2:[]}
    for f in fs:
        im=cv2.imread(f)
        if im is None or y0+2*h>im.shape[0] or x0+2*h>im.shape[1]: continue
        for c in range(3):
            g=srgb_lin(im[y0:y0+2*h,x0:x0+2*h,c].astype(float)); m=np.median(g[ring])
            if m<0.03 or m>0.95: continue
            coef,*_=np.linalg.lstsq(A,g[ring],rcond=None); acc[c].append(np.clip(g/np.maximum((E@coef).reshape(2*h,2*h),1e-4),0,2))
    if min(len(acc[c]) for c in range(3))<15: return None
    T=np.stack([np.clip(np.median(np.stack(acc[c]),axis=0),0.5,1.05) for c in range(3)],axis=2).astype(np.float32)
    np.save(OUT+"/spot_tmap.npy",T)
    return {"x0":x0,"y0":y0,"half":h,"n":int(min(len(acc[c]) for c in range(3))),"min_T":round(float(T.min()),3)}
def fit_scurve():
    try: rows=list(csv.DictReader(open(PHOT)))
    except Exception: return None
    def ok(r): return r["patch"]=="spot" and r["gain"]=="0" and float(r["clip_hi"])<0.005 and float(r["clip_lo"])<0.005
    G=defaultdict(dict); refL=defaultdict(list)
    for r in rows:
        if not ok(r): continue
        try:
            if r["role"]=="ref": refL[r["ch"]].append(float(r["bg_fit"]))
            if r["role"] in ("br0","br1","br2"):
                b=int(datetime.datetime.fromisoformat(r["ts_utc"]).timestamp()//300)
                G[(b,r["ch"])][r["role"]]=(float(r["bg_fit"]),float(r["dip_lin"]))
        except Exception: pass
    knots=[35,95,145,190,235]; S={}; sref={}; lref={}
    for ch in "bgr":
        pts=[]
        for (b,c),bb in G.items():
            if c!=ch or len(bb)<3: continue
            ds=[bb[k][1] for k in ("br0","br1","br2")]; mn=float(np.mean(ds))
            if mn<=0.003: continue
            for role in ("br0","br1","br2"): L,d=bb[role]; pts.append((L,d/mn))
        cur=[]
        for k in knots:
            v=[r for (L,r) in pts if abs(L-k)<32]; cur.append(round(float(np.median(v)),3) if len(v)>=8 else None)
        known=[(k,c) for k,c in zip(knots,cur) if c is not None]
        if not known: return None
        cur=[c if c is not None else min(known,key=lambda t:abs(t[0]-kk))[1] for kk,c in zip(knots,cur)]
        S[ch]=cur; lr=float(np.median(refL[ch])) if refL[ch] else 124.0
        lref[ch]=round(lr,0); sref[ch]=round(float(np.interp(lr,knots,cur)),3)
    return {"level_knots":knots,"s":S,"l_ref":lref,"s_ref":sref}
def drift_status():
    try:
        rows=[r for r in csv.reader(open(OUT+"/drift.csv")) if len(r)>=4 and r[0][:1].isdigit()]
        rel=[r for r in rows if abs(float(r[3]))>=0.09][-288:]
        if not rel: return {"error":"no reliable drift rows"}
        dx,dy=float(rel[-1][1]),float(rel[-1][2]); mx=max(abs(float(r[1])) for r in rel); my=max(abs(float(r[2])) for r in rel)
        return {"dx":round(dx,2),"dy":round(dy,2),"max_dx_24h":round(mx,2),"max_dy_24h":round(my,2),"n_reliable":len(rel),"warn":bool(max(abs(dx),abs(dy),mx,my)>DRIFT_WARN)}
    except Exception as e: return {"error":str(e)}
def main():
    sp,n=refine_spot(); dr=drift_status()
    ts=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"); mp=OUT+"/spot_model.json"
    try: prev=json.load(open(mp))
    except Exception: prev=None
    if sp.get("gate")=="pass":
        tmap=build_tmap(sp["full"][0],sp["full"][1]); sc=fit_scurve()
        model={"updated":ts,"frames":n,"spots":[{k:sp[k] for k in ("fx","fy","amp_pct","r_px")}],"drift":dr}
        if tmap: model["tmap"]=tmap
        if sc: model["scurve"]=sc
        json.dump(model,open(mp,"w"),indent=2)
        print("REFINE pass: full=%s amp=%.2f%% r=%.0f frames=%d tmap=%s scurve=%s"%(sp["full"],sp["amp_pct"],sp["r_px"],n,bool(tmap),bool(sc)))
    else:
        if prev is not None: prev["drift"]=dr; prev["last_check"]=ts; json.dump(prev,open(mp,"w"),indent=2)
        print("REFINE reject (%s) frames=%d — kept previous"%(sp.get("gate"),n))
    print(("AIM-DRIFT WARN " if dr.get("warn") else "aim drift OK ")+json.dumps(dr))
if __name__=="__main__": main()
